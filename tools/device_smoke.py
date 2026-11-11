"""Real-device smoke test for adbperfetto.

Runs (against the first attached device, or --serial):
  1. record 20 s -> stop -> pulled trace exists
  2. flight start -> two snapshots -> stop
  3. with --mobilelog: record with MobileLog enabled

Not part of unittest discovery; run from the repo root:
  .venv\\Scripts\\python tools\\device_smoke.py [--serial KN80000000000123]
       [--mobilelog]
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adbperfetto import pbtx  # noqa: E402
from adbperfetto.adb import Adb  # noqa: E402
from adbperfetto.device import probe  # noqa: E402
from adbperfetto.paths import resource_dir  # noqa: E402
from adbperfetto.presets import PresetStore  # noqa: E402
from adbperfetto.sessions import FlightSession, RecordSession  # noqa: E402
from adbperfetto.state import StateStore  # noqa: E402

RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f" - {detail}" if detail else ""))


def wait_status(session, statuses, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.status in statuses:
            return True
        if session.status == "failed":
            return False
        time.sleep(0.5)
    return False


def session_kwargs(adb, serial, info, state_dir, out_dir):
    return dict(adb=adb, serial=serial, info=info,
                state_store=StateStore(Path(state_dir) / "state.json"),
                output_dir=Path(out_dir))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial")
    parser.add_argument("--mobilelog", action="store_true")
    parser.add_argument("--out", default=str(
        Path(__file__).resolve().parent.parent / "traces" / "smoke"))
    args = parser.parse_args()

    adb = Adb()
    devices = [s for s, state in adb.devices() if state == "device"]
    if not devices:
        print("ERROR: no device in 'device' state")
        return 1
    serial = args.serial or devices[0]
    if serial not in [s for s, _ in adb.devices()]:
        print(f"ERROR: serial {serial} not attached")
        return 1
    print(f"Device: {serial}")
    info = probe(adb, serial)
    print(f"  model={info.model} sdk={info.sdk} "
          f"perfetto={info.perfetto_version} "
          f"mobilelog={info.has_mobilelog} clone={info.supports_clone}")

    presets = PresetStore(resource_dir() / "presets",
                          Path(tempfile.mkdtemp()) / "user")
    preset = presets.get("syshealth_google")
    config = preset["text"]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tempfile.mkdtemp()
    kwargs = session_kwargs(adb, serial, info, state_dir, out_dir)

    # --- 1. record 20 s -> stop -> pulled ---------------------------------
    print("\n== record: 20 s -> stop ==")
    session = RecordSession(**kwargs)
    try:
        session.start(config, "syshealth_google", 20000,
                      mobilelog=args.mobilelog)
    except Exception as exc:
        report("record start", False, str(exc))
        session = None
    if session:
        if wait_status(session, ("recording",), 60):
            report("record start", True, session.remote_path)
            print("   recording 20 s...")
            time.sleep(20)
            session.stop()
            if wait_status(session, ("done", "failed"), 240):
                ok = (session.status == "done" and session.outputs
                      and Path(session.outputs[0]).stat().st_size > 0)
                report("record stop+pull", ok,
                       f"status={session.status} "
                       f"outputs={session.outputs}")
            else:
                report("record stop+pull", False,
                       f"status={session.status} error={session.error}")
        else:
            report("record start", False,
                   f"status={session.status} error={session.error}")

    # --- 2. flight -> 2 snapshots -> stop ----------------------------------
    print("\n== flight: 2 snapshots -> stop ==")
    flight = FlightSession(**kwargs)
    try:
        flight.start(config, "syshealth_google", 24 * 3600000)
    except Exception as exc:
        report("flight start", False, str(exc))
        flight = None
    if flight:
        if wait_status(flight, ("recording",), 60):
            report("flight start", True,
                   f"pid={flight.pid} one_shot={flight.one_shot}")
            for index in range(2):
                flight.request_snapshot()
                deadline = time.monotonic() + 240
                while (time.monotonic() < deadline
                       and len(flight.snapshots) <= index):
                    time.sleep(0.5)
                report(f"flight snapshot {index}",
                       len(flight.snapshots) > index,
                       flight.snapshots[-1]["local"]
                       if flight.snapshots else "")
            flight.stop()
            wait_status(flight, ("done", "ended", "failed"), 60)
            report("flight stop", flight.status in ("done", "ended"),
                   f"status={flight.status} error={flight.error}")
        else:
            report("flight start", False,
                   f"status={flight.status} error={flight.error}")

    failed = [name for name, ok in RESULTS if not ok]
    print("\n" + ("SMOKE OK" if not failed
                  else f"SMOKE FAILED: {', '.join(failed)}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
