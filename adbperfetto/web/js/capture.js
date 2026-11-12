// capture.js: device rail + capture panel (record / flight).

import { api } from "./api.js";
import { t } from "./i18n.js";
import { createTape, fmtSize, fmtTime } from "./tape.js";

const ACTIVE = new Set(["starting", "recording", "stopping", "pulling"]);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, text, onClick) {
  const b = el("button", className, text);
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

function headline(s) {
  switch (s.status) {
    case "starting": return t("status.starting");
    case "recording":
      if (s.stop_requested) return t("btn.stopRequested");
      if (!s.connected) return t("state.disconnected");
      if (s.snapshot_busy) return t("btn.savingSnapshot");
      return s.mode === "flight" ? t("status.flight")
                                 : t("status.recording");
    case "stopping": return t("status.stopping");
    case "pulling":
      return s.ended_by_duration ? t("msg.limit")
                                 : t("status.pulling");
    default: return "";
  }
}

function deviceStatus(dev) {
  const s = dev.session;
  if (s && ACTIVE.has(s.status)) {
    const key = s.status === "recording"
      ? (s.mode === "flight" ? "status.flight" : "status.recording")
      : `status.${s.status}`;
    return {
      key, cls: s.connected === false ? "amber" : "live",
      active: true,
    };
  }
  if (s) {
    return {
      key: s.status === "done" ? "status.done"
        : s.status === "failed" ? "status.failed" : "status.ended",
      cls: "rule", active: false,
    };
  }
  if (dev.state === "unauthorized") {
    return { key: "state.unauthorized", cls: "amber" };
  }
  if (dev.state === "offline") {
    return { key: "state.offline", cls: "amber" };
  }
  if (dev.state === "disconnected") {
    return { key: "state.disconnected", cls: "amber" };
  }
  return { key: "state.device", cls: "idle" };
}

function elapsedS(session) {
  if (session.status === "starting") return 0;
  const end = session.stopped_at || Date.now() / 1000;
  return Math.max(0, end - session.started_at);
}

// MobileLog defaults to on; unchecking persists false per device.
function wantMobilelog(dev, prefs) {
  if (!dev.info || !dev.info.has_mobilelog) return false;
  return prefs.mobilelog === undefined ? true : !!prefs.mobilelog;
}

// Mirrors outputs.safe_filename on the backend.
function safeFilename(value) {
  return String(value || "")
    .replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/^[ ._]+|[ ._]+$/g, "") || "unknown";
}

function emptyGuide(state) {
  const wrap = el("div", "empty");
  wrap.appendChild(el("div", "empty-title", t("empty.title")));
  const steps = el("ol", "empty-steps");
  [t("guide.step1"), t("guide.step2"), t("guide.step3")]
    .forEach((text, i) => {
      const li = el("li");
      li.append(el("span", "step-num", `${i + 1}.`),
                el("span", null, text));
      steps.appendChild(li);
    });
  wrap.appendChild(steps);
  const waiting = el("div", "empty-waiting");
  waiting.append(el("span", "pulse-dot"),
                 el("span", null, t("guide.waiting")));
  wrap.appendChild(waiting);
  if (state && state.adb_ok === false) {
    const block = el("div", "block failed-block adb-error");
    block.appendChild(el("div", "block-title", t("adb.error.title")));
    if (state.adb_error) {
      block.appendChild(el("pre", "adb-error-detail", state.adb_error));
    }
    block.appendChild(el("div", "hint dim", t("adb.error.fix")));
    wrap.appendChild(block);
  }
  return wrap;
}

export function createCapture(deps) {
  // deps: {rail, main, getPrefs(serial), setPrefs(serial, patch),
  //        getDraft(), openConfigs(presetId), showModal(opts),
  //        showConfirm(title,msg,label,fn), toast(msg), onSelect(serial)}
  const ui = {
    railSig: "",
    panelSig: "",
    tape: null,
    tapeHost: null,
    refs: {},
    orphanHidden: new Set(),
    showAllSaved: new Set(),
    selected: null,
    state: null,
    presets: [],
    outputs: [],
    defaultPreset: null,
    flightLifetimeH: 24,
  };

  // ---------------------------------------------------------------- rail

  function railSignature(devices) {
    return JSON.stringify(devices.map((d) => [
      d.serial, d.state,
      d.session ? d.session.status : null,
      d.session ? d.session.mode : null,
      d.session ? d.session.connected : null,
      d.orphan ? 1 : 0,
    ]));
  }

  function sortDevices(devices) {
    return [...devices].sort((a, b) => {
      const act = (d) =>
        d.session && ACTIVE.has(d.session.status) ? 0 : 1;
      if (act(a) !== act(b)) return act(a) - act(b);
      const name = (d) => (d.info && d.info.model) || d.serial;
      return name(a).localeCompare(name(b));
    });
  }

  function renderRail() {
    deps.rail.textContent = "";
    if (!ui.state.devices.length) {
      deps.rail.appendChild(el("div", "rail-empty", t("rail.empty")));
      return;
    }
    for (const dev of sortDevices(ui.state.devices)) {
      const status = deviceStatus(dev);
      const row = el("button", "rail-row");
      row.dataset.serial = dev.serial;
      if (ui.selected && dev.serial === ui.selected.serial) {
        row.classList.add("selected");
      }
      row.appendChild(el("span", "rail-bar " + status.cls));
      const body = el("span", "rail-body");
      body.appendChild(el("span", "rail-model",
                          (dev.info && dev.info.model) || dev.serial));
      body.appendChild(el("span", "rail-serial", dev.serial));
      const right = el("span", "rail-status");
      right.appendChild(el("span", "rail-word " + status.cls,
                           t(status.key)));
      right.appendChild(el("span", "rail-time"));
      row.append(body, right);
      row.addEventListener("click", () => deps.onSelect(dev.serial));
      deps.rail.appendChild(row);
    }
  }

  function updateRailTimes() {
    deps.rail.querySelectorAll(".rail-row").forEach((row) => {
      const dev = ui.state.devices.find(
        (d) => d.serial === row.dataset.serial);
      const timeEl = row.querySelector(".rail-time");
      if (!dev || !timeEl) return;
      const s = dev.session;
      timeEl.textContent = (s && ACTIVE.has(s.status))
        ? fmtTime(elapsedS(s)) : "";
    });
  }

  // ------------------------------------------------------------ helpers

  function metaRow(dev) {
    const meta = el("div", "meta");
    const info = dev.info || {};
    const pairs = [
      [t("meta.serial"), dev.serial],
      info.sdk ? [t("meta.android"),
                  `${info.release || ""} (SDK ${info.sdk})`] : null,
      info.perfetto_version
        ? [t("meta.perfetto"), info.perfetto_version] : null,
      info.has_mobilelog !== undefined
        ? [t("meta.mtklog"), info.has_mobilelog ? "✓" : "—"] : null,
      info.sdk ? [t("meta.flight"),
                  info.supports_clone ? t("meta.clone")
                                      : t("meta.oneShot")] : null,
    ];
    for (const pair of pairs) {
      if (!pair) continue;
      const item = el("span", "meta-item");
      item.append(el("span", "meta-label", pair[0]),
                  el("span", "meta-value", pair[1]));
      meta.appendChild(item);
    }
    return meta;
  }

  function getPrefs(serial) {
    return deps.getPrefs(serial);
  }

  function panelSignature(dev) {
    const s = dev.session || {};
    return JSON.stringify([
      dev.serial, dev.state,
      dev.info ? dev.info.model : "",
      s.status, s.mode, s.connected,
      (s.snapshots || []).length,
      (s.checklist || []).length,
      (s.disconnects || []).length,
      s.mobilelog ? 1 : 0,
      dev.orphan ? 1 : 0,
      ui.orphanHidden.has(dev.serial) ? 1 : 0,
      getPrefs(dev.serial),
      (ui.outputs || []).length,
      ui.outputs && ui.outputs[0] ? ui.outputs[0].path : "",
    ]);
  }

  function presetOptions(serial, presetId, draft) {
    const sel = document.createElement("select");
    sel.id = "cfg-preset";
    const builtin = document.createElement("optgroup");
    builtin.label = t("cfg.builtin");
    const mine = document.createElement("optgroup");
    mine.label = t("cfg.myPresets");
    let found = presetId === "__draft__";
    for (const p of ui.presets) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name;
      (p.builtin ? builtin : mine).appendChild(opt);
      if (p.id === presetId) found = true;
    }
    sel.appendChild(builtin);
    if (mine.children.length) sel.appendChild(mine);
    if (draft) {
      const opt = document.createElement("option");
      opt.value = "__draft__";
      opt.textContent = t("cfg.draft");
      sel.appendChild(opt);
    }
    sel.value = found ? presetId
      : (ui.defaultPreset || (ui.presets[0] && ui.presets[0].id));
    if (sel.value !== presetId && ui.presets.length) {
      deps.setPrefs(serial, { presetId: sel.value });
    }
    sel.addEventListener("change", () => {
      deps.setPrefs(serial, { presetId: sel.value });
      refreshConfigMeta(serial);
    });
    return sel;
  }

  function currentConfig(serial) {
    const prefs = getPrefs(serial);
    if (prefs.presetId === "__draft__") {
      const draft = deps.getDraft();
      if (draft) return { text: draft.text, preset_id: null };
    }
    return { preset_id: prefs.presetId, text: null };
  }

  async function refreshConfigMeta(serial) {
    const prefs = getPrefs(serial);
    let durationMs = null;
    let bufferKb = 0;
    if (prefs.presetId === "__draft__") {
      const draft = deps.getDraft();
      if (!draft) return;
      const dm = draft.text.match(/duration_ms:\s*(\d+)/);
      if (dm) durationMs = parseInt(dm[1], 10);
      bufferKb = [...draft.text.matchAll(/size_kb:\s*(\d+)/g)]
        .reduce((a, m) => a + parseInt(m[1], 10), 0);
    } else {
      const p = ui.presets.find((x) => x.id === prefs.presetId);
      if (!p) return;
      durationMs = p.duration_ms;
      bufferKb = p.buffer_kb || 0;
    }
    const size = bufferKb ? fmtSize(bufferKb * 1024) : "";
    if (ui.refs.buffer) {
      ui.refs.buffer.textContent =
        size ? t("record.buffer", { size }) : "";
    }
    if (ui.refs.memory) {
      ui.refs.memory.textContent =
        size ? t("flight.memory", { size }) : "";
    }
    if (ui.refs.duration && durationMs && !prefs.durationSet) {
      ui.refs.duration.value =
        String(Math.max(1, Math.round(durationMs / 60000)));
    }
  }

  function tapeSpec(dev, prefs) {
    const s = dev.session;
    const mode = (s && s.mode) || prefs.mode || "record";
    if (!s) {
      const duration = prefs.noLimit
        ? null : (prefs.durationMin || 30) * 60000;
      return {
        mode, status: "idle",
        startedAt: Date.now() / 1000, elapsedS: 0,
        receivedAt: performance.now(),
        durationMs: mode === "record" ? duration : null,
        idleSpanS: mode === "record" && duration
          ? duration / 1000 : 600,
        disconnects: [], snapshots: [],
        ariaLabel: t("status.idle"),
      };
    }
    const parts = [t(`status.${s.status}`) || s.status,
                   fmtTime(elapsedS(s))];
    if (s.duration_ms) {
      parts.push(`/ ${fmtTime(s.duration_ms / 1000)}`);
    }
    if ((s.disconnects || []).length) {
      parts.push(t("phone.disconnected"));
    }
    return {
      mode, status: s.status,
      startedAt: s.started_at, elapsedS: elapsedS(s),
      receivedAt: performance.now(),
      durationMs: s.duration_ms,
      disconnects: s.disconnects || [],
      snapshots: (s.snapshots || []).map((x) => ({ at: x.at })),
      ariaLabel: parts.join(", "),
    };
  }

  // --------------------------------------------------------- sections

  function deviceHeader(dev) {
    const head = el("div", "dev-head");
    head.appendChild(el("h1", "dev-name",
                        (dev.info && dev.info.model) || dev.serial));
    head.appendChild(metaRow(dev));
    if (dev.state === "unauthorized") {
      head.appendChild(el("div", "hint amber", t("hint.unauthorized")));
    } else if (dev.state === "offline") {
      head.appendChild(el("div", "hint amber", t("hint.offline")));
    } else if (dev.state === "disconnected" &&
               !(dev.session && ACTIVE.has(dev.session.status))) {
      head.appendChild(el("div", "hint amber",
                           t("hint.disconnected")));
    }
    if (dev.wifi) {
      head.appendChild(el("div", "hint amber", t("hint.wifi")));
    }
    return head;
  }

  function segmented(dev, prefs) {
    const s = dev.session;
    const running = s && ACTIVE.has(s.status);
    const seg = el("div", "segmented");
    const mode = running ? s.mode : (prefs.mode || "record");
    for (const [key, label] of [["record", "seg.record"],
                                ["flight", "seg.flight"]]) {
      const b = el("button",
                   "seg-btn" + (mode === key ? " active" : ""),
                   t(label));
      b.disabled = !!running;
      b.addEventListener("click", () => {
        deps.setPrefs(dev.serial, { mode: key });
      });
      seg.appendChild(b);
    }
    return seg;
  }

  function configRow(dev, prefs) {
    const row = el("div", "config-row");
    row.appendChild(el("span", "field-label", t("config.label")));
    const sel = presetOptions(dev.serial, prefs.presetId,
                              deps.getDraft());
    row.appendChild(sel);
    row.appendChild(button("btn quiet small", t("config.edit"), () => {
      deps.openConfigs(sel.value === "__draft__" ? null : sel.value);
    }));
    row.appendChild(button("btn quiet small", t("config.preview"),
                           () => openPreview(dev, prefs)));
    return row;
  }

  async function openPreview(dev, prefs) {
    const mode = (dev.session && ACTIVE.has(dev.session.status))
      ? dev.session.mode : (prefs.mode || "record");
    const cfg = currentConfig(dev.serial);
    try {
      let text = cfg.text;
      if (!text) {
        const preset = await api.preset(cfg.preset_id);
        text = preset.text;
      }
      const result = await api.preview({
        text, mode,
        duration_min: mode === "record" && !prefs.noLimit
          ? parseFloat(ui.refs.duration?.value || "0") || null : null,
        lifetime_h: mode === "flight"
          ? parseFloat(ui.refs.lifetime?.value || "0") || null : null,
        serial: dev.serial,
      });
      const modal = deps.showModal({
        title: t("modal.preview.title"),
        bodyHtml: '<pre class="modal-code"></pre>',
        wide: true,
        buttons: [
          [t("btn.copy"), async (close) => {
            await navigator.clipboard.writeText(result.text);
            deps.toast(t("toast.pathCopied"));
            close();
          }],
          [t("btn.confirm"), (close) => close(), "primary"],
        ],
      });
      modal.box.querySelector(".modal-code").textContent = result.text;
    } catch (e) {
      deps.toast(e.message);
    }
  }

  function inputsRow(dev, prefs) {
    const s = dev.session;
    const running = s && ACTIVE.has(s.status);
    const mode = running ? s.mode : (prefs.mode || "record");
    const row = el("div", "inputs-row");
    if (mode === "record") {
      const dur = document.createElement("input");
      dur.type = "number";
      dur.id = "inp-duration";
      dur.min = "1";
      dur.max = "1440";
      dur.value = String(prefs.durationMin || 30);
      dur.disabled = !!running || !!prefs.noLimit;
      dur.addEventListener("change", () => {
        deps.setPrefs(dev.serial, {
          durationMin: parseInt(dur.value, 10) || 30,
          durationSet: true,
        });
      });
      const durLabel = el("label", "field");
      durLabel.append(el("span", "field-label", t("record.duration")),
                      dur,
                      el("span", "field-suffix", t("record.minutes")));
      row.appendChild(durLabel);
      const noLimit = document.createElement("input");
      noLimit.type = "checkbox";
      noLimit.checked = !!prefs.noLimit;
      noLimit.disabled = !!running;
      noLimit.addEventListener("change", () => {
        deps.setPrefs(dev.serial, { noLimit: noLimit.checked });
      });
      const nlLabel = el("label", "field check");
      nlLabel.append(noLimit,
                     el("span", "field-label", t("record.noLimit")));
      row.appendChild(nlLabel);
      const buffer = el("span", "field-meta");
      ui.refs.buffer = buffer;
      row.appendChild(buffer);
    } else {
      const life = document.createElement("input");
      life.type = "number";
      life.id = "inp-lifetime";
      life.min = "1";
      life.max = "167";
      life.value = String(prefs.lifetimeH || ui.flightLifetimeH || 24);
      life.disabled = !!running;
      life.addEventListener("change", () => {
        deps.setPrefs(dev.serial, {
          lifetimeH: parseInt(life.value, 10) || 24,
        });
      });
      const lifeLabel = el("label", "field");
      lifeLabel.append(el("span", "field-label", t("flight.lifetime")),
                       life,
                       el("span", "field-suffix", t("flight.hours")));
      row.appendChild(lifeLabel);
      const mem = el("span", "field-meta");
      ui.refs.memory = mem;
      row.appendChild(mem);
    }
    return row;
  }

  function mobilelogRow(dev, prefs) {
    const s = dev.session;
    const running = s && ACTIVE.has(s.status);
    const wrap = el("div", "mobilelog-row");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = "inp-mobilelog";
    box.checked = wantMobilelog(dev, prefs);
    box.disabled = !!running;
    box.addEventListener("change", () => {
      deps.setPrefs(dev.serial, { mobilelog: box.checked });
    });
    const mlLabel = el("label", "field check");
    mlLabel.append(box, el("span", "field-label",
                           t("record.mobilelog")));
    wrap.appendChild(mlLabel);
    wrap.appendChild(el("div", "hint dim mobilelog-hint",
                        t("record.mobilelogHint")));
    return wrap;
  }

  function readouts(dev, prefs) {
    const s = dev.session;
    if (!s) return null;
    const mode = s.mode || "record";
    const defs = mode === "record"
      ? [["elapsed", t("readout.elapsed"), true],
         ["size", t("readout.size")]]
      : [["elapsed", t("readout.elapsed"), true],
         ["snaps", t("readout.snapshots")]];
    if (ACTIVE.has(s.status)) {
      if (mode === "record") {
        if (s.mobilelog) defs.push(["mlog", t("readout.mobilelog")]);
      } else {
        defs.push(["memory", t("readout.memory")]);
      }
      defs.push(["phone", t("readout.phone")]);
    }
    const row = el("div", "readouts");
    for (const [id, label, big] of defs) {
      const item = el("div", "readout" + (big ? " big" : ""));
      const value = el("div", "readout-value");
      value.id = "ro-" + id;
      const lab = el("div", "readout-label", label);
      item.append(value, lab);
      row.appendChild(item);
    }
    return row;
  }

  function snapshotList(s) {
    const list = el("div", "snapshots");
    const snaps = s.snapshots || [];
    snaps.slice().reverse().forEach((snap) => {
      const when = new Date(snap.at * 1000);
      const hhmmss = [when.getHours(), when.getMinutes(),
                      when.getSeconds()]
        .map((v) => String(v).padStart(2, "0")).join(":");
      const row = el("div", "snapshot-row");
      row.append(
        el("span", "snap-name",
           t("snapshot.row", { n: snap.index + 1 })),
        el("span", "snap-time", hhmmss),
        el("span", "snap-size", fmtSize(snap.size)),
        button("btn quiet small", t("btn.show"), () => {
          api.reveal(snap.local).catch((e) => deps.toast(e.message));
        }),
        button("btn quiet small", t("btn.copy"), async () => {
          await navigator.clipboard.writeText(snap.local);
          deps.toast(t("toast.pathCopied"));
        }));
      list.appendChild(row);
    });
    return list;
  }

  function outputsList(dev) {
    const wrap = el("div", "saved");
    const model = (dev.info && dev.info.model) || dev.serial;
    const prefix = safeFilename(model);
    const mine = (ui.outputs || []).filter(
      (out) => out.name && out.name.startsWith(prefix));
    if (!mine.length) return wrap;
    wrap.appendChild(el("div", "saved-title", t("saved.title")));
    const list = el("div", "saved-list");
    const expanded = ui.showAllSaved.has(dev.serial);
    const shown = expanded ? mine : mine.slice(0, 8);
    for (const out of shown) {
      const row = el("div", "saved-row");
      row.append(
        el("span", "saved-name", out.name),
        el("span", "saved-size", fmtSize(out.size)),
        button("btn quiet small", t("btn.show"), () => {
          api.reveal(out.path).catch((e) => deps.toast(e.message));
        }),
        button("btn quiet small", t("btn.copy"), async () => {
          await navigator.clipboard.writeText(out.path);
          deps.toast(t("toast.pathCopied"));
        }));
      list.appendChild(row);
    }
    wrap.appendChild(list);
    if (mine.length > 8) {
      const toggle = button("btn quiet small",
        expanded ? t("saved.showLess")
                 : t("saved.showAll", { n: mine.length }), () => {
        if (expanded) ui.showAllSaved.delete(dev.serial);
        else ui.showAllSaved.add(dev.serial);
        ui.panelSig = "";
      });
      toggle.id = "saved-toggle";
      wrap.appendChild(toggle);
    }
    return wrap;
  }

  // ------------------------------------------------- status sections

  function statusSection(dev, prefs) {
    const s = dev.session;
    const sec = el("div", "status-area");
    const orphanShown = dev.orphan && !s &&
      !ui.orphanHidden.has(dev.serial);

    if (orphanShown) {
      const block = el("div", "block amber-block");
      block.appendChild(el("div", "block-text", t("orphan.text")));
      const row = el("div", "btn-row");
      row.append(
        button("btn primary", t("btn.stopSave"), async () => {
          try {
            await api.adopt(dev.serial);
            await api.stop(dev.serial, false);
            deps.toast(t("toast.stopped"));
          } catch (e) { deps.toast(e.message); }
        }),
        button("btn quiet", t("btn.leaveRunning"), () => {
          ui.orphanHidden.add(dev.serial);
          ui.panelSig = "";
        }));
      block.appendChild(row);
      sec.appendChild(block);
    }

    const msg = el("div", "message");
    msg.id = "sess-msg";
    msg.setAttribute("aria-live", "polite");
    sec.appendChild(msg);
    const detail = el("div", "message-detail");
    detail.id = "sess-detail";
    sec.appendChild(detail);

    if (!s) {
      if (!orphanShown) {
        const mode = prefs.mode || "record";
        const label = mode === "record"
          ? (wantMobilelog(dev, prefs) ? t("btn.startRecordMl")
                                       : t("btn.startRecord"))
          : t("btn.startFlight");
        sec.appendChild(button("btn primary big", label,
                               () => startSession(dev, prefs, mode)));
      }
      return sec;
    }

    if (s.status === "starting") {
      const b = button("btn primary big", t("btn.starting"));
      b.disabled = true;
      sec.appendChild(b);
    } else if (s.status === "recording") {
      if (!s.connected) {
        sec.appendChild(el("div", "block amber-block",
                           t("banner.disconnected")));
      }
      if (s.mode === "record") {
        const b = button("btn primary big",
                         s.stop_requested ? t("btn.stopRequested")
                                          : t("btn.stopSave"),
                         () => stopSession(dev));
        if (s.stop_requested) b.disabled = true;
        sec.appendChild(b);
        if (!s.stop_requested && s.connected) {
          sec.appendChild(el("div", "hint dim", t("hint.unplug")));
        }
      } else {
        const row = el("div", "btn-row");
        const snap = button(
          "btn primary big",
          s.snapshot_busy ? t("btn.savingSnapshot")
                          : t("btn.saveSnapshot"),
          () => takeSnapshot(dev));
        snap.id = "btn-snapshot";
        if (s.snapshot_busy) snap.disabled = true;
        const stop = button("btn quiet", t("btn.stop"),
                            () => stopFlightModal(dev));
        row.append(snap, el("span", "key-hint", "S"), stop);
        sec.appendChild(row);
        if (s.one_shot && (s.snapshots || []).length === 0) {
          sec.appendChild(el("div", "hint amber",
                             t("flight.oneShot")));
        }
      }
      if (s.mobilelog && s.mobilelog.paused) {
        sec.appendChild(el("div", "hint dim",
                           t("ml.paused",
                             { names: s.mobilelog.paused })));
      }
    } else if (s.status === "stopping") {
      const b = button("btn primary big", t("btn.stopping"));
      b.disabled = true;
      sec.appendChild(b);
    } else if (s.status === "pulling") {
      const wrap = el("div", "pullbar");
      const fill = el("div", "pullbar-fill");
      fill.id = "pullbar-fill";
      wrap.appendChild(fill);
      sec.appendChild(wrap);
      sec.appendChild(el("div", "hint dim", t("pull.note")));
    } else if (s.status === "done") {
      sec.appendChild(doneBlock(dev, s));
    } else if (s.status === "failed") {
      sec.appendChild(failedBlock(dev, s));
    } else if (s.status === "ended" || s.status === "abandoned") {
      sec.appendChild(el("div", "block amber-block",
                         s.mode === "flight" ? t("flight.ended")
                                             : (s.message || "")));
      sec.appendChild(dismissRow(dev));
    }
    if ((s.snapshots || []).length) {
      sec.appendChild(snapshotList(s));
    }
    return sec;
  }

  function dismissRow(dev) {
    const row = el("div", "btn-row");
    row.appendChild(button("btn quiet", t("btn.dismiss"), async () => {
      try { await api.dismiss(dev.serial); }
      catch (e) { deps.toast(e.message); }
    }));
    return row;
  }

  function doneBlock(dev, s) {
    const block = el("div", "block done-block");
    const head = el("div", "done-head");
    head.appendChild(el("span", "done-check", "✓"));
    const path = (s.outputs || [])[0];
    const info = el("div", "done-info");
    info.appendChild(el("div", "done-name",
                        path ? path.split(/[\\/]/).pop()
                             : t("done.title")));
    head.appendChild(info);
    block.appendChild(head);
    for (const item of s.checklist || []) {
      const row = el("div",
                     "check-row " + (item.ok ? "ok" : "bad"));
      row.append(el("span", "check-mark", item.ok ? "✓" : "✕"),
                 el("span", "check-label", item.label),
                 el("span", "check-size", fmtSize(item.size)));
      block.appendChild(row);
    }
    if ((s.checklist || []).some((c) => !c.ok)) {
      block.appendChild(el("div", "hint alert", t("check.missing")));
    }
    const row = el("div", "btn-row");
    row.append(
      button("btn primary", t("btn.showInFolder"), () => {
        if (path) {
          api.reveal(path).catch((e) => deps.toast(e.message));
        }
      }),
      button("btn quiet", t("btn.copyPath"), async () => {
        if (path) {
          await navigator.clipboard.writeText(path);
          deps.toast(t("toast.pathCopied"));
        }
      }),
      button("btn quiet", t("btn.newRecording"), async () => {
        try { await api.dismiss(dev.serial); }
        catch (e) { deps.toast(e.message); }
      }));
    block.appendChild(row);
    return block;
  }

  function failedBlock(dev, s) {
    const block = el("div", "block failed-block");
    block.appendChild(el("div", "block-title", t("failed.title")));
    if (s.error) {
      block.appendChild(el("pre", "failed-detail", s.error));
    }
    if (s.remote_path) {
      block.appendChild(el("div", "hint dim",
                           t("failed.onDevice",
                             { path: s.remote_path })));
    }
    block.appendChild(dismissRow(dev));
    return block;
  }

  // ------------------------------------------------- actions

  async function startSession(dev, prefs, mode) {
    const cfg = currentConfig(dev.serial);
    try {
      if (mode === "record") {
        await api.record(dev.serial, {
          preset_id: cfg.preset_id, text: cfg.text,
          duration_min: prefs.noLimit
            ? null
            : parseFloat(ui.refs.duration?.value || "0") || null,
          mobilelog: wantMobilelog(dev, prefs),
        });
      } else {
        await api.flight(dev.serial, {
          preset_id: cfg.preset_id, text: cfg.text,
          lifetime_h: parseFloat(ui.refs.lifetime?.value || "0") || null,
        });
      }
      deps.toast(t("toast.started"));
    } catch (e) {
      deps.toast(e.message);
    }
  }

  async function stopSession(dev) {
    try { await api.stop(dev.serial, false); }
    catch (e) { deps.toast(e.message); }
  }

  async function takeSnapshot(dev) {
    try {
      await api.snapshot(dev.serial);
      deps.toast(t("toast.snapshotSaved"));
    } catch (e) { deps.toast(e.message); }
  }

  function stopFlightModal(dev) {
    const modal = deps.showModal({
      title: t("modal.stopFlight.title"),
      bodyHtml: '<label class="field check modal-check">' +
        '<input type="checkbox" id="stop-save-snap" checked>' +
        '<span class="field-label"></span></label>',
      buttons: [
        [t("btn.cancel"), (close) => close()],
        [t("btn.stop"), async (close) => {
          const save = modal.box.querySelector("#stop-save-snap");
          close();
          try {
            await api.stop(dev.serial, save ? save.checked : true);
            deps.toast(t("toast.stopped"));
          } catch (e) { deps.toast(e.message); }
        }, "primary"],
      ],
    });
    modal.box.querySelector(".modal-check .field-label").textContent =
      t("modal.stopFlight.saveFirst");
  }

  // ------------------------------------------------- dynamic update

  function updateDynamic(dev) {
    const s = dev.session;
    const set = (id, text, cls) => {
      const node = ui.refs[id];
      if (!node) return;
      if (text !== undefined) node.textContent = text;
      if (cls !== undefined) node.className = cls;
    };
    const msg = document.getElementById("sess-msg");
    const detail = document.getElementById("sess-detail");
    if (s && msg) {
      msg.textContent = headline(s);
    } else if (msg) {
      msg.textContent = "";
    }
    if (detail) {
      detail.textContent = s &&
        (s.status === "starting" || s.status === "pulling")
        ? (s.message || "") : "";
    }
    if (!s) {
      return;
    }
    set("ro-elapsed", fmtTime(elapsedS(s)));
    set("ro-size", s.size_bytes ? fmtSize(s.size_bytes) : "—");
    set("ro-snaps", String((s.snapshots || []).length));
    set("ro-memory",
        s.buffer_kb ? fmtSize(s.buffer_kb * 1024) : "—");
    set("ro-phone",
        s.connected ? t("phone.connected") : t("phone.disconnected"),
        "readout-value " + (s.connected ? "live" : "amber"));
    set("ro-mlog",
        s.mobilelog && s.mobilelog.running
          ? fmtSize(s.mobilelog.log_bytes) : "");
    const fill = document.getElementById("pullbar-fill");
    if (fill) {
      fill.style.width = `${Math.round((s.pull_progress || 0) * 100)}%`;
    }
    if (ui.tape) {
      ui.tape.update(tapeSpec(dev, getPrefs(dev.serial)));
    }
  }

  // ------------------------------------------------- main render

  function renderPanel() {
    const host = deps.main;
    const dev = ui.selected;
    ui.refs = {};
    host.textContent = "";
    if (!dev) {
      host.appendChild(emptyGuide(ui.state));
      return;
    }
    const prefs = getPrefs(dev.serial);
    const inner = el("div", "panel-inner");
    host.appendChild(inner);
    inner.appendChild(deviceHeader(dev));
    inner.appendChild(segmented(dev, prefs));
    inner.appendChild(configRow(dev, prefs));
    const s = dev.session;
    const running = s && ACTIVE.has(s.status);
    const mode = running ? s.mode : (prefs.mode || "record");
    if (mode === "flight" && !s) {
      inner.appendChild(el("div", "hint dim", t("flight.explainer")));
    }
    inner.appendChild(inputsRow(dev, prefs));
    if (mode === "record" && dev.info && dev.info.has_mobilelog) {
      inner.appendChild(mobilelogRow(dev, prefs));
    }
    if (mode === "flight" && !s && dev.info && dev.info.sdk &&
        !dev.info.supports_clone) {
      inner.appendChild(el("div", "hint amber", t("flight.oneShot")));
    }
    ui.refs.duration = host.querySelector("#inp-duration");
    ui.refs.lifetime = host.querySelector("#inp-lifetime");
    ui.tapeHost = el("div", "tape-host");
    inner.appendChild(ui.tapeHost);
    const ro = readouts(dev, prefs);
    if (ro) {
      inner.appendChild(ro);
      for (const id of ["elapsed", "size", "snaps", "memory", "phone",
                        "mlog"]) {
        ui.refs["ro-" + id] = host.querySelector("#ro-" + id);
      }
    }
    inner.appendChild(statusSection(dev, prefs));
    inner.appendChild(outputsList(dev));
    if (ui.tape) ui.tape.destroy();
    ui.tape = createTape(ui.tapeHost);
    ui.tape.update(tapeSpec(dev, prefs));
    refreshConfigMeta(dev.serial);
  }

  return {
    setData({ presets, defaultPreset, flightLifetimeH, outputs }) {
      if (presets !== undefined) ui.presets = presets;
      if (defaultPreset !== undefined) ui.defaultPreset = defaultPreset;
      if (flightLifetimeH !== undefined) {
        ui.flightLifetimeH = flightLifetimeH;
      }
      if (outputs !== undefined) ui.outputs = outputs;
    },
    update(state, selectedSerial) {
      ui.state = state;
      const dev = state.devices.find(
        (d) => d.serial === selectedSerial) || null;
      const selectionChanged =
        (dev && dev.serial) !== (ui.selected && ui.selected.serial);
      ui.selected = dev;
      const railSig = railSignature(state.devices);
      if (railSig !== ui.railSig || selectionChanged) {
        ui.railSig = railSig;
        renderRail();
      }
      updateRailTimes();
      if (!dev) {
        if (ui.panelSig !== "empty") {
          ui.panelSig = "empty";
          renderPanel();
        }
        return;
      }
      const sig = panelSignature(dev);
      if (sig !== ui.panelSig) {
        ui.panelSig = sig;
        renderPanel();
      }
      updateDynamic(dev);
    },
    invalidate() {
      ui.railSig = "";
      ui.panelSig = "";
    },
    pageTitle() {
      const dev = ui.selected;
      if (dev && dev.session && ACTIVE.has(dev.session.status)) {
        const s = dev.session;
        const key = s.mode === "flight" ? "title.flight"
                                        : "title.recording";
        return t(key, { time: fmtTime(elapsedS(s)) });
      }
      return t("title.default");
    },
    requestSnapshot() {
      const dev = ui.selected;
      const s = dev && dev.session;
      if (s && s.mode === "flight" && s.status === "recording" &&
          s.connected) {
        takeSnapshot(dev);
      }
    },
  };
}
