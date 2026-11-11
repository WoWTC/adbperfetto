import unittest
from pathlib import Path

from adbperfetto import builder, pbtx
from adbperfetto.paths import resource_dir

PRESET_DIR = resource_dir() / "presets"
SAMPLE = """\
buffers {
    size_kb: 1024
    fill_policy: RING_BUFFER
}
data_sources {
    config {
        name: "linux.ftrace"
        target_buffer: 0
        ftrace_config {
            ftrace_events: "sched/sched_switch"
            ftrace_events: "sched/sched_wakeup"
            atrace_categories: "gfx"
            buffer_size_kb: 2048
        }
    }
}
data_sources {
    config {
        name: "linux.process_stats"
        target_buffer: 1
    }
}
duration_ms: 60000
"""


class ModelTests(unittest.TestCase):
    def test_model_from_every_preset(self):
        for path in sorted(PRESET_DIR.glob("*.pbtx")):
            with self.subTest(preset=path.name):
                model = builder.model_from_text(
                    path.read_text(encoding="utf-8"))
                self.assertIn("ftrace", model)
                self.assertIn("sources", model)

    def test_model_fields(self):
        model = builder.model_from_text(SAMPLE)
        self.assertEqual(model["duration_ms"], 60000)
        self.assertEqual(model["buffers"],
                         [{"size_kb": 1024, "fill_policy": "RING_BUFFER"}])
        self.assertTrue(model["ftrace"]["enabled"])
        self.assertEqual(model["ftrace"]["events"],
                         ["sched/sched_switch", "sched/sched_wakeup"])
        self.assertEqual(model["ftrace"]["atrace_categories"], ["gfx"])
        self.assertEqual(model["ftrace"]["buffer_size_kb"], 2048)
        self.assertTrue(model["sources"]["linux.process_stats"])
        self.assertFalse(model["sources"]["android.statsd"])

    def test_apply_model_noop_on_every_preset(self):
        for path in sorted(PRESET_DIR.glob("*.pbtx")):
            with self.subTest(preset=path.name):
                text = path.read_text(encoding="utf-8")
                out = builder.apply_model(
                    text, builder.model_from_text(text))
                self.assertEqual(out, pbtx.serialize(pbtx.parse(text)))


class ApplyTests(unittest.TestCase):
    def test_duration_set_and_removed(self):
        model = builder.model_from_text(SAMPLE)
        model["duration_ms"] = 5000
        out = builder.apply_model(SAMPLE, model)
        self.assertEqual(pbtx.duration_ms(out), 5000)
        model["duration_ms"] = None
        out = builder.apply_model(out, model)
        self.assertIsNone(pbtx.duration_ms(out))

    def test_buffers_count_changes(self):
        model = builder.model_from_text(SAMPLE)
        model["buffers"] = [
            {"size_kb": 2048, "fill_policy": "DISCARD"},
            {"size_kb": 512, "fill_policy": "RING_BUFFER"},
        ]
        out = builder.apply_model(SAMPLE, model)
        msg = pbtx.parse(out)
        buffers = msg.get("buffers")
        self.assertEqual(len(buffers), 2)
        self.assertEqual(buffers[0].value.first("size_kb").value, "2048")
        self.assertEqual(buffers[0].value.first("fill_policy").value,
                         "DISCARD")
        self.assertEqual(buffers[1].value.first("size_kb").value, "512")

    def test_ftrace_events_sync_keeps_order_and_appends(self):
        model = builder.model_from_text(SAMPLE)
        model["ftrace"]["events"] = ["sched/sched_wakeup", "ftrace/print"]
        out = builder.apply_model(SAMPLE, model)
        events = [e for e in builder.model_from_text(out)["ftrace"]["events"]]
        self.assertEqual(events, ["sched/sched_wakeup", "ftrace/print"])
        # "sched/sched_wakeup" keeps its position; new event appended after
        # the last ftrace_events field.
        msg = pbtx.parse(out)
        self.assertNotIn('"sched/sched_switch"', out)
        self.assertIn('"ftrace/print"', out)

    def test_ftrace_disable_and_enable(self):
        model = builder.model_from_text(SAMPLE)
        model["ftrace"]["enabled"] = False
        out = builder.apply_model(SAMPLE, model)
        self.assertNotIn("linux.ftrace", out)
        model2 = builder.model_from_text(out)
        model2["ftrace"]["enabled"] = True
        model2["ftrace"]["events"] = ["x/y"]
        out2 = builder.apply_model(out, model2)
        model3 = builder.model_from_text(out2)
        self.assertTrue(model3["ftrace"]["enabled"])
        self.assertEqual(model3["ftrace"]["events"], ["x/y"])

    def test_source_off_and_on(self):
        model = builder.model_from_text(SAMPLE)
        model["sources"]["linux.process_stats"] = False
        model["sources"]["android.power"] = True
        out = builder.apply_model(SAMPLE, model)
        self.assertNotIn("linux.process_stats", out)
        self.assertIn('"android.power"', out)
        self.assertIn("battery_poll_ms: 1000", out)

    def test_unrelated_content_and_comments_preserved(self):
        text = "# top\n" + SAMPLE + "# bottom\n"
        model = builder.model_from_text(text)
        model["duration_ms"] = 1000
        out = builder.apply_model(text, model)
        self.assertIn("# top", out)
        self.assertIn("# bottom", out)


class BuiltinListTests(unittest.TestCase):
    def test_builtin_lists(self):
        self.assertIn("sched/sched_switch", builder.BUILTIN_FTRACE_EVENTS)
        self.assertIn("binder/*", builder.BUILTIN_FTRACE_EVENTS)
        self.assertIn("gfx", builder.BUILTIN_ATRACE_CATEGORIES)
        self.assertEqual(builder.BUILTIN_FTRACE_EVENTS,
                         sorted(builder.BUILTIN_FTRACE_EVENTS))


if __name__ == "__main__":
    unittest.main()
