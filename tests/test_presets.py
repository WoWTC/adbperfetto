import tempfile
import unittest
from pathlib import Path

from adbperfetto import pbtx
from adbperfetto.paths import resource_dir
from adbperfetto.presets import PresetStore, Settings, slugify

BUILTIN_DIR = resource_dir() / "presets"


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PresetStore(BUILTIN_DIR, Path(self.tmp.name) / "user")

    def test_list_builtins(self):
        listing = self.store.list()
        self.assertEqual(len(listing), 14)
        ids = {p["id"] for p in listing}
        self.assertIn("syshealth_google", ids)
        self.assertIn("read_f2fs", ids)
        self.assertTrue(all(p["builtin"] for p in listing))
        default = next(p for p in listing if p["id"] == "syshealth_google")
        self.assertEqual(default["duration_ms"], 1800000)
        self.assertGreater(default["buffer_kb"], 0)

    def test_get(self):
        preset = self.store.get("syshealth_google")
        self.assertTrue(preset["builtin"])
        self.assertIn("buffers", preset["text"])
        with self.assertRaises(KeyError):
            self.store.get("nope")

    def test_save_get_delete_user_preset(self):
        text = "buffers { size_kb: 64 }\n"
        preset_id = self.store.save("My Preset", text)
        self.assertEqual(preset_id, "my_preset")
        preset = self.store.get("my_preset")
        self.assertFalse(preset["builtin"])
        self.assertEqual(preset["name"], "My Preset")
        listing = self.store.list()
        self.assertEqual(len(listing), 15)
        self.store.delete("my_preset")
        with self.assertRaises(KeyError):
            self.store.get("my_preset")

    def test_save_overwrites_same_id(self):
        self.store.save("My Preset", "buffers { size_kb: 1 }\n")
        self.store.save("My Preset", "buffers { size_kb: 2 }\n")
        self.assertIn("size_kb: 2", self.store.get("my_preset")["text"])

    def test_save_refuses_builtin_id(self):
        with self.assertRaises(ValueError):
            self.store.save("Syshealth Google", "buffers { size_kb: 1 }\n")

    def test_save_rejects_bad_text(self):
        with self.assertRaises(pbtx.PbtxError):
            self.store.save("bad", "buffers { size_kb:")

    def test_delete_builtin_refused(self):
        with self.assertRaises(ValueError):
            self.store.delete("syshealth_google")

    def test_slugify(self):
        self.assertEqual(slugify("My Preset!"), "my_preset")
        self.assertEqual(slugify("!!!"), "preset")


class SettingsTests(unittest.TestCase):
    def test_defaults_and_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(tmp)
            self.assertEqual(settings.default_preset, "syshealth_google")
            self.assertEqual(settings.output_dir, "")
            self.assertEqual(settings.language, "auto")
            self.assertEqual(settings.flight_lifetime_h, 24)
            settings.update({"output_dir": "D:/x", "unknown_key": 1})
            self.assertEqual(settings.output_dir, "D:/x")
            self.assertNotIn("unknown_key", settings.to_dict())
            reloaded = Settings(tmp)
            self.assertEqual(reloaded.output_dir, "D:/x")


if __name__ == "__main__":
    unittest.main()
