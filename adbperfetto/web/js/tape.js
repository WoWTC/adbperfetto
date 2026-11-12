// tape.js: precision calibrated timeline scale (SVG).
// Record: full width = target duration (zooms to session length once done).
// Flight: auto scale = max(10min, elapsed * 1.25).

const NS = "http://www.w3.org/2000/svg";
const TICK_STEPS = [2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
                    3600, 7200, 14400, 28800];
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

export function createTape(container) {
  container.classList.add("tape");
  const svg = el("svg", {
    viewBox: "0 0 1000 62",
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": "",
  });
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "62");
  container.appendChild(svg);

  const defs = el("defs", {});
  const hatch = el("pattern", {
    id: "tape-hatch", width: "7", height: "7",
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(45)",
  });
  hatch.appendChild(el("rect", {
    width: "7", height: "7", fill: "var(--amber-tint)",
  }));
  hatch.appendChild(el("line", {
    x1: "0", y1: "0", x2: "0", y2: "7",
    stroke: "var(--amber)", "stroke-width": "2",
  }));
  defs.appendChild(hatch);
  svg.appendChild(defs);

  const gTrack = el("g", { class: "tape-track" });
  const gTicks = el("g", { class: "tape-ticks" });
  const gBar = el("g", { class: "tape-bar" });
  const gDisc = el("g", { class: "tape-disc" });
  const gFlags = el("g", { class: "tape-flags" });
  const gPlay = el("g", { class: "tape-playhead" });
  svg.append(gTrack, gTicks, gBar, gDisc, gFlags, gPlay);

  let spec = null;
  let raf = null;
  let armedAt = null;
  let lastKey = "";

  const FROZEN = new Set(["starting", "stopping", "pulling", "done",
                          "ended", "failed", "abandoned"]);
  const ENDED = new Set(["done", "ended", "abandoned"]);

  function spanS(nowS) {
    if (ENDED.has(spec.status) && nowS > 0) {
      return niceSpan(Math.max(10, nowS * 1.15));
    }
    if (spec.mode === "record" && spec.durationMs) {
      return Math.max(60, spec.durationMs / 1000);
    }
    if (spec.mode === "record") {
      return niceSpan(Math.max(600, nowS * 1.25));
    }
    return niceSpan(Math.max(600, spec.idleSpanS || 600, nowS * 1.25));
  }

  function elapsedNow() {
    const base = spec.elapsedS || 0;
    if (!spec || spec.status === "idle" || FROZEN.has(spec.status)) {
      return base;
    }
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
      spec.status === "pulling" || ENDED.has(spec.status);
    const idle = spec.status === "idle";

    // Machined recessed track groove
    gTrack.textContent = "";
    gTrack.appendChild(el("rect", {
      x: 0, y: 20, width: 1000, height: 12, rx: 2,
      fill: "var(--chassis)",
      stroke: "var(--etch)",
      "stroke-width": "1",
    }));

    // Vernier scale: major + minor subdivisions
    gTicks.textContent = "";
    gTicks.setAttribute("opacity", idle ? 0.72 : 1);
    const step = niceStep(span);
    const subStep = step / 5;

    for (let sub = 0; sub <= span + 0.05; sub += subStep) {
      const px = x(sub, span);
      const isMajor = Math.abs(Math.round(sub / step) * step - sub) < 0.05;
      gTicks.appendChild(el("line", {
        x1: px, y1: 37, x2: px, y2: isMajor ? 45 : 41,
        stroke: isMajor ? "var(--etch-strong)" : "var(--etch)",
        "stroke-width": "1",
      }));
    }

    for (let sec = 0; sec <= span + 0.5; sec += step) {
      const px = x(sec, span);
      const label = el("text", {
        x: px, y: 57,
        "text-anchor": sec === 0 ? "start" : "middle",
        class: "tape-tick-label",
      });
      if (sec > 0 && px > 975) label.setAttribute("text-anchor", "end");
      label.textContent = fmtTime(sec, span >= 3600);
      gTicks.appendChild(label);
    }

    gTicks.appendChild(el("line", {
      x1: 0, y1: 37, x2: 1000, y2: 37,
      stroke: "var(--etch)", "stroke-width": "1",
    }));

    // Active / completed capture bar inside groove
    gBar.textContent = "";
    const width = x(Math.min(elapsed, span), span);
    const barColor = frozen ? "var(--live)" : "var(--live)";
    const barOpacity = frozen ? 0.55 : 0.92;
    if (!idle && elapsed > 0) {
      gBar.appendChild(el("rect", {
        x: 1, y: 21, width: Math.max(2, width - 1), height: 10, rx: 1.5,
        fill: barColor, opacity: barOpacity,
      }));
    }

    // USB disconnect hazard intervals
    gDisc.textContent = "";
    for (const [a, b] of spec.disconnects || []) {
      const startS = a - spec.startedAt;
      const endS = (b == null ? spec.startedAt + elapsed : b)
        - spec.startedAt;
      const x1 = x(Math.max(0, startS), span);
      const x2 = x(Math.min(endS, span), span);
      if (x2 - x1 < 1) continue;
      gDisc.appendChild(el("rect", {
        x: x1, y: 20, width: x2 - x1, height: 12,
        fill: "url(#tape-hatch)",
      }));
      gDisc.appendChild(el("line", {
        x1, y1: 20, x2, y2: 20,
        stroke: "var(--amber)", "stroke-width": "1.5",
      }));
      gDisc.appendChild(el("line", {
        x1, y1: 32, x2, y2: 32,
        stroke: "var(--amber)", "stroke-width": "1.5",
      }));
    }

    // Flight snapshot pins (auto-offset labels when clustered)
    gFlags.textContent = "";
    let prevBadgeRight = -100;
    let tier = 0;
    (spec.snapshots || []).forEach((snap, i) => {
      const px = x(snap.at - spec.startedAt, span);
      const flag = el("g", {});
      flag.appendChild(el("line", {
        x1: px, y1: 16, x2: px, y2: 35,
        stroke: "var(--signal)", "stroke-width": "1.5",
      }));
      const textStr = String(i + 1);
      const bw = textStr.length > 1 ? 18 : 13;
      let bx = Math.min(1000 - bw, Math.max(0, px - bw / 2));
      if (bx < prevBadgeRight + 3) {
        tier = (tier + 1) % 2;
        bx = Math.min(1000 - bw, prevBadgeRight + 2);
      } else {
        tier = 0;
      }
      prevBadgeRight = bx + bw;
      const by = tier === 0 ? 4 : 6;
      flag.appendChild(el("rect", {
        x: bx, y: by, width: bw, height: 12, rx: 3,
        fill: "var(--signal)",
      }));
      const num = el("text", {
        x: bx + bw / 2, y: by + 9,
        "text-anchor": "middle",
        class: "tape-flag-num",
      });
      num.textContent = textStr;
      flag.appendChild(num);
      gFlags.appendChild(flag);
    });

    // Precision playhead needle
    gPlay.textContent = "";
    if (!idle) {
      let px = x(Math.min(elapsed, span), span);
      if (armedAt !== null && !REDUCED) {
        const k = Math.min(1, (performance.now() - armedAt) / 280);
        px *= k;
        if (k >= 1) armedAt = null;
      } else {
        armedAt = null;
      }
      gPlay.appendChild(el("line", {
        x1: px, y1: 6, x2: px, y2: 43,
        stroke: "var(--ink)", "stroke-width": "2",
      }));
      if ((spec.snapshots || []).length === 0) {
        const label = el("text", {
          y: 14, class: "tape-playhead-label",
        });
        label.textContent = fmtTime(elapsed);
        if (px < 120) {
          label.setAttribute("x", px + 7);
          label.setAttribute("text-anchor", "start");
        } else {
          label.setAttribute("x", px - 7);
          label.setAttribute("text-anchor", "end");
        }
        gPlay.appendChild(label);
        const box = label.getBBox();
        if (box.width > 0) {
          gPlay.insertBefore(el("rect", {
            x: box.x - 4, y: box.y - 1.5,
            width: box.width + 8, height: box.height + 3,
            rx: 3, class: "tape-playhead-chip",
          }), label);
        }
      }
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
        (spec.status === "idle" || spec.status === "starting") &&
        !REDUCED;
      spec = next;
      if (isArm) armedAt = performance.now();
      if (key !== lastKey) {
        lastKey = key;
      }
      if (container.getAttribute("role") !== "img") {
        container.setAttribute("role", "img");
      }
      if (next.ariaLabel) {
        svg.setAttribute("aria-label", next.ariaLabel);
      }
      if (raf === null) raf = requestAnimationFrame(loop);
    },
    destroy() {
      if (raf !== null) cancelAnimationFrame(raf);
      raf = null;
    },
  };
}
