"""Run only on a fresh CI emulator, through the normal app and synthetic API."""

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from report import read_trial

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ["BENCH_OUT"])
TOOLS = Path(os.environ["BENCH_TOOLS"])
APKS = Path(os.environ["BENCH_APKS"])
SDK = Path(os.environ["ANDROID_HOME"])
PACKAGE = "app.cogwheel.conduit"


def adb(*args, check=True):
    return subprocess.run(
        [str(SDK / "platform-tools/adb"), "-s", "emulator-5554", *map(str, args)],
        check=check,
        capture_output=True,
        timeout=40,
    ).stdout


def nodes():
    adb("shell", "uiautomator", "dump", "/sdcard/scroll-benchmark-ui.xml")
    raw = adb("exec-out", "cat", "/sdcard/scroll-benchmark-ui.xml")
    (OUT / "latest-ui.xml").write_bytes(raw)
    return list(ET.fromstring(raw).iter("node"))


def label(node):
    return node.get("text") or node.get("content-desc") or ""


def button(items, names):
    matches = [n for n in items if label(n) in names and n.get("enabled") == "true"]
    return next((n for n in matches if n.get("clickable") == "true"), None)


def tap(node):
    x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", node.get("bounds")))
    assert 0 <= x1 < x2 <= 1080 and 0 <= y1 < y2 <= 2400
    adb("shell", "input", "tap", (x1 + x2) // 2, (y1 + y2) // 2)


def field(node, value):
    tap(node)
    adb("shell", "input", "keycombination", "113", "29")
    adb("shell", "input", "keyevent", "67")
    adb("shell", "input", "text", value)
    adb("shell", "input", "keyevent", "4")


def wait_for(predicate):
    for _ in range(25):
        result = predicate(nodes())
        if result:
            return result
        time.sleep(0.3)
    raise RuntimeError("Expected app state did not appear; see latest-ui.xml")


def sidebar(items):
    return button(items, {"Sidebar", "Open sidebar", "Open navigation menu", "Menu"})


def login():
    items = nodes()
    if sidebar(items):
        return
    provider = next(
        (
            n
            for n in items
            if label(n).startswith("Open WebUI") and n.get("clickable") == "true"
        ),
        None,
    )
    if provider is not None:
        tap(provider)
    address = wait_for(
        lambda ns: next(
            (n for n in ns if n.get("class") == "android.widget.EditText"), None
        )
    )
    field(address, "http://127.0.0.1:18896")
    tap(wait_for(lambda ns: button(ns, {"Connect", "Continue"})))
    edits = wait_for(
        lambda ns: (
            [n for n in ns if n.get("class") == "android.widget.EditText"]
            if len([n for n in ns if n.get("class") == "android.widget.EditText"]) >= 2
            else None
        )
    )
    password = next(
        (
            n
            for n in edits
            if n.get("password") == "true" or "password" in label(n).lower()
        ),
        edits[-1],
    )
    email = next(n for n in edits if n is not password)
    field(email, "demo@example.test")
    # Refresh bounds after the keyboard changes the layout.
    password = wait_for(
        lambda ns: next(
            (
                n
                for n in ns
                if n.get("class") == "android.widget.EditText"
                and (n.get("password") == "true" or "password" in label(n).lower())
            ),
            None,
        )
    )
    field(password, "synthetic-shapes")
    tap(wait_for(lambda ns: button(ns, {"Sign In", "Continue"})))
    wait_for(sidebar)


def prepare_vm():
    old = OUT / "vm-forward-port"
    if old.exists():
        adb("forward", "--remove", "tcp:" + old.read_text().strip(), check=False)
    pid = adb("shell", "pidof", PACKAGE).decode().strip()
    raw = adb("logcat", "-d", "--pid=" + pid).decode(errors="replace")
    urls = re.findall(r"Dart VM service is listening on (http://[^\s]+)", raw)
    if not urls:
        raise RuntimeError("Profile VM service was not advertised")
    uri = urllib.parse.urlparse(urls[-1])
    port = adb("forward", "tcp:0", "tcp:" + str(uri.port)).decode().strip()
    old.write_text(port)
    # These ephemeral service credentials are excluded from uploaded artifacts.
    (OUT / "vm-host-uri").write_text(
        "ws://127.0.0.1:" + port + uri.path.rstrip("/") + "/ws"
    )


def available_memory():
    text = Path("/proc/meminfo").read_text()
    return int(re.search(r"MemAvailable:\s+(\d+)", text).group(1))


def trial(name, fixture, count):
    assert available_memory() >= 3 * 1024 * 1024, "Host memory headroom is too low"
    assert not (OUT / (name + "-scroll-frames.json")).exists()
    adb("shell", "am", "force-stop", PACKAGE)
    adb("shell", "am", "start", "-n", PACKAGE + "/.MainActivity")
    time.sleep(3)
    tap(wait_for(sidebar))
    title = fixture.title() + " scroll fixture"
    target = wait_for(lambda ns: next((n for n in ns if label(n) == title), None))
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", target.get("bounds")))
    (OUT / "selection-tap.json").write_text(
        json.dumps([(x1 + x2) // 2, (y1 + y2) // 2])
    )
    prepare_vm()
    print("Starting " + name, flush=True)
    with (OUT / (name + "-capture.log")).open("x") as log:
        for tool, args in [
            ("open_capture", [name + "-open"]),
            ("capture_scroll", [name + "-scroll", str(count)]),
        ]:
            subprocess.run(
                [str(TOOLS / tool), str(OUT), *args],
                stdout=log,
                stderr=log,
                check=True,
                timeout=130,
            )
    result, _ = read_trial(OUT, name, count, os.sysconf("SC_CLK_TCK"))
    assert not result["validityIssues"], result["validityIssues"]
    print("Finished " + name, flush=True)


def install(build, manifest):
    apk = APKS / (build + ".apk")
    assert hashlib.sha256(apk.read_bytes()).hexdigest() == manifest["apkSha256"][build]
    adb("shell", "am", "force-stop", PACKAGE)
    adb("install", "-r", apk)
    installed = (
        adb("shell", "pm", "path", PACKAGE).decode().strip().removeprefix("package:")
    )
    assert (
        installed.startswith("/data/app/")
        and installed.endswith("/base.apk")
        and "\n" not in installed
    )
    actual = adb("shell", "sha256sum", installed).decode().split()[0]
    assert actual == manifest["apkSha256"][build]


def main():
    assert os.environ.get("GITHUB_ACTIONS") == "true", (
        "This runner uses a disposable CI device only"
    )
    plan = json.loads((OUT / "plan.json").read_text())
    manifest = json.loads((OUT / "builds.json").read_text())
    assert (
        plan["baseline"] == manifest["baseline"]
        and plan["candidate"] == manifest["candidate"]
    )
    assert manifest["sdk"]["frameworkVersion"] == plan["flutter"]
    emulator_pids = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if (process / "comm").read_text().startswith("qemu-system"):
                emulator_pids.append(process.name)
        except FileNotFoundError:
            pass  # An unrelated short-lived process exited during enumeration.
    assert len(emulator_pids) == 1, "Expected exactly one disposable emulator"
    os.environ["BENCH_EMULATOR_PID"] = emulator_pids[0]
    adb("reverse", "tcp:18896", "tcp:18911")
    adb("shell", "wm", "size", "1080x2400")
    adb("shell", "wm", "density", "440")
    adb("shell", "svc", "power", "stayon", "true")
    environment = {
        "architecture": adb("shell", "getprop", "ro.product.cpu.abi").decode().strip(),
        "display": adb("shell", "wm", "size").decode(),
        "density": adb("shell", "wm", "density").decode(),
        "clockTicks": adb("shell", "getconf", "CLK_TCK").decode().strip(),
        "hostClockTicks": os.sysconf("SC_CLK_TCK"),
        "hostAvailableKiB": available_memory(),
    }
    assert (
        environment["architecture"] == "x86_64" and environment["clockTicks"] == "100"
    )
    (OUT / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    classes = TOOLS / "classes"
    dex = TOOLS / "input"
    classes.mkdir()
    dex.mkdir()
    android = SDK / "platforms/android-35/android.jar"
    subprocess.run(
        [
            "javac",
            "--release",
            "8",
            "-cp",
            str(android),
            "-d",
            str(classes),
            str(HERE / "ScrollInput.java"),
        ],
        check=True,
    )
    d8 = max(
        SDK.glob("build-tools/*/d8"),
        key=lambda p: tuple(map(int, re.findall(r"\d+", p.parent.name))),
    )
    subprocess.run(
        [
            str(d8),
            "--lib",
            str(android),
            "--output",
            str(dex),
            str(classes / "ScrollInput.class"),
        ],
        check=True,
    )
    adb("push", dex / "classes.dex", "/data/local/tmp/conduit-scroll-input.dex")
    with (OUT / "fixture-server.log").open("w") as log:
        server = subprocess.Popen(
            ["python3", str(HERE / "fixture_server.py")], stdout=log, stderr=log
        )
        try:
            for attempt in range(20):
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:18911/health", timeout=1
                    ) as response:
                        assert json.load(response)["status"]
                    break
                except OSError:
                    if attempt == 19:
                        raise
                    time.sleep(0.2)
            install("a", manifest)
            adb(
                "shell",
                "pm",
                "grant",
                PACKAGE,
                "android.permission.POST_NOTIFICATIONS",
                check=False,
            )
            adb("shell", "am", "start", "-n", PACKAGE + "/.MainActivity")
            time.sleep(3)
            login()
            current = "a"
            for build in ("a", "b"):
                if current != build:
                    install(build, manifest)
                    current = build
                trial("warmup-" + build + "-mixed", "mixed", 12)
            plan["status"] = "Running fixed qualification cohort"
            (OUT / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
            for label_name in plan["order"]:
                if current != label_name[0]:
                    install(label_name[0], manifest)
                    current = label_name[0]
                for phase in plan["phases"]:
                    trial(
                        label_name + "-" + phase["fixture"],
                        phase["fixture"],
                        phase["gestures"],
                    )
            plan["status"] = "Completed fixed cohort; all runs retained"
            (OUT / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
            subprocess.run(["python3", str(HERE / "report.py"), str(OUT)], check=True)
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        (OUT / "failure.txt").write_text(
            type(error).__name__ + ": " + str(error) + "\n"
        )
        (OUT / "failure.png").write_bytes(
            adb("exec-out", "screencap", "-p", check=False)
        )
        raise
