"""Entry point: single instance check, server startup, browser open."""

import argparse
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from . import VERSION, paths
from .adb import Adb, AdbError
from .presets import PresetStore, Settings
from .server import create_app
from .sessions import SessionManager
from .state import StateStore


def _find_free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                if port == 0:
                    raise
    raise OSError("Could not bind a port")


def _existing_instance(data: Path):
    instance = data / "instance.json"
    try:
        record = json.loads(instance.read_text(encoding="utf-8"))
        port = int(record["port"])
    except (OSError, ValueError, KeyError):
        return None
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ping", timeout=2) as response:
            payload = json.loads(response.read())
        if payload.get("app") == "adbperfetto":
            return port
    except Exception:
        pass
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="adbperfetto",
        description="Record and flight-recorder Perfetto traces over ADB.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"adbperfetto {VERSION}")
        return 0

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("adbperfetto")

    demo = os.environ.get("ADBPERFETTO_DEMO") == "1"
    if demo:
        data = Path(tempfile.mkdtemp(prefix="adbperfetto_demo_"))
        demo_output = data / "traces"
    else:
        data = paths.data_dir()
        existing = _existing_instance(data)
        if existing is not None:
            url = f"http://127.0.0.1:{existing}"
            print(f"adbperfetto is already running at {url}")
            webbrowser.open(url)
            return 0

    settings = Settings(data)
    presets = PresetStore(paths.resource_dir() / "presets",
                          data / "presets")
    state = StateStore(data / "state.json")
    if demo:
        from .demo import DemoAdb
        adb = DemoAdb()

        def output_dir() -> Path:
            return demo_output
    else:
        try:
            adb = Adb()
        except AdbError as exc:
            log.warning("%s", exc)
            adb = None

        def output_dir() -> Path:
            configured = settings.output_dir
            if configured:
                return Path(configured)
            return paths.default_output_dir()

    intervals = dict(poll_interval=0.5, flight_poll_interval=0.5,
                     monitor_interval=0.5, retry_interval=1.0,
                     progress_interval=0.2) if demo else {}
    manager = SessionManager(adb, state, settings, output_dir, **intervals)
    port = _find_free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    shutdown_event = threading.Event()
    config = uvicorn.Config(app=None, host="127.0.0.1", port=port,
                            log_level="warning",
                            timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    app = create_app(manager, presets, settings,
                     quit_hook=lambda: setattr(server, "should_exit", True),
                     shutdown_event=shutdown_event,
                     demo_adb=adb if demo else None)
    config.app = app

    (data / "instance.json").write_text(
        json.dumps({"port": port, "pid": os.getpid()}),
        encoding="utf-8")

    print(f"adbperfetto {VERSION}")
    if demo:
        print("DEMO MODE (ADBPERFETTO_DEMO=1) - simulated devices, "
              "no real adb")
        print(f"Demo data: {data}")
        print(f"Demo output: {demo_output}")
    print(f"Web UI: {url}")
    print("Keep this window open while capturing. Close it to quit; "
          "recordings on the phone keep running and resume next time.")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_event.set()
        manager.shutdown()
        try:
            (data / "instance.json").unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
