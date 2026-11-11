"""Scripted FakeAdb with the same interface as adbperfetto.adb.Adb.

Routes are (regex, handler) pairs matched against the shell command /
joined args / remote path. Handlers receive a context dict and may return
Result, int (returncode), str (stdout), tuple, or None (-> rc 0).
"""

import re
import threading
from pathlib import Path

from adbperfetto.adb import AdbError, Result


class FakeAdb:
    def __init__(self):
        self.routes = []
        self.calls = []          # (kind, target, serial, input_text)
        self.device_list = []    # [(serial, state)]
        self.pull_files = {}     # remote -> bytes
        self.pull_failures = 0   # fail the next N pulls
        self.offline = False     # simulate a disconnected device
        self.lock = threading.Lock()

    def on(self, pattern):
        """Decorator: register a handler for commands matching `pattern`."""
        def decorator(fn):
            self.routes.append((re.compile(pattern), fn))
            return fn
        return decorator

    def _coerce(self, value) -> Result:
        if value is None:
            return Result(0, "", "")
        if isinstance(value, Result):
            return value
        if isinstance(value, int):
            return Result(value, "", "")
        if isinstance(value, str):
            return Result(0, value, "")
        if isinstance(value, tuple):
            return Result(value[0],
                          value[1] if len(value) > 1 else "",
                          value[2] if len(value) > 2 else "")
        raise TypeError(f"Bad FakeAdb handler result: {value!r}")

    def _dispatch(self, text, ctx):
        for pattern, fn in self.routes:
            if pattern.search(text):
                return self._coerce(fn(ctx))
        return Result(0, "", "")

    def _offline_result(self):
        return Result(1, "", "error: device not found")

    # -- Adb interface ------------------------------------------------------

    def run(self, args, *, serial=None, timeout=15, input_text=None):
        with self.lock:
            self.calls.append(("run", list(args), serial, input_text))
        if self.offline:
            return self._offline_result()
        return self._dispatch(" ".join(str(a) for a in args), {
            "kind": "run", "args": args, "serial": serial,
            "input_text": input_text})

    def shell(self, serial, command, *, timeout=15, input_text=None):
        with self.lock:
            self.calls.append(("shell", command, serial, input_text))
        if self.offline:
            return self._offline_result()
        return self._dispatch(command, {
            "kind": "shell", "command": command, "serial": serial,
            "input_text": input_text})

    def devices(self):
        with self.lock:
            self.calls.append(("devices", None, None, None))
        if self.offline:
            raise AdbError("adb devices failed")
        return list(self.device_list)

    def pull(self, serial, remote, local, *, timeout=3600):
        local = Path(local)
        with self.lock:
            self.calls.append(("pull", remote, serial, str(local)))
        if self.offline:
            return self._offline_result()
        for pattern, fn in self.routes:
            if pattern.search(remote):
                return self._coerce(fn({
                    "kind": "pull", "remote": remote, "local": local,
                    "serial": serial}))
        if self.pull_failures > 0:
            self.pull_failures -= 1
            return Result(1, "", "adb: error: pull failed")
        data = self.pull_files.get(remote, b"FAKE-TRACE-DATA")
        local.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            # directory pull: create files under local/
            local.mkdir(parents=True, exist_ok=True)
            for name, content in data.items():
                (local / name).write_bytes(content)
        else:
            local.write_bytes(data)
        return Result(0, f"{remote}: 1 file pulled", "")

    # -- test helpers ---------------------------------------------------------

    def shell_calls(self, pattern=None):
        out = []
        rx = re.compile(pattern) if pattern else None
        for call in self.calls:
            if call[0] == "shell" and (rx is None or rx.search(call[1])):
                out.append(call[1])
        return out

    def pull_calls(self):
        return [c for c in self.calls if c[0] == "pull"]


def wait_for(predicate, timeout=5.0, interval=0.01):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
