"""Keep every fixed-cohort sample, including input stalls and display outliers."""

import itertools
import json
import math
import statistics
import sys
from pathlib import Path


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        **{
            key: round(values[math.ceil(len(values) * q) - 1], 3)
            for key, q in [
                ("p50", 0.5),
                ("p95", 0.95),
                ("p99", 0.99),
                ("p99.9", 0.999),
                ("max", 1),
            ]
        },
    }


def cpu(sample, ticks=100, host=False):
    fields = sample["hostProcStat" if host else "procStat"].split(") ", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / ticks


def rss(sample, key="VmRSS", host=False):
    lines = sample["hostProcStatus" if host else "procStatus"]
    return (
        int(next(line for line in lines if line.startswith(key + ":")).split()[1])
        / 1024
    )


def load(root, name):
    return json.loads((root / (name + ".json")).read_text())


def read_trial(root, name, expected_count, host_ticks):
    opening_frames = load(root, name + "-open-frame-timings")["frames"]
    opening_memory = load(root, name + "-open-memory")
    data = load(root, name + "-scroll-frames")
    memory = load(root, name + "-scroll-memory")
    retained = load(root, name + "-scroll-retained-memory")
    assert len(data["gestures"]) == expected_count and len(memory) == expected_count + 1
    assert not data["profiled"]
    sections = [("whole", 0, expected_count)]
    if expected_count == 24:
        sections += [("first", 0, 12), ("repeat", 12, 24)]
    result, raw = {}, {}
    issues = []
    for section, start, end in sections:
        gaps, frames, moves = [], [], []
        for gesture in data["gestures"][start:end]:
            event = gesture["injection"]
            if event["injectionMode"] != 1 or event["moves"] < 110:
                issues.append(
                    f"Gesture {gesture['index']}: input did not sustain the planned rate"
                )
            a, b = event["downMicros"] + 200000, event["upMicros"] - 200000
            times = [t for t in gesture["presentations"] if a <= t / 1000 <= b]
            if len(times) < 2:
                issues.append(
                    f"Gesture {gesture['index']}: fewer than two steady-window presentations"
                )
            gaps.extend((y - x) / 1e6 for x, y in itertools.pairwise(times))
            frames.extend(f for f in data["frames"] if a <= f["startTime"] <= b)
            ts = event["moveMicros"]
            moves.extend((y - x) / 1000 for x, y in itertools.pairwise(ts))
        result[section] = {
            "displayMs": distribution(gaps),
            **{
                key + "Ms": distribution([f[key] / 1000 for f in frames])
                for key in ("build", "raster", "elapsed")
            },
            "cpuSeconds": round(cpu(memory[end]) - cpu(memory[start]), 4),
            "emulatorCpuSeconds": round(
                cpu(memory[end], host_ticks, True)
                - cpu(memory[start], host_ticks, True),
                4,
            ),
            "processHighWaterRssMiB": rss(memory[end], "VmHWM"),
            "emulatorHighWaterRssMiB": rss(memory[end], "VmHWM", True),
            "missedOpportunities": sum(
                max(0, round(v / (1000 / 60)) - 1) for v in gaps
            ),
            "inputMoveMs": distribution(moves),
        }
        raw[section] = {"gaps": gaps, "frames": frames, "moves": moves}
    result["retained"] = {
        "heapMiB": retained["dart"]["heapUsage"] / 1048576,
        "rssMiB": rss(retained),
    }
    result["opening"] = {
        "frameCount": len(opening_frames),
        "cpuSeconds": round(cpu(opening_memory[1]) - cpu(opening_memory[0]), 4),
        "emulatorCpuSeconds": round(
            cpu(opening_memory[1], host_ticks, True)
            - cpu(opening_memory[0], host_ticks, True),
            4,
        ),
        "processHighWaterRssMiB": rss(opening_memory[1], "VmHWM"),
        "retainedRssMiB": rss(opening_memory[2]),
        "retainedHeapMiB": opening_memory[2]["dart"]["heapUsage"] / 1048576,
        **{
            key + "Ms": distribution([f[key] / 1000 for f in opening_frames])
            for key in ("build", "raster", "elapsed")
        },
    }
    result["validityIssues"] = sorted(set(issues))
    return result, raw


def samples(values):
    return {
        "values": values,
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "max": max(values),
    }


def report(root):
    plan = load(root, "plan")
    assert plan["status"] == "Completed fixed cohort; all runs retained"
    environment = load(root, "environment")
    runs, raw = {}, {}
    for label in plan["order"]:
        runs[label], raw[label] = {}, {}
        for phase in plan["phases"]:
            kind = phase["fixture"]
            runs[label][kind], raw[label][kind] = read_trial(
                root,
                label + "-" + kind,
                phase["gestures"],
                environment["hostClockTicks"],
            )
    aggregate = {}
    for build in ("a", "b"):
        labels = [label for label in plan["order"] if label.startswith(build)]
        aggregate[build] = {}
        for phase in plan["phases"]:
            kind = phase["fixture"]
            rows = [runs[label][kind] for label in labels]
            out = {}
            for section in (
                ("whole", "first", "repeat") if phase["gestures"] == 24 else ("whole",)
            ):
                combined = {
                    key: [
                        value
                        for label in labels
                        for value in raw[label][kind][section][key]
                    ]
                    for key in ("gaps", "frames", "moves")
                }
                summary = {
                    "displayMs": distribution(combined["gaps"]),
                    "inputMoveMs": distribution(combined["moves"]),
                }
                summary.update(
                    {
                        key + "Ms": distribution(
                            [f[key] / 1000 for f in combined["frames"]]
                        )
                        for key in ("build", "raster", "elapsed")
                    }
                )
                for key in (
                    "cpuSeconds",
                    "emulatorCpuSeconds",
                    "processHighWaterRssMiB",
                    "emulatorHighWaterRssMiB",
                ):
                    summary[key] = samples([row[section][key] for row in rows])
                summary["missedOpportunities"] = sum(
                    row[section]["missedOpportunities"] for row in rows
                )
                out[section] = summary
            out["retained"] = {
                key: samples([row["retained"][key] for row in rows])
                for key in ("heapMiB", "rssMiB")
            }
            out["opening"] = {
                key: samples([row["opening"][key] for row in rows])
                for key in (
                    "frameCount",
                    "cpuSeconds",
                    "emulatorCpuSeconds",
                    "processHighWaterRssMiB",
                    "retainedRssMiB",
                    "retainedHeapMiB",
                )
            }
            aggregate[build][kind] = out
    result = {
        "plan": plan,
        "builds": load(root, "builds"),
        "environment": environment,
        "runs": runs,
        "aggregate": aggregate,
        "limitations": [
            "A software-rendered emulator is not a physical-phone FPS measurement.",
            "Opening timing percentiles have different sample counts when needless frames are removed.",
            "Process VmHWM includes all earlier work since process start. Emulator VmHWM is cumulative across the entire cohort; use it as a capacity diagnostic, not a per-build allocation comparison.",
            "Frame intervals use only active steady windows; no idle gaps are classified as stutters.",
            "Inspect every run and validity issue before drawing any publication conclusion.",
        ],
    }
    (root / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    for build, fixtures in aggregate.items():
        for kind, row in fixtures.items():
            print(
                json.dumps(
                    {
                        "build": build,
                        "fixture": kind,
                        "displayMs": row["whole"]["displayMs"],
                        "cpuSeconds": row["whole"]["cpuSeconds"],
                        "retained": row["retained"],
                    }
                )
            )


if __name__ == "__main__":
    report(Path(sys.argv[1]))
