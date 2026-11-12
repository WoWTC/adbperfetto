from pathlib import Path

VERSION_BASE = "2.0"

try:
    # Baked into the exe by build.bat via tools/write_buildinfo.py.
    from ._buildinfo import BUILD, VERSION
except ImportError:
    from .versioning import from_git
    VERSION, BUILD = from_git(
        VERSION_BASE, Path(__file__).resolve().parent.parent)
