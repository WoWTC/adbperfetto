import tempfile
import time
import unittest
from pathlib import Path

from adbperfetto.adb import Result
from adbperfetto.device import DeviceInfo
from adbperfetto.sessions import (
    CONFIG_FLIGHT, FLIGHT_NAME, FlightSession, RecordSession, SessionError,
    SessionManager, parse_flight_ps, parse_query_sessions,
    parse_record_status)
from adbperfetto.state import StateStore

from tests.fake_adb import FakeAdb, wait_for

SERIAL = "SER1"
INFO = DeviceInfo(serial=SERIAL, model="Test Phone", build_id="B1",
                  sdk=36, supports_clone=True)
INFO_OLD = DeviceInfo(serial=SERIAL, model="Old Phone", build_id="B0",
                      sdk=34, supports_clone=False)
CONFIG = """\
buffers { size_kb: 1024 fill_policy: RING_BUFFER }
data_sources { config { name: "linux.sys_stats" } }
duration_ms: 60000
"""

QUERY_EMPTY = """\
Not meant for machine consumption. Use --query-raw for scripts.

Service: Perfetto v49.0 (N/A)
Tracing sessions: 1 (started: 1)


TRACING SESSIONS:

ID      UID     STATE      BUF (#) KB   DUR (s)   #DS  STARTED  NAME
===     ===     =====      ==========   =======   ===  =======  ====
34      2000    STARTED    (1) 1024     300       1    05:39:44
"""

QUERY_FLIGHT = QUERY_EMPTY.replace(
    "05:39:44\n", "05:39:44 adbperfetto_flight\n")


def make_record_adb():
    adb = FakeAdb()
    adb.det = 2
    adb.trace_size = 1234
    adb.pushed = []

    @adb.on(r"echo DET=")
    def _monitor(ctx):
        return Result(0, f"DET={adb.det}\nSIZE={adb.trace_size}\n", "")

    @adb.on(r"perfetto --is_detached")
    def _detached(ctx):
        return Result(adb.det, "", "")

    @adb.on(r"cat > ")
    def _push(ctx):
        adb.pushed.append(ctx["input_text"])
        return Result(0, "", "")

    @adb.on(r"perfetto --txt -c .* --detach=")
    def _launch(ctx):
        adb.det = 0
        return Result(0, "", "")

    @adb.on(r"perfetto --attach=")
    def _attach(ctx):
        adb.det = 2
        return Result(0, "", "")

    @adb.on(r"echo MODEL=")
    def _probe(ctx):
        return Result(
            0, "MODEL=Test Phone\nSDK=36\nBUILD_ID=B1\n"
               "PERFETTO=Perfetto v49.0 (N/A)\n"
               "LOGGERUI=package:/x\n", "")

    @adb.on(r"ls -t")
    def _ls(ctx):
        return Result(
            0, "/data/misc/perfetto-traces/adbperfetto_orphan.pftrace\n", "")

    @adb.on(r"stat -c %Y")
    def _mtime(ctx):
        return Result(0, "1700000000\n", "")

    @adb.on(r"stat -c %s")
    def _size(ctx):
        return Result(0, "1234\n", "")

    return adb


def make_flight_adb(query=QUERY_EMPTY):
    adb = FakeAdb()
    adb.alive = 0
    adb.pushed = []

    @adb.on(r"perfetto --is_detached")
    def _detached(ctx):
        return Result(2, "", "")

    @adb.on(r"perfetto --query")
    def _query(ctx):
        return Result(0, query, "")

    @adb.on(r"cat > ")
    def _push(ctx):
        adb.pushed.append(ctx["input_text"])
        return Result(0, "", "")

    @adb.on(r"--background-wait")
    def _launch(ctx):
        adb.alive = 1
        return Result(0, "Connected to traced\n4321\n", "")

    @adb.on(r"echo ALIVE=")
    def _alive(ctx):
        return Result(0, f"ALIVE={0 if adb.alive else 1}\n", "")

    @adb.on(r"--clone-by-name")
    def _clone(ctx):
        return Result(0, "", "Wrote 1234 bytes into /data/...")

    @adb.on(r"kill -TERM")
    def _kill(ctx):
        adb.alive = 0
        return Result(0, "", "")

    @adb.on(r"trigger_perfetto")
    def _trigger(ctx):
        adb.alive = 0
        return Result(0, "", "")

    @adb.on(r"stat -c %s")
    def _size(ctx):
        return Result(0, "1234\n", "")

    @adb.on(r"echo MODEL=")
    def _probe(ctx):
        return Result(0, "MODEL=Test Phone\nSDK=36\n", "")

    return adb


def record_kwargs(tmp, adb, store=None, info=INFO):
    return dict(adb=adb, serial=SERIAL, info=info,
                state_store=store or StateStore(Path(tmp) / "state.json"),
                output_dir=Path(tmp) / "out", poll_interval=0.01,
                retry_interval=0.02, progress_interval=0.005,
                attach_timeout=0.5)


def manager_kwargs(tmp, adb):
    return dict(adb=adb, state_store=StateStore(Path(tmp) / "state.json"),
                settings=None, output_dir=Path(tmp) / "out",
                poll_interval=0.01, flight_poll_interval=0.01,
                monitor_interval=0.02, retry_interval=0.02,
                progress_interval=0.005, autostart=True)


class ParserTests(unittest.TestCase):
    def test_parse_record_status(self):
        status = parse_record_status("DET=0\nSIZE=123\nMB=1\nLOG=7\n")
        self.assertEqual(status["det"], 0)
        self.assertEqual(status["size"], 123)
        self.assertTrue(status["mobile_running"])
        self.assertEqual(status["log_kb"], 7)
        self.assertIsNone(parse_record_status("nonsense\nDET=oops\n"))
        self.assertIsNone(parse_record_status("SIZE=3\n"))

    def test_parse_query_sessions(self):
        sessions = parse_query_sessions(QUERY_FLIGHT)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["name"], "adbperfetto_flight")
        self.assertEqual(sessions[0]["state"], "STARTED")
        self.assertEqual(parse_query_sessions(QUERY_EMPTY)[0]["name"], "")
        self.assertEqual(parse_query_sessions("no table here"), [])

    def test_parse_flight_ps(self):
        text = (
            "  PID ARGS\n"
            "  1000 /system/bin/init\n"
            "  4321 perfetto --txt -c "
            "/data/misc/perfetto-configs/adbperfetto_flight.pbtx "
            "-o /data/misc/perfetto-traces/adbperfetto_flight_1.pftrace "
            "--background-wait\n")
        self.assertEqual(parse_flight_ps(text), (
            4321,
            "/data/misc/perfetto-traces/adbperfetto_flight_1.pftrace"))
        self.assertIsNone(parse_flight_ps("  1 init\n"))


class RecordSessionTests(unittest.TestCase):
    def test_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            session = RecordSession(**record_kwargs(tmp, adb))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            self.assertIn("write_into_file: true", adb.pushed[0])
            self.assertTrue(session.remote_path.startswith(
                "/data/misc/perfetto-traces/adbperfetto_"))
            session.stop()
            self.assertTrue(wait_for(lambda: session.status == "done", 10))
            files = list((Path(tmp) / "out").glob("*.pftrace"))
            self.assertEqual(len(files), 1)
            self.assertIn(str(files[0]), session.outputs)
            self.assertTrue(any("rm -f" in c
                                for c in adb.shell_calls("perfetto-traces")))
            self.assertEqual(session.api_dict()["mode"], "record")
            self.assertFalse(session.ended_by_duration)

    def test_refuse_when_already_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            adb.det = 0
            session = RecordSession(**record_kwargs(tmp, adb))
            with self.assertRaises(SessionError):
                session.start(CONFIG, "test", 60000)

    def test_disconnect_and_reconnect_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            session = RecordSession(**record_kwargs(tmp, adb))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            adb.offline = True
            self.assertTrue(wait_for(lambda: not session.connected))
            self.assertFalse(session.connected)
            adb.offline = False
            self.assertTrue(wait_for(lambda: session.connected))
            self.assertEqual(len(session.disconnects), 1)
            session.stop()
            self.assertTrue(wait_for(lambda: session.status == "done", 10))

    def test_stop_delivered_after_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            session = RecordSession(**record_kwargs(tmp, adb))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            adb.offline = True
            self.assertTrue(wait_for(lambda: not session.connected))
            session.stop()
            time.sleep(0.1)
            self.assertEqual(len(adb.shell_calls("--attach")), 0)
            adb.offline = False
            self.assertTrue(wait_for(lambda: session.status == "done", 10))
            self.assertGreaterEqual(len(adb.shell_calls("--attach")), 1)

    def test_duration_end_auto_pull(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            session = RecordSession(**record_kwargs(tmp, adb))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            adb.det = 2  # trace ended on its own
            self.assertTrue(wait_for(lambda: session.status == "done", 10))
            self.assertTrue(session.ended_by_duration)
            self.assertTrue(session.outputs)

    def test_pull_failure_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            adb.pull_failures = 1
            session = RecordSession(**record_kwargs(tmp, adb))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            session.stop()
            self.assertTrue(wait_for(lambda: session.status == "done", 10))
            self.assertGreaterEqual(len(adb.pull_calls()), 2)

    def test_resume_from_state_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            store = StateStore(Path(tmp) / "state.json")
            session = RecordSession(**record_kwargs(tmp, adb, store=store))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            record = store.get(SERIAL)
            self.assertEqual(record["mode"], "record")
            resumed = RecordSession(**record_kwargs(tmp, adb, store=store))
            resumed._load_record(record)
            resumed.resume()
            self.assertTrue(wait_for(
                lambda: resumed.status == "recording"))
            resumed.stop()
            self.assertTrue(wait_for(
                lambda: resumed.status == "done", 10))

    def test_resume_from_state_pulling(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            store = StateStore(Path(tmp) / "state.json")
            session = RecordSession(**record_kwargs(tmp, adb, store=store))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            record = dict(store.get(SERIAL))
            record["status"] = "pulling"
            resumed = RecordSession(**record_kwargs(tmp, adb, store=store))
            resumed._load_record(record)
            resumed.resume()
            self.assertTrue(wait_for(
                lambda: resumed.status == "done", 10))


class FlightSessionTests(unittest.TestCase):
    def test_clone_snapshot_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_flight_adb()
            session = FlightSession(**record_kwargs(tmp, adb))
            session.start(CONFIG, "test", 600000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            self.assertEqual(session.pid, 4321)
            self.assertIn("CLONE_SNAPSHOT", adb.pushed[0])
            self.assertIn(f'"{FLIGHT_NAME}"', adb.pushed[0])
            session.request_snapshot()
            self.assertTrue(wait_for(
                lambda: len(session.snapshots) == 1, 10))
            self.assertTrue(Path(session.snapshots[0]["local"]).exists())
            session.stop()
            self.assertTrue(wait_for(lambda: session.status == "done", 10))
            self.assertTrue(any("kill -TERM 4321" in c
                                for c in adb.shell_calls()))
            self.assertTrue(any(CONFIG_FLIGHT in c and "rm -f" in c
                                for c in adb.shell_calls()))

    def test_one_shot_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_flight_adb()
            session = FlightSession(
                **record_kwargs(tmp, adb, info=INFO_OLD))
            session.start(CONFIG, "test", 600000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            self.assertTrue(session.one_shot)
            self.assertIn("STOP_TRACING", adb.pushed[0])
            session.request_snapshot()
            self.assertTrue(wait_for(lambda: session.status == "done", 10))
            self.assertEqual(len(session.snapshots), 1)
            self.assertTrue(Path(session.snapshots[0]["local"]).exists())

    def test_refuse_when_already_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_flight_adb(query=QUERY_FLIGHT)
            session = FlightSession(**record_kwargs(tmp, adb))
            with self.assertRaises(SessionError):
                session.start(CONFIG, "test", 600000)

    def test_ended_when_process_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_flight_adb()
            session = FlightSession(**record_kwargs(tmp, adb))
            session.start(CONFIG, "test", 600000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            adb.alive = 0  # process died on its own
            self.assertTrue(wait_for(lambda: session.status == "ended", 10))


class ManagerTests(unittest.TestCase):
    def test_device_appears_and_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            adb.device_list = [(SERIAL, "device")]
            manager = SessionManager(**manager_kwargs(tmp, adb))
            try:
                self.assertTrue(wait_for(
                    lambda: SERIAL in manager.devices
                    and manager.devices[SERIAL]["info"] is not None))
                info = manager.devices[SERIAL]["info"]
                self.assertEqual(info.model, "Test Phone")
                self.assertTrue(info.supports_clone)
                self.assertTrue(manager.snapshot()["adb_ok"])
            finally:
                manager.shutdown()

    def test_orphan_record_and_adopt(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            adb.det = 0  # a detached session is already running
            adb.device_list = [(SERIAL, "device")]
            manager = SessionManager(**manager_kwargs(tmp, adb))
            try:
                self.assertTrue(wait_for(
                    lambda: manager.orphans.get(SERIAL) is not None))
                self.assertEqual(manager.orphans[SERIAL]["mode"], "record")
                session = manager.adopt(SERIAL)
                self.assertEqual(session.status, "recording")
                self.assertEqual(session.started_at, 1700000000)
                manager.stop(SERIAL)
                self.assertTrue(wait_for(
                    lambda: session.status == "done", 10))
                manager.dismiss(SERIAL)
                self.assertNotIn(SERIAL, manager.sessions)
            finally:
                manager.shutdown()

    def test_recovery_from_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            store = StateStore(Path(tmp) / "state.json")
            session = RecordSession(
                **record_kwargs(tmp, adb, store=store))
            session.start(CONFIG, "test", 60000)
            self.assertTrue(wait_for(
                lambda: session.status == "recording"))
            # Simulate a restart: new manager loads the persisted record.
            adb.device_list = [(SERIAL, "device")]
            manager = SessionManager(**manager_kwargs(tmp, adb))
            try:
                self.assertTrue(wait_for(
                    lambda: SERIAL in manager.sessions, 10))
                resumed = manager.sessions[SERIAL]
                self.assertTrue(wait_for(
                    lambda: resumed.status == "recording"))
            finally:
                manager.shutdown()

    def test_orphan_flight(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_flight_adb(query=QUERY_FLIGHT)
            adb.alive = 1  # the orphan flight process is still running
            adb.device_list = [(SERIAL, "device")]

            @adb.on(r"ps -A -o PID,ARGS")
            def _ps(ctx):
                return Result(
                    0, "  PID ARGS\n  4321 perfetto --txt -c "
                       f"{CONFIG_FLIGHT} -o "
                       "/data/misc/perfetto-traces/"
                       "adbperfetto_flight_1.pftrace --background-wait\n",
                    "")

            manager = SessionManager(**manager_kwargs(tmp, adb))
            try:
                self.assertTrue(wait_for(
                    lambda: manager.orphans.get(SERIAL) is not None, 10))
                self.assertEqual(manager.orphans[SERIAL]["mode"], "flight")
                session = manager.adopt(SERIAL)
                self.assertEqual(session.pid, 4321)
                manager.stop(SERIAL)
                self.assertTrue(wait_for(
                    lambda: session.status == "done", 10))
            finally:
                manager.shutdown()

    def test_start_record_via_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            adb = make_record_adb()
            adb.device_list = [(SERIAL, "device")]
            manager = SessionManager(**manager_kwargs(tmp, adb))
            try:
                self.assertTrue(wait_for(
                    lambda: SERIAL in manager.devices
                    and manager.devices[SERIAL]["info"] is not None))
                session = manager.start_record(SERIAL, CONFIG, "test",
                                               60000)
                self.assertTrue(wait_for(
                    lambda: session.status == "recording"))
                with self.assertRaises(SessionError):
                    manager.start_record(SERIAL, CONFIG, "test", 60000)
                snap = manager.snapshot()
                device = snap["devices"][0]
                self.assertEqual(device["session"]["status"], "recording")
                manager.stop(SERIAL)
                self.assertTrue(wait_for(
                    lambda: session.status == "done", 10))
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
