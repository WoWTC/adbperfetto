"""MTK MobileLog control via the DebugLoggerUI (com.debug.loggerui) app.

Ported from capture_log_trace.py. Device facts (KN8):
- shell cannot rm mobilelog files -> use the `clear_all_logs` broadcast.
- status via `getprop vendor.MB.running` / `vendor.MB.realpath`.
- cmd_target bitmask: 1=Mobile 2=Modem 4=Network 16=GPSHost.
- Modem/Network/GPSHost state only via the DebugLoggerUI notification
  `android.bigText` (dumpsys notification --noredact).
"""

import re
import shlex
import time

OTHER_LOGS = (("Modem", 2), ("Network", 4), ("GPSHost", 16))
LOG_ROOT = "/data/debuglogger/mobilelog"


class MobileLogError(Exception):
    pass


def extract_logger_bigtext(dumpsys_output: str):
    for record in dumpsys_output.split("NotificationRecord(")[1:]:
        if "pkg=com.debug.loggerui" in record:
            match = re.search(r"android\.bigText=String \((.*?)\)",
                              record, re.DOTALL)
            if match:
                return match.group(1)
    return None


def parse_other_logs(bigtext):
    if bigtext is None:
        return None
    if "All logs are stopped" in bigtext:
        return 0
    match = re.fullmatch(r"(.+?) Logs? (?:are|is) on\.?", bigtext.strip())
    if not match:
        return None
    names = {name.strip() for name in match.group(1).split(",")}
    return sum(bit for name, bit in OTHER_LOGS if name in names)


def other_log_names(mask, separator=", "):
    return separator.join(name for name, bit in OTHER_LOGS if mask & bit)


class MobileLog:
    """Controls one MobileLog session on a device."""

    def __init__(self, adb, serial: str, on_message=None, sleep=time.sleep):
        self.adb = adb
        self.serial = serial
        self.on_message = on_message or (lambda message: None)
        self._sleep = sleep
        self.log_path = ""
        self.started = False
        self.original_mask = None
        self.paused_mask = 0
        self.logs_restored = False

    def _shell(self, command: str, timeout: float = 15):
        return self.adb.shell(self.serial, command, timeout=timeout)

    def prop(self, name: str) -> str:
        return self._shell(f"getprop {name}", timeout=5).stdout.strip()

    def broadcast(self, name: str, target: int = 1):
        self._shell(
            "am broadcast -a com.debug.loggerui.ADB_CMD "
            f"-e cmd_name {name} --ei cmd_target {target} "
            "-n com.debug.loggerui/.framework.LogReceiver", timeout=15)

    def other_logs_state(self):
        result = self._shell("dumpsys notification --noredact", timeout=10)
        if result.returncode == 0:
            return parse_other_logs(extract_logger_bigtext(result.stdout))
        return None

    def wait_other_logs(self, mask: int, running: bool, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.other_logs_state()
            if state is not None and (state & mask) == (mask if running else 0):
                return True
            self._sleep(1)
        return False

    def pause_other_logs(self):
        mask = self.other_logs_state()
        self.original_mask = mask
        if mask is None:
            self.on_message(
                "Could not detect Modem/Network/GPS log state; "
                "leaving them unchanged.")
        elif mask:
            self.paused_mask = mask
            self.on_message(
                f"Pausing {other_log_names(mask, '/')} logs during capture "
                "to reduce system load...")
            self.broadcast("stop", mask)
            if not self.wait_other_logs(mask, running=False, timeout=30):
                self.on_message(
                    "Could not confirm that the other logs stopped; "
                    "continuing.")

    def wait_mobile(self, state: str, timeout: float = 30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.prop("vendor.MB.running") == state:
                return
            self._sleep(1)
        raise MobileLogError(
            f"MobileLog did not reach state {state} within "
            f"{int(timeout)} seconds.")

    def prepare(self):
        """Pause other logs, stop a previous session, start a clean one."""
        self.pause_other_logs()
        package = self._shell("pm path com.debug.loggerui")
        if package.returncode != 0 or "package:" not in package.stdout:
            raise MobileLogError(
                "MobileLog is not installed on this phone "
                "(com.debug.loggerui missing).")
        if self.prop("vendor.MB.running") == "1":
            self.on_message("Stopping the previous MobileLog session...")
            self.broadcast("stop")
            self.wait_mobile("0")
        self.started = True
        self.broadcast("start")
        self.wait_mobile("1")
        self.log_path = self.prop("vendor.MB.realpath").rstrip("/")
        if not self.log_path.startswith(LOG_ROOT + "/APLog_"):
            raise MobileLogError(
                "MobileLog returned an unexpected session path: "
                f"{self.log_path!r}")
        self.broadcast("clear_all_logs")
        self.on_message("Clearing old MobileLog sessions...")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = self._shell(f"ls -1 {shlex.quote(LOG_ROOT)}", timeout=5)
            sessions = {line.strip() for line in result.stdout.splitlines()
                        if line.strip().startswith("APLog_")}
            if (result.returncode == 0
                    and sessions == {self.log_path.rsplit("/", 1)[-1]}):
                self.on_message("Old logs cleared.")
                return
            self._sleep(2)
        self.on_message(
            "Old logs could not be confirmed cleared; continuing with the "
            "current session.")

    def stop(self):
        """Stop MobileLog and restore the logs paused during prepare()."""
        errors = []
        if self.started:
            try:
                self.on_message("Stopping MobileLog...")
                self.broadcast("stop")
                self.wait_mobile("0")
                self.started = False
            except Exception as exc:
                errors.append(str(exc))
        if self.paused_mask:
            try:
                mask = self.paused_mask
                self.broadcast("start", mask)
                if not self.wait_other_logs(mask, running=True, timeout=60):
                    raise MobileLogError(
                        f"Could not confirm {other_log_names(mask, '/')} "
                        "logs were restored.")
                self.paused_mask = 0
                self.logs_restored = True
                self.on_message(f"{other_log_names(mask, '/')} logs restored.")
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise MobileLogError("; ".join(errors))

    def monitor_fragment(self) -> str:
        """Shell fragment for the record monitor loop (MB + LOG keys)."""
        return (f"echo MB=$(getprop vendor.MB.running); "
                f"echo LOG=$(du -sk {shlex.quote(self.log_path)} "
                "2>/dev/null | cut -f1)")

    def state_dict(self) -> dict:
        return {
            "enabled": True, "log_path": self.log_path,
            "paused_mask": self.paused_mask,
            "original_mask": self.original_mask,
        }

    @classmethod
    def from_state(cls, adb, serial, record: dict, **kwargs):
        mobile = cls(adb, serial, **kwargs)
        mobile.log_path = record.get("log_path", "")
        mobile.started = bool(record.get("enabled"))
        mobile.paused_mask = record.get("paused_mask", 0)
        mobile.original_mask = record.get("original_mask")
        return mobile
