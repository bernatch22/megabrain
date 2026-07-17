/* api.js — the ONLY place that talks to the megabrain serve-api backend.
 *
 * Everything the UI needs goes through `window.api`. Swap MOCK=true to drive
 * the whole interface from canned data conforming exactly to the route
 * contracts (design/offline mode — no backend, no keys). MOCK=false (default)
 * hits the real server this bundle is served from.
 *
 * SSE note: /ask/stream and /index/stream are POST + text/event-stream, so
 * they can't use EventSource (GET-only) — we read the fetch body stream and
 * parse `event:`/`data:` frames ourselves. /prune, /scan, /repos, /providers
 * are plain GET JSON. */
(function () {
  const MOCK = false;                 // ← flip to true for offline/design mode
  const BASE = "";                    // same-origin (served by serve-api)

  // When serve-api runs with --token (e.g. a public demo box), the API routes
  // require `Authorization: Bearer <token>`. The studio reads it once from
  // ?token= in the URL (then stashes it in localStorage) and sends it on every
  // request — so a tokenized link is all you share.
  const TOKEN = (() => {
    try {
      const t = new URL(location.href).searchParams.get("token");
      if (t) { localStorage.setItem("mb-token", t); return t; }
      return localStorage.getItem("mb-token") || "";
    } catch (e) { return ""; }
  })();
  const authHeaders = (h) => (TOKEN ? { ...(h || {}), Authorization: "Bearer " + TOKEN } : (h || {}));

  async function j(path, opts) {
    const o = opts || {};
    o.headers = authHeaders(o.headers);
    const r = await fetch(BASE + path, o);
    const ct = r.headers.get("content-type") || "";
    const body = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((body && body.error) || r.statusText || ("HTTP " + r.status));
    return body;
  }
  const post = (path, obj) =>
    j(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj || {}) });

  /* POST + SSE: parse the event-stream frames and invoke onEvent per frame.
   * Returns a promise that resolves when the stream ends; call abort() to stop. */
  function sse(path, obj, onEvent) {
    const ctrl = new AbortController();
    const done = (async () => {
      const r = await fetch(BASE + path, {
        method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(obj || {}), signal: ctrl.signal,
      });
      if (!r.ok || !r.body) {
        let msg = "stream failed";
        try { msg = (await r.json()).error || msg; } catch (e) {}
        throw new Error(msg);
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let sep;
        while ((sep = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, sep); buf = buf.slice(sep + 2);
          let type = "message", data = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) type = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (!data) continue;
          try { onEvent(JSON.parse(data)); } catch (e) { /* keep-alive/comment */ }
        }
      }
    })();
    return { done, abort: () => ctrl.abort() };
  }

  const real = {
    repos: () => j("/repos"),
    providers: () => j("/providers"),
    selectProvider: (provider, model) => post("/providers/select", { provider, model }),
    startOllama: () => post("/providers/ollama/serve", {}),
    health: (repo) => j("/health" + (repo ? "?repo=" + encodeURIComponent(repo) : "")),
    scan: (path) => j("/scan?path=" + encodeURIComponent(path)),
    fsPick: () => j("/fs/pick"),          // opens the OS-native folder dialog
    search: (query, repo) => post("/search", { query, repo }),
    chunks: (file, q, repo) =>
      j("/chunks?file=" + encodeURIComponent(file) + "&q=" + encodeURIComponent(q) +
        (repo ? "&repo=" + encodeURIComponent(repo) : "")),
    prune: (q, repo, rerank) =>
      j("/prune?q=" + encodeURIComponent(q) + (repo ? "&repo=" + encodeURIComponent(repo) : "") +
        (rerank ? "&rerank=1" : "")),
    graph: (params, repo) => {
      const p = new URLSearchParams();
      for (const k of ["mode", "node", "source", "target"]) if (params[k]) p.set(k, params[k]);
      if (repo) p.set("repo", repo);
      return j("/graph?" + p.toString());
    },
    reposAdd: (path, ignore) => post("/repos/add", { path, ignore }),
    fileCode: (file, repo) =>
      j("/get?file=" + encodeURIComponent(file) + (repo ? "&repo=" + encodeURIComponent(repo) : "")),
    fileSymbols: (file, repo) =>
      j("/symbols?file=" + encodeURIComponent(file) + (repo ? "&repo=" + encodeURIComponent(repo) : "")),
    symbolNames: (repo) =>
      j("/symbols" + (repo ? "?repo=" + encodeURIComponent(repo) : "")),
    symbolDefs: (name, repo) =>
      j("/symbol?name=" + encodeURIComponent(name) + (repo ? "&repo=" + encodeURIComponent(repo) : "")),
    askStream: (body, onEvent) => sse("/ask/stream", body, onEvent),
    indexStream: (body, onEvent) => sse("/index/stream", body, onEvent),
  };

  window.api = (MOCK && window.mockApi) ? window.mockApi() : real;
  window.__MB_MOCK = MOCK && !!window.mockApi;
})();
