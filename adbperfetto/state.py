import json
import os
import threading
from pathlib import Path


class StateStore:
    """Persistent per-serial session records, JSON on disk, atomic writes."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = raw
        except (OSError, ValueError):
            pass

    def _flush(self):
        tmp = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, serial: str):
        with self._lock:
            return self._data.get(serial)

    def put(self, serial: str, record: dict):
        with self._lock:
            self._data[serial] = record
            self._flush()

    def delete(self, serial: str):
        with self._lock:
            if serial in self._data:
                del self._data[serial]
                self._flush()

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)
