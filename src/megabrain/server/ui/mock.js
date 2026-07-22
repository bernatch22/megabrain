/* mock.js — canned backend for offline/design mode (api.js MOCK=true).
 * Data conforms EXACTLY to the serve-api route contracts, so the UI behaves
 * identically to the real thing without a server, keys, or an index. */
window.mockApi = function () {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const REPOS = [
    { name: "megabrain", root: "~/code/megabrain", files: 173, chunks: 1284, embed_model: "perplexity/pplx-embed-v1-0.6b", loaded: true },
    { name: "flowcache-bench", root: "~/code/flowcache-bench", files: 24, chunks: 187, embed_model: "nomic-embed-text", loaded: true },
    { name: "engine-notebook", root: "~/scratch/engine-notebook", files: 8, chunks: 51, embed_model: "perplexity/pplx-embed-v1-0.6b", loaded: false },
  ];
  const CH = (id, name, kind, s, e, score, text, selected) =>
    ({ id, name, kind, start_line: s, end_line: e, score, text, selected });
  const SEARCH = {
    query: "", ms: 187,
    tier1: [
      { file: "engine/flow_cache.py", score: 0.92,
        chunks: [
          CH(1, "FlowCache.on_reindex", "method", 88, 104, 0.94,
            "def on_reindex(self, changed: set[str]) -> None:\n    \"\"\"Drop entries whose retrieval touched changed files.\"\"\"\n    dropped = 0\n    for key, entry in list(self._store.items()):\n        if entry.source_files & changed:\n            del self._store[key]\n            dropped += 1\n    self._metrics.record(\"cache.dropped\", dropped)", true),
          CH(2, "FlowCache.put", "method", 41, 58, 0.81,
            "def put(self, key, answer, source_files):\n    self._store[key] = Entry(answer, source_files, time.time())\n    self._evict_if_over_capacity()", false),
        ], symbols: [], neighbors: [] },
      { file: "engine/indexer.py", score: 0.87, chunks: [], symbols: [], neighbors: [] },
      { file: "engine/broadcast.py", score: 0.79, chunks: [], symbols: [], neighbors: [] },
      { file: "engine/chunker.py", score: 0.71, chunks: [], symbols: [], neighbors: [] },
    ],
    tier2: [
      { file: "engine/ask_stream.py", score: 0.58, via_graph: true, matched: [], best_chunk: null, symbols: [] },
      { file: "engine/metrics.py", score: 0.51, via_graph: false, matched: [], best_chunk: null, symbols: [] },
      { file: "engine/store.py", score: 0.49, via_graph: true, matched: [], best_chunk: null, symbols: [] },
      { file: "tests/test_cache.py", score: 0.47, via_graph: false, matched: [], best_chunk: null, symbols: [] },
    ],
  };
  const gen = (n, sel) => Array.from({ length: n }, (_, i) =>
    CH(100 + i, "chunk_" + i, ["method", "class", "import", "comment"][i % 4],
      i * 12 + 1, i * 12 + 10, +(0.15 + ((i * 37) % 60) / 100).toFixed(2),
      "def chunk_" + i + "():\n    ...", sel.includes(i)));

  return {
    repos: async () => (await wait(120), REPOS),
    providers: async () => (await wait(120), {
      claude: { available: true, default_model: "haiku" },
      openrouter: { available: true, default_model: "google/gemini-3.1-flash-lite" },
      ollama: { up: false, models: [] },
      active: { provider: "openrouter", model: "google/gemini-3.1-flash-lite" },
    }),
    health: async () => ({ ok: true }),
    selectProvider: async (provider, model) => (await wait(150), {
      claude: { available: true, default_model: "haiku" },
      openrouter: { available: true, default_model: "google/gemini-3.1-flash-lite" },
      ollama: { up: true, installed: true, models: ["gemma3:1b", "unclemusclez/jina-embeddings-v2-base-code:latest", "embeddinggemma:latest"] },
      active: { provider: provider === "ollama" ? "openrouter" : provider, label: provider,
        model: model || (provider === "claude" ? "haiku" : "google/gemini-3.1-flash-lite") },
    }),
    startOllama: async () => (await wait(1200), {
      claude: { available: true, default_model: "haiku" },
      openrouter: { available: true, default_model: "google/gemini-3.1-flash-lite" },
      ollama: { up: true, installed: true, models: ["gemma3:1b", "unclemusclez/jina-embeddings-v2-base-code:latest", "embeddinggemma:latest"] },
      active: { provider: "openrouter", label: "openrouter", model: "google/gemini-3.1-flash-lite" },
    }),
    search: async (query) => (await wait(220), { ...SEARCH, query }),
    chunks: async (file, q) => (await wait(160), {
      file, role: "core", selected_count: 3, chunks: gen(9, [2, 4, 7]) }),
    prune: async (q, repo, rerank) => (await wait(rerank ? 900 : 200), {
      query: q, repo: "megabrain", kept: rerank ? 2 : 4, pruned: rerank ? 10 : 8,
      scanned: 31, ms: 204,
      reranked: rerank ? { model: "haiku", kept: 2, dropped: 2, ms: 850 } : undefined,
      chunks: SEARCH.tier1[0].chunks.slice(0, rerank ? 2 : 4)
        .map((c) => ({ ...c, file: "engine/flow_cache.py" })),
      noise: gen(8, []).map((c) => ({ ...c, file: "engine/http.py" })),
    }),
    grep: async (q, repo, regex, icase) => {
      await wait(60);                       // grep is ~50ms for real: no LLM
      const mk = (file, line, symbol, kind, in_deg, text, reached) =>
        ({ file, line, symbol, kind, in_deg, text, reached_from: reached || [] });
      if (/^zero/i.test(q)) return { pattern: q, regex: !!regex, ignore_case: !!icase,
        matches: 0, files: 0, limit: 200,
        counts: { defines: 0, reads: 0, config: 0, tests: 0, docs: 0 },
        defines: [], reads: [], config: [], tests: [], docs: [] };
      const defines = [mk("engine/flow_cache.py", 88, q, "function", 6,
        `def ${q}(root, flows):`, ["engine/http.py", "engine/indexer.py"])];
      const reads = [
        mk("engine/http.py", 214, "do_POST", "method", 9, `    out = ${q}(root, flows)`, ["engine/serve.py"]),
        mk("engine/indexer.py", 61, "index_repo", "function", 4, `        if ${q}(root, f):`, ["engine/http.py"]),
      ];
      const config = [mk("pyproject.toml", 12, null, null, 0, `${q} = true`)];
      const tests = [mk("tests/test_cache.py", 33, "test_serve", "function", 0, `    assert ${q}(tmp, [])`)];
      const docs = [mk("README.md", 140, null, null, 0, `\`${q}\` serves a cached flow verbatim.`)];
      return { pattern: q, regex: !!regex, ignore_case: !!icase, matches: 6, files: 6,
        limit: 200, counts: { defines: 1, reads: 2, config: 1, tests: 1, docs: 1 },
        defines, reads, config, tests, docs };
    },
    graph: async (params) => {
      await wait(260);
      if (params.mode === "node") {
        const f = /cache/i.test(params.node || "") ? "engine/flow_cache.py" : (params.node || "engine/indexer.py");
        return { repo: "megabrain", file: f, resolved_from: params.node,
          community: { id: 0, label: "Cache & Index" }, degree: 4,
          out: [{ file: "engine/store.py", kind: "import" }, { file: "engine/metrics.py", kind: "call" }],
          in: [{ file: "engine/indexer.py", kind: "call" }, { file: "engine/http.py", kind: "import" }],
          semantic: [{ file: "engine/ask_stream.py", score: 0.87 }],
          symbols: [{ name: "on_reindex", kind: "method", line: 88, end_line: 104, signature: "def on_reindex(self, changed)", doc: "Drop stale entries." }],
          chunks: SEARCH.tier1[0].chunks, ms: 6 };
      }
      if (params.mode === "path") {
        return { repo: "megabrain", source: "engine/http.py", target: "engine/store.py",
          resolved_from: [params.source, params.target], found: true, ms: 4,
          hops: [{ file: "engine/http.py", via: "" }, { file: "engine/flow_cache.py", via: "import" }, { file: "engine/store.py", via: "call" }] };
      }
      const files = ["engine/flow_cache.py", "engine/indexer.py", "engine/store.py", "engine/http.py",
        "engine/metrics.py", "engine/chunker.py", "engine/ask_stream.py", "engine/broadcast.py",
        "tests/test_cache.py", "tests/test_http.py", "docs/GUIDE.md", "README.md"];
      const com = (f) => f.startsWith("tests") ? 1 : /md$/i.test(f) ? 2 : 0;
      return { repo: "megabrain", files: files.length, ms: 9,
        communities: [
          { id: 0, label: "Engine core", size: 8, files: files.filter((f) => com(f) === 0) },
          { id: 1, label: "Test harness", size: 2, files: files.filter((f) => com(f) === 1) },
          { id: 2, label: "Docs", size: 2, files: files.filter((f) => com(f) === 2) },
        ],
        god_nodes: [{ file: "engine/store.py", degree: 5, out: 1, in: 4, community: 0 },
          { file: "engine/flow_cache.py", degree: 4, out: 2, in: 2, community: 0 }],
        surprises: [{ a: "engine/ask_stream.py", b: "docs/GUIDE.md", score: 0.88, a_community: 0, b_community: 2 }],
        nodes: files.map((f) => ({ file: f, community: com(f), degree: 2 + (f.length % 4) })),
        links: [
          { s: "engine/http.py", d: "engine/flow_cache.py", kind: "import" },
          { s: "engine/flow_cache.py", d: "engine/store.py", kind: "call" },
          { s: "engine/indexer.py", d: "engine/store.py", kind: "import/call" },
          { s: "engine/indexer.py", d: "engine/chunker.py", kind: "call" },
          { s: "engine/http.py", d: "engine/ask_stream.py", kind: "import" },
          { s: "engine/broadcast.py", d: "engine/flow_cache.py", kind: "call" },
          { s: "engine/metrics.py", d: "engine/store.py", kind: "import" },
          { s: "tests/test_cache.py", d: "tests/test_http.py", kind: "semantic", score: 0.83 },
          { s: "engine/ask_stream.py", d: "docs/GUIDE.md", kind: "semantic", score: 0.88 },
        ] };
    },
    scan: async (path) => (await wait(300), {
      path, name: path.split("/").pop() || "repo", would_index: 128,
      by_ext: { ".py": 94, ".md": 22, ".toml": 8, ".yml": 4 },
      paths: [].concat(
        ["src/app.py", "src/http.py", "src/cli.py", "src/indexer.py", "src/chunker.py"],
        ["src/ask/agents.py", "src/ask/narrator.py", "src/ask/warmup.py"],
        ["src/retrieval/bundle.py", "src/retrieval/state.py", "src/retrieval/scoring.py"],
        ["tests/test_app.py", "tests/test_http.py", "tests/test_ask.py"],
        ["docs/GUIDE.md", "docs/ARCHITECTURE.md", "README.md", "pyproject.toml"]),
      top_dirs: [
        { dir: "src", files: 71, bytes: 421000 },
        { dir: "tests", files: 38, bytes: 143000 },
        { dir: "docs", files: 12, bytes: 61000 },
      ],
      flagged: [
        { path: "node_modules/react/index.js", reason: "vendored" },
        { path: "dist/bundle.min.js", reason: "vendored" },
        { path: "api/schema_pb2.py", reason: "generated" },
        { path: ".venv/lib/python3.11/site.py", reason: "excluded" },
        { path: "build/out.txt", reason: "gitignored" },
        { path: "assets/huge.bin", reason: "too-big" },
      ],
      proposed_ignore: "# proposed by scan — review before indexing\nnode_modules/    # vendored\ndist/    # vendored\n",
    }),
    fsPick: async () => (await wait(400), { path: "/Users/you/code/some-repo" }),
    reposAdd: async (path) => (await wait(150), { name: path.split("/").pop(), root: path, files: 0, chunks: 0 }),
    indexStream: (body, onEvent) => {
      const files = ["src/app.py", "src/http.py", "src/indexer.py", "src/chunker.py",
        "src/cache.py", "src/store.py", "src/router.py", "src/main.py"];
      const n = 173; let i = 0;
      const t = setInterval(() => {
        i += 7;
        if (i >= n) { clearInterval(t);
          onEvent({ type: "done", files: n, changed: 41, unchanged: 132, new_chunks: 318, seconds: 4.2 });
          return; }
        onEvent({ type: "file", file: files[i % files.length], i, n, changed: i % 3 === 0 });
      }, 90);
      return { done: Promise.resolve(), abort: () => clearInterval(t) };
    },
    askStream: (body, onEvent) => {
      const seq = [
        { type: "retrieval", repo: "megabrain", ms: 204, files: 8, model: body.model || "gemini-3.1-flash-lite", llm: true },
        { type: "classified", broad: true, reasons: ["4 CORE files within the tier1 gap", "candidates span 3 top-level dirs"], forced: false },
        { type: "planning", model: body.model || "gemini-3.1-flash-lite" },
        { type: "plan", agents: [
          { id: 0, label: "cache-invalidation", sub_query: "how does the cache detect stale entries after re-index?",
            chunks: [{ k: 1, file: "engine/flow_cache.py", start_line: 88, end_line: 104, name: "on_reindex" }] },
          { id: 1, label: "what-changed", sub_query: "what triggers a chunk to be counted as changed?",
            chunks: [{ k: 2, file: "engine/chunker.py", start_line: 62, end_line: 78, name: "fingerprint" }] },
        ] },
        { type: "agent_start", id: 0, label: "cache-invalidation", sub_query: "how does the cache detect stale entries?", files: ["flow_cache.py"] },
        { type: "agent_start", id: 1, label: "what-changed", sub_query: "what triggers a chunk as changed?", files: ["chunker.py"] },
        { type: "agent_delta", id: 0, text: "The cache subscribes to a broadcast topic \"reindex\". When Indexer.commit publishes the changed paths, on_reindex sweeps the store and drops any entry whose source_files intersect the changed set." },
        { type: "agent_tool", id: 1, tool: "get_symbol", args: { name: "chunker.canonical_text" } },
        { type: "agent_delta", id: 1, text: "A chunk is changed when its fingerprint — sha1 of the canonicalized text — differs from the store. Cosmetic edits leave it stable." },
        { type: "agent_done", id: 0, ms: 2100 },
        { type: "agent_done", id: 1, ms: 2600 },
        { type: "synthesis_start", agents: 2 },
        { type: "synthesis_delta", text: "The flow cache invalidates on re-index by keying every cached answer against the **chunk fingerprint** of the files it touched.\n\n" },
        { type: "synthesis_delta", text: "\n**`engine/flow_cache.py` L88-104** — on_reindex\n```python\ndef on_reindex(self, changed: set[str]) -> None:\n    dropped = 0\n    for key, entry in list(self._store.items()):\n        if entry.source_files & changed:\n            del self._store[key]\n```\n\n" },
        { type: "synthesis_delta", text: "Invalidation is chunk-level, not file-level: a cosmetic refactor leaves the fingerprint stable." },
        { type: "done", spans: 8, files: 3, retrieval_ms: 214, llm_ms: 5100, dropped: ["metrics.py:12", "store.py:44"], n_dropped: 2 },
      ];
      let k = 0, stop = false;
      (async () => { for (const ev of seq) { if (stop) return; await wait(ev.type.startsWith("synthesis") ? 500 : 350); onEvent(ev); } })();
      return { done: Promise.resolve(), abort: () => { stop = true; } };
    },
  };
};
