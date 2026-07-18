# megabrain graph — the plain-language guide

> You point it at an indexed repo and it draws you a **map of the codebase**:
> which files form teams, which files are the load-bearing walls, which files
> are secretly related without ever importing each other, and how to walk from
> any file to any other. No PhD required. This doc explains what you're
> looking at, how it's built, and — most importantly — **what it's actually
> good for**, with real output from real repos.

```bash
megabrain graph ~/repo                    # the map (communities, god nodes, surprises)
megabrain graph ~/repo --node "the scoring pipeline"   # one file, in depth
megabrain graph ~/repo --path scoring.py narrator.py   # how two files connect
megabrain studio ~/repo                    # → the Graph tab: the same thing, interactive
```

---

## 1. What am I looking at?

Every **dot is a file**. Three kinds of information are drawn on top:

| you see | it means |
|---|---|
| **color** | the file's **community** — a group of files that import/call each other or talk about the same thing. Detected automatically; named by an LLM once (cached). |
| **glow / halo** | a **god node** — one of the most-connected files in the repo. These are the core abstractions: break one of these, break everything. |
| **solid line** | a real code edge: one file **imports or calls** the other (extracted from the AST at index time — deterministic, no LLM). |
| **dashed line** | a **semantic** edge: the two files are talking about the same thing (embedding similarity ≥ 0.80) but there is **no code link** between them. |

The studio never shows you the whole-repo hairball. It has four views, and you
move between them by clicking:

1. **Overview (default)** — one **bubble per community**, sized by file count,
   connected by how many links cross between them. Ten shapes, not 600 dots.
2. **Community** — click a bubble (or its row in the panel) and ONLY that
   community's files appear, every one labeled. `← back to overview` returns.
3. **Search subgraph** — type anything ("chunker split php") and the engine
   runs its REAL retrieval, then draws only the relevant files and the links
   between them, ranked in the side panel.
4. **Path** — type `a -> b` and you get just the route, laid out flat, with
   the edge kind and the **carrier functions** written on each line.

In any view: **hover** for a tooltip, **click a file** for its neighbors +
symbols + real code in the side panel.

## 2. Real output — megabrain's own repo (122 files, built in 8 ms)

**Communities** (labels written by the LLM, cached):

```
[0] Search & API          81 files   the engine core: retrieval, providers, server, ask
[1] Code Chunking          7 files   chunkers/ (cAST, tree-sitter, markdown, php)
[2] Golden Query Tests     4 files   the render goldens
[3] Chunker Tests          4 files   the chunker test suite
 +  ~18 standalone docs/config files (README, SECURITY, evals …)
```

The engine found, with zero configuration, exactly the structure a maintainer
would sketch on a whiteboard: a big core, a cleanly separated chunking
subsystem, and two test islands.

**God nodes** (highest structural degree — the files everything leans on):

```
src/megabrain/providers/__init__.py   deg 37    every LLM/embedding call goes through here
src/megabrain/retrieval/bundle.py    deg 32    the retrieval assembly
src/megabrain/indexing/indexer.py    deg 29    the index pipeline
src/megabrain/storage/store.py       deg 24    the SQLite layer
src/megabrain/app.py                 deg 21    the use-case layer
src/megabrain/server/http.py         deg 21    the HTTP/studio server
```

That list IS the "read these first" onboarding order for a new contributor.

**A path** — "how does scoring reach the narrator?":

```
$ megabrain graph . --path scoring.py narrator.py
src/megabrain/retrieval/scoring.py
└─ call → src/megabrain/retrieval/bundle.py  · via score_chunks, chunks_for_file, search_with_state
└─ call → src/megabrain/ask/narrator.py      · via ask, search
```

Each hop names the **functions/classes that carry it** — scores flow into the
bundle through `score_chunks`, the bundle feeds the narrator through `ask`.
Not just *which* files connect, but *through what*. Three lines that would
otherwise take ten minutes of grep. (The carriers come from the symbols table
crossed with the real chunk text — deterministic, no new indexing.)

## 3. Real output — the "surprises" (graphify, 630 files, 37 ms)

**Surprising connections** = pairs of files that are ≥ 0.85 similar by
embedding, live in **different communities**, and have **no code link at all**.
On graphify's repo the top hits were:

```
graphify/skills/claude/references/exports.md
   ~0.974~  tools/skillgen/expected/graphify__skills__claude__references__exports.md
graphify/skills/kiro/references/exports.md
   ~0.973~  tools/skillgen/expected/graphify__skills__kiro__references__exports.md
…
```

Every surprise is a generated skill file and its golden "expected" twin —
**near-duplicated content maintained in two places**, found automatically.
That's the feature in one sentence: it shows you what you didn't know was
connected (or duplicated). On a repo with no duplication the list is empty —
megabrain's own repo reports none, which is itself information.

## 4. What is it actually good for?

1. **Landing on an unfamiliar repo.** `megabrain graph ~/repo` before reading
   any file: the communities tell you the subsystems, the god nodes tell you
   the reading order, and the sizes tell you where the mass is.
2. **Impact estimation.** About to touch a god node? Its degree is the blast
   radius. `--node store.py` lists exactly who depends on it (incoming edges).
3. **Finding duplication and drift.** The surprises list is a free
   near-duplicate detector across the whole corpus (see graphify above).
4. **"How do these two things even relate?"** `--path a b` answers with the
   actual call/import chain — or with a semantic hop when there is no code
   path, which tells you the relationship exists only in *meaning*, not in
   *code* (often a refactor smell or a missing abstraction).
5. **Feeding an agent.** `megabrain_graph mode=map` over MCP gives a coding
   agent the whole repo topology in one call — better planning input than any
   directory listing. `mode=node` hands it a file's full context (neighbors +
   symbols + verbatim chunks) without a single grep.

## 5. How it works (90 seconds, no magic)

Everything is derived from what indexing **already stored** — the graph costs
nothing extra at index time and milliseconds at query time:

1. **Structural lane.** At index time the chunkers extract import/call edges
   into SQLite (`edges` table). Deterministic; no LLM. Covered: **Python**
   (AST imports + calls), **TS/JS** (relative imports), **PHP** (`use`
   statements), **Ruby** (`require_relative` exact, `require`/`autoload`
   through load-path candidates incl. sub-gem `*/lib`), **Go** (in-repo
   imports pinned to the defining file via `alias.Name` uses, **plus**
   same-package edges — sibling files of one Go package call each other with
   no import, and that's most of a Go repo's structure). Rust indexes without
   a graph for now.
2. **Semantic lane.** Every file already has a *skeleton embedding* (its
   signatures + docstrings as one vector). Cosine ≥ 0.80 between two files
   (top-3 per file) adds a dashed edge. This is what graph tools call
   "inferred" relationships — except here it carries an honest similarity
   score and never needed a model to invent it.
3. **Communities.** Weighted label propagation over both lanes — a
   parameter-free, deterministic clustering (numpy only, no networkx). Same
   input → same communities, every run.
4. **Names.** The ONLY LLM in the whole feature: one buffered call names each
   community from its top files/symbols ("Search & API", "Code Chunking").
   Cached in the repo's own index under a graph fingerprint — you pay it once
   per graph shape, and with no key it falls back to "Community N" and
   everything else still works.
5. **Endpoints by embedding.** `--node "the scoring pipeline"` embeds your
   words and finds the closest file skeleton — you don't need to know the
   path. `--path` resolves both ends the same way, then BFS walks the edges.
6. **Real code only.** The node view splices the file's actual chunks from
   the store, verbatim — same anti-hallucination rule as `ask`.

Measured: megabrain's repo (122 files, 324 links) builds in **8 ms**;
graphify's (630 files, 1477 links) in **37 ms**. First `map` call adds the
one-off labeling LLM call (~2 s); after that it's cached.

## 6. Knobs

| knob | default | what it does |
|---|---|---|
| `--no-labels` (CLI) | off | skip the LLM community labels — fully offline |
| `SEM_EDGE_MIN` (graph.py) | 0.80 | min cosine for a dashed semantic edge |
| `SURPRISE_MIN` (graph.py) | 0.85 | min cosine for a "surprising connection" |
| `SEM_TOP_K` (graph.py) | 3 | semantic edges per file cap (keeps the map sparse) |
| studio: isolated-files chip | hidden | show/hide files with no links at all |
| studio: community click | — | focus one community; everything else dims |

Surfaces: CLI `megabrain graph`, MCP `megabrain_graph(mode=map|node|path)`,
HTTP `GET /graph`, and the studio's Graph tab. All four are the same engine —
`src/megabrain/graph.py`, one file.
