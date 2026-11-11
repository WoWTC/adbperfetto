// builder.js: visual config builder. Edits the JSON model returned by
// /api/config/parse; calls onChange(model) so the app can render text.

import { t } from "./i18n.js";

const SOURCE_KEYS = [
  "linux.process_stats", "linux.sys_stats",
  "android.surfaceflinger.frametimeline", "android.power",
  "android.log", "android.packages_list", "linux.system_info",
  "android.statsd", "track_event",
];

const FILL_POLICIES = ["RING_BUFFER", "DISCARD"];

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function field(labelText, input) {
  const label = el("label", "field");
  const span = el("span", "field-label", labelText);
  label.append(span, input);
  return label;
}

export function createBuilder(container, { onChange, getLists } = {}) {
  container.classList.add("builder");
  const tabBar = el("div", "builder-tabs");
  const body = el("div", "builder-body");
  container.append(tabBar, body);

  const ui = {
    tab: "basics",
    model: null,
    disabled: false,
    filters: { atrace: "", ftrace: "" },
    collapsed: new Set(),
    expanded: new Set(),
  };
  const TABS = [
    ["basics", "builder.basics"],
    ["atrace", "builder.atrace"],
    ["ftrace", "builder.ftrace"],
    ["sources", "builder.sources"],
  ];

  function emit() {
    if (onChange && ui.model) onChange(ui.model);
  }

  function renderTabs() {
    tabBar.textContent = "";
    for (const [key, label] of TABS) {
      const button = el("button",
                        "builder-tab" + (ui.tab === key ? " active" : ""),
                        t(label));
      button.addEventListener("click", () => {
        ui.tab = key;
        render();
      });
      tabBar.appendChild(button);
    }
  }

  function renderBasics() {
    const model = ui.model;
    const wrap = el("div", "builder-pane");

    const noLimit = document.createElement("input");
    noLimit.type = "checkbox";
    noLimit.checked = model.duration_ms == null;
    const dur = document.createElement("input");
    dur.type = "number";
    dur.min = "1";
    dur.max = "1440";
    dur.value = model.duration_ms != null
      ? String(Math.round(model.duration_ms / 60000)) : "30";
    dur.disabled = noLimit.checked;
    noLimit.addEventListener("change", () => {
      model.duration_ms = noLimit.checked
        ? null : (parseInt(dur.value, 10) || 30) * 60000;
      dur.disabled = noLimit.checked;
      emit();
    });
    dur.addEventListener("change", () => {
      model.duration_ms = (parseInt(dur.value, 10) || 30) * 60000;
      emit();
    });
    wrap.appendChild(field(t("basics.duration") + " (" +
                           t("record.minutes") + ")", dur));
    const noLimitLabel = field(t("basics.noLimit"), noLimit);
    wrap.appendChild(noLimitLabel);

    wrap.appendChild(el("div", "builder-sub", t("basics.buffers")));
    const table = el("div", "buffer-table");
    model.buffers.forEach((buf, i) => {
      const row = el("div", "buffer-row");
      const size = document.createElement("input");
      size.type = "number";
      size.min = "1";
      size.value = String(Math.round((buf.size_kb || 0) / 1024) || 32);
      size.addEventListener("change", () => {
        buf.size_kb = (parseInt(size.value, 10) || 32) * 1024;
        emit();
      });
      const policy = document.createElement("select");
      for (const name of FILL_POLICIES) {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        policy.appendChild(opt);
      }
      policy.value = buf.fill_policy || "RING_BUFFER";
      policy.addEventListener("change", () => {
        buf.fill_policy = policy.value;
        emit();
      });
      const remove = el("button", "btn tiny quiet", "✕");
      remove.addEventListener("click", () => {
        model.buffers.splice(i, 1);
        emit();
        render();
      });
      row.append(field(t("basics.sizeMb"), size),
                 field(t("basics.fillPolicy"), policy), remove);
      table.appendChild(row);
    });
    wrap.appendChild(table);
    const addBuf = el("button", "btn quiet small", t("basics.addBuffer"));
    addBuf.addEventListener("click", () => {
      model.buffers.push({ size_kb: 32768, fill_policy: "RING_BUFFER" });
      emit();
      render();
    });
    wrap.appendChild(addBuf);

    const ft = model.ftrace || (model.ftrace = {
      enabled: false, events: [], atrace_categories: [],
      atrace_apps: [], buffer_size_kb: null,
    });
    const ftToggle = document.createElement("input");
    ftToggle.type = "checkbox";
    ftToggle.checked = !!ft.enabled;
    ftToggle.addEventListener("change", () => {
      ft.enabled = ftToggle.checked;
      emit();
      render();
    });
    wrap.appendChild(el("div", "builder-sub"));
    wrap.appendChild(field(t("basics.ftrace"), ftToggle));
    const ftSize = document.createElement("input");
    ftSize.type = "number";
    ftSize.min = "0";
    ftSize.placeholder = "16384";
    ftSize.value = ft.buffer_size_kb != null ? String(ft.buffer_size_kb) : "";
    ftSize.disabled = !ft.enabled;
    ftSize.addEventListener("change", () => {
      ft.buffer_size_kb = ftSize.value
        ? parseInt(ftSize.value, 10) : null;
      emit();
    });
    wrap.appendChild(field(t("basics.ftraceBuffer"), ftSize));
    return wrap;
  }

  function checklist(items, selected, onToggle, renderLabel) {
    const list = el("div", "checklist");
    for (const item of items) {
      const name = typeof item === "string" ? item : item.name;
      const row = el("label", "checklist-row");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = selected.includes(name);
      box.addEventListener("change", () => onToggle(name, box.checked));
      const text = el("span", "checklist-text");
      text.appendChild(el("span", "checklist-name", name));
      const desc = typeof item === "object" ? item.description : "";
      if (desc) text.appendChild(el("span", "checklist-desc", desc));
      row.append(box, text);
      list.appendChild(row);
    }
    return list;
  }

  function renderAtrace() {
    const model = ui.model;
    const ft = model.ftrace;
    const lists = getLists ? getLists() : {};
    const wrap = el("div", "builder-pane");
    if (!ft.enabled) {
      wrap.appendChild(el("div", "dim", t("builder.ftraceOff")));
    }
    if (lists.atraceDeviceName) {
      wrap.appendChild(el("div", "builder-note",
                          t("atrace.fromDevice",
                            { device: lists.atraceDeviceName })));
    }
    const filter = document.createElement("input");
    filter.type = "search";
    filter.placeholder = t("atrace.filter");
    filter.value = ui.filters.atrace;
    filter.addEventListener("input", () => {
      ui.filters.atrace = filter.value;
      renderBodyOnly();
    });
    wrap.appendChild(filter);

    const cats = (lists.atrace || []).map((c) =>
      typeof c === "string" ? { name: c, description: "" } : c);
    const names = new Set(cats.map((c) => c.name));
    for (const extra of ft.atrace_categories || []) {
      if (!names.has(extra)) cats.push({ name: extra, description: "" });
    }
    const q = ui.filters.atrace.toLowerCase();
    const shown = cats.filter((c) =>
      c.name.toLowerCase().includes(q) ||
      (c.description || "").toLowerCase().includes(q));
    wrap.appendChild(checklist(
      shown, ft.atrace_categories || [],
      (name, checked) => {
        ft.atrace_categories = ft.atrace_categories || [];
        if (checked && !ft.atrace_categories.includes(name)) {
          ft.atrace_categories.push(name);
        } else if (!checked) {
          ft.atrace_categories =
            ft.atrace_categories.filter((x) => x !== name);
        }
        emit();
      }));
    wrap.querySelectorAll(".checklist-row").forEach((row) => {
      row.classList.toggle("off", !ft.enabled);
    });

    wrap.appendChild(el("div", "builder-sub", t("atrace.apps")));
    const appsRow = el("div", "apps-row");
    const apps = ft.atrace_apps || [];
    const allApps = apps.includes("*");
    const allBox = document.createElement("input");
    allBox.type = "checkbox";
    allBox.checked = allApps;
    allBox.addEventListener("change", () => {
      ft.atrace_apps = allBox.checked ? ["*"] : [];
      emit();
      render();
    });
    appsRow.appendChild(field(t("atrace.allApps"), allBox));
    if (!allApps) {
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "com.example.app";
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && input.value.trim()) {
          event.preventDefault();
          ft.atrace_apps = ft.atrace_apps || [];
          ft.atrace_apps.push(input.value.trim());
          emit();
          render();
        }
      });
      appsRow.appendChild(input);
    }
    wrap.appendChild(appsRow);
    if (!allApps && apps.length) {
      const tags = el("div", "tags");
      apps.forEach((app, i) => {
        const tag = el("span", "tag", app);
        const x = el("button", "tag-x", "✕");
        x.addEventListener("click", () => {
          apps.splice(i, 1);
          emit();
          render();
        });
        tag.appendChild(x);
        tags.appendChild(tag);
      });
      wrap.appendChild(tags);
    }
    return wrap;
  }

  function renderFtrace() {
    const model = ui.model;
    const ft = model.ftrace;
    const lists = getLists ? getLists() : {};
    const wrap = el("div", "builder-pane");
    if (!ft.enabled) {
      wrap.appendChild(el("div", "dim", t("builder.ftraceOff")));
    }
    if (lists.ftraceFromDevice === false) {
      wrap.appendChild(el("div", "builder-note",
                          t("ftrace.deviceNote")));
    }
    const filter = document.createElement("input");
    filter.type = "search";
    filter.placeholder = t("atrace.filter");
    filter.value = ui.filters.ftrace;
    filter.addEventListener("input", () => {
      ui.filters.ftrace = filter.value;
      renderBodyOnly();
    });
    wrap.appendChild(filter);

    const events = new Set(lists.ftrace || []);
    for (const extra of ft.events || []) {
      if (!events.has(extra)) events.add(extra);
    }
    const OTHER = "__other__";
    const groups = new Map();
    for (const name of events) {
      const group = name.includes("/") ? name.split("/")[0] : OTHER;
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(name);
    }
    const q = ui.filters.ftrace.toLowerCase();
    const selected = ft.events || [];
    const ordered = [...groups.entries()].sort((a, b) => {
      if (a[0] === OTHER) return 1;
      if (b[0] === OTHER) return -1;
      return a[0].localeCompare(b[0]);
    });
    for (const [group, names] of ordered) {
      const shown = q ? names.filter((n) => n.toLowerCase().includes(q))
                      : names;
      if (!shown.length) continue;
      const checked = names.filter((n) => selected.includes(n)).length;
      const label = group === OTHER ? t("ftrace.other") : group;
      const expanded = ui.expanded.has(group) ||
        (checked > 0 && !ui.collapsed.has(group));
      const header = el("button", "builder-group");
      header.append(
        el("span", null, `${expanded ? "▾" : "▸"} ${label}`),
        el("span", "group-count", `${checked}/${names.length}`));
      header.addEventListener("click", () => {
        if (expanded) {
          ui.expanded.delete(group);
          ui.collapsed.add(group);
        } else {
          ui.collapsed.delete(group);
          ui.expanded.add(group);
        }
        renderBodyOnly();
      });
      wrap.appendChild(header);
      if (expanded) {
        wrap.appendChild(checklist(shown, selected,
          (name, checked) => {
            ft.events = ft.events || [];
            if (checked && !ft.events.includes(name)) {
              ft.events.push(name);
            } else if (!checked) {
              ft.events = ft.events.filter((x) => x !== name);
            }
            emit();
          }));
      }
    }

    const addRow = el("div", "apps-row");
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "f2fs/f2fs_iget";
    const addBtn = el("button", "btn quiet small", t("ftrace.addEvent"));
    const add = () => {
      const value = input.value.trim();
      if (value && !(ft.events || []).includes(value)) {
        ft.events = ft.events || [];
        ft.events.push(value);
        emit();
        render();
      }
    };
    addBtn.addEventListener("click", add);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); add(); }
    });
    addRow.append(input, addBtn);
    wrap.appendChild(addRow);
    return wrap;
  }

  function renderSources() {
    const model = ui.model;
    const wrap = el("div", "builder-pane");
    const keys = [...SOURCE_KEYS];
    for (const key of Object.keys(model.sources || {})) {
      if (!keys.includes(key)) keys.push(key);
    }
    const items = keys.map((key) => {
      const human = t(`src.${key}`);
      return {
        name: key,
        description: human.startsWith("src.") ? "" : human,
      };
    });
    wrap.appendChild(checklist(
      items, keys.filter((k) => (model.sources || {})[k]),
      (name, checked) => {
        model.sources = model.sources || {};
        model.sources[name] = checked;
        emit();
      }));
    // Show human names, key as description.
    wrap.querySelectorAll(".checklist-name").forEach((nameEl, i) => {
      const item = items[i];
      const human = item.description;
      if (human && !human.startsWith("src.")) {
        nameEl.textContent = human;
        nameEl.nextSibling.textContent = item.name;
      }
    });
    return wrap;
  }

  function renderBodyOnly() {
    // keep focus + caret in a filter box across re-renders
    const active = document.activeElement;
    const refocus = active && body.contains(active) &&
      active.type === "search"
      ? { pos: active.selectionStart } : null;
    body.textContent = "";
    if (ui.disabled || !ui.model) {
      body.appendChild(el("div", "builder-disabled",
                          t("builder.disabledNote")));
      return;
    }
    let pane;
    if (ui.tab === "basics") pane = renderBasics();
    else if (ui.tab === "atrace") pane = renderAtrace();
    else if (ui.tab === "ftrace") pane = renderFtrace();
    else pane = renderSources();
    body.appendChild(pane);
    if (refocus) {
      const input = body.querySelector('input[type="search"]');
      if (input) {
        input.focus();
        input.setSelectionRange(refocus.pos, refocus.pos);
      }
    }
  }

  function render() {
    renderTabs();
    renderBodyOnly();
  }

  render();
  return {
    setModel(model, disabled) {
      ui.model = model;
      ui.disabled = !!disabled;
      render();
    },
    render,
  };
}
