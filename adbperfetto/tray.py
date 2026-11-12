"""System tray icon for the windowed exe (pystray + Pillow).

pystray/PIL are imported lazily inside the functions so importing this
module (and running without a tray) works without them installed.
"""

import logging
import webbrowser

log = logging.getLogger("adbperfetto.tray")


def make_icon_image(size: int = 64):
    """Dark rounded square with a live-green trace waveform."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1],
                        radius=max(2, round(size * 0.22)),
                        fill=(0x12, 0x16, 0x1C, 0xFF))
    points = [(0.16, 0.62), (0.34, 0.62), (0.44, 0.30), (0.56, 0.78),
              (0.66, 0.48), (0.84, 0.48)]
    d.line([(x * size, y * size) for x, y in points],
           fill=(0x4A, 0xDE, 0x8A, 0xFF),
           width=max(2, round(size * 0.09)), joint="curve")
    return img


def create_icon(url: str, on_quit):
    """Build the pystray Icon (not started). on_quit() is called on Quit."""
    import pystray
    return pystray.Icon(
        "adbperfetto",
        make_icon_image(),
        f"adbperfetto - {url}",
        pystray.Menu(
            pystray.MenuItem("Open adbperfetto",
                             lambda icon: webbrowser.open(url),
                             default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit adbperfetto",
                             lambda icon: on_quit()),
        ))


def start_tray(url: str, on_quit):
    """Start the tray icon detached. Returns the Icon, or None on failure."""
    try:
        icon = create_icon(url, on_quit)
        icon.run_detached()
        try:
            icon.notify("adbperfetto is running. "
                        "Open it from the tray icon.", "adbperfetto")
        except Exception:
            pass
        log.info("Tray icon started")
        return icon
    except Exception as exc:
        log.warning("Tray icon unavailable: %s", exc)
        return None
