import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from adbperfetto.adb import Result
from adbperfetto.paths import resource_dir
from adbperfetto.presets import PresetStore, Settings
from adbperfetto.server import create_app
from adbperfetto.sessions import SessionManager
from adbperfetto.state import StateStore

from tests.fake_adb import FakeAdb, wait_for
from tests.test_sessions import SERIAL, make_record_adb

BUILTIN_DIR = resource_dir() / "presets"


def make_env(tmp):
    adb = make_record_adb()
    adb.device_list = [(SERIAL, "device")]

    @adb.on(r"atrace --list_categories")
    def _atrace(ctx):
        return Result(0, "  gfx - Graphics\n  view - View\n", "")

    @adb.on(r"cat /sys/kernel/tracing/available_events")
    def _events(ctx):
        return Result(0, "sched:sched_switch\npower:cpu_frequency\n", "")

    manager = SessionManager(
        adb, StateStore(Path(tmp) / "state.json"), None,
        Path(tmp) / "out", autostart=False)
    presets = PresetStore(BUILTIN_DIR, Path(tmp) / "user")
    settings = Settings(tmp)
    app = create_app(manager, presets, settings)
    return adb, manager, presets, settings, app


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.adb, self.manager, self.presets, self.settings, app = \
            make_env(self.tmp.name)
        self.client = TestClient(app)
        self.manager.monitor.tick()

    def test_ping(self):
        payload = self.client.get("/api/ping").json()
        self.assertEqual(payload["app"], "adbperfetto")
        self.assertIn("version", payload)

    def test_state_shape_and_device(self):
        payload = self.client.get("/api/state").json()
        self.assertEqual(payload["version"], "2.0.1")
        self.assertTrue(payload["adb_ok"])
        self.assertEqual(payload["devices"][0]["serial"], SERIAL)
        self.assertEqual(payload["devices"][0]["info"]["model"],
                         "Test Phone")

    def test_record_stop_via_api(self):
        response = self.client.post(f"/api/devices/{SERIAL}/record",
                                    json={})
        self.assertEqual(response.status_code, 200)
        session = self.manager.sessions[SERIAL]
        self.assertTrue(wait_for(
            lambda: session.status == "recording"))
        self.client.post(f"/api/devices/{SERIAL}/stop", json={})
        self.assertTrue(wait_for(lambda: session.status == "done", 10))

    def test_sse_stream_closes_on_quit(self):
        import threading
        lines = []
        finished = threading.Event()

        def reader():
            try:
                with self.client.stream("GET", "/api/events") as response:
                    for line in response.iter_lines():
                        lines.append(line)
            finally:
                finished.set()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: lines, 5))
        self.client.post("/api/quit")
        self.assertTrue(finished.wait(5))

    def test_record_unknown_device_404(self):
        response = self.client.post("/api/devices/NOPE/record", json={})
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())

    def test_snapshot_on_record_session_conflict(self):
        session_holder = {}

        def recording():
            return SERIAL in self.manager.sessions

        self.client.post(f"/api/devices/{SERIAL}/record", json={})
        self.assertTrue(wait_for(recording))
        response = self.client.post(f"/api/devices/{SERIAL}/snapshot")
        self.assertEqual(response.status_code, 409)

    def test_atrace_and_ftrace(self):
        atrace = self.client.get(f"/api/devices/{SERIAL}/atrace").json()
        self.assertEqual(atrace["categories"],
                         [{"name": "gfx", "description": "Graphics"},
                          {"name": "view", "description": "View"}])
        ftrace = self.client.get(f"/api/devices/{SERIAL}/ftrace").json()
        self.assertEqual(ftrace["events"],
                         ["power/cpu_frequency", "sched/sched_switch"])

    def test_builtin_lists(self):
        payload = self.client.get("/api/builtin-lists").json()
        self.assertIn("sched/sched_switch", payload["ftrace_events"])
        self.assertIn("gfx", payload["atrace_categories"])

    def test_presets_crud(self):
        listing = self.client.get("/api/presets").json()["presets"]
        self.assertEqual(len(listing), 14)
        preset = self.client.get("/api/presets/syshealth_google").json()
        self.assertIn("linux.ftrace", preset["text"])
        response = self.client.post(
            "/api/presets", json={"name": "Mine",
                                  "text": "buffers { size_kb: 1 }\n"})
        self.assertEqual(response.json()["id"], "mine")
        self.assertEqual(len(self.client.get("/api/presets").json()
                             ["presets"]), 15)
        self.client.delete("/api/presets/mine")
        bad = self.client.post(
            "/api/presets", json={"name": "x", "text": "buffers {"})
        self.assertEqual(bad.status_code, 400)
        builtin = self.client.delete("/api/presets/syshealth_google")
        self.assertEqual(builtin.status_code, 400)

    def test_settings(self):
        self.assertEqual(self.client.get("/api/settings").json()
                         ["default_preset"], "syshealth_google")
        updated = self.client.put("/api/settings",
                                  json={"language": "zh"}).json()
        self.assertEqual(updated["language"], "zh")

    def test_prefs(self):
        self.assertEqual(self.client.get("/api/prefs").json(), {})
        updated = self.client.put(f"/api/prefs/{SERIAL}",
                                  json={"mode": "flight"}).json()
        self.assertEqual(updated["mode"], "flight")
        self.assertEqual(self.client.get("/api/prefs").json()
                         [SERIAL]["mode"], "flight")
        bad = self.client.put(f"/api/prefs/{SERIAL}",
                              json={"durationMin": "5"})
        self.assertEqual(bad.status_code, 400)

    def test_config_parse(self):
        ok = self.client.post("/api/config/parse", json={
            "text": "buffers { size_kb: 8 }\nduration_ms: 100\n"}).json()
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["duration_ms"], 100)
        self.assertEqual(ok["buffer_kb"], 8)
        bad = self.client.post("/api/config/parse",
                               json={"text": "a: 1\nb: \n"}).json()
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["line"], 2)

    def test_config_render_and_preview(self):
        text = "buffers { size_kb: 8 }\nduration_ms: 100\n"
        model = self.client.post("/api/config/parse",
                                 json={"text": text}).json()["model"]
        model["duration_ms"] = 5000
        rendered = self.client.post(
            "/api/config/render",
            json={"text": text, "model": model}).json()["text"]
        self.assertIn("duration_ms: 5000", rendered)
        preview = self.client.post("/api/config/preview", json={
            "text": text, "mode": "record",
            "duration_min": 1}).json()["text"]
        self.assertIn("write_into_file: true", preview)
        self.assertIn("duration_ms: 60000", preview)
        flight = self.client.post("/api/config/preview", json={
            "text": text, "mode": "flight", "lifetime_h": 2}).json()["text"]
        self.assertIn("CLONE_SNAPSHOT", flight)
        self.assertIn("adbperfetto_flight", flight)

    def test_outputs(self):
        listing = self.client.get("/api/outputs").json()["outputs"]
        self.assertEqual(listing, [])
        outside = self.client.post("/api/outputs/reveal",
                                   json={"path": "C:/Windows/evil.exe"})
        self.assertEqual(outside.status_code, 400)
        inside = Path(self.tmp.name) / "out" / "x.pftrace"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_bytes(b"1")
        with mock.patch("adbperfetto.outputs.reveal") as reveal:
            ok = self.client.post("/api/outputs/reveal",
                                  json={"path": str(inside)})
            self.assertEqual(ok.status_code, 200)
            reveal.assert_called_once()

    def test_quit(self):
        response = self.client.post("/api/quit")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
