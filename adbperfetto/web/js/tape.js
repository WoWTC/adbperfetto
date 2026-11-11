// tape.js: the timeline tape (SVG). X axis = session time.
// Record: full width = duration. Flight: auto scale = max(10min, elapsed*1.25).

import { t } from "./i18n.js";

const NS = "http://www.w3.org/2000/svg";
const TICK_STEPS = [10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200,
                    14400, 28800]; // seconds
const REDUCED = window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function niceStep(spanS) {
  for (const step of TICK_STEPS) {
    if (spanS / step <= 8) return step;
  }
  return TICK_STEPS[TICK_STEPS.length - 1];
}

function niceSpan(spanS) {
  const step = niceStep(spanS);
  return Math.ceil(spanS / step) * step;
}

export function fmtTime(totalS, alwaysHours) {
  totalS = Math.max(0, Math.floor(totalS));
  const h = Math.floor(totalS / 3600);
  const m = Math.floor((totalS % 3600) / 60);
  const s = totalS % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return (h || alwaysHours) ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

export function fmtSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

function el(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs || {})) {
    node.setAttribute(key, value);
  }
  return node;
}

/**
 * createTape(container): returns { update(spec), destroy() }.
 * spec = {
 *   mode: "record"|"flight"|"idle",
 *   status: session status or "idle",
 *   startedAt: unix seconds (or now for idle),
 *   receivedAt: performance.now() ms when spec was made,
 *   elapsedS: seconds at receivedAt,
 *   durationMs: record duration (null = keep recording / idle preview),
 *   idleSpanS: preview span for idle mode,
 *   disconnects: [[startS, endS|null]] unix seconds,
 *   snapshots: [{at}] unix seconds,
 *   ariaLabel: string,
 * }
 */
export function createTape(container) {
  container.classList.add("tape");
  const svg = el("svg", {
    viewBox: "0 0 1000 72", preserveAspectRatio: "none",
    role: "img", "aria-label": "",
  });
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "72");
  container.appendChild(svg);

  const defs = el("defs", {});
  const hatch = el("pattern", {
    id: "tape-hatch", width: "8", height: "8",
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(45)",
  });
  hatch.appendChild(el("rect", {
    width: "8", height: "8", fill: "transparent",
  }));
  hatch.appendChild(el("line", {
    x1: "0", y1: "0", x2: "0", y2: "8",
    stroke: "var(--amber)", "stroke-width": "2",
  }));
  defs.appendChild(hatch);
  svg.appendChild(defs);

  const gTicks = el("g", { class: "tape-ticks" });
  const gBar = el("g", { class: "tape-bar" });
  const gDisc = el("g", { class: "tape-disc" });
  const gFlags = el("g", { class: "tape-flags" });
  const gPlay = el("g", { class: "tape-playhead" });
  svg.append(gTicks, gBar, gDisc, gFlags, gPlay);

  let spec = null;
  let raf = null;
  let armedAt = null;   // performance.now() when the arm sweep started
  let lastKey = "";

  function spanS(nowS) {
    if (spec.mode === "record" && spec.durationMs) {
      return Math.max(60, spec.durationMs / 1000);
    }
    if (spec.mode === "record") {
      return niceSpan(Math.max(600, nowS * 1.25));
    }
    return niceSpan(Math.max(600, spec.idleSpanS || 600,
                             nowS * 1.25));
  }

  function elapsedNow() {
    const base = spec.elapsedS || 0;
    if (!spec || spec.status === "idle") return base;
    const extra = (performance.now() - spec.receivedAt) / 1000;
    return base + extra;
  }

  function x(sec, span) {
    return Math.min(1000, Math.max(0, (sec / span) * 1000));
  }

  function draw() {
    if (!spec) return;
    const elapsed = elapsedNow();
    const span = spanS(elapsed);
    const frozen = spec.status === "stopping" ||
      spec.status === "pulling" || spec.status === "done";
    const idle = spec.status === "idle";

    // ticks
    gTicks.textContent = "";
    const step = niceStep(span);
    for (let sec = 0; sec <= span + 0.5; sec += step) {
      const px = x(sec, span);
      gTicks.appendChild(el("line", {
        x1: px, y1: 50, x2: px, y2: 56,
        stroke: "var(--rule)", "stroke-width": "1",
      }));
      const label = el("text", {
        x: px, y: 68, "text-anchor": sec === 0 ? "start" : "middle",
        class: "tape-tick-label",
      });
      if (sec > 0 && px > 980) label.setAttribute("text-anchor", "end");
      label.textContent = fmtTime(sec, span >= 3600);
      gTicks.appendChild(label);
    }
    gTicks.appendChild(el("line", {
      x1: 0, y1: 50, x2: 1000, y2: 50,
      stroke: "var(--rule)", "stroke-width": "1",
    }));

    // recorded bar
    gBar.textContent = "";
    const endS = frozen || spec.status === "done" ? elapsed : elapsed;
    const width = x(Math.min(endS, span), span);
    const barColor = frozen || spec.status === "done"
      ? "var(--scale)" : "var(--live)";
    const barOpacity = idle ? 0 : (frozen ? 0.6 : 1);
    if (idle) {
      gBar.appendChild(el("rect", {
        x: 0, y: 30, width: x(span, span) * 0.15 || 24, height: 14,
        fill: "var(--rule)", opacity: 0.4,
      }));
    } else {
      gBar.appendChild(el("rect", {
        x: 0, y: 30, width, height: 14,
        fill: barColor, opacity: barOpacity,
      }));
    }

    // disconnect hatching
    gDisc.textContent = "";
    for (const [a, b] of spec.disconnects || []) {
      const startS = a - spec.startedAt;
      const endS = (b == null ? spec.startedAt + elapsed : b)
        - spec.startedAt;
      const x1 = x(Math.max(0, startS), span);
      const x2 = x(Math.min(endS, span), span);
      if (x2 - x1 < 1) continue;
      gDisc.appendChild(el("rect", {
        x: x1, y: 30, width: x2 - x1, height: 14,
        fill: "url(#tape-hatch)",
      }));
      gDisc.appendChild(el("line", {
        x1: x1, y1: 30, x2: x2, y2: 30,
        stroke: "var(--amber)", "stroke-width": "1.5",
      }));
    }

    // snapshot flags
    gFlags.textContent = "";
    (spec.snapshots || []).forEach((snap, i) => {
      const px = x(snap.at - spec.startedAt, span);
      const flag = el("g", {});
      flag.appendChild(el("line", {
        x1: px, y1: 22, x2: px, y2: 44,
        stroke: "var(--recorder)", "stroke-width": "2",
      }));
      flag.appendChild(el("polygon", {
        points: `${px},22 ${px + 12},26 ${px},30`,
        fill: "var(--recorder)",
      }));
      const num = el("text", {
        x: px + 3, y: 19, class: "tape-flag-num",
      });
      num.textContent = String(i + 1);
      flag.appendChild(num);
      gFlags.appendChild(flag);
    });

    // playhead
    gPlay.textContent = "";
    if (!idle) {
      let px = x(Math.min(elapsed, span), span);
      if (armedAt !== null && !REDUCED) {
        const k = Math.min(1, (performance.now() - armedAt) / 300);
        px *= k;
        if (k >= 1) armedAt = null;
      } else {
        armedAt = null;
      }
      gPlay.appendChild(el("line", {
        x1: px, y1: 8, x2: px, y2: 50,
        stroke: "var(--scale)", "stroke-width": "2",
      }));
      const label = el("text", {
        x: Math.min(px + 6, 940), y: 18,
        class: "tape-playhead-label",
      });
      label.textContent = fmtTime(elapsed);
      gPlay.appendChild(label);
    }
  }

  function loop() {
    draw();
    raf = requestAnimationFrame(loop);
  }

  return {
    update(next) {
      const key = JSON.stringify([
        next.mode, next.status, next.startedAt, next.durationMs,
        next.snapshots ? next.snapshots.length : 0,
        next.disconnects ? next.disconnects.length : 0,
        next.disconnects && next.disconnects.length
          ? next.disconnects[next.disconnects.length - 1][1] : 0,
      ]);
      const isArm = spec && next.status === "recording" &&
        spec.status === "idle" && !REDUCED;
      spec = next;
      if (isArm) armedAt = performance.now();
      if (key !== lastKey || true) {
        lastKey = key;
        if (container.getAttribute("role") !== "img") {
          container.setAttribute("role", "img");
        }
        if (next.ariaLabel) {
          svg.setAttribute("aria-label", next.ariaLabel);
        }
      }
      if (raf === null) raf = requestAnimationFrame(loop);
    },
    destroy() {
      if (raf !== null) cancelAnimationFrame(raf);
      raf = null;
    },
  };
}
