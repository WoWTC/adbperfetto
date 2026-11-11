import sys
from pathlib import Path


def app_dir() -> Path:
    """Directory that holds mutable app data next to the entry point."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory that holds read-only bundled resources (presets, web)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "adbperfetto"
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    path = app_dir() / "adbperfetto_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    return app_dir() / "traces"
