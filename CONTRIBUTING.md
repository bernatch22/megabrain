# Contributing to megabrain

## Dev setup

```bash
git clone https://github.com/bernatch22/megabrain && cd megabrain
pip install -e ".[languages]" pytest ruff
python -m pytest          # fully offline — no API key, no network
ruff check .
```

## The rules that are locked by experimental data

These were each decided by measured experiments (see README → Design). Don't
send PRs that violate them without new evidence:

1. **No LLM in the retrieval/query path.** LLM pruning was tested four ways and
   always cost bundle completeness. The only LLM calls are `ask` (narration)
   and `--best` (optional reorder) — both fail-open.
2. **Completeness beats ordering.** Changes must not lower bundle completeness;
   ranking wins that cost recall are rejected.
3. **The graph never ranks.** Import/call edges supply candidates and map
   annotations only (PageRank-as-ranking measurably hurt).
4. **Chunks are a line partition.** Every chunker must keep
   `validate_partition` clean: no gaps, no overlaps, full file coverage.
5. **`ask` shows real code only.** The model cites `[[k]]` spans; the engine
   splices verbatim code from disk. Never let the model emit code.

## Testing

The public suite (`python -m pytest`) is offline and covers chunkers
(partition guarantees per language), retrieval plumbing, the MCP server, and
security containment. The end-to-end retrieval benchmark (a 30-query golden
set over a private corpus) runs maintainer-side before releases; PRs that
could shift ranking (weights in `query.py`, chunk budgets, issue grounding)
will be gated on it — say so in the PR description so it gets run.

## Adding a language (the best first contribution)

A language is a config entry, not a subsystem:

1. Add a `LangSpec` in `megabrain/chunkers/treesitter.py` (node types → kinds,
   name fields, container types). Look at `RUST_SPEC` / `PHP_SPEC` for the
   tricky cases (nested names, `impl` blocks).
2. Register it in `megabrain/strategies.py` `_TREE_SITTER_LANGS` with its
   extensions and `tree_sitter_<lang>` module name — it activates only when
   the grammar is installed.
3. Add the grammar to the `languages` extra in `pyproject.toml`.
4. Add a `tests/test_<lang>_chunker.py` that feeds real-world-shaped source and
   asserts `validate_partition(result) == []` plus sensible symbols/kinds
   (copy the structure of `tests/test_chunker_ts.py`).

Optional level 2: an import-edge extractor in `graph.py` + a strategy
`build_edge_ctx`/`extract_edges` (see `PhpStrategy`). Retrieval works without
it; edges only add graph candidates.

Private formats or DSLs that don't belong in core don't need a PR at all:
implement the `ChunkStrategy` protocol and pass it to
`index_repo(root, strategies=[...])` — see `examples/02_custom_chunker.py`.

## Style

`ruff check .` must pass (config in `pyproject.toml`). Match the codebase's
voice: comments explain *why* (constraints, evidence), not *what*.

## Releasing (maintainers)

Bump `megabrain/__init__.py:__version__`, update `CHANGELOG.md`, tag `vX.Y.Z`,
push the tag — the release workflow builds and publishes via PyPI Trusted
Publishing.
