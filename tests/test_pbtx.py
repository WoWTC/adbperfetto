import unittest
from pathlib import Path

from adbperfetto import pbtx
from adbperfetto.paths import resource_dir

PRESET_DIR = resource_dir() / "presets"


class RoundTripTests(unittest.TestCase):
    def test_all_builtin_presets_round_trip(self):
        files = sorted(PRESET_DIR.glob("*.pbtx"))
        self.assertEqual(len(files), 14)
        for path in files:
            with self.subTest(preset=path.name):
                text = path.read_text(encoding="utf-8")
                tree = pbtx.parse(text)
                once = pbtx.serialize(tree)
                self.assertEqual(pbtx.serialize(pbtx.parse(once)), once)
                self.assertEqual(pbtx.parse(once), tree)

    def test_comments_preserved(self):
        text = "# header\nbuffers {\n  # size comment\n  size_kb: 8\n}\n# tail\n"
        out = pbtx.serialize(pbtx.parse(text))
        self.assertIn("# header", out)
        self.assertIn("# size comment", out)
        self.assertIn("# tail", out)

    def test_list_syntax_expands(self):
        msg = pbtx.parse('ftrace_events: ["a/b", "c/d"]\n')
        fields = msg.get("ftrace_events")
        self.assertEqual([f.value for f in fields], ['"a/b"', '"c/d"'])

    def test_optional_semicolons_and_colons(self):
        msg = pbtx.parse("a: 1; b { c: 2 } d: { e: 3 }\n")
        self.assertEqual(msg.first("a").value, "1")
        self.assertEqual(msg.first("b").value.first("c").value, "2")
        self.assertEqual(msg.first("d").value.first("e").value, "3")

    def test_error_reports_line_and_col(self):
        with self.assertRaises(pbtx.PbtxError) as ctx:
            pbtx.parse("a: 1\nb: \n")
        self.assertEqual(ctx.exception.line, 2)
        self.assertGreaterEqual(ctx.exception.col, 1)

    def test_unexpected_closing_brace(self):
        with self.assertRaises(pbtx.PbtxError):
            pbtx.parse("}\n")

    def test_single_quoted_string_and_escapes(self):
        msg = pbtx.parse(r"name: 'it\'s'")
        self.assertEqual(msg.first("name").value, r"'it\'s'")


class TransformTests(unittest.TestCase):
    CONFIG = """\
buffers {
    size_kb: 1024
    fill_policy: DISCARD
}
duration_ms: 60000
trigger_config {
    trigger_mode: STOP_TRACING
}
"""

    def test_to_record(self):
        out = pbtx.to_record(self.CONFIG, 20000)
        msg = pbtx.parse(out)
        self.assertEqual(msg.first("write_into_file").value, "true")
        self.assertEqual(msg.first("file_write_period_ms").value, "2500")
        self.assertEqual(msg.first("duration_ms").value, "20000")
        self.assertIsNone(msg.first("trigger_config"))

    def test_to_record_keeps_existing_write_period(self):
        text = self.CONFIG + "file_write_period_ms: 5000\n"
        out = pbtx.to_record(text, None)
        msg = pbtx.parse(out)
        self.assertEqual(msg.first("file_write_period_ms").value, "5000")
        self.assertEqual(msg.first("duration_ms").value, "60000")

    def test_to_flight_clone(self):
        out = pbtx.to_flight(
            self.CONFIG, session_name="sess", trigger_name="trig",
            lifetime_ms=600000, clone=True)
        msg = pbtx.parse(out)
        for name in ("duration_ms", "write_into_file",
                     "file_write_period_ms", "max_file_size_bytes"):
            self.assertIsNone(msg.first(name))
        self.assertEqual(msg.first("unique_session_name").value, '"sess"')
        self.assertEqual(msg.first("buffers").value
                         .first("fill_policy").value, "RING_BUFFER")
        tc = msg.first("trigger_config").value
        self.assertEqual(tc.first("trigger_mode").value, "CLONE_SNAPSHOT")
        self.assertEqual(tc.first("trigger_timeout_ms").value, "600000")
        triggers = tc.first("triggers").value
        self.assertEqual(triggers.first("name").value, '"trig"')
        self.assertEqual(triggers.first("stop_delay_ms").value, "2000")

    def test_to_flight_stop_tracing_variant(self):
        out = pbtx.to_flight(
            self.CONFIG, session_name="s", trigger_name="t",
            lifetime_ms=1000, clone=False)
        tc = pbtx.parse(out).first("trigger_config").value
        self.assertEqual(tc.first("trigger_mode").value, "STOP_TRACING")
        self.assertEqual(tc.first("use_clone_snapshot_if_available").value,
                         "true")

    def test_to_flight_lifetime_bounds(self):
        for bad in (0, -1, pbtx.MAX_FLIGHT_LIFETIME_MS,
                    pbtx.MAX_FLIGHT_LIFETIME_MS + 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    pbtx.to_flight(self.CONFIG, session_name="s",
                                   trigger_name="t", lifetime_ms=bad,
                                   clone=True)

    def test_duration_and_buffer_helpers(self):
        self.assertEqual(pbtx.duration_ms(self.CONFIG), 60000)
        self.assertIsNone(pbtx.duration_ms("buffers { size_kb: 1 }"))
        self.assertEqual(pbtx.total_buffer_kb(
            "buffers { size_kb: 4 }\nbuffers { size_kb: 6 }"), 10)


if __name__ == "__main__":
    unittest.main()
