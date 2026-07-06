"""megabrain as a library: index -> search -> render -> warm state -> ask.

    python examples/01_programmatic.py [repo_path] ["question"]

Needs OPENROUTER_API_KEY (or a local OpenAI-compatible endpoint via
MEGABRAIN_EMBED_BASE_URL / MEGABRAIN_CHAT_BASE_URL — see the README).
"""

import sys

import megabrain

root = sys.argv[1] if len(sys.argv) > 1 else "."
question = sys.argv[2] if len(sys.argv) > 2 else "where is the main entry point"

# 1. Index (incremental by sha256 — instant when nothing changed).
stats = megabrain.index_repo(root, quiet=True)
print(f"indexed: {stats['files']} files, {stats['changed']} changed, "
      f"{stats['seconds']}s\n")

# 2. One-shot retrieval: no LLM, ~10-200ms. `res` is a plain dict —
#    tier1 = CORE files with full matching chunks, tier2 = RELATED map.
res = megabrain.search(root, question)
for t in res["tier1"]:
    print(f"CORE     {t['file']}  score={t['score']:.2f}")
for t in res["tier2"][:5]:
    print(f"RELATED  {t['file']}  score={t['score']:.2f}")

# 3. Or render the same result as the view-ready markdown map the CLI prints.
print("\n" + megabrain.render(res, compact=True)[:1500], "…\n")

# 4. Many queries against one repo? Load the state ONCE (matrices out of
#    SQLite) and reuse it — this is exactly what `megabrain serve-api` does.
state = megabrain.load_state(root)
for q in ("error handling", "configuration loading"):
    r = megabrain.search_with_state(state, q)
    top = r["tier1"][0]["file"] if r["tier1"] else "-"
    print(f"{r['ms']:>4}ms  {q!r:28} -> {top}")

# 5. The LLM walkthrough (module import — ask() retrieves, one chat call
#    narrates, the engine splices VERBATIM code into each [[k]] citation).
from megabrain.ask import ask, render_ask  # noqa: E402

out = ask(root, question, state=state)     # reuses the warm state
print("\n" + render_ask(out)[:2000], "…")
