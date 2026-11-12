// editor.js: pbtx code editor = transparent textarea over a highlighted
// <pre>, synchronized scroll, line-number gutter, Tab -> 4 spaces,
// Ctrl+S -> save callback, error line marker.

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Highlight pbtx: comments dim italic, field names scale, values cyan,
// braces/punctuation dim.
function highlightLine(line) {
  const commentIndex = line.indexOf("#");
  let code = line;
  let comment = "";
  if (commentIndex >= 0) {
    code = line.slice(0, commentIndex);
    comment = line.slice(commentIndex);
  }
  let html = escapeHtml(code).replace(
    /("[^"\n]*")|(\b\d+\b|\b(?:true|false)\b|\b[A-Z][A-Z_0-9]+\b)/g,
    (_m, s, v) => s ? `<span class="tok-str">${s}</span>`
                    : `<span class="tok-val">${v}</span>`);
  html = html.replace(
    /(^|\s)([a-z_][a-zA-Z0-9_]*)(\s*:|\s*\{)/g,
    '$1<span class="tok-name">$2</span>$3');
  html = html.replace(/[{}[\]]/g, '<span class="tok-brace">$&</span>');
  if (comment) {
    html += `<span class="tok-comment">${escapeHtml(comment)}</span>`;
  }
  return html;
}

export function createEditor(container, { onInput, onSave } = {}) {
  container.classList.add("editor");
  const gutter = document.createElement("pre");
  gutter.className = "editor-gutter";
  const highlight = document.createElement("pre");
  highlight.className = "editor-highlight";
  highlight.setAttribute("aria-hidden", "true");
  const area = document.createElement("textarea");
  area.className = "editor-input";
  area.spellcheck = false;
  area.setAttribute("wrap", "off");
  container.append(gutter, highlight, area);

  let errorLine = 0;

  function render() {
    const text = area.value;
    const lines = text.split("\n");
    const htmlLines = lines.map((line, i) => {
      const cls = (i + 1 === errorLine) ? ' class="line-error"' : "";
      return `<div${cls}>${highlightLine(line) || " "}</div>`;
    });
    highlight.innerHTML = htmlLines.join("") + "\n";
    gutter.innerHTML = lines.map((_l, i) => {
      const err = (i + 1 === errorLine) ? ' class="gutter-error"' : "";
      return `<div${err}>${i + 1}</div>`;
    }).join("");
  }

  function syncScroll() {
    highlight.scrollTop = area.scrollTop;
    highlight.scrollLeft = area.scrollLeft;
    gutter.scrollTop = area.scrollTop;
  }

  area.addEventListener("input", () => {
    render();
    if (onInput) onInput(area.value);
  });
  area.addEventListener("scroll", syncScroll);
  area.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const start = area.selectionStart;
      const end = area.selectionEnd;
      area.setRangeText("    ", start, end, "end");
      area.dispatchEvent(new Event("input"));
    } else if ((event.ctrlKey || event.metaKey) &&
               event.key.toLowerCase() === "s") {
      event.preventDefault();
      if (onSave) onSave(area.value);
    }
  });

  render();
  return {
    getText: () => area.value,
    setText(text, keepScroll) {
      const top = area.scrollTop;
      const left = area.scrollLeft;
      area.value = text;
      render();
      if (keepScroll) {
        area.scrollTop = top;
        area.scrollLeft = left;
        syncScroll();
      }
    },
    setError(line) {
      errorLine = line || 0;
      render();
    },
    focus() { area.focus(); },
    isFocused: () => document.activeElement === area,
    element: area,
  };
}
