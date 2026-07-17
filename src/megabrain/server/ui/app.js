/* app.js — megabrain studio SPA (vanilla, no framework).
 * Renders the rail + topbar + the four views (search / prune / ask / graph),
 * the settings slide-over, and the add-repo flow (scan census → live indexing
 * progress). The graph view is a force-directed canvas over /graph (no libs).
 * All backend access is through window.api (see api.js). */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ls = {
    get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
  };

  // ── icons (stroke = currentColor) ────────────────────────────────────
  const I = (p, w) => `<svg viewBox="0 0 24 24" width="${w || 14}" height="${w || 14}" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
  const ico = {
    logo: I('<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>', 14),
    search: I('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>', 16),
    prune: I('<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><path d="M22 4L12 14.01l-3-3"/>', 16),
    ask: I('<path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>', 16),
    graph: I('<circle cx="5" cy="6" r="2.2"/><circle cx="19" cy="6" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M7 7.2l3.6 8.6M17 7.2l-3.6 8.6M7.2 6h9.6"/>', 16),
    gear: I('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>', 14),
    sun: I('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>', 14),
    moon: I('<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>', 14),
    plus: I('<path d="M12 5v14M5 12h14"/>', 12),
    chev: I('<path d="M6 9l6 6 6-6"/>', 12),
    close: I('<path d="M18 6L6 18M6 6l12 12"/>', 14),
    link: I('<path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/>', 10),
    refresh: I('<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>', 11),
    check: I('<path d="M20 6L9 17l-5-5"/>', 11),
    x: I('<path d="M18 6L6 18M6 6l12 12"/>', 10),
    folder: I('<path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>', 13),
    // lucide-accurate glyphs for the scan tree
    chevronR: I('<path d="m9 18 6-6-6-6"/>', 14),
    folderL: I('<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>', 15),
    folderOpen: I('<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>', 15),
    fileL: I('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>', 14),
    checked: I('<rect width="18" height="18" x="3" y="3" rx="2"/><path d="m9 12 2 2 4-4"/>', 16),
    unchecked: I('<rect width="18" height="18" x="3" y="3" rx="2"/>', 16),
    indeterminate: I('<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M8 12h8"/>', 16),
    hardDrive: I('<line x1="22" x2="2" y1="12" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>', 14),
  };

  // ── state ────────────────────────────────────────────────────────────
  const st = {
    theme: ls.get("mb-theme", "dark"),
    view: "search",   // search | bundle | ask | graph
    repos: [], repo: null,
    providers: null,
    provider: ls.get("mb-provider", ""), model: ls.get("mb-model", ""),
    q: "",
    search: null, loading: false,
    prune: null, pruneRerank: ls.get("mb-rerank", "0") === "1",
    ask: null, askCtl: null,
    graph: null, graphLoading: false, graphSel: null, graphNode: null,
    graphPath: null, graphPos: {},       // {file:{x,y}} — layout survives repaints
    graphFocusCom: null,
    gmode: "overview",                   // overview | com | sub | path
    gsub: null,                          // subgraph search: {q, files:[{file,score}]}
    gplay: null,                         // path walkthrough: {k: hop idx, t: seconds}
    overlay: null,          // 'settings' | 'add'
    add: null,              // add-repo flow state
  };

  const accentDim = () => getComputedStyle(document.documentElement).getPropertyValue("--accent-dim");
  const activeModel = () => st.model || (st.providers && st.providers.active && st.providers.active.model) || "default";
  const activeProvider = () => (st.providers && st.providers.active && st.providers.active.label) || st.provider || "…";

  function toast(msg) {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), 5000);
  }

  // ── top-level render ─────────────────────────────────────────────────
  function render() {
    document.documentElement.setAttribute("data-theme", st.theme);
    $("#app").innerHTML = rail() + main();
    renderOverlays();
    bind();
    const q = $("#q"); if (q && document.activeElement !== q) { /* keep value */ }
  }

  function rail() {
    const repos = st.repos.length ? st.repos.map((r) => {
      const active = st.repo === r.name;
      const cold = r.loaded === false;      // in the machine registry, not warm here
      return `<button class="repo-row ${active ? "active" : ""}" data-act="repo" data-name="${esc(r.name)}"
        ${cold ? 'data-cold="1" title="indexed on this machine — click to load"' : ""} style="${cold ? "opacity:.55" : ""}">
        <div style="display:flex;align-items:center;gap:9px;min-width:0;flex:1">
          <div class="repo-dot">${esc((r.name[0] || "?"))}</div>
          <div style="min-width:0;flex:1">
            <div class="repo-name">${esc(r.name)}</div>
            <div class="repo-meta mono">${cold ? "on disk · click to load" : `${r.files} files · ${r.chunks} chunks`}</div>
          </div>
        </div>${active ? '<div class="repo-active-bar"></div>' : ""}</button>`;
    }).join("") : `<div style="padding:6px 8px;font-size:11px;color:var(--muted)">No repos yet.</div>`;
    return `<aside class="rail">
      <div class="rail-brand">
        <div class="logo">${ico.logo}</div>
        <div><div class="rail-title">megabrain</div><div class="rail-sub mono">STUDIO · serve-api</div></div>
      </div>
      <div class="rail-section">
        <div class="rail-label mono">INDEXED REPOS</div>
        ${repos}
        <button class="add-repo" data-act="add-open">${ico.plus}<span>Add repo</span></button>
      </div>
      <div class="rail-foot">
        <button class="rail-foot-btn" data-act="settings">${ico.gear}<span>Settings &amp; providers</span></button>
        <button class="rail-foot-btn" data-act="theme">${st.theme === "dark" ? ico.moon : ico.sun}<span>${st.theme === "dark" ? "Dark" : "Light"} theme</span></button>
      </div>
    </aside>`;
  }

  function main() {
    const tabs = [["search", "Search"], ["ask", "Ask"], ["graph", "Graph"]].map(([id, l]) =>
      `<button class="tab ${st.view === id ? "active" : ""}" data-act="view" data-id="${id}">${l}</button>`).join("");
    const root = st.repo ? (st.repos.find((r) => r.name === st.repo) || {}).root || "" : "";
    return `<main class="main">
      <header class="topbar">
        <div style="display:flex;align-items:center;gap:14px;min-width:0;flex:1">
          <div class="crumb mono"><span>${esc(root)}</span></div>
          <div class="divider"></div>
          <div class="tabs">${tabs}</div>
        </div>
        <button class="model-chip mono" data-act="settings">
          <div class="dotlive"></div>
          <span style="color:var(--muted)">${esc(activeProvider())}</span>
          <span style="opacity:0.4">·</span>
          <span style="font-weight:600">${esc(shortModel(activeModel()))}</span>
          ${ico.chev}
        </button>
      </header>
      <div class="viewport"><div id="view"></div></div>
    </main>`;
  }

  const shortModel = (m) => String(m).split("/").pop().replace(/-preview$/, "");

  // ── views ────────────────────────────────────────────────────────────
  function renderView() {
    const v = $("#view"); if (!v) return;
    v.className = "";
    stopSim();                            // leaving/repainting kills the RAF loop
    if (st.view === "search") v.innerHTML = viewSearch();
    else if (st.view === "graph") v.innerHTML = viewGraph();
    else v.innerHTML = viewAsk();
    bindView();
    if (st.view === "graph") mountGraph();
  }

  function queryBar(placeholder, right) {
    return `<div class="query-wrap">
      <div class="query-icon">${st.view === "search" ? ico.prune : st.view === "ask" ? ico.ask : ico.search}</div>
      <input id="q" class="query-input" value="${esc(st.q)}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false"/>
      ${right || ""}
    </div>`;
  }

  function viewSearch() {
    const r = st.search;
    const rerankBtn = `<button class="btn-ghost" data-act="rerank-toggle" title="LLM pass: drop vocabulary-only matches (tests/evals), reorder — fails open to the deterministic list"
        style="${st.rerank ? "background:var(--accent-dim);border-color:var(--accent-bd);color:var(--accent)" : ""}">✨<span>LLM rerank ${st.rerank ? "on" : "off"}</span></button>`;
    const right = rerankBtn + (st.loading ? `<div class="badge"><span class="spinner"></span></div>`
      : r ? `<div class="badge"><b style="color:var(--accent)">${r.kept}</b><span>kept</span><span style="opacity:.5">·</span><span style="color:var(--muted)">${r.pruned} pruned</span></div>` : "");
    let body;
    if (r) {
      const rr = r.reranked;
      body = `<div class="stats-row">
          <div><b>${r.scanned}</b> chunks scanned</div><div class="sdot"></div>
          <div><span style="color:var(--muted)">retrieval</span> <b class="mono">${r.ms}ms</b></div>
          ${rr ? `<div class="sdot"></div><div>✨ reranked by <b class="mono">${esc(shortModel(rr.model))}</b> · dropped <b>${rr.dropped}</b> tangential · +${(rr.ms / 1000).toFixed(1)}s</div>`
             : st.rerank && r.reranked === false ? `<div class="sdot"></div><div style="color:var(--muted)">rerank failed open — deterministic list shown</div>` : ""}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:22px">
          <div>
            <div class="section-head" style="margin-top:0"><div class="signal-label mono"><div class="dotlive"></div>SIGNAL · KEPT</div><div class="section-rule"></div><div class="mono" style="font-size:10.5px;font-weight:600">${r.kept}</div></div>
            <div style="display:flex;flex-direction:column;gap:8px">${(r.chunks || []).map(signalCard).join("") || emptyMini("nothing kept")}</div>
          </div>
          <div>
            <div class="section-head" style="margin-top:0"><div class="noise-label mono">${ico.x} NOISE · PRUNED</div><div class="section-rule"></div><div class="mono" style="font-size:10.5px">${r.pruned}</div></div>
            <div style="display:flex;flex-direction:column;gap:5px">${(r.noise || []).map(noiseRow).join("") || emptyMini("no noise")}</div>
          </div>
        </div>`;
    } else {
      body = emptyState("The money-shot: what the engine READ vs what it IGNORED.", "Type a query and hit ⏎ to see signal vs noise, side by side.");
    }
    return `<div class="view-wrap mb-fade" style="max-width:1280px">${queryBar("What did the engine read vs ignore?", right)}${body}</div>`;
  }

  function signalCard(s) {
    return `<div class="chunk" style="border-left:2px solid var(--accent)">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px 6px">
        <div style="display:flex;align-items:center;gap:10px;min-width:0">
          <div class="kind-pill on">${esc(s.kind || "chunk")}</div>
          <div class="mono" style="font-size:12px;font-weight:500">${esc(s.name || "")}</div>
        </div>
        <div class="mono" style="font-size:10.5px;font-weight:600;color:var(--accent)">${(s.score || 0).toFixed(2)}</div>
      </div>
      <button class="mono" data-act="vopen" data-file="${esc(s.file)}" data-line="${s.start_line}" style="display:block;font-size:10.5px;color:var(--muted);padding:0 12px 6px;text-align:left" title="open the whole file here">${esc(s.file)}<span style="opacity:.55">:L${s.start_line}–${s.end_line}</span> ⤢</button>
      ${s.text ? `<pre class="mono" style="font-size:11px;padding:8px 12px 10px;background:var(--code);border-top:1px solid var(--border);overflow-x:auto">${hl(s.text, langFor(s.file))}</pre>` : ""}
    </div>`;
  }
  function noiseRow(n) {
    return `<div class="flag-row" data-act="vopen" data-file="${esc(n.file)}" data-line="${n.start_line}" style="justify-content:space-between;border:1px solid var(--border);border-radius:5px;opacity:.72;cursor:pointer">
      <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1">
        <div class="kind-pill">${esc(n.kind || "")}</div>
        <div class="mono" style="min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(n.name || "")} <span style="opacity:.55">· ${esc(n.file)}</span></div>
      </div>
      <div class="mono" style="flex-shrink:0">${(n.score || 0).toFixed(2)}</div>
    </div>`;
  }

  // ── code navigator: a read-only IDE over the index ───────────────────
  // Full files (real bytes via /get), scrolled to the exact line, connection
  // lines highlighted, EVERY identifier clickable -> /symbol go-to-definition,
  // a symbols outline rail, and a step bar for walking a graph connection.
  // Opens from search/prune/ask/graph alike (data-act="vopen").

  function stripFence(code) {
    const m = String(code).match(/^```[^\n]*\n([\s\S]*?)\n?```\s*$/);
    return m ? m[1] : String(code);
  }

  async function symbolIndex() {
    // one cheap fetch per repo: bare name -> definition count
    if (st.symIndex && st.symIndexRepo === st.repo) return st.symIndex;
    try {
      const r = await api.symbolNames(st.repo);
      st.symIndex = r.names || {}; st.symIndexRepo = st.repo;
    } catch (e) { st.symIndex = {}; }          // no links beats fake links
    return st.symIndex;
  }

  async function viewerLoad(file, focus, hiLines, conn, fresh) {
    let gc, sy, links;
    try {
      [gc, sy, links] = await Promise.all([api.fileCode(file, st.repo),
                                           api.fileSymbols(file, st.repo),
                                           symbolIndex()]);
    } catch (e) { toast(e.message); return; }
    const stack = fresh || !st.viewer ? [] : st.viewer.stack;
    const symbols = (sy && sy.symbols) || [];
    // link policy: local definitions (jump is exact) + names with EXACTLY one
    // definition repo-wide. Ambiguous names (`get`, `run`, `props`…) are NOT
    // links — a jump that could land anywhere is worse than none.
    const local = new Set(symbols.map((s) => s.name.split(".").pop()));
    const linkSet = new Set(local);
    for (const [name, count] of Object.entries(links || {}))
      if (count === 1) linkSet.add(name);
    st.viewer = { file, code: stripFence(gc.code), lang: langFor(file),
      symbols, focus: focus || 1, links: linkSet,
      hiLines: hiLines || new Set(), conn: conn || null, stack };
    paintViewer();
  }

  function viewerClose() {
    st.viewer = null; st.gplay = null;
    paintViewer(); paintPanel();
  }

  async function viewerJumpSymbol(name) {
    const v = st.viewer; if (!v) return;
    // local definition wins: the jump is exact, no fetch needed
    const loc = v.symbols.find((s) => s.name.split(".").pop() === name);
    if (loc) {
      v.focus = loc.line; v.hiLines = new Set([loc.line]); paintViewer();
      return;
    }
    // otherwise the link policy guarantees a single repo-wide definition
    let r;
    try { r = await api.symbolDefs(name, st.repo); }
    catch (e) { toast(e.message); return; }
    const defs = (r && r.defs) || [];
    if (!defs.length) { toast(`no definition of ${name} in the index`); return; }
    const d = defs[0];
    v.stack.push({ file: v.file, focus: v.focus, hiLines: v.hiLines });
    await viewerLoad(d.file, d.line, new Set([d.line]), v.conn);
  }

  async function viewerBack() {
    const v = st.viewer; if (!v || !v.stack.length) return;
    const s = v.stack.pop();
    await viewerLoad(s.file, s.focus, s.hiLines, v.conn);
  }

  function paintViewer() {
    const el = $("#viewer"); if (!el) return;
    const v = st.viewer;
    if (!v) { el.innerHTML = ""; return; }
    const conn = v.conn;
    let stepBar = "";
    if (conn) {
      const s = conn.steps[conn.k];
      stepBar = `<div style="display:flex;align-items:center;gap:10px;padding:9px 16px;border-bottom:1px solid var(--border);background:var(--panel2);flex-shrink:0">
        <div class="flag-reason" style="width:auto;padding:2px 8px">step ${conn.k + 1} / ${conn.steps.length}</div>
        <div class="mono" style="font-size:11.5px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.label}</div>
        <button class="chip mono" data-act="vconn-prev" ${conn.k ? "" : "disabled style='opacity:.35'"}>‹ prev</button>
        <button class="chip mono" data-act="vconn-next" ${conn.k < conn.steps.length - 1 ? "" : "disabled style='opacity:.35'"} style="background:var(--accent-dim);border-color:var(--accent-bd);color:var(--accent)">next ›</button>
      </div>`;
    }
    const lines = v.code.split("\n");
    const body = lines.map((ln, i) => {
      const n = i + 1;
      return `<div class="vln ${v.hiLines.has(n) ? "hi" : ""}" id="v-ln-${n}">
        <span class="vno mono">${n}</span><span class="vcode mono">${hl(ln, v.lang, v.links)}</span></div>`;
    }).join("");
    const outline = v.symbols.map((s) =>
      `<button class="flag-row" data-act="vgoto" data-line="${s.line}" style="width:100%;text-align:left;border-radius:5px">
        <span class="mono" style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;color:${s.line <= v.focus && v.focus <= (s.end_line || s.line) ? "var(--accent)" : "var(--text)"}">${esc(s.name)}</span>
        <span class="mono" style="font-size:9.5px;color:var(--muted);flex-shrink:0">${s.line}</span></button>`).join("");
    el.innerHTML = `<aside class="viewer-panel">
      <div style="display:flex;align-items:center;gap:10px;padding:11px 16px;border-bottom:1px solid var(--border);flex-shrink:0">
        ${v.stack.length ? `<button class="chip mono" data-act="vback">← back</button>` : ""}
        <div class="mono" style="font-size:12.5px;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(v.file)}</div>
        <div class="mono" style="font-size:10px;color:var(--muted);flex-shrink:0">click any symbol → its definition</div>
        <button class="close-btn" data-act="viewer-close">${ico.close}</button>
      </div>
      ${stepBar}
      <div style="flex:1;display:flex;min-height:0">
        <div id="vcode" style="flex:1;overflow:auto;padding:8px 0;min-width:0">${body}</div>
        <div style="width:216px;border-left:1px solid var(--border);overflow-y:auto;padding:10px 8px;flex-shrink:0">
          <div class="mono" style="font-size:9.5px;color:var(--muted);letter-spacing:.06em;padding:0 6px 8px">SYMBOLS</div>
          ${outline || emptyMini("no symbols indexed")}</div>
      </div>
    </aside>`;
    requestAnimationFrame(() => {
      const t = $("#v-ln-" + v.focus);
      if (t) t.scrollIntoView({ block: "center" });
    });
  }

  // connection mode: manual next/prev through every call -> definition pair.
  // In the graph behind, the pulse loops on the CURRENT hop (tickLoop).
  function connSteps() {
    const p = st.graphPath; if (!p || !p.found) return [];
    const steps = [];
    p.hops.slice(1).forEach((h, i) => {
      const c = h.code || {};
      const abs = (sn) => (sn.hi_rows || []).map((r) => sn.start_line + r);
      if (c.use) steps.push({ file: c.use.file, hop: i + 1, kind: "the call",
        line: c.use.start_line + ((c.use.hi_rows || [])[0] || 0), hi: abs(c.use),
        label: `hop ${i + 1} · <span style="color:var(--accent)">THE CALL</span> — ${esc(c.symbol || "")}()${c.use.in_symbol ? ` inside <b>${esc(c.use.in_symbol)}()</b>` : ""} · ${esc(c.use.file.split("/").pop())}` });
      if (c.def) steps.push({ file: c.def.file, hop: i + 1, kind: "the definition",
        line: c.def.start_line + ((c.def.hi_rows || [])[0] || 0), hi: abs(c.def),
        label: `hop ${i + 1} · <span style="color:var(--accent)">THE DEFINITION</span> — ${esc(c.symbol || "")}() · ${esc(c.def.file.split("/").pop())}` });
    });
    return steps;
  }

  async function runConnection() {
    const steps = connSteps();
    if (!steps.length) { toast("no code steps on this route (semantic links only)"); return; }
    await viewerConnGo({ steps, k: 0 }, 0, true);
  }

  async function viewerConnGo(conn, k, fresh) {
    conn.k = k;
    const s = conn.steps[k];
    st.gplay = { k: s.hop, t: 0, file: s.file, kind: s.kind };  // canvas pulse
    const v = st.viewer;
    if (!fresh && v && v.file === s.file) {
      v.conn = conn; v.focus = s.line; v.hiLines = new Set(s.hi); paintViewer();
    } else {
      await viewerLoad(s.file, s.line, new Set(s.hi), conn, fresh);
    }
  }

  // ── graph view (force-directed canvas over /graph, no libs) ──────────
  let SIM = null;                        // live simulation; rebuilt per mount
  const comColor = (cid, a) =>
    `hsla(${(cid * 137.508) % 360}, 58%, 62%, ${a == null ? 1 : a})`;

  function viewGraph() {
    const g = st.graph;
    const badge = st.graphLoading ? `<div class="badge"><span class="spinner"></span></div>`
      : g ? `<div class="badge"><div class="dotlive" style="animation:mb-pulse 1.6s infinite"></div><span>${g.files} files</span><span style="opacity:.5">·</span><span>${g.links.length} links</span><span style="opacity:.5">·</span><span>${g.ms}ms</span></div>` : "";
    const crumb = {
      overview: "",
      com: g && st.graphFocusCom != null
        ? `◉ ${(g.communities.find((c) => c.id === st.graphFocusCom) || {}).label || "community"}` : "",
      sub: st.gsub ? `⌕ "${st.gsub.q}" — ${st.gsub.files.length} relevant files` : "",
      path: st.graphPath ? `${(st.graphPath.source || "?").split("/").pop()} → ${(st.graphPath.target || "?").split("/").pop()}` : "",
    }[st.gmode] || "";
    const body = g ? `
      <div style="display:flex;gap:14px;margin-top:16px;height:calc(100vh - 200px);min-height:420px">
        <div id="gwrap" style="flex:1;position:relative;min-width:0;background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden">
          <canvas id="gcanvas" style="position:absolute;inset:0;cursor:grab"></canvas>
          <div id="gtip" class="mono" style="position:absolute;display:none;pointer-events:none;z-index:5;padding:6px 10px;background:var(--panel2);border:1px solid var(--border2);border-radius:6px;font-size:11px;box-shadow:var(--shadow);max-width:360px"></div>
          <div style="position:absolute;right:12px;top:10px;z-index:4;display:flex;flex-direction:column;gap:5px">
            <button class="chip mono" data-act="gzoom-in" title="zoom in" style="width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:15px;padding:0">+</button>
            <button class="chip mono" data-act="gzoom-out" title="zoom out" style="width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:15px;padding:0">−</button>
            <button class="chip mono" data-act="gzoom-fit" title="fit everything" style="width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:13px;padding:0">⊙</button>
          </div>
          ${st.gmode !== "overview" ? `<button class="chip mono" data-act="goverview" style="position:absolute;top:10px;left:12px;z-index:4;background:var(--accent-dim);border-color:var(--accent-bd);color:var(--accent)">← back to overview${crumb ? " · " + esc(crumb) : ""}</button>` : ""}
          <div class="mono" style="position:absolute;left:12px;bottom:10px;font-size:10px;color:var(--muted);pointer-events:none">
            ${st.gmode === "overview" ? "each bubble = a community — click one to open it"
              : st.gmode === "path" ? "the route between your two endpoints — click a file for its code"
              : "drag · wheel zoom · click a file for neighbors + code"}
            · <span style="color:var(--text)">solid</span> import/call · <span style="color:var(--text)">dashed</span> semantic</div>
        </div>
        <div id="gpanel" style="flex:0 1 380px;min-width:260px;max-width:34%;overflow-y:auto;display:flex;flex-direction:column;gap:10px">${graphPanel()}</div>
      </div>` :
      emptyState("The repo as a living map: communities, god nodes, hidden connections.",
        st.graphLoading ? "Building the graph…" : "Loads by itself — or search anything, or  a -> b  for a path.");
    return `<div class="view-wrap mb-fade" style="max-width:none;padding:20px 16px 14px">${queryBar("search files/concepts…  or  a -> b  for the path between two", badge)}${body}</div>`;
  }

  function graphPanel() {
    const g = st.graph; if (!g) return "";
    if (st.graphNode) return nodePanel(st.graphNode);
    if (st.graphPath) {
      const p = st.graphPath;
      const hops = p.hops.map((h, i) => `<button class="flag-row" data-act="gopen" data-file="${esc(h.file)}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px;flex-wrap:wrap">
          <div class="flag-reason">${i === 0 ? "start" : esc(h.via.split("/")[0] || "hop")}</div>
          <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0">${esc(h.file)}</span>
          ${(h.symbols || []).length ? `<span class="mono" style="width:100%;padding-left:86px;font-size:10px;color:var(--accent)">via ${h.symbols.map(esc).join(" · ")}</span>` : ""}</button>`).join("");
      // the connection, told in words — one bullet per hop, from the real
      // use/def sides (who calls what, where it lives)
      const story = p.found ? p.hops.slice(1).map((h) => {
        const c = h.code || {};
        const name = (f) => `<b style="color:var(--text)">${esc(f.split("/").pop())}</b>`;
        if (c.use && c.def && c.symbol)
          return `<li>${name(c.use.file)}${c.use.in_symbol ? ` — from inside <b style="color:var(--text)">${esc(c.use.in_symbol)}()</b> —` : ""} calls <b style="color:var(--accent)">${esc(c.symbol)}()</b>, which lives in ${name(c.def.file)}${c.verified === false ? ` <span style="opacity:.65">· inferred (variable receiver — unverified)</span>` : ""}</li>`;
        if (/^semantic/.test(h.via))
          return `<li>${name(h.file)} has <b style="color:var(--text)">no code link</b> here — it's related by meaning (${esc(h.via)})</li>`;
        return `<li>reaches ${name(h.file)} via ${esc(h.via)}${(h.symbols || []).length ? ` — ${h.symbols.slice(0, 3).map(esc).join(", ")}` : ""}</li>`;
      }).join("") : "";
      // the full storyboard: every hop's call + definition, highlighted, inline
      const seq = p.found ? p.hops.slice(1).map((h, i) => {
        const c = h.code || {};
        if (!c.use && !c.def) return "";
        const head = `${esc((c.use || { file: p.hops[i].file }).file.split("/").pop())} → ` +
          `${esc((c.def || { file: h.file }).file.split("/").pop())}` +
          `${c.symbol ? ` · <span style="color:var(--accent)">${esc(c.symbol)}()</span>` : ""}`;
        const pills = (h.symbols || []).map((s) => `<span class="file-pill mono">${esc(s)}</span>`).join("");
        return `<details ${i === 0 ? "open" : ""} class="prov-card" style="padding:10px 14px">
          <summary style="cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px">
            <div class="flag-reason" style="width:auto;padding:2px 8px">step ${i + 1}</div>
            <span class="mono" style="font-size:11.5px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${head}</span>
            <span class="chev" style="flex-shrink:0">${ico.chev}</span>
          </summary>
          ${pills ? `<div style="display:flex;gap:5px;flex-wrap:wrap;margin-top:9px">${pills}</div>` : ""}
          <div style="display:flex;flex-direction:column;gap:5px;margin-top:9px">
            ${c.use ? `<button class="flag-row" data-act="vopen" data-file="${esc(c.use.file)}" data-line="${c.use.start_line + ((c.use.hi_rows || [])[0] || 0)}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px">
              <div class="flag-reason" style="color:var(--accent)">the call</div>
              <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${c.use.in_symbol ? `inside ${esc(c.use.in_symbol)}() · ` : ""}${esc(c.use.file)}:${c.use.start_line + ((c.use.hi_rows || [])[0] || 0)}</span></button>` : ""}
            ${c.def ? `<button class="flag-row" data-act="vopen" data-file="${esc(c.def.file)}" data-line="${c.def.start_line + ((c.def.hi_rows || [])[0] || 0)}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px">
              <div class="flag-reason" style="color:var(--accent)">definition</div>
              <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.def.file)}:${c.def.start_line + ((c.def.hi_rows || [])[0] || 0)}</span></button>` : ""}
          </div>
        </details>`;
      }).join("") : "";
      return `<div class="prov-card" style="padding:14px 16px">
        <div style="font-size:12.5px;font-weight:600">Path — ${p.found ? p.hops.length + " hops" : "not found"}</div>
        <div class="mono" style="font-size:10.5px;color:var(--muted);margin-top:4px">${esc(p.source || "?")} → ${esc(p.target || "?")}</div>
        ${p.flipped ? `<div style="font-size:10.5px;color:var(--accent);margin-top:6px">↻ shown in call-flow order — the calls actually run this way, opposite to how you asked</div>` : ""}
        ${p.chain === false ? `<div style="font-size:11px;color:var(--bad);margin-top:8px;padding:8px 10px;background:var(--bad-bg);border:1px solid var(--bad-bd);border-radius:6px">⚠ <b>not a call chain</b> — ${esc((p.source || "").split("/").pop())} and ${esc((p.target || "").split("/").pop())} never call each other. Both connect <b>into ${esc((p.meet || "a shared file").split("/").pop())}</b> — follow the arrowheads on the canvas.</div>` : ""}
        <div style="display:flex;flex-direction:column;gap:5px;margin-top:10px">${hops || emptyMini("no route — the endpoints live on disconnected islands")}</div>
        <div style="display:flex;gap:8px;margin-top:12px">
          ${p.found && p.hops.length > 1 ? `<button class="btn-primary" data-act="gplay" style="flex:1">▶ Run the connection</button>` : ""}
          <button class="chip" data-act="goverview" style="background:var(--accent-dim);border-color:var(--accent-bd);color:var(--accent)">← overview</button>
        </div>
        ${story ? `<div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin:14px 0 6px">HOW IT CONNECTS</div>
        <ul style="margin:0;padding-left:18px;font-size:11.5px;line-height:1.8;color:var(--muted)">${story}</ul>` : ""}</div>${seq}`;
    }
    if (st.gmode === "sub" && st.gsub) {
      const rows = st.gsub.files.map((f) => `<button class="flag-row" data-act="gopen" data-file="${esc(f.file)}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px">
          <div class="flag-reason">${f.score.toFixed(2)}</div>
          <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.file)}</span></button>`).join("");
      return `<div class="prov-card" style="padding:14px 16px">
          <div style="font-size:12.5px;font-weight:600">Relevant to "${esc(st.gsub.q)}"</div>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">Real retrieval (same engine as Search), drawn as its subgraph — only these files and the links between them are on the canvas.</div>
          <div style="display:flex;flex-direction:column;gap:5px;margin-top:10px">${rows}</div></div>`;
    }
    if (st.gmode === "com" && st.graphFocusCom != null) {
      const c = g.communities.find((x) => x.id === st.graphFocusCom);
      if (c) {
        const deg = {}; g.nodes.forEach((n) => deg[n.file] = n.degree);
        const rows = c.files.map((f) => `<button class="flag-row" data-act="gopen" data-file="${esc(f)}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px">
            <div class="flag-reason">deg ${deg[f] || 0}</div>
            <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f)}</span></button>`).join("");
        return `<div class="prov-card" style="padding:14px 16px">
            <div style="display:flex;align-items:center;gap:8px"><span style="width:10px;height:10px;border-radius:3px;background:${comColor(c.id)}"></span>
            <div style="font-size:12.5px;font-weight:600">${esc(c.label)}</div>
            <span class="mono" style="font-size:10px;color:var(--muted)">${c.size} files</span></div>
            <div style="display:flex;flex-direction:column;gap:5px;margin-top:10px;max-height:60vh;overflow-y:auto">${rows}</div></div>`;
      }
    }
    // overview panel: communities + god nodes + surprises
    const comBtn = (c) => `<button class="flag-row" data-act="gcom" data-id="${c.id}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px">
        <span style="width:9px;height:9px;border-radius:3px;background:${comColor(c.id)};flex-shrink:0"></span>
        <span style="font-size:12px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(c.label)}</span>
        <span class="mono" style="font-size:10px;color:var(--muted)">${c.size}</span></button>`;
    const multi = g.communities.filter((c) => c.size > 1);
    const singles = g.communities.filter((c) => c.size === 1);
    const coms = multi.map(comBtn).join("") + (singles.length ? `
      <details style="margin-top:2px"><summary class="mono" style="cursor:pointer;font-size:10.5px;color:var(--muted);padding:4px 6px">+${singles.length} standalone files (docs, configs — no code links)</summary>
      <div style="display:flex;flex-direction:column;gap:4px;margin-top:4px">${singles.map((c) => `<button class="flag-row" data-act="gopen" data-file="${esc(c.files[0])}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px"><span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.files[0])}</span></button>`).join("")}</div></details>` : "");
    const hint = `<div class="prov-card" style="padding:12px 16px;font-size:11.5px;line-height:1.65;color:var(--muted)">
        <b style="color:var(--text)">Start here</b> — each bubble is a <b style="color:var(--text)">community</b>: files that
        import/call each other or talk about the same thing. <b style="color:var(--text)">Click one</b> (bubble or row) to open
        just that community. <b style="color:var(--text)">Search anything</b> above to see only the relevant files as a small
        graph, or type <span class="mono" style="color:var(--text)">a -> b</span> for the route between two files/concepts.</div>`;
    const gods = g.god_nodes.map((n) => `<button class="flag-row" data-act="gopen" data-file="${esc(n.file)}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px">
        <div class="flag-reason">deg ${n.degree}</div>
        <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(n.file)}</span></button>`).join("");
    const sur = g.surprises.map((s) => `<div class="flag-row" style="border:1px solid var(--border);border-radius:6px;flex-wrap:wrap">
        <button class="mono" data-act="gopen" data-file="${esc(s.a)}" style="font-size:11px;color:var(--text)">${esc(s.a.split("/").pop())}</button>
        <span class="mono" style="font-size:10px;color:var(--accent)">~${s.score}~</span>
        <button class="mono" data-act="gopen" data-file="${esc(s.b)}" style="font-size:11px;color:var(--text)">${esc(s.b.split("/").pop())}</button></div>`).join("");
    return `${hint}<div class="prov-card" style="padding:14px 16px">
        <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin-bottom:8px">COMMUNITIES</div>
        <div style="display:flex;flex-direction:column;gap:5px">${coms}</div></div>
      <div class="prov-card" style="padding:14px 16px">
        <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin-bottom:8px">GOD NODES · CORE ABSTRACTIONS</div>
        <div style="display:flex;flex-direction:column;gap:5px">${gods || emptyMini("no structural edges yet")}</div></div>
      ${sur ? `<div class="prov-card" style="padding:14px 16px">
        <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin-bottom:8px">SURPRISING CONNECTIONS</div>
        <div style="display:flex;flex-direction:column;gap:5px">${sur}</div></div>` : ""}`;
  }

  function nodePanel(n) {
    const c = n.community || {};
    const row = (e, mark) => `<button class="flag-row" data-act="gopen" data-file="${esc(e.file)}" style="width:100%;text-align:left;border:1px solid var(--border);border-radius:6px">
        <div class="flag-reason">${esc(mark)}</div>
        <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.file)}</span></button>`;
    const chunks = (n.chunks || []).map((ch) => `
      <details class="chunk" style="margin-top:6px">
        <summary style="display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;list-style:none">
          <div class="kind-pill on">${esc(ch.kind || "chunk")}</div>
          <div class="mono" style="font-size:11.5px;font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(ch.name || "")}</div>
          <div class="mono" style="font-size:10px;color:var(--muted)">L${ch.start_line}–${ch.end_line}</div>
          <button class="chip mono" data-act="vopen" data-file="${esc(n.file)}" data-line="${ch.start_line}" title="open the whole file here">⤢</button>
        </summary>
        <pre class="mono">${hl(ch.text || "", langFor(n.file))}</pre>
      </details>`).join("");
    return `<div class="prov-card" style="padding:14px 16px">
        <div class="mono" style="font-size:12.5px;font-weight:600;word-break:break-all">${esc(n.file)}</div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap">
          <span class="chip" style="border-color:${comColor(c.id || 0, 0.5)};color:${comColor(c.id || 0)}">● ${esc(c.label || "Community " + (c.id || 0))}</span>
          <span class="file-pill mono">degree ${n.degree}</span>
          ${n.resolved_from && n.resolved_from !== n.file ? `<span class="file-pill mono" title="resolved by embedding">← "${esc(n.resolved_from)}"</span>` : ""}
        </div>
        <button class="chip" data-act="gclear" style="margin-top:10px;background:var(--accent-dim);border-color:var(--accent-bd);color:var(--accent)">← back</button></div>
      ${n.out.length ? `<div class="prov-card" style="padding:12px 16px"><div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin-bottom:6px">OUTGOING</div><div style="display:flex;flex-direction:column;gap:4px">${n.out.map((e) => row(e, e.kind)).join("")}</div></div>` : ""}
      ${n.in.length ? `<div class="prov-card" style="padding:12px 16px"><div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin-bottom:6px">INCOMING</div><div style="display:flex;flex-direction:column;gap:4px">${n.in.map((e) => row(e, e.kind)).join("")}</div></div>` : ""}
      ${n.semantic.length ? `<div class="prov-card" style="padding:12px 16px"><div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin-bottom:6px">SEMANTICALLY CLOSE</div><div style="display:flex;flex-direction:column;gap:4px">${n.semantic.map((e) => row(e, "~" + e.score)).join("")}</div></div>` : ""}
      ${chunks ? `<div class="prov-card" style="padding:12px 16px"><div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin-bottom:4px">CHUNKS · REAL CODE</div>${chunks}</div>` : ""}`;
  }

  async function loadGraph() {
    if (st.graphLoading || !st.repo) return;
    st.graphLoading = true; renderView();
    try { st.graph = await api.graph({ mode: "map" }, st.repo); }
    catch (e) { toast("graph: " + e.message); }
    st.graphLoading = false; renderView();
  }
  async function openGraphNode(file) {
    st.graphSel = file;
    paintPanel();
    try { st.graphNode = await api.graph({ mode: "node", node: file }, st.repo); st.graphSel = st.graphNode.file; }
    catch (e) { toast("graph: " + e.message); }
    paintPanel();
  }
  async function runGraphQuery() {
    const q = st.q.trim(); if (!q) return;
    if (q.includes("->")) {
      const [a, b] = q.split("->").map((x) => x.trim());
      if (!a || !b) return;
      try {
        st.graphPath = await api.graph({ mode: "path", source: a, target: b }, st.repo);
        st.graphNode = null; st.graphSel = null; st.gsub = null;
        st.gmode = "path"; st.graphView = null;
      } catch (e) { toast("graph: " + e.message); }
      renderView();
      return;
    }
    // free text = REAL retrieval (the same engine as the Search tab), drawn as
    // the induced subgraph — never "resolve to one node" over a hairball
    st.graphLoading = true; renderView();
    try {
      const r = await api.search(q, st.repo);
      const files = r.tier1.map((t) => ({ file: t.file, score: t.score }))
        .concat(r.tier2.slice(0, 10).map((t) => ({ file: t.file, score: t.score || 0 })));
      const known = new Set(st.graph.nodes.map((n) => n.file));
      st.gsub = { q, files: files.filter((f) => known.has(f.file)) };
      st.gmode = "sub"; st.graphPath = null; st.graphNode = null; st.graphSel = null;
      st.graphView = null;
    } catch (e) { toast("graph: " + e.message); }
    st.graphLoading = false; renderView();
  }
  const paintPanel = () => { const p = $("#gpanel"); if (p) p.innerHTML = graphPanel(); };

  // ── the simulation ────────────────────────────────────────────────────
  function stopSim() {
    if (SIM && SIM.raf) cancelAnimationFrame(SIM.raf);
    if (SIM && SIM.ro) SIM.ro.disconnect();
    SIM = null;
  }

  function fitAll(pad) {
    const S = SIM; if (!S || !S.nodes.length) return;
    pad = pad || 70;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const p of S.nodes) {
      x0 = Math.min(x0, p.x - p.r); y0 = Math.min(y0, p.y - p.r);
      x1 = Math.max(x1, p.x + p.r); y1 = Math.max(y1, p.y + p.r);
    }
    // the auto-fit only ever zooms OUT (cap 1). Node radii already encode
    // meaning (degree / community size); scaling past 1 to "fill" a wide pane
    // ballooned 4 bubbles across the screen — that was the too-much-zoom.
    // Zooming in is the + button's job, on demand.
    if (S.mode === "path") pad = Math.max(pad, 130);   // room for end labels
    const s = Math.min(1, Math.max(0.15,
      Math.min(S.W / (x1 - x0 + pad * 2), S.H / (y1 - y0 + pad * 2))));
    S.scale = s;
    S.tx = S.W / 2 - ((x0 + x1) / 2) * s;
    S.ty = S.H / 2 - ((y0 + y1) / 2) * s;
  }

  function pathLayout() {
    // static zigzag from the CURRENT canvas size (re-run on every resize —
    // stale mount-time positions were pushing nodes out of the viewport).
    // Margin adapts: on a narrow pane a fixed 110px margin exceeded W/2 and
    // the span went NEGATIVE (nodes rendered in reverse order).
    const S = SIM; if (!S || S.mode !== "path") return;
    const m = Math.min(110, S.W * 0.15), span = Math.max(1, S.nodes.length - 1);
    S.nodes.forEach((n, i) => {
      n.x = m + (S.W - 2 * m) * (i / span);
      n.y = S.H / 2 + (i % 2 ? 60 : -60);
    });
  }

  function zoomBy(k) {
    const S = SIM; if (!S) return;
    S.userView = true;                   // manual zoom pauses the auto-fit
    const ns = Math.min(5, Math.max(0.15, S.scale * k));
    S.tx = S.W / 2 - ((S.W / 2 - S.tx) / S.scale) * ns;   // zoom around center
    S.ty = S.H / 2 - ((S.H / 2 - S.ty) / S.scale) * ns;
    S.scale = ns;
  }

  function mountGraph() {
    if (!st.graph) { loadGraph(); return; }
    const cv = $("#gcanvas"), wrap = $("#gwrap");
    if (!cv || !wrap) return;
    const g = st.graph;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr;
    const mode = st.gmode;
    const idx = {};
    let nodes = [], links = [];

    if (mode === "overview") {
      // one BUBBLE per multi-file community — never the whole-repo hairball
      const multi = g.communities.filter((c) => c.size > 1);
      const comOf = {}; g.nodes.forEach((n) => comOf[n.file] = n.community);
      nodes = multi.map((c, i) => {
        idx["com:" + c.id] = i;
        return { f: "com:" + c.id, c: c.id, d: c.size, size: c.size,
          label: c.label, bubble: true,
          r: 22 + Math.min(60, Math.sqrt(c.size) * 7),
          x: W / 2 + (Math.random() - 0.5) * 120,
          y: H / 2 + (Math.random() - 0.5) * 120, vx: 0, vy: 0, fix: false };
      });
      const cross = {};                  // inter-community link counts
      for (const l of g.links) {
        const a = comOf[l.s], b = comOf[l.d];
        if (a === b || idx["com:" + a] == null || idx["com:" + b] == null) continue;
        const k = a < b ? a + "-" + b : b + "-" + a;
        cross[k] = (cross[k] || 0) + 1;
      }
      links = Object.entries(cross).map(([k, n]) => {
        const [a, b] = k.split("-");
        return { a: idx["com:" + a], b: idx["com:" + b], sem: false, count: n };
      });
    } else if (mode === "path" && st.graphPath && st.graphPath.found) {
      // graphify-style: ONLY the route, laid out as a clean zigzag, edge labels on
      const hops = st.graphPath.hops;
      const m = 110, span = Math.max(1, hops.length - 1);
      nodes = hops.map((h, i) => {
        idx[h.file] = i;
        return { f: h.file, c: (g.nodes.find((n) => n.file === h.file) || {}).community || 0,
          d: 0, r: 9, big: true, x: m + (W - 2 * m) * (i / span),
          y: H / 2 + (i % 2 ? 60 : -60), vx: 0, vy: 0, fix: true };
      });
      links = hops.slice(1).map((h, i) => {
        const c = h.code || {};
        return { a: i, b: i + 1, i, sem: /^semantic/.test(h.via), via: h.via,
          symbols: h.symbols || [],
          uf: c.use && c.use.file, df: c.def && c.def.file };  // true call direction
      });
    } else {
      // com = one community's files · sub = the search result's files
      let files;
      if (mode === "com" && st.graphFocusCom != null) {
        const c = g.communities.find((x) => x.id === st.graphFocusCom);
        files = new Set(c ? c.files : []);
      } else {
        files = new Set((st.gsub ? st.gsub.files : []).map((f) => f.file));
      }
      const shown = g.nodes.filter((n) => files.has(n.file));
      const R = Math.min(W, H) * 0.34;
      nodes = shown.map((n, i) => {
        idx[n.file] = i;
        const th = (i / Math.max(1, shown.length)) * Math.PI * 2;
        return { f: n.file, c: n.community, d: n.degree,
          r: 4 + Math.min(9, Math.sqrt(n.degree || 0) * 1.6),
          x: W / 2 + R * Math.cos(th), y: H / 2 + R * Math.sin(th),
          vx: 0, vy: 0, fix: false };
      });
      links = g.links.map((l) => ({ a: idx[l.s], b: idx[l.d],
        sem: l.kind === "semantic" })).filter((l) => l.a != null && l.b != null);
    }

    const godSet = new Set(g.god_nodes.map((n) => n.file));
    const byCom = {};
    nodes.forEach((n, i) => (byCom[n.bubble ? "b" + i : n.c] = byCom[n.bubble ? "b" + i : n.c] || []).push(i));
    const v = st.graphView || {};        // pan/zoom survives repaints (user's only)
    SIM = { cv, ctx: cv.getContext("2d"), W, H, dpr, nodes, links, idx, byCom,
      godSet, mode, alpha: mode === "path" ? 0 : 1,
      tx: v.user ? v.tx : 0, ty: v.user ? v.ty : 0,
      scale: v.user ? v.scale : 1, userView: !!v.user,
      drag: null, panning: null, raf: 0,
      labels: Object.fromEntries(g.communities.map((c) => [c.id, c.label])) };
    pathLayout();                        // static modes lay out from live size
    if (!SIM.userView) fitAll();         // centered, everything visible, always
    bindSim();
    // the canvas shares its row with the code card / panel: when they open or
    // close (or the window resizes) re-measure, or the drawing skews
    SIM.ro = new ResizeObserver(() => {
      const S = SIM; if (!S) return;
      const w = wrap.clientWidth, h = wrap.clientHeight;
      if (!w || !h || (w === S.W && h === S.H)) return;
      S.W = w; S.H = h; S.cv.width = w * S.dpr; S.cv.height = h * S.dpr;
      pathLayout();                      // static modes re-lay for the new size
      if (!S.userView) fitAll();
    });
    SIM.ro.observe(wrap);
    tickLoop();
  }

  function tickLoop() {
    if (!SIM) return;
    if (SIM.alpha > 0.012 && !document.hidden) simTick();
    // keep the WHOLE graph centered and visible, every frame, in every mode —
    // until the user takes the camera (wheel/pan/drag/±). Gating this on the
    // sim's alpha left late drift uncorrected; a bbox loop over N nodes is
    // nothing next to the draw we already do each frame.
    if (!SIM.userView) fitAll();
    if (SIM.mode === "path" && st.gplay && st.graphPath && !document.hidden) {
      // the pulse LOOPS on the current step's hop — the navigator's next/prev
      // moves it; nothing auto-advances (the user reads at their own pace)
      st.gplay.t += 1 / 60;
      if (st.gplay.t > 3.4) st.gplay.t = 0;
    }
    drawSim();
    st.graphView = { tx: SIM.tx, ty: SIM.ty, scale: SIM.scale, user: SIM.userView };
    SIM.raf = requestAnimationFrame(tickLoop);
  }

  function simTick() {
    const S = SIM, N = S.nodes;
    const alpha = S.alpha = Math.max(0.011, S.alpha * 0.985);
    // repulsion INSIDE each community only (communities pre-cluster, so global
    // O(n²) isn't needed) + community centers repel each other + link springs.
    for (const cid in S.byCom) {
      const ids = S.byCom[cid], m = ids.length;
      const stride = m > 260 ? 2 : 1;              // big community: sample pairs
      const rep = 620 * (1 + Math.sqrt(m) / 6);  // big communities push harder
      for (let a = 0; a < m; a += 1)
        for (let b = a + stride; b < m; b += stride) {
          const p = N[ids[a]], q = N[ids[b]];
          let dx = p.x - q.x, dy = p.y - q.y;
          let d2 = dx * dx + dy * dy; if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
          const f = Math.min(3, rep / d2) * alpha;
          const d = Math.sqrt(d2); dx /= d; dy /= d;
          if (!p.fix) { p.vx += dx * f; p.vy += dy * f; }
          if (!q.fix) { q.vx -= dx * f; q.vy -= dy * f; }
        }
    }
    // community centroids: mild mutual repulsion + pull members toward centroid
    const cent = {};
    for (const cid in S.byCom) {
      let x = 0, y = 0; const ids = S.byCom[cid];
      for (const i of ids) { x += N[i].x; y += N[i].y; }
      cent[cid] = [x / ids.length, y / ids.length, ids.length];
    }
    const cids = Object.keys(cent);
    for (let a = 0; a < cids.length; a++)
      for (let b = a + 1; b < cids.length; b++) {
        const A = cent[cids[a]], B = cent[cids[b]];
        let dx = A[0] - B[0], dy = A[1] - B[1];
        const d2 = Math.max(400, dx * dx + dy * dy), d = Math.sqrt(d2);
        const f = Math.min(2.2, (24000 * Math.sqrt(Math.min(A[2], B[2]))) / d2) * alpha;
        dx /= d; dy /= d;
        for (const i of S.byCom[cids[a]]) if (!N[i].fix) { N[i].vx += dx * f; N[i].vy += dy * f; }
        for (const i of S.byCom[cids[b]]) if (!N[i].fix) { N[i].vx -= dx * f; N[i].vy -= dy * f; }
      }
    for (const cid in S.byCom) {
      const [cx, cy] = cent[cid];
      for (const i of S.byCom[cid]) { const p = N[i]; if (!p.fix) { p.vx += (cx - p.x) * 0.012 * alpha; p.vy += (cy - p.y) * 0.012 * alpha; } }
    }
    for (const l of S.links) {                    // springs (gentle: k must stay
      const p = N[l.a], q = N[l.b];               // small or far-apart linked
      const dx = q.x - p.x, dy = q.y - p.y;       // nodes explode the sim)
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const want = S.mode === "overview" ? p.r + q.r + 70 : l.sem ? 150 : 70;
      const f = ((d - want) / d) * (l.sem ? 0.004 : 0.02) * alpha;
      if (!p.fix) { p.vx += dx * f; p.vy += dy * f; }
      if (!q.fix) { q.vx -= dx * f; q.vy -= dy * f; }
    }
    const gx = S.W / 2, gy = S.H / 2, VMAX = 24;
    for (const p of N) {
      if (p.fix) continue;
      p.vx += (gx - p.x) * 0.004 * alpha; p.vy += (gy - p.y) * 0.004 * alpha;
      p.vx = Math.max(-VMAX, Math.min(VMAX, p.vx * 0.82));
      p.vy = Math.max(-VMAX, Math.min(VMAX, p.vy * 0.82));
      p.x += p.vx; p.y += p.vy;
      if (!isFinite(p.x) || !isFinite(p.y)) {     // never let a blowup go blank
        p.x = gx + (Math.random() - 0.5) * 60; p.y = gy + (Math.random() - 0.5) * 60;
        p.vx = p.vy = 0;
      }
      st.graphPos[p.f] = { x: p.x, y: p.y };
    }
  }

  function drawSim() {
    const S = SIM; if (!S) return;
    const { ctx, W, H, dpr, nodes: N } = S;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.translate(S.tx, S.ty); ctx.scale(S.scale, S.scale);
    const cs = getComputedStyle(document.documentElement);
    const muted = cs.getPropertyValue("--muted").trim() || "#7a7a84";
    const textCol = cs.getPropertyValue("--text").trim() || "#e9e9ec";
    // ── edges ──
    for (const l of S.links) {
      const p = N[l.a], q = N[l.b];
      ctx.beginPath();
      ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y);
      if (l.sem) ctx.setLineDash([3, 4]); else ctx.setLineDash([]);
      if (S.mode === "overview") {
        ctx.strokeStyle = muted;
        ctx.globalAlpha = 0.35;
        ctx.lineWidth = Math.min(6, 1 + Math.log2(1 + (l.count || 1))) / S.scale;
      } else if (S.mode === "path") {
        const gp = st.gplay, cur = gp ? gp.k - 1 : -1;
        const state = !gp ? "plain" : l.i < cur ? "done" : l.i === cur ? "now" : "future";
        ctx.strokeStyle = comColor(p.c, 0.9);
        ctx.globalAlpha = state === "future" ? 0.15 : state === "plain" ? 0.9 : state === "done" ? 0.95 : 0.3;
        ctx.lineWidth = (state === "done" ? 2.8 : 2.4) / S.scale;
        // EVERY hop wears its true arrowhead (use -> def): scoring -> http <-
        // rerank reads as a MEETING, not a flow. Hops with no verified
        // direction get no arrow.
        if (l.uf && l.df && S.idx[l.uf] != null && S.idx[l.df] != null) {
          const uN = N[S.idx[l.uf]], dN = N[S.idx[l.df]];
          const an = Math.atan2(dN.y - uN.y, dN.x - uN.x), ah = 9 / S.scale;
          const ax = dN.x - Math.cos(an) * (dN.r + 4), ay = dN.y - Math.sin(an) * (dN.r + 4);
          ctx.save();
          ctx.globalAlpha = state === "future" ? 0.25 : 0.9;
          ctx.beginPath();
          ctx.moveTo(ax, ay);
          ctx.lineTo(ax - ah * Math.cos(an - 0.45), ay - ah * Math.sin(an - 0.45));
          ctx.lineTo(ax - ah * Math.cos(an + 0.45), ay - ah * Math.sin(an + 0.45));
          ctx.closePath();
          ctx.fillStyle = comColor(p.c, 0.95); ctx.fill();
          ctx.restore();
        }
        if (state === "now") {
          // the pulse travels the hop's TRUE call direction (use -> def);
          // with the arrows always on, truth beats left-to-right continuity
          ctx.stroke();
          let from = p, to = q;
          if (l.uf && l.df && S.idx[l.uf] != null && S.idx[l.df] != null) {
            from = N[S.idx[l.uf]]; to = N[S.idx[l.df]];
          }
          const t01 = Math.min(1, gp.t / 3.0);
          const mx = from.x + (to.x - from.x) * t01, my = from.y + (to.y - from.y) * t01;
          ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(mx, my);
          ctx.globalAlpha = 0.95; ctx.lineWidth = 3 / S.scale; ctx.stroke();
          ctx.beginPath(); ctx.arc(mx, my, 5.5 / S.scale, 0, Math.PI * 2);
          ctx.shadowColor = comColor(p.c); ctx.shadowBlur = 16;
          ctx.fillStyle = comColor(p.c, 1); ctx.fill(); ctx.shadowBlur = 0;
          ctx.globalAlpha = 0.3; ctx.lineWidth = 2.4 / S.scale;
          ctx.beginPath(); ctx.moveTo(p.x, p.y);   // keep label pass on the base line
          ctx.lineTo(q.x, q.y);
        }
      } else {
        ctx.strokeStyle = l.sem ? comColor(p.c, 0.22) : muted;
        ctx.globalAlpha = l.sem ? 0.5 : 0.3;
        ctx.lineWidth = 1.1 / S.scale;
      }
      ctx.stroke();
      if (S.mode === "path" && l.via) {           // edge label ON the line —
        const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
        // alternate above/below per segment: every zigzag midpoint sits at the
        // SAME height, so same-side labels of adjacent hops collided
        const up = l.i % 2 === 0;
        ctx.setLineDash([]);
        ctx.font = `italic 600 ${11 / S.scale}px ui-monospace, Menlo, monospace`;
        ctx.fillStyle = comColor(p.c, 0.95);
        ctx.textAlign = "center";
        ctx.fillText(l.via, mx, my + (up ? -26 : 22) / S.scale);
        if (l.symbols.length) {
          ctx.font = `${10 / S.scale}px ui-monospace, Menlo, monospace`;
          ctx.fillStyle = muted;
          ctx.fillText("via " + l.symbols.slice(0, 2).join(", "),
                       mx, my + (up ? -10 : 38) / S.scale);
        }
        ctx.textAlign = "left";
      }
    }
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    // ── nodes ──
    for (const p of N) {
      const sel = st.graphSel === p.f || S.hover === p.f;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r + (sel ? 1.5 : 0), 0, Math.PI * 2);
      if (p.bubble) {
        ctx.fillStyle = comColor(p.c, sel ? 0.5 : 0.3);
        ctx.fill();
        ctx.strokeStyle = comColor(p.c, 0.95);
        ctx.lineWidth = (sel ? 2.4 : 1.6) / S.scale;
        ctx.stroke();
      } else {
        if ((S.godSet.has(p.f) || p.big)) { ctx.shadowColor = comColor(p.c); ctx.shadowBlur = 14; }
        ctx.fillStyle = comColor(p.c, sel ? 1 : 0.85);
        ctx.fill();
        ctx.shadowBlur = 0;
        if (sel) { ctx.strokeStyle = textCol; ctx.lineWidth = 1.4 / S.scale; ctx.stroke(); }
      }
    }
    // ── path walkthrough: ring + tag the node the card is talking about ──
    if (S.mode === "path" && st.gplay && st.gplay.file &&
        S.idx[st.gplay.file] != null) {
      // ring + tag the node the navigator is showing right now
      const act = N[S.idx[st.gplay.file]];
      ctx.beginPath();
      ctx.arc(act.x, act.y, act.r + 6 / S.scale, 0, Math.PI * 2);
      ctx.strokeStyle = comColor(act.c, 0.95);
      ctx.lineWidth = 2 / S.scale;
      ctx.setLineDash([4 / S.scale, 3 / S.scale]);
      ctx.stroke(); ctx.setLineDash([]);
      ctx.textAlign = "center";
      ctx.font = `600 ${10.5 / S.scale}px ui-monospace, Menlo, monospace`;
      ctx.fillStyle = comColor(act.c, 1);
      ctx.fillText(st.gplay.kind || "", act.x, act.y - act.r - 12 / S.scale);
      ctx.textAlign = "left";
    }
    // ── labels ──
    ctx.textAlign = "center";
    if (S.mode === "overview") {
      for (const p of N) {                        // label + size inside/under bubble
        ctx.font = `600 ${Math.max(11, Math.min(15, p.r / 3.2)) / S.scale}px ui-monospace, Menlo, monospace`;
        ctx.fillStyle = textCol;
        ctx.fillText(p.label || "", p.x, p.y - 2);
        ctx.font = `${10 / S.scale}px ui-monospace, Menlo, monospace`;
        ctx.fillStyle = muted;
        ctx.fillText(p.size + " files", p.x, p.y + 14 / S.scale);
      }
    } else {
      // com/sub: EVERY node gets its name (these views are small by design);
      // path: bold names under the big dots
      const small = N.length <= 60;
      for (const p of N) {
        const hot = st.graphSel === p.f || S.hover === p.f;
        if (!small && !hot && !S.godSet.has(p.f) && !p.big && S.scale < 1.4) continue;
        ctx.font = `${p.big ? "600 " : ""}${(p.big ? 12 : 10.5) / S.scale}px ui-monospace, Menlo, monospace`;
        ctx.fillStyle = hot || p.big ? textCol : muted;
        ctx.fillText(p.f.split("/").pop(), p.x, p.y + p.r + 12 / S.scale);
      }
    }
    ctx.textAlign = "left";
  }

  function bindSim() {
    const S = SIM, cv = S.cv;
    const toWorld = (e) => {
      const r = cv.getBoundingClientRect();
      return [(e.clientX - r.left - S.tx) / S.scale, (e.clientY - r.top - S.ty) / S.scale];
    };
    const hit = (x, y) => {
      const slack = Math.max(4, 9 / S.scale);      // zoomed out, dots stay clickable
      for (let i = S.nodes.length - 1; i >= 0; i--) {
        const p = S.nodes[i];
        if (st.graphFocusCom != null && p.c !== st.graphFocusCom) continue;
        const dx = x - p.x, dy = y - p.y;
        const rr = p.r + slack;
        if (dx * dx + dy * dy <= rr * rr) return p;
      }
      return null;
    };
    const tip = $("#gtip");
    const showTip = (e, p) => {
      if (!tip) return;
      if (!p) { tip.style.display = "none"; return; }
      const wr = $("#gwrap").getBoundingClientRect();
      tip.innerHTML = p.bubble
        ? `<b style="color:var(--text)">${esc(p.label)}</b> · ${p.size} files` +
          `<br><span style="opacity:.65">click to open this community</span>`
        : `<b style="color:var(--text)">${esc(p.f)}</b><br>` +
          `<span style="color:${comColor(p.c)}">● ${esc(S.labels[p.c] || "Community " + p.c)}</span>` +
          ` · ${p.d} link${p.d === 1 ? "" : "s"}${S.godSet.has(p.f) ? " · ★ god node" : ""}` +
          `<br><span style="opacity:.65">click for neighbors + code</span>`;
      tip.style.display = "block";
      tip.style.left = Math.min(e.clientX - wr.left + 14, wr.width - 300) + "px";
      tip.style.top = (e.clientY - wr.top + 12) + "px";
    };
    cv.onmousedown = (e) => {
      const [x, y] = toWorld(e);
      const p = hit(x, y);
      if (p) { S.drag = { p, x0: e.clientX, y0: e.clientY, moved: false }; p.fix = true; }
      else S.panning = { x: e.clientX - S.tx, y: e.clientY - S.ty };
      cv.style.cursor = "grabbing";
    };
    cv.onmousemove = (e) => {
      const [x, y] = toWorld(e);
      if (S.drag) {
        // a click with 2px of jitter is still a click, not a drag
        if (Math.abs(e.clientX - S.drag.x0) + Math.abs(e.clientY - S.drag.y0) > 4) S.drag.moved = true;
        if (S.drag.moved) { S.userView = true; S.drag.p.x = x; S.drag.p.y = y; S.alpha = Math.max(S.alpha, 0.25); }
      }
      else if (S.panning) { S.userView = true; S.tx = e.clientX - S.panning.x; S.ty = e.clientY - S.panning.y; showTip(e, null); }
      else {
        const p = hit(x, y); S.hover = p ? p.f : null;
        cv.style.cursor = p ? "pointer" : "grab";
        showTip(e, p);
      }
    };
    const up = () => {
      if (S.drag) {
        const { p, moved } = S.drag;
        if (S.mode !== "path") p.fix = false;
        if (!moved) {
          if (p.bubble) {                      // open that community
            st.graphFocusCom = p.c; st.gmode = "com";
            st.graphView = null; st.graphNode = null; st.graphSel = null;
            renderView();
          } else openGraphNode(p.f);
        }
      }
      S.drag = null; S.panning = null; cv.style.cursor = "grab";
    };
    cv.onmouseup = up;
    cv.onmouseleave = () => { up(); S.hover = null; showTip(null, null); };
    cv.onwheel = (e) => {
      e.preventDefault();
      S.userView = true;
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const k = e.deltaY < 0 ? 1.12 : 0.89;
      const ns = Math.min(5, Math.max(0.15, S.scale * k));
      S.tx = mx - ((mx - S.tx) / S.scale) * ns;
      S.ty = my - ((my - S.ty) / S.scale) * ns;
      S.scale = ns;
    };
  }

  // ── ask view (incremental) ───────────────────────────────────────────
  function viewAsk() {
    const running = !!st.askCtl;
    const right = `<button class="btn-ghost" data-act="ask-run">${running ? '<span class="spinner"></span>' : ico.refresh}<span>${running ? "Running" : "Run"}</span></button>`;
    const a = st.ask;
    let body = "";
    if (a) {
      body = `<div id="ask-info"></div><div id="ask-agents" style="display:flex;flex-direction:column;gap:10px;margin-top:14px"></div><div id="ask-synth" style="margin-top:22px"></div><div id="ask-foot"></div>`;
    } else {
      body = emptyState("The star: a senior-engineer walkthrough with the REAL code spliced in.", "Broad questions fan out into parallel sub-agents you can watch work. Hit ⏎.");
    }
    return `<div class="view-wrap mb-fade" style="max-width:1000px;padding-bottom:80px">${queryBar("Ask how something works…", right)}${body}</div>`;
  }

  function askRender() {
    // full paint of the current ask state (called on each event; cheap)
    const a = st.ask; if (!a) return;
    const info = $("#ask-info");
    if (info) {
      let h = "";
      if (a.retrieval) h += `<div style="display:flex;align-items:center;gap:8px">
        <div class="chip-ic">${ico.check}</div><div class="mono" style="font-size:11px;color:var(--muted)">retrieval</div>
        <div class="mono" style="font-size:12px;font-weight:600">${a.retrieval.ms}ms · ${a.retrieval.files} files</div></div>`;
      if (a.classified) h += `<div class="divider"></div><div style="display:flex;align-items:center;gap:8px">
        <div style="width:6px;height:6px;border-radius:50%;background:${a.classified.broad ? "var(--blue)" : "var(--muted)"}"></div>
        <div class="mono" style="font-size:11px;color:var(--muted)">classified</div>
        <div style="font-size:12px;font-weight:600">${a.classified.broad ? "broad" : "scoped"}</div>
        ${a.classified.broad ? `<div style="font-size:11px;color:var(--muted)">· fans out into <b style="color:var(--text)">${a.agents.length || "…"}</b> agents</div>` : ""}</div>`;
      info.innerHTML = h ? `<div class="info-bar">${h}</div>` : "";
    }
    const ag = $("#ask-agents");
    if (ag) ag.innerHTML = a.agents.map(agentCard).join("");
    const sy = $("#ask-synth");
    if (sy) {
      if (a.synthText || a.synthActive) {
        sy.innerHTML = `<div class="section-head" style="margin-top:0"><div class="signal-label mono">
            <div class="dotlive" style="animation:mb-pulse 1.6s infinite"></div>SYNTHESIS${a.done ? "" : " · STREAMING"}</div>
            <div class="section-rule"></div><div class="mono" style="font-size:10.5px;color:var(--muted)">${esc(shortModel(a.model || activeModel()))}</div></div>
          <div class="synth">${md(a.synthText)}${a.done ? "" : '<span class="caret"></span>'}</div>`;
      } else if (a.bundle) {
        sy.innerHTML = `<div class="synth">${a.bundleNote ? `<p style="color:var(--muted)">${esc(a.bundleNote)} — full bundle:</p>` : ""}<pre class="mono">${esc(a.bundle)}</pre></div>`;
      } else sy.innerHTML = "";
    }
    const ft = $("#ask-foot");
    if (ft && a.done) {
      const d = a.done;
      ft.innerHTML = `<div class="foot-bar"><span><b>${d.spans}</b> code spans</span><div class="sdot"></div>
        <span><b>${d.files}</b> files</span><div class="sdot"></div>
        <span><b>${d.retrieval_ms}ms</b> retrieval</span><span style="opacity:.6">+</span>
        <span><b>${(d.llm_ms / 1000).toFixed(1)}s</b> explain</span>
        <div style="flex:1"></div>${d.n_dropped ? `<span style="opacity:.7">${d.n_dropped} not cited</span>` : ""}</div>`;
    } else if (ft) ft.innerHTML = "";
  }

  function agentCard(a) {
    const cls = a.status === "working" ? "working" : "";
    const badge = a.status === "done" ? "done" : "";
    const status = a.status === "done" ? "done" : a.status === "working" ? "streaming" : "queued";
    const spill = a.stream || a.tool;
    return `<div class="agent-card ${cls}">
      <div style="display:flex;align-items:center;gap:12px;padding:14px 16px">
        <div class="agent-badge ${badge}">A${a.id + 1}</div>
        <div style="min-width:0;flex:1">
          <div style="font-size:12.5px;font-weight:600">${esc(a.label)}</div>
          <div style="font-size:11.5px;color:var(--muted);margin-top:3px">${esc(a.sub_query || "")}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
          ${(a.files || []).slice(0, 2).map((f) => `<button class="file-pill mono" data-act="vopen" data-file="${esc(f)}" title="open ${esc(f)}">${esc(f.split("/").pop())}</button>`).join("")}
          <div class="status-pill ${badge || (a.status === "working" ? "working" : "")}">${status}</div>
        </div>
      </div>
      ${spill && a.status !== "done" ? `<div style="padding:0 16px 14px;border-top:1px solid var(--border)">
        ${a.stream ? `<div class="agent-stream">${esc(a.stream)}<span class="caret"></span></div>` : ""}
        ${a.tool ? `<div class="tool-chip mono">${ico.search}<span>${esc(a.tool.tool)}</span><span style="opacity:.55">·</span><span style="color:var(--text)">${esc(JSON.stringify(a.tool.args))}</span></div>` : ""}
      </div>` : ""}
    </div>`;
  }

  // minimal markdown → html (paragraphs, ## headings, fenced code, **bold**, `code`)
  function md(src) {
    if (!src) return "";
    const parts = String(src).split(/```/);
    let out = "";
    for (let i = 0; i < parts.length; i++) {
      if (i % 2 === 1) {                       // fenced code block
        const nl = parts[i].indexOf("\n");
        const lang = langAlias(nl >= 0 ? parts[i].slice(0, nl).trim() : "");
        const code = (nl >= 0 ? parts[i].slice(nl + 1) : parts[i]).replace(/\n$/, "");
        out += `<pre class="mono">${hl(code, lang)}</pre>`;
      } else {
        out += inlineMd(parts[i]);
      }
    }
    return out;
  }
  function inlineMd(t) {
    const blocks = t.split(/\n{2,}/);
    return blocks.map((b) => {
      b = b.trim(); if (!b) return "";
      if (b.startsWith("## ")) return `<h2>${fmt(b.slice(3))}</h2>`;
      if (b.startsWith("# ")) return `<h2>${fmt(b.slice(2))}</h2>`;
      return `<p>${fmt(b)}</p>`;
    }).join("");
  }
  function fmt(s) {
    s = esc(s);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<em>$1</em>");
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    return s;
  }

  // ── syntax highlighting (a small hand-rolled scanner, no deps) ─────────
  const KW = {
    python: "def class return for in if elif else import from as with while try except finally raise yield lambda pass break continue global nonlocal async await del assert not and or is None True False self super print",
    js: "function return for in of if else while do switch case break continue const let var new class extends super import from export default async await try catch finally throw typeof instanceof this null undefined true false yield delete void static get set",
    go: "func return for range if else switch case break continue const var type struct interface map chan go defer package import nil true false string int error bool",
    rust: "fn return for in if else match while loop break continue const let mut struct enum impl trait use pub mod async await move ref self Some None Ok Err true false",
    default: "def fn func function class struct return if else elif for while in import from const let var public private static void int string bool true false null nil None True False new async await try catch",
  };
  const KWSET = {}; for (const k in KW) KWSET[k] = new Set(KW[k].split(" "));
  function langAlias(l) {
    l = (l || "").toLowerCase();
    if (["py", "python"].includes(l)) return "python";
    if (["js", "jsx", "ts", "tsx", "javascript", "typescript"].includes(l)) return "js";
    if (l === "go") return "go";
    if (["rs", "rust"].includes(l)) return "rust";
    return "default";
  }
  function langFor(file) {
    const e = (file || "").split(".").pop().toLowerCase();
    return langAlias({ py: "python", js: "js", jsx: "js", ts: "js", tsx: "js",
      go: "go", rs: "rust" }[e] || "default");
  }
  // `links` (a Set of indexed symbol names) turns identifiers into
  // go-to-definition links — ONLY in the navigator, and only for names that
  // actually have a definition: a clickable word that leads nowhere is a lie.
  function hl(code, lang, links) {
    const kw = KWSET[lang] || KWSET.default;
    const hash = lang === "python" || lang === "rust" || /^(sh|bash|yaml|toml|ruby|rb)$/.test(lang);
    const isId = (c) => c && /[A-Za-z0-9_$]/.test(c);
    const out = [];
    const push = (cls, txt) => out.push(cls ? `<span style="color:var(--syn-${cls})">${esc(txt)}</span>` : esc(txt));
    let i = 0; const n = code.length;
    while (i < n) {
      const c = code[i];
      if ((hash && c === "#") || (!hash && code.startsWith("//", i))) {
        let j = code.indexOf("\n", i); if (j < 0) j = n; push("cm", code.slice(i, j)); i = j; continue;
      }
      if (!hash && code.startsWith("/*", i)) { let j = code.indexOf("*/", i); j = j < 0 ? n : j + 2; push("cm", code.slice(i, j)); i = j; continue; }
      if (c === '"' || c === "'" || c === "`") {
        let j = i + 1; while (j < n && code[j] !== c) { if (code[j] === "\\") j++; j++; }
        j = Math.min(j + 1, n); push("str", code.slice(i, j)); i = j; continue;
      }
      if (/[0-9]/.test(c) && !isId(code[i - 1])) {
        let j = i; while (j < n && /[0-9._a-fA-FxX]/.test(code[j])) j++; push("num", code.slice(i, j)); i = j; continue;
      }
      if (isId(c)) {
        let j = i; while (j < n && isId(code[j])) j++;
        const w = code.slice(i, j);
        if (kw.has(w)) push("kw", w);
        else {
          let k = j; while (k < n && code[k] === " ") k++;
          const cls = code[k] === "(" ? "fn" : null;
          if (links && links.has(w))
            out.push(`<span ${cls ? `style="color:var(--syn-${cls})" ` : ""}data-sym="${esc(w)}">${esc(w)}</span>`);
          else push(cls, w);
        }
        i = j; continue;
      }
      push(null, c); i++;
    }
    return out.join("");
  }

  function emptyState(title, sub) {
    return `<div class="empty"><div class="query-icon" style="width:44px;height:44px">${st.view === "ask" ? ico.ask : st.view === "search" ? ico.prune : ico.search}</div>
      <div style="font-size:14px;font-weight:600;color:var(--text)">${esc(title)}</div>
      <div style="font-size:12.5px;max-width:440px;line-height:1.6">${esc(sub)}</div></div>`;
  }
  const emptyMini = (t) => `<div style="font-size:11px;color:var(--muted);padding:8px">${esc(t)}</div>`;

  // ── actions ──────────────────────────────────────────────────────────
  async function runSearch() {
    if (!st.q.trim() || !st.repo) return;
    st.loading = true; renderView();
    try { st.search = await api.prune(st.q.trim(), st.repo, st.rerank); }
    catch (e) { toast(e.message); }
    st.loading = false; renderView();
  }
  function runAsk() {
    if (!st.q.trim() || !st.repo) return;
    if (st.askCtl) { try { st.askCtl.abort(); } catch (e) {} st.askCtl = null; }
    const a = { agents: [], synthText: "", synthActive: false, done: null,
      retrieval: null, classified: null, model: st.model || null, bundle: null };
    st.ask = a; renderView();
    const body = { question: st.q.trim(), repo: st.repo, agents: "auto" };
    if (st.model) body.model = st.model;
    const ctl = api.askStream(body, (ev) => onAskEvent(a, ev));
    st.askCtl = ctl;
    ctl.done.then(() => { st.askCtl = null; askRender(); paintAskChip(); })
      .catch((e) => { toast("ask: " + e.message); st.askCtl = null; askRender(); paintAskChip(); });
    paintAskChip();
  }
  function paintAskChip() {
    const b = document.querySelector('[data-act="ask-run"]');
    if (b) b.innerHTML = (st.askCtl ? '<span class="spinner"></span><span>Running</span>' : ico.refresh + "<span>Run</span>");
  }

  function onAskEvent(a, ev) {
    const find = (id) => a.agents.find((x) => x.id === id);
    switch (ev.type) {
      case "retrieval": a.retrieval = ev; a.model = ev.model || a.model; break;
      case "cached": a.synthText = ev.text; a.done = { spans: 0, files: 0, retrieval_ms: ev.ms || 0, llm_ms: 0, n_dropped: 0 }; break;
      case "classified": a.classified = ev; break;
      case "planning": a.model = ev.model || a.model; break;
      case "plan": a.agents = ev.agents.map((p) => ({ id: p.id, label: p.label, sub_query: p.sub_query,
        files: (p.chunks || []).map((c) => c.file), status: "pending", stream: "", tool: null })); break;
      case "agent_start": { const x = find(ev.id); if (x) { x.status = "working"; x.files = ev.files || x.files; } break; }
      case "agent_delta": { const x = find(ev.id); if (x) { x.status = "working"; x.stream = (x.stream || "") + ev.text; } break; }
      case "agent_tool": { const x = find(ev.id); if (x) x.tool = { tool: ev.tool, args: ev.args }; break; }
      case "agent_done": { const x = find(ev.id); if (x) x.status = "done"; break; }
      case "agent_error": { const x = find(ev.id); if (x) { x.status = "done"; x.stream = "(failed: " + ev.msg + ")"; } break; }
      case "synthesis_start": a.synthActive = true; break;
      case "synthesis_delta": a.synthActive = true; a.synthText = (a.synthText || "") + ev.text; break;
      case "length": break;
      case "bundle": a.bundle = ev.text; a.bundleNote = ev.note; break;
      case "error": toast(ev.msg || "ask error"); break;
      case "done": a.done = ev; break;
      default: break;
    }
    askRender();
  }

  // ── add-repo flow (scan → progress) ──────────────────────────────────
  function openAdd() { st.overlay = "add"; st.add = { step: "path", path: "", scan: null, ignore: "", scanning: false, index: null, excluded: null, expanded: null }; renderOverlays(); bindOverlay(); }
  async function pickFolder() {
    // opens the OS-native folder dialog on the machine serve-api runs on
    let r; try { r = await api.fsPick(); } catch (e) { toast(e.message); return; }
    if (!r || r.cancelled) return;
    if (r.path) { st.add.path = r.path; doScan(); }   // census the picked folder
  }
  async function doScan() {
    const p = st.add.path.trim(); if (!p) return;
    st.add.scanning = true; renderOverlays(); bindOverlay();
    try {
      const rep = await api.scan(p);
      st.add.scan = rep;
      st.add.ignore = rep.proposed_ignore || "";
      st.add.tree = buildTree(rep.paths || []);
      st.add.excluded = new Set();
      st.add.expanded = new Set(Object.values(st.add.tree.children)
        .filter((c) => c.dir).map((c) => c.path));   // top-level dirs open
      st.add.step = "review";
    } catch (e) { toast(e.message); }
    st.add.scanning = false; renderOverlays(); bindOverlay();
  }

  // ── scan tree (choose what indexes / what's ignored) ─────────────────
  function buildTree(paths) {
    const root = { name: "", path: "", dir: true, count: 0, children: {} };
    for (const p of paths) {
      const parts = p.split("/");
      let node = root, acc = "";
      for (let i = 0; i < parts.length; i++) {
        const isFile = i === parts.length - 1;
        acc = acc ? acc + "/" + parts[i] : parts[i];
        if (!node.children[parts[i]]) node.children[parts[i]] =
          { name: parts[i], path: acc, dir: !isFile, count: 0, children: {} };
        const child = node.children[parts[i]];
        if (!isFile) child.count++;              // file counts toward each ancestor dir
        node = child;
      }
    }
    return root;
  }
  const sortedChildren = (node) => Object.values(node.children)
    .sort((a, b) => (a.dir === b.dir ? a.name.localeCompare(b.name) : a.dir ? -1 : 1));
  function isExcluded(path) {
    const ex = st.add.excluded;
    if (ex.has(path)) return true;
    for (const e of ex) if (path.startsWith(e + "/")) return true;
    return false;
  }
  const descendantExcluded = (path) => {
    for (const e of st.add.excluded) if (e.startsWith(path + "/")) return true;
    return false;
  };
  function includedCount() {
    return (st.add.scan.paths || []).filter((p) => !isExcluded(p)).length;
  }
  function excludedIgnoreLines() {
    // one .megabrainignore line per user-excluded node (dir → `path/`, file → `path`)
    const dirSet = new Set();
    (st.add.scan.paths || []).forEach((p) => { const parts = p.split("/"); for (let i = 1; i < parts.length; i++) dirSet.add(parts.slice(0, i).join("/")); });
    return [...st.add.excluded].map((e) => (dirSet.has(e) ? e + "/" : e));
  }
  function treeToggleExpand(path) {
    const ex = st.add.expanded;
    if (ex.has(path)) ex.delete(path); else ex.add(path);
    renderOverlays(); bindOverlay();
  }
  function treeToggleInclude(path) {
    const ex = st.add.excluded;
    if (ex.has(path)) ex.delete(path);
    else if (isExcluded(path)) { /* excluded via ancestor — re-include ancestor first */ toast("re-include the parent folder first"); return; }
    else ex.add(path);
    renderOverlays(); bindOverlay();
  }
  function treeRows(node, depth) {
    let out = "";
    for (const c of sortedChildren(node)) {
      const excl = isExcluded(c.path);
      const box = excl ? "unchecked" : (c.dir && descendantExcluded(c.path) ? "indeterminate" : "checked");
      const open = c.dir && st.add.expanded.has(c.path);
      out += `<div style="display:flex;align-items:center;gap:7px;padding:3px 6px;border-radius:6px;padding-left:${8 + depth * 16}px" class="tree-row">
        <button data-act="tree-check" data-path="${esc(c.path)}" style="display:flex;color:${excl ? "var(--muted)" : box === "indeterminate" ? "var(--accent)" : "var(--accent)"};flex-shrink:0" title="${excl ? "excluded" : "included"}">${ico[box]}</button>
        ${c.dir
          ? `<button data-act="tree-toggle" data-path="${esc(c.path)}" style="display:flex;color:var(--muted);flex-shrink:0;transition:transform 150ms;transform:rotate(${open ? 90 : 0}deg)">${ico.chevronR}</button>
             <span style="display:flex;color:${excl ? "var(--muted)" : "var(--accent)"};flex-shrink:0">${open ? ico.folderOpen : ico.folderL}</span>`
          : `<span style="width:14px;flex-shrink:0"></span><span style="display:flex;color:var(--muted);flex-shrink:0">${ico.fileL}</span>`}
        <span class="mono" style="font-size:12px;color:${excl ? "var(--muted)" : "var(--text)"};text-decoration:${excl ? "line-through" : "none"};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(c.name)}</span>
        ${c.dir ? `<span class="mono" style="font-size:10px;color:var(--muted);flex-shrink:0">${c.count}</span>` : ""}
      </div>`;
      if (open) out += treeRows(c, depth + 1);
    }
    return out;
  }
  async function doAddIndex() {
    const p = st.add.path.trim();
    // the ignore sent = the tree's user-excluded paths + any advanced patterns
    const ignore = [...excludedIgnoreLines(), st.add.ignore].filter((x) => x && x.trim()).join("\n");
    st.add.step = "index"; st.add.index = { i: 0, n: 0, file: "", changed: false, ticker: [], done: null };
    renderOverlays(); bindOverlay();
    try {
      await api.reposAdd(p, ignore);
    } catch (e) { toast(e.message); }
    const ctl = api.indexStream({ path: p, scan_filters: true }, (ev) => {
      const ix = st.add.index; if (!ix) return;
      if (ev.type === "file") {
        ix.i = ev.i; ix.n = ev.n; ix.file = ev.file; ix.changed = ev.changed;
        ix.ticker.unshift({ file: ev.file, changed: ev.changed }); ix.ticker = ix.ticker.slice(0, 5);
      } else if (ev.type === "done") { ix.done = ev; }
      else if (ev.type === "error") { toast("index: " + ev.msg); }
      paintIndex();
    });
    ctl.done.then(async () => { await refreshRepos(); paintIndex(); });
  }

  function renderOverlays() {
    const o = $("#overlays");
    let h = "";
    if (st.overlay === "settings") h += settingsPanel();
    if (st.overlay === "add") h += addModal();
    if (st.overlay === "reindex") h += reindexModal();
    o.innerHTML = h;
  }

  function reindexModal() {
    const rx = st.reindex; if (!rx) return "";
    let body;
    if (rx.index) {
      body = `<div style="padding:0 24px 22px">${indexProgress(rx.index, rx.repo)}</div>`;
    } else {
      const up = st.providers && st.providers.ollama && st.providers.ollama.up;
      const presets = [
        { m: "perplexity/pplx-embed-v1-0.6b", local: false, label: "pplx-embed · cloud (default)" },
        { m: "unclemusclez/jina-embeddings-v2-base-code:latest", local: true, label: "jina-code · local (ollama)" },
      ];
      const chips = presets.map((pr) => `<button class="chip mono" data-act="embed-preset" data-model="${esc(pr.m)}" data-local="${pr.local ? 1 : 0}" style="${rx.embed_model === pr.m ? "background:var(--accent-dim);border-color:var(--accent-bd);color:var(--accent)" : ""}${pr.local && !up ? ";opacity:.5" : ""}">${esc(pr.label)}</button>`).join("");
      body = `<div style="padding:0 24px 22px">
        <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin:6px 0 8px">EMBEDDING MODEL</div>
        <input id="rx-model" class="field mono" value="${esc(rx.embed_model)}" placeholder="embedding model slug"/>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">${chips}</div>
        <label style="display:flex;align-items:center;gap:8px;margin-top:14px;font-size:12px;cursor:pointer">
          <input type="checkbox" id="rx-local" ${rx.local ? "checked" : ""}/> local endpoint (ollama <span class="mono">:11434</span>)${up ? "" : ' <span style="color:var(--muted)">— start ollama first</span>'}</label>
        <div style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.5">Re-embeds every file with the new model (the query embedding switches to match, so search keeps working). The current index used <span class="mono" style="color:var(--text)">${esc(rx.current || "—")}</span>.</div>
        <button class="btn-primary" data-act="do-reindex" style="width:100%;margin-top:16px">${ico.refresh}<span>Re-index ${esc(rx.repo)}</span></button>
      </div>`;
    }
    return `<div class="overlay-bg" data-act="reindex-close-bg"><div class="card" data-stop>
      <div class="card-head">
        <div><div class="card-title">${rx.index ? "Re-indexing" : "Re-index with a different embedding"}</div>
          <div class="card-sub mono">${esc(rx.repo)}</div></div>
        <button class="close-btn" data-act="reindex-close">${ico.close}</button>
      </div>${body}</div></div>`;
  }

  function addModal() {
    const a = st.add; if (!a) return "";
    let body = "";
    if (a.step === "path") {
      body = `<div style="padding:0 24px 22px">
        <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin:6px 0 8px">REPOSITORY PATH</div>
        <div style="display:flex;gap:8px">
          <input id="add-path" class="field mono" placeholder="/Users/you/code/some-repo" value="${esc(a.path)}"/>
          <button class="btn-ghost" data-act="add-browse" style="margin:0;flex-shrink:0">${ico.folder}<span>Browse…</span></button>
          <button class="btn-primary" data-act="do-scan" ${a.scanning ? "disabled" : ""} style="flex-shrink:0">${a.scanning ? '<span class="spinner"></span>' : ico.search}<span>Scan</span></button>
        </div>
        <div style="font-size:11.5px;color:var(--muted);margin-top:10px;line-height:1.5">Pick a folder or paste a path — megabrain censuses it first, so you SEE exactly what indexes (and what's skipped, and why) before committing.</div>
      </div>`;
    } else if (a.step === "review") {
      const s = a.scan;
      const byExt = Object.entries(s.by_ext || {}).slice(0, 6).map(([e, n]) => `<span class="file-pill mono">${esc(e)} ${n}</span>`).join("");
      const inc = includedCount();
      const excl = s.would_index - inc;
      const reasons = {};
      (s.flagged || []).forEach((f) => reasons[f.reason] = (reasons[f.reason] || 0) + 1);
      const rsum = Object.entries(reasons).map(([r, n]) => `${n} ${r}`).join(" · ");
      const flags = (s.flagged || []).slice(0, 60).map((f) => `<div class="flag-row"><div class="flag-reason">${esc(f.reason)}</div><span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.path)}</span></div>`).join("");
      body = `<div style="padding:0 24px 22px;overflow-y:auto">
        <div style="display:flex;align-items:baseline;gap:10px;margin:4px 0 14px">
          <div style="font-size:28px;font-weight:700;letter-spacing:-.02em;color:var(--accent)">${inc}</div>
          <div style="font-size:12.5px;color:var(--muted)">files will index${excl ? ` · <span style="color:var(--text)">${excl}</span> excluded by you` : ""}</div>
          <div style="flex:1"></div><div style="display:flex;gap:6px;flex-wrap:wrap">${byExt}</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em">CHOOSE WHAT INDEXES</div>
          <div class="section-rule"></div>
          <button class="chip" data-act="tree-all">All</button>
          <button class="chip" data-act="tree-none">None</button>
        </div>
        <div style="max-height:300px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;padding:6px 4px;background:var(--panel2)">${treeRows(a.tree, 0)}${s.paths_truncated ? '<div class="flag-row" style="opacity:.6;padding-left:8px">… tree truncated (very large repo)</div>' : ""}</div>
        <details ${(s.flagged || []).length ? "" : "hidden"} style="margin-top:12px">
          <summary class="mono" style="cursor:pointer;font-size:11px;color:var(--muted);padding:4px 0">${(s.flagged || []).length} auto-skipped${rsum ? " — " + rsum : ""}</summary>
          <div style="max-height:150px;overflow-y:auto;margin-top:6px;border:1px solid var(--border);border-radius:6px;padding:4px">${flags}${(s.flagged || []).length > 60 ? `<div class="flag-row" style="opacity:.6">… +${s.flagged.length - 60} more</div>` : ""}</div>
        </details>
        <details style="margin-top:8px">
          <summary class="mono" style="cursor:pointer;font-size:11px;color:var(--muted);padding:4px 0">Advanced — extra .megabrainignore patterns</summary>
          <textarea id="add-ignore" class="field mono" style="margin-top:6px">${esc(a.ignore)}</textarea>
        </details>
        <div style="display:flex;gap:8px;margin-top:16px;align-items:center">
          <button class="btn-ghost" data-act="add-back" style="margin:0">Back</button>
          <div style="flex:1"></div>
          <button class="btn-primary" data-act="do-index" ${inc ? "" : "disabled"}>${ico.hardDrive}<span>Index ${inc} files</span></button>
        </div>
      </div>`;
    } else {
      body = `<div style="padding:0 24px 22px">${indexProgress(a.index, a.scan ? a.scan.name : "")}</div>`;
    }
    return `<div class="overlay-bg" data-act="add-close-bg"><div class="card" data-stop>
      <div class="card-head">
        <div><div class="card-title">${a.step === "index" ? "Indexing repo" : "Add repository"}</div>
          <div class="card-sub mono">${a.step === "path" ? "scan → review → index" : esc(a.path)}</div></div>
        <button class="close-btn" data-act="add-close">${ico.close}</button>
      </div>${body}</div></div>`;
  }

  function indexProgress(ix, name) {
    if (!ix) return "";
    const pct = ix.n ? Math.round((ix.i / ix.n) * 100) : 0;
    const indet = !ix.n && !ix.done;
    const done = ix.done;
    const ticker = (ix.ticker || []).map((t, k) => `<div class="flag-row" style="opacity:${1 - k * 0.18}">
      <div class="index-tag ${t.changed ? "chg" : ""}">${t.changed ? "CHG" : "CACHE"}</div>
      <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.file)}</span></div>`).join("");
    return `<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px">
        <div class="mono" style="font-size:11px;font-weight:600">${done ? "complete" : ix.n ? ix.i + " / " + ix.n : "starting…"}</div>
        <div class="mono" style="font-size:11px;color:var(--muted)">${done ? "100" : pct}%</div>
      </div>
      <div class="progress-track"><div class="progress-bar ${indet ? "indet" : ""}" style="width:${done ? 100 : pct}%"></div></div>
      ${!done ? `<div style="margin-top:14px;padding:10px 12px;background:var(--panel2);border:1px solid var(--border);border-radius:6px;display:flex;align-items:center;gap:10px">
        <div class="index-tag ${ix.changed ? "chg" : ""}">${ix.changed ? "CHG" : "SCAN"}</div>
        <span class="mono" style="font-size:11.5px;min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(ix.file || "discovering files…")}</span></div>
      <div style="margin-top:8px;display:flex;flex-direction:column;gap:3px">${ticker}</div>`
      : `<div style="margin-top:14px;display:flex;flex-wrap:wrap;gap:8px">
          <div class="file-pill mono"><b style="color:var(--text)">${done.files}</b> files</div>
          <div class="file-pill mono"><b style="color:var(--accent)">${done.changed}</b> changed</div>
          <div class="file-pill mono">${done.unchanged} cached</div>
          <div class="file-pill mono"><b style="color:var(--text)">${done.new_chunks}</b> chunks</div>
          <div class="file-pill mono">${done.seconds}s</div>
        </div>
        <button class="btn-primary" data-act="add-done" style="width:100%;margin-top:16px">${ico.check}<span>Open ${esc(name)}</span></button>`}`;
  }

  function repaintProgress(ix, name) {
    const card = $("#overlays .card"); if (!card) return;
    const bodyDiv = card.querySelector(".card-head").nextElementSibling;
    if (bodyDiv) bodyDiv.innerHTML = indexProgress(ix, name);
    bindOverlay();
  }
  function paintIndex() {
    const a = st.add; if (!a || a.step !== "index") return;
    repaintProgress(a.index, a.scan ? a.scan.name : "");
  }
  function paintReindex() {
    const rx = st.reindex; if (!rx || !rx.index) return;
    repaintProgress(rx.index, rx.repo);
  }

  function openReindex() {
    const r = st.repos.find((x) => x.name === st.repo) || {};
    st.overlay = "reindex";
    st.reindex = { repo: st.repo, current: r.embed_model || "",
      embed_model: r.embed_model || "perplexity/pplx-embed-v1-0.6b",
      local: false, index: null };
    renderOverlays(); bindOverlay();
  }
  async function doReindex() {
    const rx = st.reindex;
    if (!rx.embed_model.trim()) { toast("pick an embedding model"); return; }
    rx.index = { i: 0, n: 0, file: "", changed: false, ticker: [], done: null };
    renderOverlays(); bindOverlay();
    const body = { repo: rx.repo, force: true, scan_filters: false, embed_model: rx.embed_model.trim() };
    if (rx.local) body.embed_base = "http://localhost:11434/v1";
    const ctl = api.indexStream(body, (ev) => {
      const ix = st.reindex && st.reindex.index; if (!ix) return;
      if (ev.type === "file") {
        ix.i = ev.i; ix.n = ev.n; ix.file = ev.file; ix.changed = ev.changed;
        ix.ticker.unshift({ file: ev.file, changed: ev.changed }); ix.ticker = ix.ticker.slice(0, 5);
      } else if (ev.type === "done") { ix.done = ev; }
      else if (ev.type === "error") { toast("reindex: " + ev.msg); }
      paintReindex();
    });
    ctl.done.then(async () => { await refreshRepos(); paintReindex(); })
      .catch((e) => toast("reindex: " + e.message));
  }

  async function doSelect(provider, model) {
    st.provider = provider; st.model = model || "";
    ls.set("mb-provider", provider); ls.set("mb-model", st.model);
    try { st.providers = await api.selectProvider(provider, model || undefined); }
    catch (e) { toast(e.message); }
    render();                            // updates the topbar chip + settings
  }
  async function doStartOllama() {
    toast("starting ollama serve…");
    try { st.providers = await api.startOllama(); }
    catch (e) { toast(e.message); }
    renderOverlays(); bindOverlay();
  }

  // ── settings ─────────────────────────────────────────────────────────
  function settingsPanel() {
    const p = st.providers;
    const cards = p ? providerCards(p) : `<div style="padding:20px;display:flex;justify-content:center"><span class="spinner"></span></div>`;
    return `<div class="overlay-bg" data-act="settings-bg" style="justify-content:flex-end"></div>
      <aside class="settings-panel">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:22px 24px 18px;border-bottom:1px solid var(--border)">
          <div><div class="card-title">Settings &amp; providers</div><div class="card-sub">Local-first. Selections persist across sessions.</div></div>
          <button class="close-btn" data-act="settings-close">${ico.close}</button>
        </div>
        <div style="padding:22px 24px;overflow-y:auto;flex:1">
          <div class="mono" style="font-size:10.5px;color:var(--muted);letter-spacing:.08em;margin-bottom:12px">PROVIDERS</div>
          <div style="display:flex;flex-direction:column;gap:10px">${cards}</div>
          ${p ? indexSection() : ""}
        </div>
      </aside>`;
  }

  function providerCards(p) {
    const cur = activeProvider();
    const defs = [
      { key: "claude", name: "Claude SDK", initial: "C", info: p.claude,
        up: p.claude.available, reason: p.claude.available ? "claude_agent_sdk detected · subscription or ANTHROPIC_API_KEY" : "claude_agent_sdk not installed",
        chips: ["haiku", "sonnet", "opus"] },
      { key: "openrouter", name: "OpenRouter", initial: "O", info: p.openrouter,
        up: p.openrouter.available, reason: p.openrouter.available ? "OPENROUTER_API_KEY set · 300+ models" : "no OPENROUTER_API_KEY",
        chips: ["google/gemini-3.1-flash-lite-preview", "qwen/qwen3-coder", "google/gemini-3-flash-preview"] },
      { key: "ollama", name: "Ollama", initial: "o", info: p.ollama,
        up: p.ollama.up, reason: p.ollama.up ? `${(p.ollama.models || []).length} local model(s) · fully offline` :
          p.ollama.installed ? "installed · server is down on :11434" : "no server on :11434 · not installed",
        // chat chips only — hide embedding models (they can't narrate; they're
        // the reindex embedding pickers instead)
        chips: (p.ollama.models || []).filter((m) => !/embed|jina/i.test(m)),
        noChat: p.ollama.up && !(p.ollama.models || []).some((m) => !/embed|jina/i.test(m)) },
    ];
    return defs.map((d) => {
      const sel = cur === d.key;
      const activate = d.up && !sel
        ? `<button class="chip" data-act="use-provider" data-provider="${d.key}" style="flex-shrink:0">Use</button>` : "";
      const models = d.chips.map((c) => {
        const on = sel && (st.model === c || (!st.model && p.active.model === c));
        return `<button class="chip mono" data-act="model" data-provider="${d.key}" data-model="${esc(c)}" style="${on ? "background:var(--accent-dim);border-color:var(--accent-bd);color:var(--accent)" : ""}">${esc(labelModel(c, d.key))}</button>`;
      }).join("");
      const ollamaStart = d.key === "ollama" && !d.up && d.info.installed
        ? `<div style="padding:0 16px 14px;border-top:1px solid var(--border)"><button class="btn-primary" data-act="start-ollama" style="width:100%;margin-top:12px">${ico.refresh}<span>Start <span class="mono">ollama serve</span></span></button></div>` : "";
      return `<div class="prov-card ${sel ? "sel" : ""}">
        <div style="display:flex;align-items:center;gap:12px;padding:14px 16px">
          <div class="prov-avatar">${d.initial}</div>
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:8px">
              <div style="font-size:13px;font-weight:600">${d.name}</div>
              <div class="status-chip ${d.up ? "" : "off"}"><div style="width:5px;height:5px;border-radius:50%;background:currentColor"></div>${d.up ? "detected" : "not detected"}</div>
            </div>
            <div style="font-size:11.5px;color:var(--muted);margin-top:4px">${esc(d.reason)}</div>
          </div>
          ${sel ? '<div class="active-chip">ACTIVE</div>' : activate}
        </div>
        ${d.up && d.chips.length ? `<div style="padding:0 16px 14px;border-top:1px solid var(--border)">
          <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin:12px 0 8px">MODEL</div>
          ${d.key === "openrouter" ? `<div style="display:flex;gap:6px;margin-bottom:8px"><input id="or-model" class="field mono" placeholder="any openrouter slug — ⏎ to use" value="${esc(cur === "openrouter" ? (st.model || "") : "")}"/></div>` : ""}
          <div style="display:flex;flex-wrap:wrap;gap:6px">${models}</div>
        </div>` : d.noChat ? `<div style="padding:0 16px 14px;border-top:1px solid var(--border)">
          <div style="font-size:11.5px;color:var(--muted);margin-top:12px;line-height:1.5">Only embedding models are pulled (great for indexing). For local <b style="color:var(--text)">narration</b>, pull a chat model: <span class="mono" style="color:var(--text)">ollama pull gemma3:1b</span></div>
        </div>` : ollamaStart}
      </div>`;
    }).join("");
  }
  function labelModel(c, key) {
    if (key === "openrouter") {
      if (c.includes("flash-lite")) return "gemini flash-lite · fastest";
      if (c.includes("qwen")) return "qwen3-coder · open";
      if (c.includes("gemini-3-flash")) return "gemini flash · default";
    }
    return shortModel(c);
  }

  // INDEX section — which embedding the active repo used + re-index with another
  function indexSection() {
    const r = st.repos.find((x) => x.name === st.repo);
    if (!r) return "";
    return `<div class="mono" style="font-size:10.5px;color:var(--muted);letter-spacing:.08em;margin:26px 0 12px">INDEX</div>
      <div class="prov-card" style="padding:14px 16px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
          <div style="font-size:13px;font-weight:600">${esc(r.name)}</div>
          <div class="file-pill mono">${r.files} files · ${r.chunks} chunks</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:11.5px;color:var(--muted)">
          <span>embedding</span>
          <span class="mono" style="color:var(--text)">${esc(r.embed_model || "—")}</span>
        </div>
        <button class="chip" data-act="reindex-open" style="margin-top:12px">${ico.refresh} Re-index with a different embedding</button>
      </div>`;
  }

  // ── events ───────────────────────────────────────────────────────────
  function bind() { renderView(); }
  function bindView() { const q = $("#q"); if (q) { q.oninput = (e) => { st.q = e.target.value; }; } }
  function bindOverlay() {
    const ap = $("#add-path"); if (ap) ap.oninput = (e) => { st.add.path = e.target.value; };
    const ai = $("#add-ignore"); if (ai) ai.oninput = (e) => { st.add.ignore = e.target.value; };
    const om = $("#or-model");
    if (om) om.onkeydown = (e) => { if (e.key === "Enter") { const v = e.target.value.trim(); if (v) doSelect("openrouter", v); } };
    const rm = $("#rx-model"); if (rm) rm.oninput = (e) => { st.reindex.embed_model = e.target.value; };
    const rl = $("#rx-local"); if (rl) rl.onchange = (e) => { st.reindex.local = e.target.checked; };
  }

  document.addEventListener("click", (e) => {
    // go-to-definition: identifiers inside the navigator's code area only
    const sym = e.target.closest("[data-sym]");
    if (sym && e.target.closest("#vcode")) { viewerJumpSymbol(sym.dataset.sym); return; }
    const t = e.target.closest("[data-act]"); if (!t) return;
    const act = t.dataset.act;
    if (act === "view") { st.view = t.dataset.id; render(); }
    else if (act === "repo") {
      if (t.dataset.cold) { loadColdRepo(t.dataset.name); return; }
      st.repo = t.dataset.name; clearRepoState(); render();
    }
    else if (act === "rerank-toggle") { st.rerank = !st.rerank; ls.set("mb-rerank", st.rerank ? "1" : "0"); if (st.q.trim()) runSearch(); else renderView(); }
    else if (act === "gopen") { openGraphNode(t.dataset.file); }
    else if (act === "gclear") { st.graphNode = null; st.graphSel = null; paintPanel(); }
    else if (act === "gcom") {
      st.graphFocusCom = +t.dataset.id; st.gmode = "com";
      st.graphView = null; st.graphNode = null; st.graphSel = null;
      renderView();
    }
    else if (act === "goverview") {
      st.gmode = "overview"; st.graphFocusCom = null; st.graphPath = null;
      st.gsub = null; st.graphNode = null; st.graphSel = null; st.graphView = null;
      st.gplay = null;
      renderView();
    }
    else if (act === "gplay") { runConnection(); }
    else if (act === "vconn-prev") { const v = st.viewer; if (v && v.conn && v.conn.k > 0) viewerConnGo(v.conn, v.conn.k - 1); }
    else if (act === "vconn-next") { const v = st.viewer; if (v && v.conn && v.conn.k < v.conn.steps.length - 1) viewerConnGo(v.conn, v.conn.k + 1); }
    else if (act === "viewer-close") { viewerClose(); }
    else if (act === "vback") { viewerBack(); }
    else if (act === "vgoto") { const v = st.viewer; if (v) { v.focus = +t.dataset.line; v.hiLines = new Set([v.focus]); paintViewer(); } }
    else if (act === "vopen") {
      viewerLoad(t.dataset.file, +(t.dataset.line || 1),
                 new Set(t.dataset.line ? [+t.dataset.line] : []), null, true);
    }
    else if (act === "gzoom-in") { zoomBy(1.25); }
    else if (act === "gzoom-out") { zoomBy(0.8); }
    else if (act === "gzoom-fit") { if (SIM) { SIM.userView = false; fitAll(); } }
    else if (act === "theme") { st.theme = st.theme === "dark" ? "light" : "dark"; ls.set("mb-theme", st.theme); render(); }
    else if (act === "settings") { st.overlay = "settings"; renderOverlays(); loadProviders(); }
    else if (act === "settings-close" || act === "settings-bg") { st.overlay = null; renderOverlays(); }
    else if (act === "ask-run") { runAsk(); }
    else if (act === "add-open") { openAdd(); }
    else if (act === "add-close" || act === "add-close-bg") { if (act === "add-close-bg" && !e.target.classList.contains("overlay-bg")) return; st.overlay = null; st.add = null; renderOverlays(); }
    else if (act === "do-scan") { doScan(); }
    else if (act === "add-browse") { pickFolder(); }
    else if (act === "add-back") { st.add.step = "path"; renderOverlays(); bindOverlay(); }
    else if (act === "tree-toggle") { treeToggleExpand(t.dataset.path); }
    else if (act === "tree-check") { treeToggleInclude(t.dataset.path); }
    else if (act === "tree-all") { st.add.excluded.clear(); renderOverlays(); bindOverlay(); }
    else if (act === "tree-none") { st.add.excluded = new Set(sortedChildren(st.add.tree).map((c) => c.path)); renderOverlays(); bindOverlay(); }
    else if (act === "do-index") { doAddIndex(); }
    else if (act === "add-done") { st.overlay = null; st.repo = st.add.scan ? st.add.scan.name : st.repo; st.add = null; render(); }
    else if (act === "model") { doSelect(t.dataset.provider, t.dataset.model); }
    else if (act === "use-provider") { doSelect(t.dataset.provider, ""); }
    else if (act === "start-ollama") { doStartOllama(); }
    else if (act === "reindex-open") { openReindex(); }
    else if (act === "reindex-close") { st.overlay = "settings"; st.reindex = null; renderOverlays(); bindOverlay(); }
    else if (act === "reindex-close-bg") { if (!e.target.classList.contains("overlay-bg")) return; if (st.reindex && st.reindex.index && !st.reindex.index.done) return; st.overlay = "settings"; st.reindex = null; renderOverlays(); bindOverlay(); }
    else if (act === "do-reindex") { doReindex(); }
    else if (act === "embed-preset") { st.reindex.embed_model = t.dataset.model; st.reindex.local = t.dataset.local === "1"; renderOverlays(); bindOverlay(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/input|textarea/i.test((document.activeElement || {}).tagName || "")) {
      e.preventDefault(); const q = $("#q"); if (q) q.focus();
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault(); cycleRepo();
    }
    if (e.key === "Enter" && document.activeElement && document.activeElement.id === "q") {
      st.q = document.activeElement.value;
      if (st.view === "search") runSearch();
      else if (st.view === "graph") runGraphQuery(); else runAsk();
    }
    if (e.key === "Enter" && document.activeElement && document.activeElement.id === "add-path") { doScan(); }
    if (e.key === "Escape") {
      if (st.viewer) { viewerClose(); return; }
      if (st.overlay) { st.overlay = null; st.add = null; renderOverlays(); }
    }
    if (st.viewer && st.viewer.conn && !/input|textarea/i.test((document.activeElement || {}).tagName || "")) {
      if (e.key === "ArrowRight" && st.viewer.conn.k < st.viewer.conn.steps.length - 1)
        viewerConnGo(st.viewer.conn, st.viewer.conn.k + 1);
      if (e.key === "ArrowLeft" && st.viewer.conn.k > 0)
        viewerConnGo(st.viewer.conn, st.viewer.conn.k - 1);
    }
  });

  function clearRepoState() {
    st.search = st.ask = null;
    st.graph = null; st.graphNode = null; st.graphSel = null;
    st.graphPath = null; st.graphPos = {};
    st.graphFocusCom = null; st.graphView = null;
    st.gmode = "overview"; st.gsub = null;
  }
  function cycleRepo() {
    const warm = st.repos.filter((r) => r.loaded !== false);
    if (warm.length < 2) return;
    const i = warm.findIndex((r) => r.name === st.repo);
    st.repo = warm[(i + 1) % warm.length].name;
    clearRepoState(); render();
  }
  async function loadColdRepo(name) {
    const r = st.repos.find((x) => x.name === name); if (!r) return;
    toast("loading " + name + "…");
    try {
      await api.reposAdd(r.root, "");     // registers a warm session (index exists)
      await refreshRepos();
      st.repo = name; clearRepoState(); render();
    } catch (e) { toast(e.message); }
  }
  // ── data loading ─────────────────────────────────────────────────────
  async function refreshRepos() {
    try {
      st.repos = await api.repos();
      if (!st.repo && st.repos[0])
        st.repo = (st.repos.find((r) => r.loaded !== false) || st.repos[0]).name;
    }
    catch (e) { toast("repos: " + e.message); }
    render();
  }
  async function loadProviders() {
    if (!st.providers) {
      try { st.providers = await api.providers(); } catch (e) { toast("providers: " + e.message); }
    }
    if (st.overlay === "settings") { renderOverlays(); bindOverlay(); }
    else render();                      // reflect the active provider in the topbar chip
  }

  // ── boot ─────────────────────────────────────────────────────────────
  // debug handle (state is IIFE-scoped; this is the only window into it)
  window.__mb = { st, get SIM() { return SIM; } };
  render();
  refreshRepos();
  loadProviders();          // fill the topbar chip with the active provider
})();
