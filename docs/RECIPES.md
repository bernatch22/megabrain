# Recipes

Concrete goals, copy-paste answers. New here? Read the **[Guide](GUIDE.md)** first —
it's the tour. Looking up a flag? → **[Reference](REFERENCE.md)**.

- [Give your coding agent the whole repo](#give-your-coding-agent-the-whole-repo)
- [Run fully local — no keys, no cloud](#run-fully-local--no-keys-no-cloud)
- [Turn a repo into a team knowledge base](#turn-a-repo-into-a-team-knowledge-base)
- [Run a public read-only demo](#run-a-public-read-only-demo)
- [Make it read a file type it doesn't know](#make-it-read-a-file-type-it-doesnt-know)
- [Search several repos at once](#search-several-repos-at-once)
- [Use it as a Python library](#use-it-as-a-python-library)
- [Make ask cheaper, or faster](#make-ask-cheaper-or-faster)
- [Keep secrets and junk out of the index](#keep-secrets-and-junk-out-of-the-index)

---

## Give your coding agent the whole repo

```bash
megabrain install                 # registers the MCP server everywhere it's detected
megabrain index ~/repo            # once per repo you want the agent to see
```

Then put **this** in your agent's rules file (`CLAUDE.md`, `.cursorrules`, a skill —
whatever your assistant reads):

> **For any question about how the code works — a flow, where something is handled, why a
> value is what it is — call `megabrain_ask` FIRST, before grepping or reading files. One
> call returns the whole flow with the real code; only fall back to file-by-file reading
> if it misses.**

That one instruction is the difference between an agent that burns 15 turns reconstructing
a flow and one that gets it in a single grounded call.

**Which tool for which agent?** Both are grounded — pick by who does the reasoning:

- **`megabrain_search`** (rerank on by default over MCP) — for a strong agent that wants to
  reason over raw code itself. ~200 ms of no-LLM retrieval returns the exact chunks worth
  reading; the rerank then drops the vocabulary-only matches embeddings can't distinguish.
- **`megabrain_ask`** — the repo *explained*. This is relevance curation for whatever model
  reads it: an agent on a smaller, cheaper LLM that could never navigate the repo alone
  gets handed the connected story, already assembled and grounded.

Doing it by hand instead of `megabrain install`:

```bash
claude mcp add megabrain -- python3 -m megabrain.mcp_server
```

```json
{ "mcpServers": { "megabrain": { "command": "python3",
                                 "args": ["-m", "megabrain.mcp_server"] } } }
```

---

## Run fully local — no keys, no cloud

Your code never leaves the machine. Two knobs here are **not obvious** and megabrain will
look broken without them.

```bash
ollama serve

# 1. embeddings — pick one, see the table below
ollama pull bge-m3
export MEGABRAIN_EMBED_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_EMBED_MODEL=bge-m3

# 2. the narrator
export MEGABRAIN_CHAT_BASE_URL=http://localhost:11434/v1
export MEGABRAIN_ASK_MODEL=qwen3-coder:30b
export MEGABRAIN_CHAT_EXTRA='{"reasoning_effort": "none"}'   # ← knob 1
export MEGABRAIN_ASK_CTX_CHARS=105000                        # ← knob 2
export OLLAMA_CONTEXT_LENGTH=40960

megabrain index ~/repo --force        # --force re-embeds with the new model
megabrain ask   ~/repo "how does X work"
```

### Which local embedder

Measured the same day, same corpus, same 22 golden queries, each force-reindexed — because
the corpus drifts, and comparing a fresh local number against a stale table row hides the
real gap:

| model | R@1 | bundle_full | size | dims |
|---|---|---|---|---|
| `perplexity/pplx-embed-v1-0.6b` *(cloud control)* | **0.864** | **0.955** | — | 1024 |
| **`bge-m3`** — the local pick | **0.773** | 0.909 | 1.2 GB | 1024 |
| `jina-embeddings-v2-base-code` Q8 GGUF | 0.682 | 0.909 | **172 MB** | **768** |

**Take `bge-m3`.** It ranks the #1 slot meaningfully better than the code-tuned jina
(0.773 vs 0.682) and they tie on `bundle_full` — the number that decides whether `ask` has
the right code to splice. Reach for jina only when footprint matters more than ranking:
it's 7× smaller and its 768 dims make a 25% smaller index and faster search.

Both are 8K-context models. The 512-token BERT embedders (e5, gte, bge-large) **cannot**
be used — megabrain's chunks overflow their context and the request fails outright.

**Knob 1 — `MEGABRAIN_CHAT_EXTRA`.** Hybrid-thinking models (`qwen3:*`) burn hundreds of
hidden reasoning tokens per answer through Ollama's OpenAI endpoint, which **ignores** the
native `think:false`. `reasoning_effort:"none"` is the field it honors (Ollama ≥ 0.12).
Non-thinking models (`qwen3-coder:*`) don't need it.

**Knob 2 — `MEGABRAIN_ASK_CTX_CHARS`.** `ask`'s candidate budget is sized for cloud windows
(200K chars ≈ 50K tokens). A 40K-token local model gets its prompt **silently truncated**
by the runtime. Cap the budget below the model's window (~3 chars/token, leave ~3K tokens
for the answer).

### Which local narrator

Same retrieval bundle, different chat model, over the golden queries. `cite_recall` = the
share of a query's expected files the model actually cited; VRAM is a realistic 4-bit quant.

| model | cite_recall | latency | ~VRAM (Q4) |
|---|---|---|---|
| **`qwen3-coder:30b`** *(MoE, ~3B active)* ⭐ | **0.583** | **15 s** | ~18–20 GB |
| `qwen3:30b` — the same size, **not** code-specialized | 0.333 | 12 s | ~18–20 GB |
| `qwen3:8b` | 0.417 | 41 s | ~5–6 GB |
| `qwen3:14b` | 0.333 | 41 s | ~9–10 GB |
| `gemma-3-12b` | 0.333 | 42 s | ~8–9 GB |
| — *cloud baselines, for scale* — | | | |
| `qwen/qwen3-coder` (480B MoE) | 0.750 | 21 s | ✗ |
| `anthropic/claude-haiku-4.5` | 0.833 | 19 s | ✗ |

**Take `qwen3-coder`, the current version.** Two findings worth internalizing before you
pick something smaller to save VRAM:

- **The lightweight dense models lose on both axes.** They cite fewer files *and* run ~2.7×
  slower (~41 s vs 15 s) — they think harder per token with no MoE speedup. Being frugal
  buys you a worse *and* slower narrator, not a trade.
- **Code specialization is not cosmetic.** The general-purpose sibling of the very same 30B
  MoE scores **half** the citation recall on a code corpus (0.333 vs 0.583).

**What you give up versus the cloud** is *secondary*-citation completeness, not
correctness. The citation/splice mechanism is model-agnostic — every model tested spliced
real code on most queries — so a weaker narrator cites fewer surrounding files but never
invents one, and the primary answer file is essentially always in the bundle either way.

---

## Turn a repo into a team knowledge base

The [flow cache](GUIDE.md#5-it-remembers--the-flow-cache) makes megabrain accumulate your
team's understanding. Two commands make that deliberate instead of incidental:

```bash
megabrain index ~/repo --warm-flows 12    # discover + pre-cache the 12 main workflows now
megabrain flows ~/repo --refresh          # after big changes: re-ask stale flows
```

`--warm-flows` reads the graph's hub files and their doclines, writes N research questions
covering the system's main workflows, and runs one `ask` each — so the cache starts full on
day one instead of building up lazily. It costs N LLM calls, which is why it's explicit.

**Better: commit the questions.** Put a `.megabrainqueries` at the repo root, one question
per line (`#` comments allowed):

```
# Starter questions — these are our main workflows
How does a request get authenticated end to end?
How does the billing webhook reconcile a payment?
How does the job queue retry a failed task?
```

That single file does three jobs: it documents the repo's main flows for a newcomer, it
becomes the [starter chips](GUIDE.md#starter-questions) in the studio, **and** it seeds
`flows --warm` — so warming caches exactly the questions the chips offer, and every chip
then serves instantly with no LLM.

Everything lives in the repo's own `.megabrain/db.sqlite`. Commit it and the whole team
inherits the cache, or leave it gitignored and let each machine build its own.

---

## Run a public read-only demo

Serve the real studio publicly without letting visitors index anything or burn your budget.

```bash
megabrain studio --readonly --rate-limit 30 --trust-proxy --port 2137
```

- `--readonly` — 403s every mutating and config route (index, add-repo, scan, provider
  switching, flow deletes). Enforced **server-side**; the UI also hides those affordances
  because it reads `GET /config`, but the lock never depends on the UI.
- `--rate-limit 30` — at most 30 LLM asks per hour per client IP. Retrieval routes stay
  unlimited: they're local and ~free.
- `--trust-proxy` — take the client IP from `X-Forwarded-For`. Set this **only** behind
  your own reverse proxy; the header is spoofable.

nginx, mounted under a path prefix (the studio's routes are prefix-aware):

```nginx
location /megabrain/demo/ask/stream {     # SSE needs buffering off + a long timeout
    proxy_pass http://127.0.0.1:2137/ask/stream;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
location /megabrain/demo/ {
    proxy_pass http://127.0.0.1:2137/;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

This is exactly how [bernardocastro.dev/megabrain/demo](https://bernardocastro.dev/megabrain/demo/)
runs. Want auth instead of anonymous access? `--token "$(openssl rand -hex 16)"` requires a
Bearer header on every route but `/health` and `/config` — and the studio picks the token up
from `?token=…` in the URL, so a tokenized link is all you share.

---

## Make it read a file type it doesn't know

```bash
megabrain forge ~/repo --list     # free census: which text types aren't indexed
megabrain forge ~/repo            # write + validate + install a chunker per type
```

An LLM writes a chunking strategy from the contract source and real sample files, and it is
accepted **only** after chunking every matching file into an exact line partition. Nothing
unvetted installs. Details and the trust model: [Guide §7](GUIDE.md#7-teach-it-your-file-types).

Already-covered type chunked badly? That's `--specialize`, and it's measure-only — see
[the chunk budget](GUIDE.md#the-chunk-budget) first, because the default is a real optimum.

---

## Search several repos at once

```bash
megabrain index  ~/api ~/web                              # index each once
megabrain search ~/api,~/web "how do they share auth"     # comma-separated roots
```

Results merge by score across repos. Scoping works the same way — any path *inside* an
indexed repo confines retrieval to files under it:

```bash
megabrain ask ~/api/src/billing "how is a refund issued"
```

Over MCP, pass `scope_path` instead.

---

## Use it as a Python library

Everything the CLI does is importable, lazily, with types:

```python
from megabrain import index_repo, load_state, search_with_state, prune_search
from megabrain.ask import ask, render_ask

index_repo("~/repo")                                    # incremental

state = load_state("~/repo")                            # warm: load the matrices once
for _ in range(100):                                    # …then query many times
    res = search_with_state(state, "retry logic")

signal = prune_search(state, "retry logic")             # flat ranked chunks + code
print(render_ask(ask("~/repo", "how does auth work")))  # narrated + spliced
```

Public API: `index_repo · search · search_with_state · prune_search · prune_search_root ·
render · render_pruned · get_code · load_state · Store · ChunkMeta · ChunkStrategy · Chunk ·
Symbol · FileResult · validate_partition` + the error taxonomy (`MegabrainError`,
`IndexNotFound`, `EmptyIndex`, `MissingAPIKey`, `ProviderError`).

Writing a custom chunker means implementing `ChunkStrategy` and passing it to
`index_repo(root, strategies=[MyStrategy()])` — the only hard requirement is that your
chunks form an exact line partition.

---

## Make ask cheaper, or faster

`ask` is **output-bound** — the narration dominates, not retrieval. So the model choice is
the whole lever:

```bash
export MEGABRAIN_ASK_MODEL=qwen/qwen3-coder          # cheapest: ~$0.0035/ask, ~14 s
export MEGABRAIN_ASK_MODEL=google/gemini-3-flash-preview   # ~2× faster, ~$0.007/ask
```

Three ways to pay less that aren't model swaps:

1. **Let the cache work.** A repeated question costs **$0 and ~0 ms**. On a team repo, warm
   the main workflows once (above) and most questions never reach an LLM again.
2. **Use `search --prune` when you don't need prose.** Zero LLM, ~200 ms, and a capable
   agent reads raw code fine.
3. **Skip the fan-out** on questions you know are narrow: `--no-agents` (or `agents: false`
   over MCP) keeps it to a single call.

The rerank has its own model knob (`MEGABRAIN_RERANK_MODEL`) and falls back to the ask
model — reranking is cheap, so any fast model works. On a remote HTTP lane the judge
sees full chunk bodies in parallel batches of 8 (`MEGABRAIN_RERANK_BATCH`; ~$0.009 and
~1.3 s per rerank on the default model); a local endpoint stays on a compact one-line
view so Ollama-class servers aren't fed parallel 9K-token prompts.

---

## Keep secrets and junk out of the index

```bash
megabrain scan ~/repo             # census: what WOULD index, and everything skipped + why
megabrain scan ~/repo --write     # write the proposed .megabrainignore
megabrain index ~/repo --scan     # show the census, then index honoring those filters
```

`.megabrainignore` sits at the repo root, one pattern per line — same idea as
`.gitignore`, but it has **no `!` negation**:

```
*.md
vendor/
tests/fixtures/
```

`scan` flags candidates with a reason (`gitignored` · `vendored` · `generated` ·
`too-big`), so you can see what the smart filters would drop before committing to them. A
plain `megabrain index` stays byte-identical — the filters only apply with `--scan`.

Deleting an index is `rm -rf ~/repo/.megabrain`. There's no command for it, on purpose.
