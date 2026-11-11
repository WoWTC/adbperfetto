"""Demo mode: an in-memory Adb implementation for UI development.

Enabled with ADBPERFETTO_DEMO=1. Simulates phones, perfetto sessions and
MobileLog without touching a real device. Demo-only control endpoints are
registered under /api/demo/ to drive UI states (unplug, unauthorized,
orphans, failures).
"""

import fnmatch
import re
import shlex
import time
from pathlib import Path

from .adb import AdbError, Result
from .mobilelog import LOG_ROOT

TRACE_RATE_BPS = 150_000          # remote trace growth ~150 KB/s
LOG_RATE_KBS = 50                 # MobileLog growth ~50 KB/s
PULL_CAP = 20 * 1024 * 1024       # max bytes written per demo pull
ONE_SHOT_DELAY_S = 1.5

KNOCK = b"\x0a\x3f"               # plausible perfetto-ish leading bytes


class DemoDevice:
    def __init__(self, serial, model, manufacturer, device, sdk, release,
                 build_id, abi="arm64-v8a", hardware="", perfetto="v49.0",
                 mobilelog=False, state="device"):
        self.serial = serial
        self.model = model
        self.manufacturer = manufacturer
        self.device = device
        self.sdk = sdk
        self.release = release
        self.build_id = build_id
        self.abi = abi
        self.hardware = hardware
        self.perfetto = perfetto
        self.mobilelog = mobilelog
        self.state = state
        self.detached = 2            # record detach state (0 = running)
        self.record_started = 0.0
        self.record_duration_ms = 0
        self.record_remote = ""
        self.flight_alive = False
        self.flight_pid = None
        self.flight_base = ""
        self.one_shot_deadline = None
        self.remote_files = {}       # remote path -> bytes
        self.mb_running = "0"
        self.mb_path = f"{LOG_ROOT}/APLog_demo"
        self.mb_started = 0.0
        self.other_mask = 22
        self.fail_next_start = False

    @property
    def supports_clone(self):
        return self.sdk >= 35

    def probe_output(self) -> str:
        loggerui = "package:/x" if self.mobilelog else ""
        return (
            f"MODEL={self.model}\n"
            f"MANUFACTURER={self.manufacturer}\n"
            f"DEVICE={self.device}\n"
            f"SDK={self.sdk}\n"
            f"RELEASE={self.release}\n"
            f"BUILD_ID={self.build_id}\n"
            f"FINGERPRINT={self.manufacturer}/{self.device}/"
            f"{self.device}:{self.release}/{self.build_id}:"
            "user/release-keys\n"
            f"ABI={self.abi}\n"
            f"HARDWARE={self.hardware}\n"
            f"PERFETTO=Perfetto {self.perfetto} (N/A)\n"
            f"LOGGERUI={loggerui}\n")


def default_devices() -> dict[str, DemoDevice]:
    devices = {}
    for dev in (
        DemoDevice("KN8DEMO0001", "TECNO KN8", "TECNO", "TECNO-KN8",
                   36, "16", "KN8-16.2.0.154(OP001PF001AZ)DEMO",
                   hardware="mt6835", mobilelog=True),
        DemoDevice("PIXEL7DEMO", "Pixel 7", "Google", "panther",
                   34, "14", "UP1A.231005.007.DEMO",
                   hardware="gs201", perfetto="v39.0"),
        DemoDevice("R58MDEMO", "R58M", "TECNO", "R58M-TEST",
                   33, "13", "R58M-13.0.0.001DEMO", state="unauthorized"),
    ):
        devices[dev.serial] = dev
    return devices


class DemoAdb:
    """In-memory Adb stand-in. Same call surface as adbperfetto.adb.Adb."""

    def __init__(self):
        self.dev = default_devices()

    # -- control surface (called by /api/demo/* endpoints) ------------------

    def unplug(self, serial: str):
        dev = self.dev.get(serial)
        if dev is None:
            raise AdbError(f"Unknown demo device: {serial}")
        dev.state = "unplugged"

    def replug(self, serial: str):
        dev = self.dev.get(serial)
        if dev is None:
            raise AdbError(f"Unknown demo device: {serial}")
        dev.state = "device"

    def set_state(self, serial: str, state: str):
        dev = self.dev.get(serial)
        if dev is None:
            raise AdbError(f"Unknown demo device: {serial}")
        if state not in ("device", "unauthorized", "offline"):
            raise AdbError(f"Unsupported demo state: {state}")
        dev.state = state

    def remove_all(self):
        for dev in self.dev.values():
            dev.state = "unplugged"

    def fail_next_start(self, serial: str):
        dev = self.dev.get(serial)
        if dev is None:
            raise AdbError(f"Unknown demo device: {serial}")
        dev.fail_next_start = True

    def plant_orphan(self, serial: str, mode: str = "record"):
        """Give a device a foreign session the manager does not own."""
        dev = self.dev.get(serial)
        if dev is None:
            raise AdbError(f"Unknown demo device: {serial}")
        if mode == "record":
            dev.detached = 0
            dev.record_started = time.time() - 300
            dev.record_duration_ms = 0
            dev.record_remote = ("/data/misc/perfetto-traces/"
                                 "adbperfetto_orphan.pftrace")
        else:
            dev.flight_alive = True
            dev.flight_pid = 24444
            dev.flight_base = ("/data/misc/perfetto-traces/"
                               "adbperfetto_flight_orphan.pftrace")
            dev.remote_files[dev.flight_base] = b""

    def reset(self):
        self.dev = default_devices()

    # -- Adb interface --------------------------------------------------------

    def devices(self) -> list[tuple[str, str]]:
        return [(serial, dev.state) for serial, dev in self.dev.items()
                if dev.state != "unplugged"]

    def run(self, args, *, serial=None, timeout=15, input_text=None):
        return Result(0, "", "")

    def _device(self, serial: str) -> DemoDevice:
        dev = self.dev.get(serial)
        if dev is None:
            raise AdbError(f"error: device '{serial}' not found")
        if dev.state == "unauthorized":
            return Result(1, "", f"error: device unauthorized.")
        if dev.state != "device":
            return Result(1, "", f"error: device '{serial}' not found")
        return dev

    def _lazy_tick(self, dev: DemoDevice):
        """Advance time-dependent state (duration end, one-shot kill)."""
        now = time.time()
        if (dev.detached == 0 and dev.record_duration_ms
                and (now - dev.record_started) * 1000
                >= dev.record_duration_ms):
            dev.detached = 2
            dev.remote_files[dev.record_remote] = self._trace_bytes(
                self.record_size(dev))
        if (dev.one_shot_deadline is not None
                and now >= dev.one_shot_deadline):
            dev.flight_alive = False
            dev.one_shot_deadline = None
            dev.remote_files[dev.flight_base] = self._trace_bytes(2_500_000)

    @staticmethod
    def _trace_bytes(size: int) -> bytes:
        size = max(1, min(int(size), PULL_CAP))
        body = (KNOCK * 256)[:4096]
        data = (body * (size // len(body) + 1))[:size]
        return data

    def record_size(self, dev: DemoDevice) -> int:
        if dev.record_remote and dev.record_remote in dev.remote_files:
            return len(dev.remote_files[dev.record_remote])
        if dev.detached == 0:
            return int((time.time() - dev.record_started) * TRACE_RATE_BPS)
        return 0

    def shell(self, serial: str, command: str, *, timeout=15,
              input_text=None) -> Result:
        checked = self._device(serial)
        if isinstance(checked, Result):
            return checked
        dev = checked
        self._lazy_tick(dev)

        # --- probe -------------------------------------------------------
        if "echo MODEL=$(getprop" in command:
            return Result(0, dev.probe_output(), "")
        if command.strip() == "getprop":
            return Result(0, self._prop_dump(dev), "")
        if command.startswith("getprop "):
            name = command.split(None, 1)[1]
            return Result(0, self._prop(dev, name) + "\n", "")
        if command.startswith("pm path "):
            pkg = command.split()[-1]
            if pkg == "com.debug.loggerui" and dev.mobilelog:
                return Result(0, "package:/data/app/~~x/loggerui.apk\n", "")
            return Result(1, "", "")
        if command.startswith("am broadcast"):
            return self._broadcast(dev, command)
        if "dumpsys notification" in command:
            return Result(0, self._notification(dev), "")
        if command.startswith("du -sk "):
            kb = 0
            if dev.mb_running == "1":
                kb = int((time.time() - dev.mb_started) * LOG_RATE_KBS)
            return Result(0, f"{kb}\t{dev.mb_path}\n", "")
        if command.startswith("ls -1") and LOG_ROOT in command:
            if dev.mb_running == "1":
                return Result(0, "APLog_demo\n", "")
            return Result(0, "", "")
        if command.startswith("date"):
            return Result(0, time.strftime("%a %b %d %H:%M:%S %Z %Y\n"), "")

        # --- perfetto: shared -------------------------------------------
        if command.startswith("atrace --list_categories"):
            return Result(0, "  gfx - Graphics\n  view - View\n"
                             "  am - Activity Manager\n  wm - Window "
                             "Manager\n  sched - CPU Scheduling\n", "")
        if "available_events" in command:
            if dev.sdk < 35:
                return Result(1, "", "Permission denied")
            return Result(0, "sched:sched_switch\nsched:sched_wakeup\n"
                             "power:cpu_frequency\nmm_event:mm_event_"
                             "record\n", "")
        if command.startswith("perfetto --query"):
            return Result(0, self._query(dev), "")
        if "echo ALIVE=" in command:
            match = re.search(r"/proc/(\d+)/cmdline", command)
            pid = int(match.group(1)) if match else 0
            alive = dev.flight_alive and pid == dev.flight_pid
            return Result(0, f"ALIVE={0 if alive else 1}\n", "")
        if "echo DET=" in command:
            size = self.record_size(dev)
            extra = ""
            if "echo MB=" in command:
                log_kb = (int((time.time() - dev.mb_started)
                              * LOG_RATE_KBS)
                          if dev.mb_running == "1" else 0)
                extra = f"MB={dev.mb_running}\nLOG={log_kb}\n"
            return Result(0, f"DET={dev.detached}\nSIZE={size}\n"
                             f"{extra}", "")
        if command.startswith("perfetto --is_detached"):
            return Result(dev.detached, "", "")
        if command.startswith("perfetto --attach"):
            size = self.record_size(dev)
            dev.detached = 2
            dev.remote_files[dev.record_remote] = self._trace_bytes(size)
            return Result(0, "", "")
        if command.startswith("perfetto --txt") and "--detach=" in command:
            if dev.fail_next_start:
                dev.fail_next_start = False
                return Result(1, "", "demo: start failed")
            match = re.search(r"-o (\S+)", command)
            dev.record_remote = match.group(1) if match else \
                "/data/misc/perfetto-traces/adbperfetto_demo.pftrace"
            dev.detached = 0
            dev.record_started = time.time()
            config = input_text or ""
            match = re.search(r"duration_ms:\s*(\d+)", config)
            dev.record_duration_ms = (int(match.group(1))
                                      if match else 0)
            return Result(0, "", "")
        if command.startswith("perfetto --txt") and "--background-wait" \
                in command:
            if dev.fail_next_start:
                dev.fail_next_start = False
                return Result(1, "", "demo: start failed")
            match = re.search(r"-o (\S+)", command)
            dev.flight_base = (match.group(1) if match else
                               "/data/misc/perfetto-traces/flight.pftrace")
            dev.flight_pid = int(time.time()) % 30000 + 10000
            dev.flight_alive = True
            dev.remote_files[dev.flight_base] = b""
            return Result(0, "Connected to traced\n"
                             f"{dev.flight_pid}\n", "")
        if "clone-by-name" in command:
            if not dev.flight_alive:
                return Result(1, "", "session not found")
            match = re.search(r"-o (\S+)", command)
            if match:
                dev.remote_files[match.group(1)] = self._trace_bytes(
                    900_000 + len(dev.remote_files) * 300_000)
            time.sleep(0.3)  # let the UI show "Saving snapshot..."
            return Result(0, "", "Wrote 1234 bytes")
        if command.startswith("trigger_perfetto"):
            if not dev.flight_alive:
                return Result(1, "", "trigger not registered")
            dev.one_shot_deadline = time.time() + ONE_SHOT_DELAY_S
            return Result(0, "", "")
        if command.startswith("kill -TERM"):
            dev.flight_alive = False
            return Result(0, "", "")
        if command.startswith("kill -0") or "kill -0" in command:
            return Result(1 if not dev.flight_alive else 0, "", "")
        if command.startswith("cat > "):
            return Result(0, "", "")
        if command.startswith("rm -f"):
            for path in list(dev.remote_files):
                base = command.split("rm -f ", 1)[1]
                for token in shlex.split(base):
                    token = token.rstrip("*")
                    if token and path.startswith(token):
                        dev.remote_files.pop(path, None)
            return Result(0, "", "")
        if command.startswith("stat -c %s"):
            path = command.split()[-1]
            if path == dev.record_remote:
                return Result(0, f"{dev.record_size()}\n", "")
            if path == dev.flight_base:
                data = dev.remote_files.get(path, b"")
                return Result(0, f"{len(data)}\n", "")
            data = dev.remote_files.get(path)
            return (Result(0, f"{len(data)}\n", "") if data is not None
                    else Result(1, "", "No such file"))
        if command.startswith("stat -c %Y"):
            when = (dev.record_started or time.time()) - 120
            return Result(0, f"{int(when)}\n", "")
        if command.startswith("ls -t") or command.startswith("ls -1"):
            tokens = shlex.split(command)
            pattern = next((t for t in tokens[1:]
                            if t.startswith("/")), "")
            found = sorted(
                (p for p in dev.remote_files
                 if fnmatch.fnmatch(p, pattern)), reverse=True)
            if (dev.detached == 0
                    and fnmatch.fnmatch(dev.record_remote, pattern)
                    and dev.record_remote not in found):
                found.insert(0, dev.record_remote)
            return Result(0, "\n".join(found) + ("\n" if found else ""),
                          "")
        if command.startswith("ps -A"):
            if dev.flight_alive:
                return Result(
                    0, "  PID ARGS\n"
                       f"  {dev.flight_pid} perfetto --txt -c "
                       "/data/misc/perfetto-configs/"
                       "adbperfetto_flight.pbtx -o "
                       f"{dev.flight_base} --background-wait\n", "")
            return Result(0, "  PID ARGS\n  1 /init\n", "")
        return Result(0, "", "")

    # -- pull ---------------------------------------------------------------

    def pull(self, serial: str, remote: str, local: Path, *,
             timeout=3600) -> Result:
        checked = self._device(serial)
        if isinstance(checked, Result):
            return checked
        dev = checked
        self._lazy_tick(dev)
        local = Path(local)
        if remote == dev.mb_path or remote.startswith(LOG_ROOT):
            # Directory pull (MobileLog session folder).
            local.mkdir(parents=True, exist_ok=True)
            kb = int((time.time() - dev.mb_started) * LOG_RATE_KBS) \
                if dev.mb_running == "1" else 128
            for name, text in (
                    ("main_log", b"M" * min(kb * 1024, 4_000_000)),
                    ("kernel_log", b"K" * 65_536),
                    ("sys_log", b"S" * 131_072),
                    ("events_log", b"E" * 65_536)):
                (local / name).write_bytes(text)
            return Result(0, f"{remote}: 1 file pulled", "")
        if remote == dev.record_remote and dev.detached == 0:
            return Result(1, "", "remote still recording")
        data = dev.remote_files.get(remote)
        if data is None:
            return Result(1, "",
                          f"adb: error: remote object '{remote}' "
                          "does not exist")
        local.parent.mkdir(parents=True, exist_ok=True)
        chunk = max(1, len(data) // 12)
        with open(local, "wb") as handle:
            for offset in range(0, len(data), chunk):
                handle.write(data[offset:offset + chunk])
                time.sleep(0.03)  # visible pull progress
        return Result(0, f"{remote}: 1 file pulled", "")

    # -- mobilelog helpers ---------------------------------------------------

    def _prop(self, dev: DemoDevice, name: str) -> str:
        props = {
            "vendor.MB.running": dev.mb_running,
            "vendor.MB.realpath": dev.mb_path if dev.mb_running == "1"
            else "",
            "ro.product.model": dev.model,
            "ro.build.version.sdk": str(dev.sdk),
            "ro.build.version.release": dev.release,
        }
        return props.get(name, "")

    def _prop_dump(self, dev: DemoDevice) -> str:
        return ("[ro.product.model]: [" + dev.model + "]\n"
                "[ro.build.version.sdk]: [" + str(dev.sdk) + "]\n"
                "[vendor.MB.running]: [" + dev.mb_running + "]\n")

    def _broadcast(self, dev: DemoDevice, command: str) -> Result:
        name = re.search(r"cmd_name (\S+)", command).group(1)
        match = re.search(r"cmd_target (\d+)", command)
        target = int(match.group(1)) if match else 1
        if name == "start" and target == 1:
            dev.mb_running = "1"
            dev.mb_started = time.time()
        elif name == "stop" and target == 1:
            dev.mb_running = "0"
        elif name == "stop":
            dev.other_mask &= ~target
        elif name == "start":
            dev.other_mask |= target
        return Result(0, "Broadcast completed: result=0\n", "")

    def _notification(self, dev: DemoDevice) -> str:
        names = [n for n, b in (("Modem", 2), ("Network", 4),
                                ("GPSHost", 16)) if dev.other_mask & b]
        text = (",".join(names) + " Log are on." if names
                else "Warning : All logs are stopped!!!")
        return ("NotificationRecord(x pkg=com.debug.loggerui)\n"
                f"  android.bigText=String ({text})\n")

    def _query(self, dev: DemoDevice) -> str:
        row = ""
        if dev.flight_alive:
            row = ("34      2000    STARTED    (1) 1024     300       1"
                   "    05:39:44 adbperfetto_flight\n")
        return ("Not meant for machine consumption. Use --query-raw "
                "for scripts.\n\nService: Perfetto v49.0 (N/A)\n"
                "Tracing sessions: 1 (started: 1)\n\n\n"
                "TRACING SESSIONS:\n\n"
                "ID      UID     STATE      BUF (#) KB   DUR (s)   #DS"
                "  STARTED  NAME\n"
                "===     ===     =====      ==========   =======   ==="
                "  =======  ====\n" + row)
