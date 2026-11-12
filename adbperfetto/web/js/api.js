// api.js: fetch wrappers for the adbperfetto REST API.

async function req(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(url, opts);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload && payload.detail) detail = payload.detail;
    } catch (e) { /* keep status text */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  get: (url) => req("GET", url),
  post: (url, body) => req("POST", url, body),
  put: (url, body) => req("PUT", url, body),
  del: (url) => req("DELETE", url),

  state: () => req("GET", "/api/state"),
  outputs: () => req("GET", "/api/outputs"),
  presets: () => req("GET", "/api/presets"),
  preset: (id) => req("GET", `/api/presets/${encodeURIComponent(id)}`),
  savePreset: (name, text) => req("POST", "/api/presets", { name, text }),
  deletePreset: (id) => req("DELETE", `/api/presets/${encodeURIComponent(id)}`),
  settings: () => req("GET", "/api/settings"),
  putSettings: (patch) => req("PUT", "/api/settings", patch),
  prefs: () => req("GET", "/api/prefs"),
  putPrefs: (serial, patch) =>
    req("PUT", `/api/prefs/${encodeURIComponent(serial)}`, patch),
  parse: (text) => req("POST", "/api/config/parse", { text }),
  render: (text, model) => req("POST", "/api/config/render", { text, model }),
  preview: (args) => req("POST", "/api/config/preview", args),
  builtinLists: () => req("GET", "/api/builtin-lists"),
  atrace: (serial) => req("GET", `/api/devices/${serial}/atrace`),
  ftrace: (serial) => req("GET", `/api/devices/${serial}/ftrace`),

  record: (serial, body) =>
    req("POST", `/api/devices/${serial}/record`, body),
  flight: (serial, body) =>
    req("POST", `/api/devices/${serial}/flight`, body),
  stop: (serial, saveSnapshot) =>
    req("POST", `/api/devices/${serial}/stop`,
        { save_snapshot: !!saveSnapshot }),
  snapshot: (serial) => req("POST", `/api/devices/${serial}/snapshot`),
  adopt: (serial) => req("POST", `/api/devices/${serial}/adopt`),
  dismiss: (serial) => req("POST", `/api/devices/${serial}/dismiss`),
  abandon: (serial) => req("POST", `/api/devices/${serial}/abandon`),

  reveal: (path) => req("POST", "/api/outputs/reveal", { path }),
  openDir: () => req("POST", "/api/outputs/open-dir"),
  quit: () => req("POST", "/api/quit"),

  demo: (name, body) => req("POST", `/api/demo/${name}`, body || {}),
};

// SSE: call onState(state) for each "state" event; onClose() when the
// stream drops. Returns the EventSource (caller may close() it).
export function events(onState, onClose) {
  const source = new EventSource("/api/events");
  source.addEventListener("state", (event) => {
    try {
      onState(JSON.parse(event.data));
    } catch (e) { /* ignore malformed payloads */ }
  });
  source.onerror = () => {
    source.close();
    if (onClose) onClose();
  };
  return source;
}
