"""Regenerate adbperfetto/assets/adbperfetto.ico.

Draws tray.make_icon_image at each ICO size so the waveform stays crisp.
Run:  .venv\\Scripts\\python tools\\make_icon.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adbperfetto.tray import make_icon_image

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    out = ROOT / "adbperfetto" / "assets" / "adbperfetto.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    images = [make_icon_image(s) for s in SIZES]
    images[-1].save(out, format="ICO", append_images=images[:-1])
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
