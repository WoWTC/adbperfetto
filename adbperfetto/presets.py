import json
import os
import re
from pathlib import Path

from . import pbtx


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "preset"


class PresetStore:
    """Built-in presets ship with the app; user presets live in data_dir."""

    def __init__(self, builtin_dir, user_dir):
        self.builtin_dir = Path(builtin_dir)
        self.user_dir = Path(user_dir)
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def _builtin_ids(self) -> dict:
        return {p.stem: p for p in self.builtin_dir.glob("*.pbtx")
                if p.is_file()}

    def _user_ids(self) -> dict:
        return {p.stem: p for p in self.user_dir.glob("*.pbtx")
                if p.is_file()}

    def _user_name(self, preset_id: str) -> str:
        sidecar = self.user_dir / f"{preset_id}.name"
        try:
            name = sidecar.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass
        return preset_id

    def list(self) -> list[dict]:
        out = []
        for preset_id in sorted(self._builtin_ids()):
            text = self._builtin_ids()[preset_id].read_text(encoding="utf-8")
            out.append({
                "id": preset_id, "name": preset_id, "builtin": True,
                "duration_ms": pbtx.duration_ms(text),
                "buffer_kb": pbtx.total_buffer_kb(text),
            })
        for preset_id in sorted(self._user_ids()):
            text = self._user_ids()[preset_id].read_text(encoding="utf-8")
            out.append({
                "id": preset_id, "name": self._user_name(preset_id),
                "builtin": False,
                "duration_ms": pbtx.duration_ms(text),
                "buffer_kb": pbtx.total_buffer_kb(text),
            })
        return out

    def get(self, preset_id: str) -> dict:
        builtins = self._builtin_ids()
        if preset_id in builtins:
            return {
                "id": preset_id, "name": preset_id, "builtin": True,
                "text": builtins[preset_id].read_text(encoding="utf-8"),
            }
        users = self._user_ids()
        if preset_id in users:
            return {
                "id": preset_id, "name": self._user_name(preset_id),
                "builtin": False,
                "text": users[preset_id].read_text(encoding="utf-8"),
            }
        raise KeyError(f"Unknown preset: {preset_id}")

    def save(self, name: str, text: str) -> str:
        pbtx.parse(text)  # raises PbtxError on bad config
        preset_id = slugify(name)
        if preset_id in self._builtin_ids():
            raise ValueError(
                f"'{preset_id}' is a built-in preset and cannot be replaced")
        (self.user_dir / f"{preset_id}.pbtx").write_text(
            text, encoding="utf-8")
        (self.user_dir / f"{preset_id}.name").write_text(
            name, encoding="utf-8")
        return preset_id

    def delete(self, preset_id: str):
        if preset_id in self._builtin_ids():
            raise ValueError("Built-in presets cannot be deleted")
        path = self.user_dir / f"{preset_id}.pbtx"
        if not path.is_file():
            raise KeyError(f"Unknown preset: {preset_id}")
        path.unlink()
        try:
            (self.user_dir / f"{preset_id}.name").unlink()
        except OSError:
            pass


_SETTINGS_DEFAULTS = {
    "default_preset": "syshealth_google",
    "output_dir": "",
    "language": "auto",
    "flight_lifetime_h": 24,
}


class Settings:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "settings.json"
        self._data = dict(_SETTINGS_DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            for key in _SETTINGS_DEFAULTS:
                if key in stored:
                    self._data[key] = stored[key]
        except (OSError, ValueError):
            pass

    def to_dict(self) -> dict:
        return dict(self._data)

    def __getattr__(self, key):
        if key in _SETTINGS_DEFAULTS:
            return self._data[key]
        raise AttributeError(key)

    def update(self, partial: dict):
        for key, value in partial.items():
            if key in _SETTINGS_DEFAULTS:
                self._data[key] = value
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
