"""Real-device end-to-end test against a live adbperfetto server.

Drives the HTTP API of a normal (non-demo) server against the attached
KN8 (KN80000000000123):

  a. record + MobileLog -> adb kill-server -> assert disconnected ->
     adb start-server -> reconnect -> stop -> zip + checklist OK +
     Modem/Network/GPS state restored
  b. record -> hard-kill the server -> restart -> session resumes ->
     stop -> done
  c. duration end -> auto pull to done
  d. flight -> 2 snapshots -> stop with final snapshot -> 3 files
  e. orphan detached session planted via adb -> detected -> adopt ->
     stop -> pulled

Leaves the phone clean. Run: .venv\\Scripts\\python tools\\device_e2e.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8973
BASE = f"http://127.0.0.1:{PORT}"
SERIAL = "KN80000000000123"
CFG_REMOTE = "/data/misc/perfetto-configs/adbperfetto_orphan_test.pbtx"
TRACE_REMOTE = "/data/misc/perfetto-traces/adbperfetto_orphan_test.pftrace"

sys.path.insert(0, str(ROOT))
from adbperfetto import mobilelog, pbtx  # noqa: E402

OUT = Path(tempfile.mkdtemp(prefix="adbperfetto_e2e_"))


def http(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def wait(pred, what="condition", timeout=60, interval=0.5):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = pred()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timeout: {what}; last={last}")


def _decode(raw):
    return raw.decode("utf-8", errors="replace") if raw else ""


def adb(*args, timeout=30, input_text=None):
    cmd = ["adb", "-s", SERIAL, *args]
    result = subprocess.run(
        cmd, capture_output=True, timeout=timeout,
        input=(input_text.encode("utf-8") if input_text else None))
    result.stdout = _decode(result.stdout)
    result.stderr = _decode(result.stderr)
    return result


def adb_host(*args, timeout=30):
    result = subprocess.run(["adb", *args], capture_output=True,
                            timeout=timeout)
    result.stdout = _decode(result.stdout)
    result.stderr = _decode(result.stderr)
    return result


def shell(cmd, timeout=30, input_text=None):
    return adb("shell", cmd, timeout=timeout, input_text=input_text)


def device():
    for dev in http("/api/state")["devices"]:
        if dev["serial"] == SERIAL:
            return dev
    return None


def status():
    dev = device()
    sess = dev and dev.get("session")
    return sess["status"] if sess else None


def other_logs_mask():
    result = shell("dumpsys notification --noredact", timeout=15)
    bigtext = mobilelog.extract_logger_bigtext(result.stdout)
    return mobilelog.parse_other_logs(bigtext)


class E2E:
    def __init__(self):
        self.proc = None
        self.results = []

    def check(self, name, ok, detail=""):
        self.results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'} {name} {detail}")
        if not ok:
            raise AssertionError(f"{name}: {detail}")

    def start_server(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "adbperfetto",
             "--no-browser", "--port", str(PORT)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait(lambda: self._ping(), "server up", 40)

    def _ping(self):
        try:
            return http("/api/ping")["app"] == "adbperfetto"
        except Exception:
            return False

    def stop_server(self):
        try:
            http("/api/quit", {})
        except Exception:
            pass
        if self.proc:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def clean_start(self):
        """Make sure the serial has no tracked session or orphan."""
        for _ in range(30):
            dev = device()
            if dev is None:
                time.sleep(0.5)
                continue
            if dev.get("orphan"):
                http(f"/api/devices/{SERIAL}/abandon", {})
                time.sleep(0.5)
                continue
            if dev.get("session"):
                http(f"/api/devices/{SERIAL}/dismiss", {})
                time.sleep(0.5)
                continue
            return
        raise AssertionError("could not reach an empty device state")

    def wait_done(self, timeout=90):
        return wait(lambda: status() in ("done", "ended", "failed"),
                    "done", timeout)

    # ------------------------------------------------------------ steps

    # Kill the adb server, then keep TCP 5037 bound so the daemon
    # cannot auto-restart for `seconds` — simulates a PC-side usb/adb
    # loss while the cable stays physically connected.
    _BLOCKER = (
        "import socket,subprocess,time\n"
        "end=time.time()+%(secs)s\n"
        "sock=None\n"
        "while time.time()<end:\n"
        "    if sock is None:\n"
        "        try:\n"
        "            sock=socket.socket()\n"
        "            sock.bind(('127.0.0.1',5037))\n"
        "            sock.listen(4)\n"
        "        except OSError:\n"
        "            subprocess.run(['adb','kill-server'],"
        "capture_output=True)\n"
        "            time.sleep(0.3)\n"
        "    else:\n"
        "        time.sleep(0.3)\n")

    def adb_outage(self, seconds=15):
        adb_host("kill-server")
        time.sleep(0.3)
        blocker = subprocess.Popen(
            [sys.executable, "-c",
             self._BLOCKER % {"secs": seconds}])
        return blocker

    def step_a(self, mask_before):
        print("a: record + mobilelog + usb loss")
        http(f"/api/devices/{SERIAL}/record",
             {"preset_id": "syshealth_google",
              "duration_min": 2, "mobilelog": True})
        wait(lambda: status() == "recording", "recording")
        time.sleep(20)
        blocker = self.adb_outage(15)
        dev = wait(lambda: (d := device())
                   and d.get("session")
                   and d["session"].get("connected") is False
                   and d, "session disconnected", 20)
        blocker.wait(timeout=25)
        self.check("a disconnected state",
                   dev["state"] == "disconnected",
                   dev["state"])
        adb_host("start-server")
        wait(lambda: (d := device())
             and d.get("session")
             and d["session"].get("connected") is True,
             "reconnected", 90)
        self.check("a reconnect resumes", True)
        http(f"/api/devices/{SERIAL}/stop", {"save_snapshot": False})
        self.wait_done()
        dev = device()
        sess = dev["session"]
        outs = sess.get("outputs") or []
        zip_ok = bool(outs) and outs[0].endswith(".zip") \
            and Path(outs[0]).is_file()
        checks = sess.get("checklist") or []
        self.check("a zip pulled + checklist ok",
                   zip_ok and all(c["ok"] for c in checks),
                   f"{outs[0] if outs else '-'} "
                   f"{[(c['label'], c['ok']) for c in checks]}")
        end = time.time() + 30
        mask_after = other_logs_mask()
        while mask_after != mask_before and time.time() < end:
            time.sleep(1)
            mask_after = other_logs_mask()
        self.check("a other-logs state restored",
                   mask_after == mask_before,
                   f"before={mask_before} after={mask_after}")
        http(f"/api/devices/{SERIAL}/dismiss", {})

    def step_b(self):
        print("b: record -> server kill -> resume")
        http(f"/api/devices/{SERIAL}/record",
             {"preset_id": "syshealth_google",
              "duration_min": 5, "mobilelog": False})
        wait(lambda: status() == "recording", "recording")
        time.sleep(3)
        self.proc.kill()
        self.proc.wait(timeout=10)
        self.start_server()
        wait(lambda: status() == "recording", "resumed", 30)
        self.check("b session resumes after kill", True)
        http(f"/api/devices/{SERIAL}/stop", {"save_snapshot": False})
        self.wait_done()
        outs = (device()["session"] or {}).get("outputs") or []
        self.check("b trace pulled",
                   bool(outs) and Path(outs[0]).is_file()
                   and Path(outs[0]).stat().st_size > 0,
                   outs[0] if outs else "no output")
        http(f"/api/devices/{SERIAL}/dismiss", {})

    def step_c(self):
        print("c: duration end auto pull")
        http(f"/api/devices/{SERIAL}/record",
             {"preset_id": "syshealth_google",
              "duration_min": 1, "mobilelog": False})
        wait(lambda: status() == "recording", "recording")
        self.wait_done(timeout=120)
        outs = (device()["session"] or {}).get("outputs") or []
        self.check("c auto-pulls to done",
                   bool(outs) and Path(outs[0]).is_file(),
                   outs[0] if outs else "no output")
        http(f"/api/devices/{SERIAL}/dismiss", {})

    def step_d(self):
        print("d: flight + snapshots + final snapshot stop")
        http(f"/api/devices/{SERIAL}/flight",
             {"preset_id": "syshealth_google", "lifetime_h": 24})
        wait(lambda: status() == "recording", "flight recording")
        http(f"/api/devices/{SERIAL}/snapshot", {})
        wait(lambda: len((device()["session"] or {})
                         .get("snapshots", [])) >= 1, "snap 1", 30)
        http(f"/api/devices/{SERIAL}/snapshot", {})
        wait(lambda: len((device()["session"] or {})
                         .get("snapshots", [])) >= 2, "snap 2", 30)
        http(f"/api/devices/{SERIAL}/stop", {"save_snapshot": True})
        self.wait_done(timeout=120)
        snaps = (device()["session"] or {}).get("snapshots", [])
        ok = len(snaps) >= 3 and all(
            s.get("local") and Path(s["local"]).is_file()
            and Path(s["local"]).stat().st_size > 0 for s in snaps)
        self.check("d 3 snapshot files pulled", ok,
                   "; ".join(f"{Path(s['local']).name}="
                             f"{Path(s['local']).stat().st_size}B"
                             for s in snaps))
        http(f"/api/devices/{SERIAL}/dismiss", {})

    def step_e(self):
        print("e: orphan adopt")
        wait(lambda: status() is None, "no tracked session", 30)
        text = pbtx.to_record(
            (ROOT / "adbperfetto/presets/syshealth_google.pbtx")
            .read_text(encoding="utf-8"), None)
        pushed = shell(f"cat > {CFG_REMOTE}", input_text=text)
        self.check("e orphan config pushed", pushed.returncode == 0)
        launched = shell(
            "perfetto --txt -c " + CFG_REMOTE +
            " --detach=adbperfetto -o " + TRACE_REMOTE, timeout=30)
        self.check("e orphan session started",
                   launched.returncode == 0)
        # Orphans are detected when a device (re)reaches `device`
        # state — restart the server to trigger the recovery check.
        self.stop_server()
        self.start_server()
        wait(lambda: device() and device().get("orphan"),
             "orphan detected", 60)
        self.check("e orphan detected", True)
        http(f"/api/devices/{SERIAL}/adopt", {})
        wait(lambda: status() == "recording", "adopted", 60)
        self.check("e adopted -> recording", True)
        http(f"/api/devices/{SERIAL}/stop", {"save_snapshot": False})
        self.wait_done()
        outs = (device()["session"] or {}).get("outputs") or []
        self.check("e orphan trace pulled",
                   bool(outs) and Path(outs[0]).is_file()
                   and Path(outs[0]).stat().st_size > 0,
                   outs[0] if outs else "no output")
        http(f"/api/devices/{SERIAL}/dismiss", {})

    def cleanup(self, mask_before, mb_before):
        print("cleanup")
        shell(f"rm -f {CFG_REMOTE}")
        shell("rm -f /data/misc/perfetto-traces/adbperfetto_*.pftrace")
        shell("rm -f /data/misc/perfetto-configs/adbperfetto_*.pbtx")
        pid = shell("pidof perfetto").stdout.strip()
        leftover = shell(
            "ls /data/misc/perfetto-traces/ /data/misc/perfetto-configs/"
            " 2>/dev/null | grep adbperfetto || true").stdout.strip()
        mb = shell("getprop vendor.MB.running").stdout.strip()
        mask_now = other_logs_mask()
        self.check("phone clean",
                   not pid and not leftover,
                   f"pidof={pid or '-'} leftover={leftover or '-'}")
        self.check("mobilelog state as before",
                   mb == mb_before and mask_now == mask_before,
                   f"MB.running={mb} mask={mask_now}")

    def preclean(self):
        """Stop any leftover adbperfetto sessions/files on the phone."""
        if shell("perfetto --is_detached=adbperfetto"
                 ).returncode == 0:
            shell("perfetto --attach=adbperfetto --stop", timeout=60)
        shell("kill -TERM $(ps -A -o PID,ARGS | grep "
              "adbperfetto_flight | awk '{print $1}') 2>/dev/null")
        shell("rm -f /data/misc/perfetto-traces/adbperfetto_*.pftrace")
        shell("rm -f /data/misc/perfetto-configs/adbperfetto_*.pbtx")
        shell("rm -f " + CFG_REMOTE)

    def run(self):
        print(f"output dir: {OUT}")
        self.preclean()
        mb_before = shell("getprop vendor.MB.running").stdout.strip()
        mask_before = other_logs_mask()
        print(f"  before: MB.running={mb_before} mask={mask_before}")
        self.start_server()
        prev_out = http("/api/settings").get("output_dir")
        try:
            http("/api/settings",
                 {"output_dir": str(OUT)}, method="PUT")
            self.clean_start()
            self.step_a(mask_before)
            self.step_b()
            self.step_c()
            self.step_d()
            self.step_e()
            self.cleanup(mask_before, mb_before)
        finally:
            try:
                http("/api/settings",
                     {"output_dir": prev_out or ""}, method="PUT")
            except Exception:
                pass
            self.stop_server()
        ok = all(r[1] for r in self.results)
        print(f"\ndevice_e2e: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(E2E().run())
