# megabrain studio — the whole engine, in your browser

> `megabrain studio` opens a local web app that drives the real engine
> live — no canned data, no build step, no CDN. It's the fastest way to *see*
> what megabrain does: search, prune, ask, and a navigable knowledge graph,
> plus a read-only code navigator. This doc is the tour: what each view is for,
> how the server works, and the recipes.

```bash
megabrain studio               # UI + JSON API, every indexed repo → http://localhost:2134
megabrain studio ~/repo        # …or boot straight into one repo
megabrain serve-api ~/repo     # the same JSON API, headless (no UI)
```

---

## 1. One server, two commands

There is a single stdlib HTTP server (`server/http.py`). `studio` mounts the
web UI at `/` on top of the JSON API; `serve-api` runs the same API with no UI
(embed it, or use it as a demo backend). Nothing else differs — same routes,
same warm state.

- **Warm state** — the embedding matrix loads once per repo (not per request),
  behind a lock, and reloads automatically when the index changes on disk
  (mtime invalidation). No restart after a re-index.
- **Every repo on the machine** — `studio` pre-loads the global registry
  (`~/.megabrain/registry.json`): every repo you've ever indexed is in the
  left rail, selectable immediately. `RepoSession` is lazy, so this costs
  nothing at boot — a repo's matrix loads the first time you query it.
- **Auth** — `--token <t>` (or `$MEGABRAIN_API_TOKEN`) requires
  `Authorization: Bearer <t>` on every route except `/health` and the UI.
  Off by default (localhost). `--cors <origin>` for a cross-origin browser.
- **No LLM?** — `--no-llm` disables `/ask`. Search, prune, and graph never
  need one.

## 2. The views

### Search
The tab an agent's `megabrain_search` mirrors: the pruned **signal list** —
the exact chunks worth reading, ranked, with code, noise dropped. `kept` vs
`pruned` in the badge. Flip **LLM rerank** on to watch a cheap model drop the
vocabulary-only matches (tests, evals) and reorder — fail-open to the
deterministic list, with the model + dropped count shown.

### Ask
Type a question and watch it work. A **broad** question (the classifier reads
the bundle shape, no LLM) **fans out into parallel sub-agents** — one card per
agent, its tool calls and prose streaming in — then a synthesis with the
**real code spliced in** as it types. A scoped question skips straight to the
answer. Every citation is verbatim from disk; the model never emits code.

### Graph
The repo as a navigable knowledge graph — see **[docs/GRAPH.md](GRAPH.md)** for
the full guide. Four views: an **overview** of community bubbles, one
**community** expanded, a **search subgraph** (real retrieval drawn as a graph),
and a **path** between two files/concepts with `▶ Run the connection` — a
step-through of the call→definition chain, each step openable in the navigator.

### The code navigator (opens over any view)
Click any file — a search chunk's `⤢ open`, a related card, an ask agent's
file pill, a graph node, a path step — and the **full file** opens in a
slide-over: real bytes, line numbers, syntax-highlighted, scrolled to the exact
line, connection lines marked. **Every identifier with a resolvable definition
is a link** (receiver-aware, import-anchored — `Path(x).resolve()` links to
nothing because it's stdlib; `store.stats()` jumps to store.py). A back stack,
a symbols outline rail, `Esc` to close.

### Add a repo → it scans first
Paste a path or pick a folder (native OS dialog). Studio **censuses it before
committing**: how many files WILL index, the by-extension breakdown, and
everything skipped with a reason (`.gitignore` · vendored · generated ·
too-big). Refine the selection in a **tri-state file tree** — click a row to
expand a folder or toggle a file, filter by name, navigate with the keyboard
(↑↓ move, →← expand/collapse, space toggles), All/None/Expand/Collapse. Each
folder shows `included/total`, and the footer states how many
`.megabrainignore` lines your choice will write (excluding a folder then
re-including one child splits the rule into siblings, since the format has no
`!` negation). Then a **live progress bar** indexes it file by file. The new
repo joins the rail.

### Settings / providers
Claude SDK · OpenRouter · Ollama, auto-detected with a why ("no OPENROUTER_API_KEY",
"no server on :11434"). **Switch the narrator without leaving the page**, pick
the model (chips per provider, free slug for OpenRouter), and **start
`ollama serve` in one click** to go fully local. Selection persists.

## 3. The JSON API (what serve-api exposes)

Every route accepts an optional `?repo=` / `"repo"` (absent = the boot repo).

| route | returns |
|---|---|
| `GET /health` | `{ok, repo, files, chunks, embed_model, uptime}` |
| `GET /repos` | warm sessions (`loaded: true`) + registry repos (`loaded: false`) |
| `GET /providers` | detection for the settings panel |
| `GET /scan?path=` | the add-repo census |
| `GET /get?file=` | one file's real code |
| `GET /symbols?file=` | a file's symbol outline · no `file` = the repo's name→def-count index |
| `GET /symbol?name=` | repo-wide definitions of a bare name (go-to-definition) |
| `GET /chunks?file=&q=` | every chunk of one file, scored + `selected` |
| `GET /prune?q=&rerank=` | the flat signal list (+ `rerank=1` for the LLM lane) |
| `GET /graph?mode=&node=&source=&target=` | knowledge graph: map / node / path |
| `POST /search {query}` | the raw CORE/RELATED bundle |
| `POST /ask {question,model?,agents?}` | buffered narrated answer |
| `POST /ask/stream` | the multi-agent live view (SSE) |
| `POST /index {force?}` · `POST /index/stream` | (re)index, blocking or per-file SSE |
| `POST /repos/add {path,ignore?}` | register + load a repo |
| `POST /providers/select` · `POST /providers/ollama/serve` | switch/​start providers |

## 4. Recipes

```bash
# fully local demo box, everything behind a token
MEGABRAIN_CHAT_BASE_URL=http://localhost:11434/v1 \
  megabrain studio ~/repo --host 0.0.0.0 --token "$(openssl rand -hex 16)"

# headless backend for your own frontend (the bernardocastro.dev demo does this)
megabrain serve-api ~/repo --cors https://yourdomain.com

# share a tokenized studio link — the token rides in ?token=, stashed to localStorage
open "http://localhost:2134/?token=abc123"
```

Keyboard: `/` focuses the query, `⌘K` cycles repos, `Esc` closes overlays.
Dark/light follows your pick (persisted). The whole bundle is vanilla JS +
inline SVG — no framework, no runtime CDN — matching the engine's stdlib stance.
