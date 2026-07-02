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

The offline suite is self-contained: `conftest.py` provides a **FakeEmbedder**
(deterministic bag-of-tokens hash vectors — similar text → similar vector), so
index/search behave realistically with no network and no key.

## Maintainer-only benchmarks (not in the repo)

The retrieval-quality gates run against a private corpus and live only on
maintainer machines (gitignored): `test_engine_golden.py` (R@1 / bundle_full
bars), `test_multi_repo.py`, `test_scale.py`, plus `evals/` bakeoff harnesses.
CI quality bars: golden `bundle_full` must stay **1.00**.
