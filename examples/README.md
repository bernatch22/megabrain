# Examples

Runnable, self-contained scripts showing megabrain as a **library** — not just a CLI.

| script | shows | needs API key? |
|---|---|---|
| [`01_programmatic.py`](01_programmatic.py) | index → search → render → warm state → `ask`, all from Python | yes¹ |
| [`02_custom_chunker.py`](02_custom_chunker.py) | teach megabrain a **new content type** (`.sql`) with a custom `ChunkStrategy` — no fork needed | chunking: **no** · search step: yes¹ |
| [`03_chunk_map.py`](03_chunk_map.py) | terminal heatmap of every chunk in one file, scored against a query (the signal-vs-noise view) | yes¹ |
| [`webui/`](webui/) | **live web demo** — ask a question, watch the engine rank files and light up the selected chunks, then hit **Explain ⚡** for the LLM walkthrough with a **Claude vs OpenRouter** A/B toggle; ships a 2003-style legacy-PHP sample, works on any repo | yes¹ |
| [`forged/`](forged/) | **real `forge --specialize` output** — an LLM-generated, A/B-gated shape-router for Ruby (sinatra), with the measured span-IoU wins that let it install | — (read-only) |

¹ "key" = `OPENROUTER_API_KEY`, **or** a keyless local endpoint
(`MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1` + a local embed model —
see the README's provider section).

```bash
pip install megabrain
python examples/02_custom_chunker.py            # chunking part runs offline
python examples/01_programmatic.py ~/some/repo "how does auth work"
python examples/03_chunk_map.py ~/some/repo src/big_file.py "retry logic"

python examples/webui/server.py                 # web demo on the bundled PHP sample
python examples/webui/server.py /tmp/click ~/x  # …or any repos you pass (auto-indexed)
# then open http://localhost:8688
```

The web demo is stdlib-only (one port, no frameworks): type a question →
the real engine ranks the bundle files (CORE/RELATED, ~ms, no LLM) → click a
file → every chunk appears scored, with what retrieval actually **selected**
highlighted and the noise dimmed. It ships the legacy-PHP sample; pick
**"Other…"** in the repo dropdown to load any repo by absolute path from the
UI (indexed on demand), or pass paths on the command line. Try a small GitHub
repo: `git clone --depth 1 https://github.com/pallets/click /tmp/click`.

## Custom chunkers in one paragraph

A chunking strategy is any object with `exts`, `chunk_file(relpath, source) ->
FileResult`, and the two edge hooks (`build_edge_ctx` / `extract_edges`, both
returning `None` when the content type has no dependency graph). The one hard
rule: the chunks of a file must be an exact **line partition** — check yours
with `validate_partition(result) == []`. Pass instances via
`index_repo(root, strategies=[MyStrategy()])`; custom strategies are matched
**before** the built-ins, so you can claim a new extension (`.sql`, `.proto`,
`.ipynb`…) or override how an existing one is chunked. Everything downstream
(embedding, storage, retrieval, `ask`) is content-agnostic and just works.

For a *programming language* with a tree-sitter grammar, prefer contributing a
`LangSpec` to the engine instead (see [CONTRIBUTING](../CONTRIBUTING.md)) — a
custom strategy is the right tool for private formats, DSLs, and content types
that don't belong in core.
