import json
import tempfile
import unittest
from pathlib import Path

from report import cpu, distribution, read_trial


def memory(ticks):
    fields = ["0"] * 30
    fields[0] = "R"
    fields[11] = str(ticks)
    fields[12] = "0"
    stat = "1 (sample process) " + " ".join(fields)
    status = ["VmRSS:\t4096 kB", "VmHWM:\t8192 kB"]
    return {
        "procStat": stat,
        "procStatus": status,
        "hostProcStat": stat,
        "hostProcStatus": status,
        "dart": {"heapUsage": 1048576},
    }


class ReportTest(unittest.TestCase):
    def test_nearest_rank_tails_and_empty_samples(self):
        self.assertEqual(distribution([]), {"n": 0})
        self.assertEqual(distribution([7])["p99.9"], 7)
        self.assertEqual(
            distribution(range(1, 1001)),
            {
                "n": 1000,
                "p50": 500,
                "p95": 950,
                "p99": 990,
                "p99.9": 999,
                "max": 1000,
            },
        )

    def test_process_cpu_units_and_names_with_spaces(self):
        self.assertEqual(cpu(memory(123)), 1.23)
        self.assertEqual(cpu(memory(123), ticks=50, host=True), 2.46)

    def test_active_window_preserves_long_pauses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {
                "sample-open-frame-timings": {"frames": []},
                "sample-open-memory": [memory(0), memory(10), memory(11)],
                "sample-scroll-memory": [memory(11), memory(34)],
                "sample-scroll-retained-memory": memory(35),
                "sample-scroll-frames": {
                    "profiled": False,
                    "frames": [
                        {
                            "startTime": 1220000,
                            "build": 1000,
                            "raster": 2000,
                            "elapsed": 3000,
                        }
                    ],
                    "gestures": [
                        {
                            "index": 0,
                            "injection": {
                                "downMicros": 1000000,
                                "upMicros": 2000000,
                                "injectionMode": 1,
                                "moves": 119,
                                "moveMicros": list(range(1008000, 1960000, 8000)),
                            },
                            "presentations": [
                                1000000000,
                                1200000000,
                                1216000000,
                                1236000000,
                                1800000000,
                                1900000000,
                            ],
                        }
                    ],
                },
            }
            for name, value in values.items():
                (root / (name + ".json")).write_text(json.dumps(value))
            result, raw = read_trial(root, "sample", 1, 100)
            self.assertEqual(raw["whole"]["gaps"], [16, 20, 564])
            self.assertEqual(result["whole"]["displayMs"]["max"], 564)
            self.assertEqual(result["whole"]["cpuSeconds"], 0.23)
            self.assertEqual(result["whole"]["processHighWaterRssMiB"], 8)
            self.assertEqual(result["whole"]["missedOpportunities"], 33)
            self.assertEqual(result["retained"], {"heapMiB": 1, "rssMiB": 4})
            self.assertEqual(result["validityIssues"], [])


if __name__ == "__main__":
    unittest.main()
