"""Generate the 14 review screenshots against a demo server.

Run:  .venv\\Scripts\\python tools\\ui_screenshots.py
Output: %TEMP%\\adbperfetto_shots\\NN_name.png
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8972
BASE = f"http://127.0.0.1:{PORT}"
KN8 = "KN8DEMO0001"
PIXEL = "PIXEL7DEMO"
REALME = "R58MDEMO"
OUT = Path(tempfile.gettempdir()) / "adbperfetto_shots"

from playwright.sync_api import sync_playwright


def http(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def device(serial):
    for d in http("/api/state")["devices"]:
        if d["serial"] == serial:
            return d
    return None


def status(serial):
    d = device(serial)
    return d["session"]["status"] if d and d["session"] else None


def wait(pred, what="condition", timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    raise AssertionError("timeout: " + what)


class Shots:
    def __init__(self):
        self.proc = None
        self.errors = []
        OUT.mkdir(parents=True, exist_ok=True)

    def start_server(self):
        env = dict(os.environ, ADBPERFETTO_DEMO="1")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "adbperfetto",
             "--no-browser", "--port", str(PORT)],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait(lambda: self._ping(), "server up", 30)

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

    def shot(self, page, name):
        path = OUT / f"{name}.png"
        page.screenshot(path=str(path))
        print("  " + path.name)

    def select(self, page, serial):
        page.locator(f'.rail-row[data-serial="{serial}"]').click()
        page.wait_for_timeout(400)

    def dismiss_if_needed(self, page, serial):
        # the panel re-renders on the next state event; poll for the
        # dismiss button instead of checking once.
        for _ in range(40):
            row = page.locator(".btn-row .btn.quiet")
            for text in ("关闭", "录制"):
                b = row.filter(has_text=text)
                if b.count():
                    b.first.click()
                    page.wait_for_timeout(600)
                    return
            if status(serial) is None:
                return
            page.wait_for_timeout(300)

    def run(self):
        self.start_server()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge",
                                            headless=True)
                page = browser.new_page(
                    viewport={"width": 1440, "height": 900})
                page.on("console", lambda m:
                        self.errors.append(m.text)
                        if m.type == "error" else None)
                page.on("pageerror",
                        lambda e: self.errors.append(str(e)))
                page.goto(BASE, wait_until="networkidle")
                page.wait_for_timeout(1800)
                self.shoot_all(page)
                browser.close()
        finally:
            self.stop_server()
        for f in sorted(OUT.glob("*.png")):
            print("saved:", f)
        if self.errors:
            print("CONSOLE ERRORS:", self.errors)
        return 0 if not self.errors else 1

    def shoot_all(self, page):
        # 01 empty (no phones)
        http("/api/demo/remove_all", {})
        wait(lambda: not http("/api/state")["devices"], "empty state")
        page.wait_for_timeout(700)
        self.shot(page, "01_empty")

        http("/api/demo/reset", {})
        wait(lambda: http("/api/state")["devices"], "devices back")
        page.wait_for_timeout(800)

        # 02 record idle (KN8)
        self.select(page, KN8)
        page.locator(".seg-btn", has_text="录制").click()
        page.locator(".seg-btn.active",
                     has_text="录制").wait_for(timeout=10000)
        page.wait_for_timeout(400)
        self.shot(page, "02_record_idle")

        # 03 recording, disconnected (closed + open gap on tape)
        # short duration so the disconnect gaps are visible on the tape
        dur = page.locator("#inp-duration")
        dur.fill("1")
        dur.press("Tab")
        page.wait_for_timeout(300)
        page.locator(".btn.primary.big").click()
        wait(lambda: status(KN8) == "recording", "recording")
        page.wait_for_timeout(1200)
        http("/api/demo/unplug", {"serial": KN8})
        page.wait_for_timeout(1800)
        http("/api/demo/replug", {"serial": KN8})
        page.wait_for_timeout(1800)
        http("/api/demo/unplug", {"serial": KN8})
        page.wait_for_timeout(1500)
        self.shot(page, "03_recording_disconnected")

        # 04 pulling
        http("/api/demo/replug", {"serial": KN8})
        page.wait_for_timeout(1200)
        page.locator(".btn.primary.big").click()
        try:
            wait(lambda: status(KN8) == "pulling", "pulling",
                 timeout=10)
            page.wait_for_timeout(150)
            self.shot(page, "04_pulling")
        except AssertionError:
            wait(lambda: status(KN8) == "done", "done")
            self.shot(page, "04_pulling")
            print("  (pulling too fast to catch; shot is done state)")
        wait(lambda: status(KN8) == "done", "done", 30)
        self.dismiss_if_needed(page, KN8)
        dur = page.locator("#inp-duration")
        dur.wait_for(timeout=10000)
        dur.fill("30")
        dur.press("Tab")
        page.wait_for_timeout(300)

        # 05 done with mobilelog checklist
        page.locator("#inp-mobilelog").wait_for(timeout=10000)
        page.locator("#inp-mobilelog").check()
        page.wait_for_timeout(300)
        assert page.locator("#inp-mobilelog").is_checked()
        page.locator(".btn.primary.big").click()
        wait(lambda: status(KN8) == "recording", "ml recording")
        page.wait_for_timeout(1500)
        page.locator(".btn.primary.big").click()
        wait(lambda: status(KN8) == "done", "ml done", 30)
        page.wait_for_timeout(600)
        self.shot(page, "05_done_mobilelog")
        self.dismiss_if_needed(page, KN8)

        # 06 flight running with snapshots
        page.locator(".seg-btn", has_text="飞行").click()
        page.locator(".seg-btn.active",
                     has_text="飞行").wait_for(timeout=10000)
        page.wait_for_timeout(300)
        page.locator(".btn.primary.big").click()
        wait(lambda: status(KN8) == "recording", "flight rec")
        for _ in range(3):
            page.wait_for_timeout(1100)
            page.locator("#btn-snapshot").click()
        wait(lambda: len(device(KN8)["session"]
                         .get("snapshots", [])) >= 3, "3 snapshots")
        page.wait_for_timeout(800)
        self.shot(page, "06_flight_running")

        # 07 flight stop modal
        page.locator(".btn-row .btn.quiet", has_text="停止").click()
        page.wait_for_selector("#stop-save-snap", timeout=5000)
        page.wait_for_timeout(300)
        self.shot(page, "07_flight_stop_modal")
        page.locator(".modal-foot .btn.primary").click()
        wait(lambda: status(KN8) in ("done", "ended"), "flight end",
             30)
        self.dismiss_if_needed(page, KN8)

        # 08 orphan on the Pixel (re-scan by replugging it)
        http("/api/demo/unplug", {"serial": PIXEL})
        page.wait_for_timeout(800)
        http("/api/demo/orphan", {"serial": PIXEL, "mode": "record"})
        http("/api/demo/replug", {"serial": PIXEL})
        wait(lambda: device(PIXEL) and device(PIXEL)["orphan"],
             "orphan detected")
        self.select(page, PIXEL)
        page.wait_for_timeout(700)
        self.shot(page, "08_orphan")
        page.locator(".btn-row .btn.quiet",
                     has_text="保持运行").click()
        page.wait_for_timeout(400)

        # 09 unauthorized
        self.select(page, REALME)
        page.wait_for_timeout(500)
        self.shot(page, "09_unauthorized")

        # 10 configs builder ftrace
        page.locator('.topbar-btn[data-view="configs"]').click()
        page.wait_for_timeout(1000)
        page.locator(".builder-tab", has_text="Ftrace").click()
        page.wait_for_timeout(500)
        self.shot(page, "10_configs_builder_ftrace")

        # 11 editor error
        area = page.locator(".editor-input")
        area.click()
        area.press("Control+A")
        area.type("broken {{{ config")
        page.wait_for_timeout(900)
        self.shot(page, "11_editor_error")
        page.locator('.preset-item[data-id="syshealth_google"]') \
            .click()
        page.wait_for_timeout(600)
        if page.locator("#modal-overlay.open").count():
            page.locator(".modal-foot .btn.primary").click()
        page.wait_for_timeout(800)

        # 12 record idle, English
        page.locator("#lang-select").select_option("en")
        page.wait_for_timeout(800)
        page.locator('.topbar-btn[data-view="capture"]').click()
        self.select(page, KN8)
        page.locator(".seg-btn").first.click()
        page.locator('.seg-btn.active:text-is("Record")') \
            .wait_for(timeout=10000)
        page.wait_for_timeout(400)
        self.shot(page, "12_record_idle_en")

        # 13 narrow 860 while recording (zh UI)
        page.locator("#lang-select").select_option("zh")
        page.wait_for_timeout(700)
        page.locator(".btn.primary.big").click()
        wait(lambda: status(KN8) == "recording", "narrow rec")
        wait(lambda: (device(KN8)["session"] or {}).get("mode")
             == "record", "record mode")
        page.wait_for_timeout(1500)
        page.set_viewport_size({"width": 860, "height": 900})
        page.wait_for_timeout(600)
        self.shot(page, "13_narrow_860")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(800)
        page.locator(".btn.primary.big").click()
        wait(lambda: status(KN8) in ("done", "failed", "ended"),
             "narrow done", 40)
        if status(KN8) == "failed":
            print("  session failed:",
                  device(KN8)["session"].get("error"))
        self.dismiss_if_needed(page, KN8)

        # 14 pixel flight one-shot notice
        self.select(page, PIXEL)
        page.locator(".seg-btn", has_text="飞行").click()
        page.locator(".seg-btn.active",
                     has_text="飞行").wait_for(timeout=10000)
        page.wait_for_timeout(400)
        self.shot(page, "14_pixel_flight_one_shot_notice")


if __name__ == "__main__":
    sys.exit(Shots().run())
