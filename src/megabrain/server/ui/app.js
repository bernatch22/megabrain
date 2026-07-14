/* app.js — megabrain studio SPA (vanilla, no framework).
 * Renders the rail + topbar + the three views (search / prune / ask), the
 * settings slide-over, and the add-repo flow (scan census → live indexing
 * progress). All backend access is through window.api (see api.js). */
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
  };

  // ── state ────────────────────────────────────────────────────────────
  const st = {
    theme: ls.get("mb-theme", "dark"),
    view: "search",
    repos: [], repo: null,
    providers: null,
    provider: ls.get("mb-provider", ""), model: ls.get("mb-model", ""),
    q: "",
    search: null, openFile: null, chunks: {}, loading: false,
    prune: null,
    ask: null, askCtl: null,
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
      const lang = /\.py|python/i.test(r.root) ? "py" : (r.name[0] || "?");
      return `<button class="repo-row ${active ? "active" : ""}" data-act="repo" data-name="${esc(r.name)}">
        <div style="display:flex;align-items:center;gap:9px;min-width:0;flex:1">
          <div class="repo-dot">${esc((r.name[0] || "?"))}</div>
          <div style="min-width:0;flex:1">
            <div class="repo-name">${esc(r.name)}</div>
            <div class="repo-meta mono">${r.files} files · ${r.chunks} chunks</div>
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
    const tabs = [["search", "Search"], ["prune", "Prune"], ["ask", "Ask"]].map(([id, l]) =>
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
    if (st.view === "search") v.innerHTML = viewSearch();
    else if (st.view === "prune") v.innerHTML = viewPrune();
    else v.innerHTML = viewAsk();
    bindView();
  }

  function queryBar(placeholder, right) {
    return `<div class="query-wrap">
      <div class="query-icon">${st.view === "prune" ? ico.prune : st.view === "ask" ? ico.ask : ico.search}</div>
      <input id="q" class="query-input" value="${esc(st.q)}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false"/>
      ${right || ""}
    </div>`;
  }

  function viewSearch() {
    const r = st.search;
    const badge = st.loading ? `<div class="badge"><span class="spinner"></span></div>`
      : r ? `<div class="badge"><div class="dotlive" style="animation:mb-pulse 1.6s infinite"></div><span>${r.ms}ms</span><span style="opacity:0.5">·</span><span>no LLM</span></div>` : "";
    let body = "";
    if (r) {
      const scanned = (r.tier1 || []).reduce((a, t) => a + (t.chunks ? t.chunks.length : 0), 0);
      body = `<div class="stats-row">
          <div><b>${r.tier1.length}</b> core files</div><div class="sdot"></div>
          <div><b>${r.tier2.length}</b> related via graph</div><div class="sdot"></div>
          <div><b>${scanned}</b> chunks in core</div>
        </div>
        <div class="section-head"><div class="section-label mono">CORE</div><div class="section-rule"></div><div class="mono" style="font-size:10.5px;color:var(--muted);letter-spacing:.06em">RANKED · TIER 1</div></div>
        <div style="display:flex;flex-direction:column;gap:10px">${r.tier1.map(tier1Card).join("")}</div>`;
      if (r.tier2.length) body += `<div class="section-head" style="margin-top:36px"><div class="section-label mono">RELATED</div><div class="section-rule"></div><div class="mono" style="font-size:10.5px;color:var(--muted);letter-spacing:.06em">VIA IMPORT GRAPH · TIER 2</div></div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px">${r.tier2.map(tier2Card).join("")}</div>`;
    } else {
      body = emptyState("Search returns every related file in ~200ms — pure vector math, no LLM.", "Ask a question or search for code, then hit ⏎.");
    }
    return `<div class="view-wrap mb-fade" style="max-width:1080px">${queryBar("Ask a question or search for code…", badge)}${body}</div>`;
  }

  function tier1Card(f) {
    const hot = f.score >= 0.85;
    const open = st.openFile === f.file;
    const summary = (f.chunks && f.chunks[0] && f.chunks[0].name) ||
      (f.symbols && f.symbols[0] && f.symbols[0].name) || f.file.split("/").pop();
    let inner = "";
    if (open) {
      const ch = st.chunks[f.file];
      if (!ch) inner = `<div style="padding:20px;display:flex;justify-content:center"><span class="spinner"></span></div>`;
      else inner = chunkHeatmap(ch);
    }
    return `<div class="file-card ${open ? "open" : ""}">
      <button class="file-head" data-act="file" data-file="${esc(f.file)}">
        <div style="display:flex;align-items:center;gap:12px;min-width:0;flex:1">
          <div class="score-bar" style="background:${hot ? "var(--accent)" : "var(--muted)"};opacity:${hot ? 1 : 0.4};box-shadow:${hot ? "0 0 6px var(--accent)" : "none"}"></div>
          <div style="min-width:0;flex:1">
            <div class="file-path mono">${esc(f.file)}</div>
            <div class="file-summary">${esc(summary)}</div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:14px;flex-shrink:0">
          <div class="mono" style="font-size:11px;font-weight:600;color:${hot ? "var(--accent)" : "var(--text)"}">${f.score.toFixed(2)}</div>
          <div class="mono" style="font-size:10.5px;color:var(--muted)">${f.chunks ? f.chunks.length : 0} chunks</div>
          <span class="chev" style="transform:rotate(${open ? 180 : 0}deg)">${ico.chev}</span>
        </div>
      </button>
      ${open ? `<div style="padding:0 16px 16px;border-top:1px solid var(--border)">${inner}</div>` : ""}
    </div>`;
  }

  function chunkHeatmap(ch) {
    const chunks = ch.chunks || [];
    const lang = langFor(ch.file);
    const heat = chunks.map((c) => {
      const sel = !!c.selected;
      const op = sel ? 0.95 : Math.max(0.12, Math.min(1, c.score || 0.2));
      return `<div class="heat ${sel ? "sel" : ""}" style="opacity:${op}" title="${esc(c.name || "chunk")} · ${(c.score || 0).toFixed(2)}"></div>`;
    }).join("");
    const bodies = chunks.filter((c) => c.selected).slice(0, 4).map((c) => `
      <div class="chunk">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px">
          <div style="display:flex;align-items:center;gap:10px">
            <div class="kind-pill on">${esc(c.kind || "chunk")}</div>
            <div class="mono" style="font-size:12px;font-weight:500">${esc(c.name || "")}</div>
            <div class="mono" style="font-size:10.5px;color:var(--muted)">L${c.start_line}–${c.end_line}</div>
          </div>
          <div class="mono" style="font-size:10.5px;font-weight:600;color:${(c.score || 0) >= 0.85 ? "var(--accent)" : "var(--text)"}">${(c.score || 0).toFixed(2)}</div>
        </div>
        <pre class="mono">${hl(c.text || "", lang)}</pre>
      </div>`).join("");
    return `<div style="display:flex;gap:14px;align-items:center;padding:14px 0 12px">
        <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em">CHUNK HEATMAP</div>
        <div style="flex:1;display:flex;gap:3px;align-items:center">${heat}</div>
        <div style="display:flex;align-items:center;gap:6px;font-size:10px;color:var(--muted)" class="mono">
          <div class="heat sel" style="width:8px;height:8px;flex:none;border-radius:2px"></div>signal
          <div class="heat" style="width:8px;height:8px;flex:none;border-radius:2px;margin-left:6px"></div>noise
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">${bodies || '<div style="font-size:11px;color:var(--muted);padding:4px">No chunk was selected as signal for this query.</div>'}</div>`;
  }

  function tier2Card(t) {
    return `<div class="t2-card">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
        <div class="file-path mono" style="font-size:12px">${esc(t.file)}</div>
        <div class="mono" style="font-size:10.5px;font-weight:600;color:var(--muted);flex-shrink:0">${(t.score || 0).toFixed(2)}</div>
      </div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:10.5px;color:var(--muted)">
        ${ico.link}<span>via <span class="mono" style="color:var(--text)">${t.via_flow ? "flow cache" : t.via_graph ? "import graph" : "content"}</span></span>
      </div>
    </div>`;
  }

  function viewPrune() {
    const r = st.prune;
    const right = st.loading ? `<div class="badge"><span class="spinner"></span></div>`
      : r ? `<div class="badge"><b style="color:var(--accent)">${r.kept}</b><span>kept</span><span style="opacity:.5">·</span><span style="color:var(--muted)">${r.pruned} pruned</span></div>` : "";
    let body;
    if (r) {
      body = `<div class="stats-row">
          <div><b>${r.scanned}</b> chunks scanned</div><div class="sdot"></div>
          <div><span style="color:var(--muted)">retrieval</span> <b class="mono">${r.ms}ms</b></div>
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
      <div class="mono" style="font-size:10.5px;color:var(--muted);padding:0 12px 6px">${esc(s.file)}<span style="opacity:.55">:L${s.start_line}–${s.end_line}</span></div>
      ${s.text ? `<pre class="mono" style="font-size:11px;padding:8px 12px 10px;background:var(--code);border-top:1px solid var(--border);overflow-x:auto">${hl(s.text, langFor(s.file))}</pre>` : ""}
    </div>`;
  }
  function noiseRow(n) {
    return `<div class="flag-row" style="justify-content:space-between;border:1px solid var(--border);border-radius:5px;opacity:.72">
      <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1">
        <div class="kind-pill">${esc(n.kind || "")}</div>
        <div class="mono" style="min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(n.name || "")} <span style="opacity:.55">· ${esc(n.file)}</span></div>
      </div>
      <div class="mono" style="flex-shrink:0">${(n.score || 0).toFixed(2)}</div>
    </div>`;
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
          ${(a.files || []).slice(0, 2).map((f) => `<div class="file-pill mono">${esc(f.split("/").pop())}</div>`).join("")}
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
  function hl(code, lang) {
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
        else { let k = j; while (k < n && code[k] === " ") k++; push(code[k] === "(" ? "fn" : null, w); }
        i = j; continue;
      }
      push(null, c); i++;
    }
    return out.join("");
  }

  function emptyState(title, sub) {
    return `<div class="empty"><div class="query-icon" style="width:44px;height:44px">${st.view === "ask" ? ico.ask : st.view === "prune" ? ico.prune : ico.search}</div>
      <div style="font-size:14px;font-weight:600;color:var(--text)">${esc(title)}</div>
      <div style="font-size:12.5px;max-width:440px;line-height:1.6">${esc(sub)}</div></div>`;
  }
  const emptyMini = (t) => `<div style="font-size:11px;color:var(--muted);padding:8px">${esc(t)}</div>`;

  // ── actions ──────────────────────────────────────────────────────────
  async function runSearch() {
    if (!st.q.trim() || !st.repo) return;
    st.loading = true; st.openFile = null; st.chunks = {}; renderView();
    try { st.search = await api.search(st.q.trim(), st.repo); }
    catch (e) { toast(e.message); }
    st.loading = false; renderView();
  }
  async function runPrune() {
    if (!st.q.trim() || !st.repo) return;
    st.loading = true; renderView();
    try { st.prune = await api.prune(st.q.trim(), st.repo); }
    catch (e) { toast(e.message); }
    st.loading = false; renderView();
  }
  async function toggleFile(file) {
    if (st.openFile === file) { st.openFile = null; renderView(); return; }
    st.openFile = file; renderView();
    if (!st.chunks[file]) {
      try { st.chunks[file] = await api.chunks(file, st.q.trim(), st.repo); }
      catch (e) { toast(e.message); st.chunks[file] = { chunks: [] }; }
      if (st.openFile === file) renderView();
    }
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
  function openAdd() { st.overlay = "add"; st.add = { step: "path", path: "", scan: null, ignore: "", scanning: false, index: null }; renderOverlays(); bindOverlay(); }
  async function doScan() {
    const p = st.add.path.trim(); if (!p) return;
    st.add.scanning = true; renderOverlays(); bindOverlay();
    try {
      const rep = await api.scan(p);
      st.add.scan = rep; st.add.ignore = rep.proposed_ignore || ""; st.add.step = "review";
    } catch (e) { toast(e.message); }
    st.add.scanning = false; renderOverlays(); bindOverlay();
  }
  async function doAddIndex() {
    const p = st.add.path.trim();
    st.add.step = "index"; st.add.index = { i: 0, n: 0, file: "", changed: false, ticker: [], done: null };
    renderOverlays(); bindOverlay();
    try {
      await api.reposAdd(p, st.add.ignore);
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
          <button class="btn-primary" data-act="do-scan" ${a.scanning ? "disabled" : ""} style="flex-shrink:0">${a.scanning ? '<span class="spinner"></span>' : ico.search}<span>Scan</span></button>
        </div>
        <div style="font-size:11.5px;color:var(--muted);margin-top:10px;line-height:1.5">megabrain will census the repo first — you SEE exactly what indexes (and what's skipped, and why) before committing.</div>
      </div>`;
    } else if (a.step === "review") {
      const s = a.scan;
      const byExt = Object.entries(s.by_ext || {}).slice(0, 8).map(([e, n]) => `<span class="file-pill mono">${esc(e)} ${n}</span>`).join("");
      const dirs = (s.top_dirs || []).slice(0, 5).map((d) => `<div class="flag-row" style="justify-content:space-between"><span class="mono" style="color:var(--text)">${esc(d.dir)}</span><span class="mono">${d.files} files · ${(d.bytes / 1024 | 0)} KB</span></div>`).join("");
      const reasons = {};
      (s.flagged || []).forEach((f) => reasons[f.reason] = (reasons[f.reason] || 0) + 1);
      const rsum = Object.entries(reasons).map(([r, n]) => `${n} ${r}`).join(" · ");
      const flags = (s.flagged || []).slice(0, 40).map((f) => `<div class="flag-row"><div class="flag-reason">${esc(f.reason)}</div><span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.path)}</span></div>`).join("");
      body = `<div style="padding:0 24px 22px;overflow-y:auto">
        <div style="display:flex;align-items:baseline;gap:10px;margin:4px 0 12px">
          <div style="font-size:26px;font-weight:700;letter-spacing:-.02em">${s.would_index}</div>
          <div style="font-size:12.5px;color:var(--muted)">files would index</div>
          <div style="flex:1"></div><div style="display:flex;gap:6px;flex-wrap:wrap">${byExt}</div>
        </div>
        <div style="background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:14px">${dirs}</div>
        <details ${(s.flagged || []).length ? "" : "hidden"}>
          <summary class="mono" style="cursor:pointer;font-size:11px;color:var(--muted);padding:4px 0">${(s.flagged || []).length} files skipped${rsum ? " — " + rsum : ""}</summary>
          <div style="max-height:150px;overflow-y:auto;margin-top:6px;border:1px solid var(--border);border-radius:6px;padding:4px">${flags}${(s.flagged || []).length > 40 ? `<div class="flag-row" style="opacity:.6">… +${s.flagged.length - 40} more</div>` : ""}</div>
        </details>
        <div class="mono" style="font-size:10px;color:var(--muted);letter-spacing:.06em;margin:16px 0 8px">.megabrainignore (editable — applied before indexing)</div>
        <textarea id="add-ignore" class="field mono">${esc(a.ignore)}</textarea>
        <div style="display:flex;gap:8px;margin-top:14px">
          <button class="btn-ghost" data-act="add-back" style="margin:0">Back</button>
          <div style="flex:1"></div>
          <button class="btn-primary" data-act="do-index">${ico.folder}<span>Index ${s.would_index} files</span></button>
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
    const t = e.target.closest("[data-act]"); if (!t) return;
    const act = t.dataset.act;
    if (act === "view") { st.view = t.dataset.id; render(); }
    else if (act === "repo") { st.repo = t.dataset.name; st.search = st.prune = st.ask = null; st.openFile = null; render(); }
    else if (act === "theme") { st.theme = st.theme === "dark" ? "light" : "dark"; ls.set("mb-theme", st.theme); render(); }
    else if (act === "settings") { st.overlay = "settings"; renderOverlays(); loadProviders(); }
    else if (act === "settings-close" || act === "settings-bg") { st.overlay = null; renderOverlays(); }
    else if (act === "file") { toggleFile(t.dataset.file); }
    else if (act === "ask-run") { runAsk(); }
    else if (act === "add-open") { openAdd(); }
    else if (act === "add-close" || act === "add-close-bg") { if (act === "add-close-bg" && !e.target.classList.contains("overlay-bg")) return; st.overlay = null; st.add = null; renderOverlays(); }
    else if (act === "do-scan") { doScan(); }
    else if (act === "add-back") { st.add.step = "path"; renderOverlays(); bindOverlay(); }
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
      if (st.view === "search") runSearch(); else if (st.view === "prune") runPrune(); else runAsk();
    }
    if (e.key === "Enter" && document.activeElement && document.activeElement.id === "add-path") { doScan(); }
    if (e.key === "Escape") { if (st.overlay) { st.overlay = null; st.add = null; renderOverlays(); } }
  });

  function cycleRepo() {
    if (st.repos.length < 2) return;
    const i = st.repos.findIndex((r) => r.name === st.repo);
    st.repo = st.repos[(i + 1) % st.repos.length].name;
    st.search = st.prune = st.ask = null; render();
  }
  // ── data loading ─────────────────────────────────────────────────────
  async function refreshRepos() {
    try { st.repos = await api.repos(); if (!st.repo && st.repos[0]) st.repo = st.repos[0].name; }
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
  render();
  refreshRepos();
  loadProviders();          // fill the topbar chip with the active provider
})();
