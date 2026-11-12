// app.js: shell — header, views, SSE, modals, shortcuts.

import { api, events } from "./api.js";
import { t, setLang, getLang, resolveLang } from "./i18n.js";
import { fmtSize } from "./tape.js";
import { createEditor } from "./editor.js";
import { createBuilder } from "./builder.js";
import { createCapture } from "./capture.js";

const $ = (sel) => document.querySelector(sel);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const app = {
  state: null,
  settings: {},
  presets: [],
  builtin: { atrace: [], ftrace: [] },
  deviceLists: {},          // serial -> {atrace, ftrace, fromDevice}
  outputs: [],
  view: "capture",
  selected: null,
  prefsCache: {},
  prefsLoaded: false,
  prefsSave: Promise.resolve(),
  draft: null,              // {text, name}
  editor: null,
  builder: null,
  capture: null,
  editorModel: null,
  editorValid: true,
  editorDirty: false,
  loadedPresetId: null,
  parseTimer: null,
  lastSeenError: "",
};

// ------------------------------------------------------------------ prefs

const PREFS_KEY_PREFIX = "adbperfetto.prefs.";

function getPrefs(serial) {
  if (!app.prefsCache[serial]) app.prefsCache[serial] = {};
  return app.prefsCache[serial];
}

function setPrefs(serial, patch) {
  const p = getPrefs(serial);
  Object.assign(p, patch);
  app.prefsSave = app.prefsSave
    .then(() => api.putPrefs(serial, patch))
    .catch((e) => toast(e.message));
}

async function migratePrefs() {
  for (const key of Object.keys(localStorage)) {
    if (!key.startsWith(PREFS_KEY_PREFIX)) continue;
    const serial = key.slice(PREFS_KEY_PREFIX.length);
    try {
      if (app.prefsCache[serial] === undefined) {
        const parsed = JSON.parse(localStorage.getItem(key));
        app.prefsCache[serial] = await api.putPrefs(serial, parsed);
      }
    } catch (e) { /* skip this key */ }
    localStorage.removeItem(key);
  }
}

// ------------------------------------------------------------------ toast

function toast(msg) {
  const host = $("#toast");
  host.textContent = msg;
  host.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => host.classList.remove("show"), 4000);
}

// ------------------------------------------------------------------ modal

function showModal({ title, body, bodyHtml, buttons, wide }) {
  const overlay = $("#modal-overlay");
  overlay.textContent = "";
  const box = el("div", "modal" + (wide ? " wide" : ""));
  const head = el("div", "modal-head");
  head.appendChild(el("div", "modal-title", title));
  const x = el("button", "modal-x", "✕");
  head.appendChild(x);
  box.appendChild(head);
  const content = el("div", "modal-body");
  if (bodyHtml) content.innerHTML = bodyHtml;
  else if (typeof body === "string") content.textContent = body;
  else if (body) content.appendChild(body);
  box.appendChild(content);
  const foot = el("div", "modal-foot");
  for (const [label, fn, cls] of buttons || []) {
    const b = el("button", "btn " + (cls || "quiet"), label);
    b.addEventListener("click", () => fn(close));
    foot.appendChild(b);
  }
  box.appendChild(foot);
  overlay.appendChild(box);
  overlay.classList.add("open");
  function close() {
    overlay.classList.remove("open");
    overlay.textContent = "";
    document.removeEventListener("keydown", escHandler, true);
  }
  function escHandler(e) {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  }
  x.addEventListener("click", close);
  document.addEventListener("keydown", escHandler, true);
  overlay.onmousedown = (e) => {
    if (e.target === overlay) close();
  };
  x.focus();
  return { close, box };
}

function confirmModal(title, message, confirmLabel, onConfirm) {
  const { box } = showModal({
    title,
    bodyHtml: '<div class="modal-text"></div>',
    buttons: [
      [t("btn.cancel"), (c) => c()],
      [confirmLabel, async (c) => { c(); await onConfirm(); }, "primary"],
    ],
  });
  box.querySelector(".modal-text").textContent = message;
}

function anyModalOpen() {
  return $("#modal-overlay").classList.contains("open");
}

// ------------------------------------------------------------------ theme

const THEME_KEY = "adbperfetto.theme";
const themeMedia = window.matchMedia &&
  window.matchMedia("(prefers-color-scheme: light)");
let themeMode = "auto";   // auto | light | dark

const ICON_SUN = '<svg viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
  '<circle cx="12" cy="12" r="4"/>' +
  '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4' +
  'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const ICON_MOON = '<svg viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'stroke-linejoin="round">' +
  '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>';

function effectiveTheme() {
  if (themeMode === "light" || themeMode === "dark") return themeMode;
  return themeMedia && themeMedia.matches ? "light" : "dark";
}

function applyTheme() {
  const eff = effectiveTheme();
  document.documentElement.setAttribute("data-theme", eff);
  const btn = $("#btn-theme");
  if (!btn) return;
  btn.innerHTML = eff === "dark" ? ICON_SUN : ICON_MOON;
  const label = t(eff === "dark" ? "theme.toLight" : "theme.toDark");
  btn.setAttribute("aria-label", label);
  btn.title = label;
}

function initTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark" || stored === "auto") {
      themeMode = stored;
    }
  } catch (e) { /* keep auto */ }
  applyTheme();
  if (themeMedia && themeMedia.addEventListener) {
    themeMedia.addEventListener("change", () => {
      if (themeMode === "auto") applyTheme();
    });
  }
}

function reconcileTheme(serverTheme) {
  // An explicit server setting always wins; a stored explicit value
  // beats the server default "auto" so the no-flash pick survives.
  if (serverTheme && serverTheme !== "auto" || themeMode === "auto") {
    themeMode = serverTheme || "auto";
  }
  try { localStorage.setItem(THEME_KEY, themeMode); } catch (e) {}
  applyTheme();
}

async function toggleTheme() {
  themeMode = effectiveTheme() === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, themeMode); } catch (e) {}
  applyTheme();
  try { app.settings = await api.putSettings({ theme: themeMode }); }
  catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ i18n

function applyStaticStrings() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.documentElement.lang = getLang() === "zh" ? "zh-CN" : "en";
  $("#out-path-label").textContent = t("outdir.label");
  applyTheme();
  markDirty(app.editorDirty);
  document.title = app.capture ? app.capture.pageTitle()
                               : t("title.default");
}

async function switchLang(next) {
  setLang(next);
  try { await api.putSettings({ language: next }); }
  catch (e) { /* language still switched locally */ }
  renderAll();
}

function renderAll() {
  applyStaticStrings();
  renderPresetList();
  renderHeaderInfo();
  if (app.capture) {
    app.capture.invalidate();
    renderCapture();
  }
  if (app.builder) app.builder.render();
}

// ------------------------------------------------------------------ views

function setView(view) {
  app.view = view;
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${view}`);
  });
  document.querySelectorAll(".topbar-btn[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
  });
}

// ------------------------------------------------------------------ SSE

function connectEvents() {
  let delay = 400;
  const connect = () => {
    events(
      (state) => {
        delay = 400;
        setConn(true);
        onState(state);
      },
      () => {
        setConn(false);
        setTimeout(connect, delay);
        delay = Math.min(delay * 1.6, 5000);
      });
  };
  connect();
}

function setConn(ok) {
  $("#conn-dot").classList.toggle("dead", !ok);
}

function compactPath(dir) {
  if (!dir || dir.length <= 36) return dir || "";
  const sep = dir.includes("\\") ? "\\" : "/";
  const parts = dir.split(/[\\/]+/).filter(Boolean);
  if (parts.length <= 3) return dir;
  return `${parts[0]}${sep}…${sep}${parts.slice(-2).join(sep)}`;
}

function onState(state) {
  app.state = state;
  const anyActive = state.devices.some(
    (d) => d.session && ["starting", "recording"].includes(d.session.status));
  $("#conn-dot").classList.toggle("recording", anyActive);
  const outSig = JSON.stringify(state.devices.map((d) => [
    d.serial,
    d.session ? d.session.status : null,
    d.session && d.session.snapshots ? d.session.snapshots.length : 0,
  ]));
  if (outSig !== app.lastOutSig) {
    app.lastOutSig = outSig;
    refreshOutputs().then(() => renderCapture());
  }
  renderHeaderInfo();
  for (const dev of state.devices) {
    if (dev.state === "device" && !app.deviceLists[dev.serial]) {
      app.deviceLists[dev.serial] = { loading: true };
      loadDeviceLists(dev.serial);
    }
    const s = dev.session;
    if (s && s.status === "failed" && s.error &&
        s.error !== app.lastSeenError) {
      app.lastSeenError = s.error;
      toast(s.error);
    }
  }
  if (!app.selected ||
      !state.devices.some((d) => d.serial === app.selected)) {
    const first = [...state.devices].sort((a, b) =>
      ((a.info && a.info.model) || a.serial)
        .localeCompare((b.info && b.info.model) || b.serial))[0];
    app.selected = first ? first.serial : null;
  }
  renderCapture();
}

async function loadDeviceLists(serial) {
  try {
    const [atrace, ftrace] = await Promise.all([
      api.atrace(serial), api.ftrace(serial),
    ]);
    app.deviceLists[serial] = {
      atrace: atrace.categories || [],
      ftrace: ftrace.events,
      fromDevice: true,
    };
  } catch (e) {
    app.deviceLists[serial] = {
      atrace: [], ftrace: null, fromDevice: false,
    };
  }
  if (app.builder && app.view === "configs") app.builder.render();
}

function getLists() {
  const lists = app.deviceLists[app.selected] || {};
  const dev = (app.state && app.state.devices.find(
    (d) => d.serial === app.selected)) || null;
  const loading = lists.loading;
  return {
    atrace: loading ? [] : (lists.atrace || app.builtin.atrace),
    ftrace: loading ? []
      : (lists.ftrace && lists.ftrace.length
         ? lists.ftrace : app.builtin.ftrace),
    atraceDeviceName: (!loading && lists.fromDevice &&
                       lists.atrace && lists.atrace.length &&
                       dev && dev.info)
      ? (dev.info.model || dev.serial) : null,
    ftraceFromDevice: !!(lists.ftrace && lists.ftrace.length),
  };
}

// ------------------------------------------------------------------ capture

function renderCapture() {
  if (!app.capture || !app.state || !app.prefsLoaded) return;
  app.capture.setData({
    presets: app.presets,
    defaultPreset: app.settings.default_preset,
    flightLifetimeH: app.settings.flight_lifetime_h,
    outputs: app.outputs,
  });
  app.capture.update(app.state, app.selected);
  document.title = app.capture.pageTitle();
}

async function refreshOutputs() {
  try {
    const data = await api.outputs();
    app.outputs = data.outputs || [];
  } catch (e) { /* keep stale list */ }
}

// ------------------------------------------------------------------ configs

function initConfigs() {
  app.editor = createEditor($("#pbtx-editor"), {
    onInput: (text) => {
      if (app.loadedPresetId) markDirty(true);
      clearTimeout(app.parseTimer);
      app.parseTimer = setTimeout(() => parseText(text), 300);
    },
    onSave: () => saveConfig(),
  });
  app.builder = createBuilder($("#builder"), {
    getLists,
    onChange: async (model) => {
      try {
        const res = await api.render(app.editor.getText(), model);
        app.editorModel = model;
        app.editor.setText(res.text, true);
        app.editor.setError(0);
        app.editorValid = true;
        if (app.loadedPresetId) markDirty(true);
        parseText(res.text);
      } catch (e) { toast(e.message); }
    },
  });
  $("#cfg-name").addEventListener("input", () => markDirty(true));
}

function markDirty(dirty) {
  app.editorDirty = dirty;
  const mark = $("#edited-mark");
  mark.textContent = dirty ? `● ${t("cfg.edited")}` : "";
  mark.classList.toggle("hidden", !dirty);
}

function setParseStatus({ ok, error, durationMs, bufferKb }) {
  const host = $("#status-pills");
  host.textContent = "";
  $("#btn-apply-default").disabled = !ok;
  const add = (text, cls) => {
    const pill = el("span", "pill" + (cls ? " " + cls : ""), text);
    host.appendChild(pill);
  };
  if (ok) {
    app.editor.setError(0);
    $("#parse-note").classList.add("hidden");
    add(t("editor.valid"), "ok");
    if (bufferKb) {
      add(t("record.buffer", { size: fmtSize(bufferKb * 1024) }));
    }
    add(durationMs ? t("editor.durationMin",
                       { min: Math.round(durationMs / 60000) })
                   : t("record.noLimit"));
  } else if (error) {
    add(t("editor.invalid"), "bad");
    app.editor.setError(error.line);
    const note = $("#parse-note");
    note.textContent = t("editor.lineError",
      { line: error.line, col: error.col, msg: error.message });
    note.classList.remove("hidden");
  }
}

async function parseText(text) {
  try {
    const res = await api.parse(text);
    if (!res.ok) {
      app.editorValid = false;
      setParseStatus({ error: res.error });
      app.builder.setModel(app.editorModel, true);
      return;
    }
    app.editorModel = res.model;
    app.editorValid = true;
    setParseStatus({ ok: true, durationMs: res.duration_ms,
                     bufferKb: res.buffer_kb });
    app.builder.setModel(res.model, false);
  } catch (e) {
    toast(e.message);
  }
}

async function loadPreset(id, skipConfirm) {
  if (!skipConfirm && app.editorDirty) {
    confirmModal(t("tab.configs"), t("cfg.unsaved"),
                 t("btn.confirm"), () => loadPreset(id, true));
    return;
  }
  try {
    const p = await api.preset(id);
    app.editor.setText(p.text);
    app.editorModel = null;
    app.loadedPresetId = id;
    $("#cfg-name").value = p.name;
    markDirty(false);
    markPresetSelection();
    parseText(p.text);
  } catch (e) { toast(e.message); }
}

function loadDraftText(text, name) {
  app.editor.setText(text);
  app.editorModel = null;
  app.loadedPresetId = null;
  app.draft = { text, name };
  $("#cfg-name").value = name || t("cfg.draft");
  markDirty(true);
  markPresetSelection();
  parseText(text);
}

function markPresetSelection() {
  document.querySelectorAll(".preset-item").forEach((n) => {
    n.classList.toggle("selected", n.dataset.id === app.loadedPresetId);
  });
}

function renderPresetList() {
  const host = $("#preset-list");
  host.textContent = "";
  const groups = [
    [t("cfg.builtin"), app.presets.filter((p) => p.builtin)],
    [t("cfg.myPresets"), app.presets.filter((p) => !p.builtin)],
  ];
  for (const [label, items] of groups) {
    if (!items.length) continue;
    host.appendChild(el("div", "preset-group", label));
    for (const p of items) {
      const row = el("div", "preset-row");
      const item = el("button", "preset-item", p.name);
      item.dataset.id = p.id;
      if (p.id === app.loadedPresetId) item.classList.add("selected");
      item.addEventListener("click", () => loadPreset(p.id));
      row.appendChild(item);
      const star = el("button",
        "preset-star" +
          (p.id === app.settings.default_preset ? " is-default" : ""),
        p.id === app.settings.default_preset ? "★" : "☆");
      star.title = t("cfg.setDefault");
      star.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          app.settings = await api.putSettings({ default_preset: p.id });
          renderPresetList();
          toast(t("toast.defaultSet"));
        } catch (err) { toast(err.message); }
      });
      row.appendChild(star);
      if (!p.builtin) {
        const del = el("button", "preset-del", "✕");
        del.addEventListener("click", (e) => {
          e.stopPropagation();
          confirmModal(t("cfg.delete"),
                       t("cfg.deletePreset", { name: p.name }),
                       t("cfg.delete"), async () => {
            try {
              await api.deletePreset(p.id);
              app.presets = (await api.presets()).presets || [];
              renderPresetList();
            } catch (err) { toast(err.message); }
          });
        });
        row.appendChild(del);
      }
      host.appendChild(row);
    }
  }
}

async function savePresetAs(name) {
  const res = await api.savePreset(name, app.editor.getText());
  app.presets = (await api.presets()).presets || [];
  app.loadedPresetId = res.id;
  $("#cfg-name").value = name;
  app.draft = null;
  markDirty(false);
  renderPresetList();
  toast(t("toast.presetSaved"));
}

function saveConfig() {
  const loaded = app.presets.find((p) => p.id === app.loadedPresetId);
  if (loaded && !loaded.builtin) {
    savePresetAs(loaded.name).catch((e) => toast(e.message));
    return;
  }
  saveAsPreset();
}

function saveAsPreset() {
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = t("cfg.namePrompt");
  input.value = $("#cfg-name").value || "";
  const modal = showModal({
    title: t("cfg.saveAs"),
    body: input,
    buttons: [
      [t("btn.cancel"), (c) => c()],
      [t("btn.confirm"), async (c) => {
        const name = input.value.trim();
        if (!name) return;
        try {
          await savePresetAs(name);
          c();
        } catch (e) { toast(e.message); }
      }, "primary"],
    ],
  });
  input.focus();
}

async function importConfigFile(file) {
  if (!/\.(pbtx|conf|txt|cfg)$/i.test(file.name)) return;
  const text = await file.text();
  loadDraftText(text, file.name.replace(/\.[^.]+$/, ""));
  toast(t("toast.imported"));
}

function applyAsDefault() {
  if (!app.editorValid) {
    toast(t("builder.disabledNote"));
    return;
  }
  app.draft = { text: app.editor.getText(), name: t("cfg.draft") };
  if (app.selected) {
    setPrefs(app.selected, { presetId: "__draft__" });
    if (app.capture) app.capture.invalidate();
  }
  setView("capture");
  renderCapture();
}

// ------------------------------------------------------------------ header

function renderHeaderInfo() {
  const ver = app.state && app.state.version;
  const vnode = $("#app-version");
  vnode.textContent = ver ? `v${ver}` : "";
  vnode.title = (app.state && app.state.build) || "";
  const dir = (app.state && app.state.output_dir) ||
    app.settings.output_dir || "";
  const node = $("#out-path");
  node.textContent = compactPath(dir);
  node.title = dir;
  const lang = getLang();
  $("#lang-zh").classList.toggle("active", lang === "zh");
  $("#lang-en").classList.toggle("active", lang === "en");
  const dev = app.state && app.state.devices.find(
    (d) => d.serial === app.selected);
  const model = dev && dev.info && dev.info.model;
  $("#btn-apply-default").textContent = model
    ? t("cfg.useFor", { device: model })
    : t("btn.applyDefault");
}

function changeOutputDir() {
  const input = document.createElement("input");
  input.type = "text";
  input.value = app.settings.output_dir || "";
  input.placeholder = t("outdir.changePrompt");
  const modal = showModal({
    title: t("topbar.change"),
    body: input,
    buttons: [
      [t("btn.cancel"), (c) => c()],
      [t("btn.confirm"), async (c) => {
        try {
          app.settings = await api.putSettings(
            { output_dir: input.value.trim() });
          renderHeaderInfo();
          c();
        } catch (e) { toast(e.message); }
      }, "primary"],
    ],
  });
  input.focus();
}

function quitApp() {
  const hasSession = app.state && app.state.devices.some(
    (d) => d.session &&
      ["starting", "recording", "stopping", "pulling"]
        .includes(d.session.status));
  const doQuit = async () => {
    try { await api.quit(); } catch (e) { /* server already gone */ }
    $("#quit-overlay").classList.remove("hidden");
    $("#quit-overlay .quit-text").textContent = t("downbar");
    setTimeout(() => window.close(), 1500);
  };
  if (hasSession) {
    confirmModal(t("quit.title"), t("quit.body"),
                 t("btn.confirmQuit"), doQuit);
  } else {
    doQuit();
  }
}

// ------------------------------------------------------------------ events

function bindStatic() {
  document.querySelectorAll(".topbar-btn[data-view]").forEach((b) => {
    b.addEventListener("click", () => setView(b.dataset.view));
  });
  document.querySelectorAll(".pane-btn[data-pane]").forEach((b) => {
    b.addEventListener("click", () => {
      const mode = b.dataset.pane;
      $("#workbench-body").dataset.pane = mode;
      document.querySelectorAll(".pane-btn[data-pane]").forEach((x) => {
        x.classList.toggle("active", x === b);
      });
    });
  });
  $("#btn-quit").addEventListener("click", quitApp);
  $("#lang-zh").addEventListener("click", () => switchLang("zh"));
  $("#lang-en").addEventListener("click", () => switchLang("en"));
  $("#btn-theme").addEventListener("click", toggleTheme);
  $("#btn-open-dir").addEventListener("click", async () => {
    try { await api.openDir(); }
    catch (e) { toast(e.message); }
  });
  $("#btn-change-dir").addEventListener("click", changeOutputDir);
  $("#btn-save-preset").addEventListener("click", saveAsPreset);
  $("#btn-apply-default").addEventListener("click", applyAsDefault);
  $("#btn-import").addEventListener("click",
                                   () => $("#import-file").click());
  $("#import-file").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) importConfigFile(file);
    e.target.value = "";
  });
  const cfgView = $("#view-configs");
  let dragDepth = 0;
  cfgView.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragDepth += 1;
    $("#configs-wrap").classList.add("dragging");
  });
  cfgView.addEventListener("dragover", (e) => e.preventDefault());
  cfgView.addEventListener("dragleave", (e) => {
    e.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $("#configs-wrap").classList.remove("dragging");
  });
  cfgView.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    $("#configs-wrap").classList.remove("dragging");
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) importConfigFile(file);
  });

  document.addEventListener("keydown", (e) => {
    const tag = document.activeElement && document.activeElement.tagName;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(tag);
    if (e.key === "Escape" && anyModalOpen()) return; // handled by modal
    if (typing || anyModalOpen()) return;
    if (e.key.toLowerCase() === "s" && !e.ctrlKey && !e.metaKey &&
        app.view === "capture") {
      app.capture.requestSnapshot();
    } else if (/^[1-9]$/.test(e.key) && app.view === "capture" &&
               app.state) {
      const dev = app.state.devices[parseInt(e.key, 10) - 1];
      if (dev) {
        app.selected = dev.serial;
        renderCapture();
      }
    } else if (e.key === "?") {
      const modal = showModal({
        title: t("help.title"),
        bodyHtml: '<pre class="modal-code"></pre>',
        buttons: [[t("btn.confirm"), (c) => c(), "primary"]],
      });
      modal.box.querySelector(".modal-code").textContent = t("help.body");
    }
  });
}

// ------------------------------------------------------------------ boot

async function boot() {
  setLang(resolveLang("auto"));
  initTheme();
  bindStatic();
  initConfigs();
  app.capture = createCapture({
    rail: $("#device-rail"),
    main: $("#capture-main"),
    getPrefs, setPrefs,
    getDraft: () => app.draft,
    openConfigs: (presetId) => {
      setView("configs");
      if (presetId) loadPreset(presetId);
    },
    showModal,
    showConfirm: confirmModal,
    toast,
    onSelect: (serial) => {
      app.selected = serial;
      renderCapture();
    },
  });
  connectEvents();
  try {
    const [settings, presets, outputs, builtin, prefs] =
      await Promise.all([
        api.settings(), api.presets(), api.outputs(), api.builtinLists(),
        api.prefs(),
      ]);
    app.settings = settings;
    reconcileTheme(settings.theme);
    if (settings.language && settings.language !== "auto") {
      setLang(resolveLang(settings.language));
    }
    app.presets = presets.presets || [];
    app.outputs = outputs.outputs || [];
    app.builtin = {
      atrace: builtin.atrace_categories || [],
      ftrace: builtin.ftrace_events || [],
    };
    app.prefsCache = prefs || {};
    await migratePrefs();
    app.prefsLoaded = true;
    renderCapture();
    applyStaticStrings();
    renderPresetList();
    renderHeaderInfo();
    const def = app.settings.default_preset ||
      (app.presets[0] && app.presets[0].id);
    if (def) loadPreset(def, true);
    refreshOutputs();
    setInterval(refreshOutputs, 15000);
    setInterval(() => {
      if (app.capture && app.state) {
        document.title = app.capture.pageTitle();
        app.capture.update(app.state, app.selected);
      }
    }, 1000);
  } catch (e) {
    toast(e.message);
  }
}

window.addEventListener("DOMContentLoaded", boot);
window.__app = app;  // for tools/ui_smoke.py
