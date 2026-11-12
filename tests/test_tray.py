import unittest
from unittest import mock

try:
    import PIL  # noqa: F401
    import pystray
    HAVE_TRAY = True
except ImportError:
    HAVE_TRAY = False

from adbperfetto import tray

URL = "http://127.0.0.1:1234"


@unittest.skipUnless(HAVE_TRAY, "pystray/PIL not installed")
class TrayTests(unittest.TestCase):
    def test_make_icon_image(self):
        img = tray.make_icon_image(64)
        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, "RGBA")
        small = tray.make_icon_image(16)
        self.assertEqual(small.size, (16, 16))
        self.assertEqual(small.mode, "RGBA")

    def _items(self, icon):
        return [i for i in icon.menu.items
                if i is not pystray.Menu.SEPARATOR]

    def test_create_icon_shape(self):
        icon = tray.create_icon(URL, lambda: None)
        self.assertIn(URL, icon.title)
        items = self._items(icon)
        self.assertEqual([i.text for i in items],
                         ["Open adbperfetto", "Quit adbperfetto"])
        self.assertTrue(items[0].default)

    def test_open_item_opens_url(self):
        icon = tray.create_icon(URL, lambda: None)
        items = self._items(icon)
        with mock.patch("adbperfetto.tray.webbrowser.open") as wb:
            items[0](icon)
        wb.assert_called_once_with(URL)

    def test_quit_item_calls_on_quit(self):
        calls = []
        icon = tray.create_icon(URL, lambda: calls.append(1))
        items = self._items(icon)
        items[1](icon)
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
