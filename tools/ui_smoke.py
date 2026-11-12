"""UI smoke test: start the demo server and drive the real UI in Edge.

Covers: KN8 record -> unplug -> replug -> stop -> done; KN8 flight ->
two snapshots (button + S key) -> stop with final snapshot; configs
(preset load, builder toggle, syntax error); language switch.
Fails on any browser console error.

Run:  .venv\\Scripts\\python tools\\ui_smoke.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8971
BASE = f"http://127.0.0.1:{PORT}"
KN8 = "KN8DEMO0001"
WIFI = "192.168.1.23:5555"

sys.path.insert(0, str(ROOT))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("FAIL: playwright is not installed (pip install playwright)")
    sys.exit(1)


def http(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def wait_for(predicate, timeout=20, what="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.25)
    raise AssertionError(f"timeout waiting for {what}; last={last}")


def device_state(serial):
    state = http("/api/state")
    return next((d for d in state["devices"] if d["serial"] == serial),
                None)


def session_status(serial):
    dev = device_state(serial)
    return dev["session"]["status"] if dev and dev["session"] else None


class Smoke:
    def __init__(self):
        self.proc = None
        self.errors = []
        self.results = []

    def check(self, name, ok, detail=""):
        self.results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'} {name} {detail}")
        if not ok:
            raise AssertionError(f"{name}: {detail}")

    def start_server(self):
        env = dict(os.environ, ADBPERFETTO_DEMO="1")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "adbperfetto",
             "--no-browser", "--port", str(PORT)],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_for(lambda: self._ping(), timeout=30, what="server up")

    def _ping(self):
        try:
            return http("/api/ping")["app"] == "adbperfetto"
        except Exception:
            return False

    def stop_server(self):
        try:
            http("/api/quit", {})
        except Exception:
            pass
        if self.proc:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def run(self):
        self.start_server()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge",
                                            headless=True)
                page = browser.new_page(
                    viewport={"width": 1440, "height": 900})
                page.on("console", lambda m:
                        self.errors.append(f"console: {m.text}")
                        if m.type == "error" else None)
                page.on("pageerror", lambda e:
                        self.errors.append(f"pageerror: {e}"))
                page.goto(BASE, wait_until="networkidle")
                self.test_version(page)
                self.test_record_flow(page)
                self.test_flight_flow(page)
                self.test_configs(page)
                self.test_language(page)
                self.test_theme(page)
                self.test_prefs(page)
                self.test_wifi_hint(page)
                browser.close()
        finally:
            self.stop_server()
        if self.errors:
            print("\nCONSOLE ERRORS:")
            for err in self.errors:
                print("  " + err)
        ok = not self.errors
        print(f"\nui_smoke: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    # ------------------------------------------------------------ tests

    def test_version(self, page):
        print("version")
        version = http("/api/ping")["version"]
        wait_for(lambda: page.locator("#app-version").inner_text()
                 == f"v{version}", timeout=10, what="topbar version")
        self.check("topbar shows version", True, f"v{version}")

    def select_kn8(self, page):
        page.locator(f'.rail-row[data-serial="{KN8}"]').click()
        page.wait_for_timeout(400)

    def wait_status(self, serial, statuses, timeout=25):
        if isinstance(statuses, str):
            statuses = {statuses}
        return wait_for(
            lambda: session_status(serial) in statuses,
            timeout=timeout,
            what=f"{serial} in {statuses}")

    def test_record_flow(self, page):
        print("record flow")
        self.select_kn8(page)
        page.locator(".btn.primary.big").click()
        self.wait_status(KN8, "recording")
        self.check("record starts", True)

        http("/api/demo/unplug", {"serial": KN8})
        wait_for(lambda: (d := device_state(KN8))
                 and d["session"]["connected"] is False,
                 timeout=15, what="session disconnected")
        self.check("session survives unplug", True)

        http("/api/demo/replug", {"serial": KN8})
        wait_for(lambda: (d := device_state(KN8))
                 and d["session"]["connected"] is True,
                 timeout=15, what="session reconnected")
        self.check("reconnect resumes", True)

        page.locator(".btn.primary.big").click()   # stop and save
        self.wait_status(KN8, {"done"}, timeout=30)
        self.check("stop pulls trace -> done", True)
        dev = device_state(KN8)
        outputs = dev["session"]["outputs"]
        self.check("output file exists",
                   bool(outputs) and Path(outputs[0]).is_file(),
                   outputs[0] if outputs else "no output")
        page.locator(".btn-row .btn.quiet", has_text="录制").click()
        page.wait_for_timeout(600)

    def test_flight_flow(self, page):
        print("flight flow")
        self.select_kn8(page)
        page.locator(".seg-btn", has_text="飞行").click()
        page.wait_for_timeout(400)
        page.locator(".btn.primary.big").click()
        self.wait_status(KN8, "recording")
        self.check("flight starts", True)

        page.locator("#btn-snapshot").click()          # snapshot via button
        wait_for(lambda: len((device_state(KN8)["session"]
                              or {}).get("snapshots", [])) >= 1,
                 timeout=20, what="snapshot 1")
        page.keyboard.press("s")                        # snapshot via S key
        wait_for(lambda: len((device_state(KN8)["session"]
                              or {}).get("snapshots", [])) >= 2,
                 timeout=20, what="snapshot 2")
        self.check("two snapshots (button + S key)", True)

        page.locator(".btn-row .btn.quiet",
                     has_text="停止").click()           # stop -> modal
        page.wait_for_selector("#stop-save-snap", timeout=5000)
        self.check("stop modal opens", True)
        page.locator(".modal-foot .btn.primary").click()
        self.wait_status(KN8, {"done", "ended"}, timeout=30)
        dev = device_state(KN8)
        snaps = dev["session"].get("snapshots", [])
        self.check("final snapshot saved", len(snaps) >= 3,
                   f"snapshots={len(snaps)}")
        dismiss = page.locator(".btn-row .btn.quiet",
                               has_text="关闭")
        if dismiss.count() == 0:
            dismiss = page.locator(".btn-row .btn.quiet",
                                   has_text="录制")
        dismiss.first.click()
        page.wait_for_timeout(400)

    def test_configs(self, page):
        print("configs")
        page.locator('.topbar-btn[data-view="configs"]').click()
        page.wait_for_timeout(800)

        # load a different preset
        page.locator('.preset-item[data-id="atrace_apps"]').click()
        page.wait_for_timeout(800)
        before = page.locator(".editor-input").input_value()
        self.check("preset loads into editor",
                   "data_sources" in before, f"{len(before)} chars")

        # toggle an atrace category in the builder -> text changes
        page.locator(".builder-tab", has_text="Atrace").click()
        page.wait_for_timeout(400)
        row = page.locator(".checklist-row").first
        row.click()
        page.wait_for_timeout(800)
        after = page.locator(".editor-input").input_value()
        self.check("builder toggle changes text", after != before)

        # editor header: name field + status pills
        name = page.locator("#cfg-name").input_value()
        self.check("preset name field", name == "atrace_apps", name)
        wait_for(lambda: page.locator("#status-pills .pill").count()
                 >= 3, timeout=10, what="status pills")
        pills = page.locator("#status-pills .pill")
        self.check("status pills shown", pills.count() >= 3,
                   str(pills.count()))
        ok_cls = pills.first.get_attribute("class") or ""
        self.check("valid pill", "ok" in ok_cls, ok_cls)

        # set as default via the star
        row = page.locator(".preset-row",
                           has=page.locator(
                               '.preset-item[data-id="launcher3"]'))
        row.locator(".preset-star").click()
        wait_for(lambda: http("/api/settings").get("default_preset")
                 == "launcher3", timeout=10, what="default preset")
        self.check("set as default", True)

        # type a syntax error -> error line + invalid status
        area = page.locator(".editor-input")
        area.click()
        area.press("Control+A")
        area.type("not valid {{{ pbtx")
        page.wait_for_timeout(900)
        bad = page.locator("#status-pills .pill.bad")
        self.check("syntax error flagged", bad.count() >= 1)
        edited = page.locator("#edited-mark").is_visible()
        self.check("edited marker shown", edited)
        marked = page.locator(".editor-highlight .line-error").count()
        self.check("error line highlighted", marked >= 1, str(marked))

        # import a config file -> draft loads into the editor
        tmp = Path(os.environ.get("TEMP", ".")) / "ui_smoke_import.pbtx"
        text = http("/api/presets/syshealth_google")["text"]
        tmp.write_text(text, encoding="utf-8")
        page.locator("#import-file").set_input_files(str(tmp))
        page.wait_for_timeout(900)
        draft_name = page.locator("#cfg-name").input_value()
        self.check("import loads draft",
                   draft_name == "ui_smoke_import", draft_name)

        # Ctrl+S opens save-as and saves the draft as a preset
        page.locator(".editor-input").click()
        page.keyboard.press("Control+s")
        page.wait_for_selector(".modal-overlay.open", timeout=5000)
        modal_in = page.locator(".modal-body input[type='text']")
        modal_in.fill("smoke_saved")
        page.locator(".modal-foot .btn.primary").click()
        wait_for(lambda: any(p["id"] == "smoke_saved"
                             for p in http("/api/presets")["presets"]),
                 timeout=10, what="saved preset")
        self.check("Ctrl+S saves preset", True)

        # restore a valid preset so later screens look sane
        # (editor is dirty -> unsaved-changes confirm appears)
        page.locator('.preset-item[data-id="syshealth_google"]').click()
        page.wait_for_timeout(600)
        if page.locator("#modal-overlay.open").count():
            page.locator(".modal-foot .btn.primary").click()
            page.wait_for_timeout(800)

    def test_language(self, page):
        print("language switch")
        page.locator('.topbar-btn[data-view="capture"]').click()
        page.wait_for_timeout(500)
        page.locator("#lang-en").click()
        page.wait_for_timeout(800)
        seg = page.locator(".seg-btn").first.inner_text()
        self.check("English strings applied", seg.strip() == "Record",
                   seg)
        active = page.locator("#lang-en").get_attribute("class") or ""
        self.check("EN toggle active", "active" in active, active)
        page.locator("#lang-zh").click()
        page.wait_for_timeout(600)

    def test_theme(self, page):
        print("theme toggle")
        get_theme = "document.documentElement.dataset.theme"
        initial = page.evaluate(get_theme)
        self.check("initial theme resolved",
                   initial in ("light", "dark"), initial)
        page.locator("#btn-theme").click()
        page.wait_for_timeout(400)
        flipped = page.evaluate(get_theme)
        self.check("theme flips on click",
                   flipped in ("light", "dark") and flipped != initial,
                   f"{initial} -> {flipped}")
        self.check("theme saved to server",
                   http("/api/settings").get("theme") == flipped,
                   http("/api/settings").get("theme"))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(600)
        after = page.evaluate(get_theme)
        self.check("theme persists across reload",
                   after == flipped, after)
        # restore the original theme for a clean exit
        if after != initial:
            page.locator("#btn-theme").click()
            page.wait_for_timeout(400)

    def test_prefs(self, page):
        print("prefs persistence")
        self.select_kn8(page)
        page.locator(".seg-btn").nth(1).click()      # flight mode
        wait_for(lambda: http("/api/prefs").get(KN8, {}).get("mode")
                 == "flight", timeout=10, what="flight mode saved")
        self.check("flight mode saved to server", True)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(600)
        self.select_kn8(page)
        flight_cls = page.locator(".seg-btn").nth(1) \
            .get_attribute("class") or ""
        self.check("flight seg active after reload",
                   "active" in flight_cls, flight_cls)
        page.locator(".seg-btn").nth(0).click()      # back to record
        wait_for(lambda: http("/api/prefs").get(KN8, {}).get("mode")
                 == "record", timeout=10, what="record mode saved")
        self.check("record mode restored", True)

    def test_wifi_hint(self, page):
        print("wifi hint")
        page.locator(f'.rail-row[data-serial="{WIFI}"]').click()
        page.wait_for_timeout(500)

        def hint_texts():
            hints = page.locator(".dev-head .hint.amber")
            return [hints.nth(i).inner_text()
                    for i in range(hints.count())]

        wait_for(lambda: any("WiFi" in text
                             for text in hint_texts()),
                 timeout=10, what="wifi hint on wifi device")
        self.check("wifi device shows wifi hint", True)

        self.select_kn8(page)
        texts = hint_texts()
        self.check("usb device has no wifi hint",
                   not any("WiFi" in text for text in texts),
                   str(texts))


if __name__ == "__main__":
    sys.exit(Smoke().run())
