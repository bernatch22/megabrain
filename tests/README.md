# Tests

```bash
python3 -m pytest            # the whole offline suite — no network, no API key
```

## Layout

| file | covers | needs |
|---|---|---|
| `test_cast_chunker.py` | Python cAST chunker: merge/split, breadcrumbs, line-partition guarantee | — |
| `test_chunker_ts.py` | tree-sitter chunker (TS/JS + LangSpec languages) | — |
| `test_markdown_chunker.py` | QMD-style markdown chunker | — |
| `test_ask_citation.py` | `[[k]]` / `[[k:lo-hi]]` citation parsing + verbatim splice | — |
| `test_providers.py` | key resolution (env/zshrc/local/native), retry, OpenAI SSE parsing | — |
| `test_embeddings.py` | int8/float wire decode, L2 norm, disk cache, dims, batching | — |
| `test_indexer_search.py` | end-to-end index→search on a tmp repo: incremental sha, `--force`, embed-model-change auto re-embed, orphans, retrieval + path filter | — |
| `test_mcp_server.py` | MCP tool schemas, `repo_path`/`scope_path` resolution | — |
| `test_path_scope.py` | root resolution + path-filter unit tests (2 e2e cases auto-skip without the private corpus) | — |
| `test_php_chunker.py` / `test_php_legacy_chunker.py` | PHP LangSpec chunker, legacy-PHP section chunker + shape routing, `use`-import graph, edge preservation | `tree_sitter_php` |
| `test_get_code_security.py` | repo-root containment: `../` and absolute paths never escape | — |
| `test_custom_strategy.py` | `index_repo(strategies=[...])`: protocol conformance, partition, override precedence | — |
| `test_ask_modes.py` | ask candidate modes: code-only / docs-only / code+docs | — |
| `test_claude_provider.py` | claude provider routing + delta streaming (fake Agent SDK) | — |
| `test_provider_autodetect.py` | chat provider auto-default (claude when SDK present, else openrouter) | — |
| `test_render_related.py` | RELATED renders as a map by default; `related_code=True` restores bodies | — |

The offline suite is self-contained: `conftest.py` provides a **FakeEmbedder**
(deterministic bag-of-tokens hash vectors — similar text → similar vector), so
index/search behave realistically with no network and no key.

## Maintainer-only benchmarks (not in the repo)

The retrieval-quality gates are script-style (not pytest-collected) and run
against corpora that live on maintainer machines: `test_engine_golden.py`
(R@1 / bundle_full bars) and `test_multi_repo.py` are gitignored with the
private golden set; `test_scale.py` is tracked but needs a local
`~/vscode-js-debug` checkout. Plus `evals/` bakeoff harnesses (local).
Quality bar for merging: golden `bundle_full` must stay **1.00**.
