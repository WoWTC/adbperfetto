import re
from dataclasses import dataclass, asdict


@dataclass
class DeviceInfo:
    serial: str
    model: str = ""
    manufacturer: str = ""
    device: str = ""
    sdk: int = 0
    release: str = ""
    build_id: str = ""
    fingerprint: str = ""
    abi: str = ""
    hardware: str = ""
    perfetto_version: str = ""
    has_mobilelog: bool = False
    supports_clone: bool = False

    def to_dict(self):
        return asdict(self)


_PROPS = {
    "MODEL": "ro.product.model",
    "MANUFACTURER": "ro.product.manufacturer",
    "DEVICE": "ro.product.device",
    "SDK": "ro.build.version.sdk",
    "RELEASE": "ro.build.version.release",
    "BUILD_ID": "ro.build.display.id",
    "FINGERPRINT": "ro.build.fingerprint",
    "ABI": "ro.product.cpu.abi",
    "HARDWARE": "ro.hardware",
}


def probe(adb, serial) -> DeviceInfo:
    parts = [f"echo {key}=$(getprop {prop})" for key, prop in _PROPS.items()]
    parts.append("echo PERFETTO=$(perfetto --version 2>/dev/null | head -1)")
    parts.append("echo LOGGERUI=$(pm path com.debug.loggerui 2>/dev/null | head -1)")
    result = adb.shell(serial, "; ".join(parts), timeout=15)
    values = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    info = DeviceInfo(serial=serial)
    info.model = values.get("MODEL", "")
    info.manufacturer = values.get("MANUFACTURER", "")
    info.device = values.get("DEVICE", "")
    info.sdk = int(values["SDK"]) if values.get("SDK", "").isdigit() else 0
    info.release = values.get("RELEASE", "")
    info.build_id = values.get("BUILD_ID", "")
    info.fingerprint = values.get("FINGERPRINT", "")
    info.abi = values.get("ABI", "")
    info.hardware = values.get("HARDWARE", "")
    match = re.search(r"v?\d+\.\d+", values.get("PERFETTO", ""))
    if match:
        version = match.group(0)
        info.perfetto_version = version if version.startswith("v") else "v" + version
    info.has_mobilelog = "package:" in values.get("LOGGERUI", "")
    info.supports_clone = info.sdk >= 35
    return info


def atrace_categories(adb, serial) -> list[dict]:
    result = adb.shell(serial, "atrace --list_categories", timeout=15)
    categories = []
    if result.returncode != 0:
        return categories
    for line in result.stdout.splitlines():
        match = re.match(r"^\s{2,}(\S+)\s+-\s+(.*\S)\s*$", line)
        if match:
            categories.append(
                {"name": match.group(1), "description": match.group(2)})
    return categories


def ftrace_events(adb, serial) -> list[str] | None:
    for path in ("/sys/kernel/tracing/available_events",
                 "/sys/kernel/debug/tracing/available_events"):
        result = adb.shell(serial, f"cat {path}", timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            events = set()
            for line in result.stdout.splitlines():
                line = line.strip()
                if ":" in line:
                    group, name = line.split(":", 1)
                    if name:
                        events.add(f"{group}/{name}")
            if events:
                return sorted(events)
    return None
