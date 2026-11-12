import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AdbError(Exception):
    pass


def is_wifi_serial(serial: str) -> bool:
    """True for adb Wi-Fi serials: ip:port or mDNS service names."""
    return (re.fullmatch(r".+:\d+", serial) is not None
            or "._adb-tls-connect._tcp" in serial
            or "._adb._tcp" in serial)


def parse_devices_output(stdout: str) -> list[tuple[str, str, str]]:
    """Parse `adb devices -l` into `(serial, state, model_hint)` tuples."""
    devices = []
    for line in stdout.splitlines():
        parts = line.strip().split()
        if (len(parts) >= 2 and parts[0] != "List"
                and not parts[0].startswith("*")):
            hint = ""
            for token in parts[2:]:
                if token.startswith("model:"):
                    hint = token[6:].replace("_", " ").strip()
                    break
            devices.append((parts[0], parts[1], hint))
    return devices


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


class Adb:
    """Thin wrapper over the adb binary. The only place that spawns adb."""

    def __init__(self, path: str | None = None):
        self.path = path or shutil.which("adb")
        if not self.path:
            raise AdbError(
                "ADB was not found in PATH. "
                "Install Android platform-tools and add it to PATH."
            )

    def run(self, args: list[str], *, serial: str | None = None,
            timeout: float = 15, input_text: str | None = None) -> Result:
        cmd = [self.path]
        if serial:
            cmd += ["-s", serial]
        cmd += [str(a) for a in args]
        try:
            proc = subprocess.run(
                cmd, input=input_text, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"ADB command timed out: {' '.join(args)}") from exc
        except OSError as exc:
            raise AdbError(f"Could not run ADB: {exc}") from exc
        return Result(proc.returncode, proc.stdout, proc.stderr)

    def shell(self, serial: str, command: str, *, timeout: float = 15,
              input_text: str | None = None) -> Result:
        return self.run(["shell", command], serial=serial, timeout=timeout,
                        input_text=input_text)

    def devices_with_hints(self) -> list[tuple[str, str, str]]:
        result = self.run(["devices", "-l"], timeout=10)
        if result.returncode == 0:
            return parse_devices_output(result.stdout)
        return []

    def devices(self) -> list[tuple[str, str]]:
        return [(serial, state)
                for serial, state, _hint in self.devices_with_hints()]

    def pull(self, serial: str, remote: str, local: Path, *,
             timeout: float = 3600) -> Result:
        return self.run(["pull", remote, str(local)], serial=serial,
                        timeout=timeout)
