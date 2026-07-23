"""app — the application-service layer: one use-case per verb.

The three frontends (CLI, MCP, serve-api) used to each hand-roll the same
pre-steps: resolve a repo root from a possibly-sub-path, join a bare filename
onto a scope, normalize the agents tri-state, decide whether to auto-reindex.
That produced 4 copies of the rel-join fallback, 3 different agents parsings,
and an inconsistent reindex policy. Those pre-steps live HERE now; a frontend
maps its transport args to a use-case call and renders the result — nothing
more. Each use-case preserves the behavior of the strictest current caller;
where the frontends diverged, the docstring says which behavior won.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ── shared pre-steps (the de-duplicated logic) ─────────────────────────────

def resolve_scope(path: str | Path, scope_path: str | None = None) -> tuple[Path, str | None]:
    """(repo_root, path_filter) for PATH-SCOPE. `path` may be a sub-path inside
    an indexed repo; an explicit `scope_path` is appended to it. path_filter is
    None at the root. Raises IndexNotFound when no index is found up the tree."""
    from .storage.store import resolve_root
    p = Path(path).expanduser()
    sub = (scope_path or "").strip().strip("/")
    if sub:
        p = p / sub
    root, subpath = resolve_root(p)
    return root, (subpath or None)


def rel_join(root: Path, sub: str | None, rel: str) -> str:
    """THE one copy of the 'bare file under a sub-path' fallback (was pasted 4×
    across CLI/MCP get+chunks): `megabrain get ~/repo/src dispatch.ts` finds
    src/dispatch.ts when the bare name doesn't exist at the root but does under
    the scope."""
    if sub and not (Path(root) / rel).exists() and (Path(root) / sub / rel).exists():
        return (Path(sub) / rel).as_posix()
    return rel


def normalize_agents(value: Any) -> bool | None:
    """The multi-agent tri-state, parsed ONE way (was 3): None or "auto" ->
    None (AUTO: fan out only when the question is broad); anything else -> its
    truthiness (True forces the fan-out, False forbids it)."""
    if value is None or value == "auto":
        return None
    return bool(value)


def _maybe_reindex(root: Path, reindex: bool) -> None:
    if reindex:
        from .indexing.indexer import maybe_reindex
        maybe_reindex(root)          # answers match disk (60s TTL, fail-open)


# ── use-cases (one per verb; thin — the engine does the work) ───────────────

# THE content policy, in one place. Search is CODE or DOCS — never a blend:
# with both indexed, a big README wins on prose-shaped questions and buries the
# implementation (sinatra's README took CORE from lib/sinatra/base.rb on "how
# are routes defined and dispatched?" the moment docs entered its index). `ask`
# has always resolved this by excluding docs before scoring; `search` now does
# the same, and `docs=True` flips the whole bundle to markdown instead. The
# retrieval primitives stay neutral — this is the only place that decides.
# Public because serve-api can't route through the use-cases below: it holds a
# WARM SearchState and calls the retrieval primitives directly, so it imports
# the policy instead of restating it.
def content_filters(docs: bool) -> dict:
    return {"exclude_docs": not docs, "only_docs": bool(docs)}


def query(root: Path, task: str, path_filter: str | None = None,
          reindex: bool = True, docs: bool = False) -> dict:
    """Single-repo retrieval bundle over the CODE; `docs=True` searches the
    indexed markdown instead. Both sides fail open, so a docs-only repo still
    answers a code search (and vice versa)."""
    from .retrieval.bundle import search
    _maybe_reindex(root, reindex)
    return search(root, task, path_filter=path_filter, **content_filters(docs))


def query_multi(roots: list[Path], task: str,
                path_filters: list[str | None] | None = None,
                reindex: bool = True, docs: bool = False) -> dict:
    """Cross-repo retrieval (CLI comma-separated paths)."""
    from .retrieval.bundle import search_multi
    if reindex:
        for r in dict.fromkeys(roots):
            _maybe_reindex(r, True)
    return search_multi(roots, task, path_filters=path_filters, **content_filters(docs))


def prune(root: Path, task: str, path_filter: str | None = None,
          with_text: bool = True, include_pruned: bool = False,
          reindex: bool = True, llm_rerank: bool = False,
          docs: bool = False, expand: bool = False,
          model: str | None = None, with_docs: bool = False) -> dict:
    """No-LLM noise pruning -> flat ranked signal chunks, over the CODE;
    `docs=True` prunes the indexed markdown instead. `expand` runs the
    shared expander first (one cheap LLM call names the mechanism terms the
    query lacks, a second deterministic pass widens the pool) and
    `llm_rerank` adds the judge on top (drop vocabulary-only matches,
    reorder). Both fail open, so the deterministic floor never drops.

    `with_docs` (code searches only) runs the SAME pruner a second time over
    the DOCS, reusing the expander's terms so it costs no extra LLM call, and
    attaches the related doc files as `res["related_docs"]`. A feature fix
    touches three layers — code, tests, docs — but a code-only search shows
    one; the agent then hunts the docs with host greps (field run: the click
    aliases task burned minutes on `grep aliases docs/`). One search now
    returns all three."""
    from .retrieval.bundle import prune_search
    from .retrieval.state import load_state
    _maybe_reindex(root, reindex)
    cf = content_filters(docs)
    with load_state(Path(root)) as st:
        res = prune_search(st, task, path_filter=path_filter,
                           with_text=with_text, include_pruned=include_pruned,
                           **cf)
        terms: list[str] = []
        if expand:
            from .retrieval.mapcard import expand_pool
            ex = expand_pool(st, task, res, model,
                             path_filter=path_filter, with_text=with_text, **cf)
            terms = (ex or {}).get("terms", []) or []
        # the judge runs BEFORE the docs/tests closures so they read the
        # JUDGED surface (kept + set-aside), not the raw det order — on the
        # click aliases task Command.__init__ sat past det rank 12 but the
        # judge kept it, and the closure missed to_info_dict (and with it
        # tests/test_info_dict.py) by looking at the wrong list
        if llm_rerank:
            from .retrieval.rerank import llm_rerank as _rerank
            res = _rerank(res, task, model=model)
        if with_docs and not docs:
            # reuse the expander's mechanism terms; the docs corpus is small,
            # so a single deterministic pass (no rerank) picks the right files
            q = task + ((" " + " ".join(terms)) if terms else "")
            dres = prune_search(st, q, path_filter=path_filter,
                                with_text=False, only_docs=True,
                                exclude_docs=False)
            seen: list[str] = []
            for c in dres["chunks"]:
                if c["file"] not in seen:
                    seen.append(c["file"])
            # carry the best chunk's SPAN, not just the file — a bare file
            # name makes the agent read the whole doc (field run: 426 lines
            # of commands-and-groups.md fetched for an ~80-line section)
            res["related_docs"] = [
                {"file": f,
                 "start_line": next(c["start_line"] for c in dres["chunks"]
                                    if c["file"] == f),
                 "end_line": next(c["end_line"] for c in dres["chunks"]
                                  if c["file"] == f)}
                for f in seen[:5]]
            # The changelog is a FIXED edit target of any behavior change —
            # not a retrieval question. Ranked retrieval reliably misses it
            # (its entries describe OTHER features), and the duel agent
            # guessed CHANGES.rst on an .md repo and burned a recovery turn.
            # Deterministic: if the repo has one, it is always listed, named.
            listed = {d["file"] for d in res["related_docs"]}
            for name in ("CHANGES.md", "CHANGELOG.md", "CHANGES.rst",
                         "CHANGELOG.rst", "HISTORY.md", "NEWS.md"):
                if not (Path(root) / name).is_file():
                    continue
                if name not in listed:
                    res["related_docs"].append(
                        {"file": name, "start_line": 1})
                # flag by NAME, not by which path added it — when the docs
                # pruner itself surfaces the changelog, the entry must still
                # carry the label
                for d in res["related_docs"]:
                    if d["file"] == name:
                        d["changelog"] = True
                break
            # Deterministic pinning-tests closure. The judge-dependent tests
            # bucket appears and disappears with the pool (field runs: the
            # same click task phrased two ways produced two different test
            # sections). The tests that PIN a mechanism are the test files
            # that NAME its symbols — a literal scan over the indexed test
            # corpus, megabrain_grep's TESTS-role doctrine: zero LLM, stable
            # across phrasings. Symbols come from the judged surface (kept +
            # set-aside; det head when the judge is off), multi-word only
            # ("command" alone would match every test in the repo).
            from .retrieval.scoring import _is_test_path
            syms: list[str] = []
            # the whole set-aside joins the head: it is small (<=8) and holds
            # exactly the near-tie spans whose symbols the judge undervalued
            surface = list(res["chunks"])[:12] + list(res.get("setaside") or [])
            for c in surface:
                for n in (c.get("name") or "").split(","):
                    n = n.strip().rsplit(".", 1)[-1]
                    if (len(n) >= 6 and n not in syms
                            and ("_" in n or any(ch.isupper()
                                                 for ch in n[1:]))):
                        syms.append(n)
            if syms:
                import re as _re
                hits: dict[str, dict] = {}
                for file, start, text in st.store.db.execute(
                        "SELECT file, start_line, text FROM chunks"):
                    if not _is_test_path(file):
                        continue
                    n_hit = sum(1 for s in syms if s in text)
                    if not n_hit:
                        continue
                    # a test file NAMED after a mechanism symbol is its
                    # DEDICATED spec and must dominate the n=1 tie-break
                    # (field run: test_info_dict.py lost a tie against
                    # test_defaults.py and fell off the cap — the one file
                    # whose golden the feature was guaranteed to change)
                    base = _re.sub(r"^test[_-]?|[_-]?tests?$",
                                   "", file.rsplit("/", 1)[-1].rsplit(".", 1)[0])
                    if base and any(base in s or s in base for s in syms):
                        n_hit += 10
                    h = hits.setdefault(file, {"file": file,
                                               "start_line": start, "n": 0})
                    h["n"] = max(h["n"], n_hit)
                res["related_tests"] = sorted(
                    hits.values(), key=lambda h: -h["n"])[:8]
    return res


def ask(root: Path, question: str, path_filter: str | None = None,
        docs_only: bool = False,
        agents: Any = None, reindex: bool = True,
        model: str | None = None) -> dict:
    """Buffered ask (MCP / POST /ask). `agents` accepts the raw transport value;
    normalized here. `model` overrides the narrator model for this call (the UI
    model picker); None = the provider default. Returns the ask() out dict
    (render with ask.render_ask)."""
    from .ask import ask as _ask
    _maybe_reindex(root, reindex)
    return _ask(root, question, docs_only=docs_only,
                path_filter=path_filter, agents=normalize_agents(agents),
                model=model)


def graph(root: Path, mode: str = "map", node: str | None = None,
          source: str | None = None, target: str | None = None,
          path_filter: str | None = None, reindex: bool = True,
          label: bool = True) -> dict:
    """Knowledge-graph views over the indexed repo: map (communities + god
    nodes + surprises), node (one file: neighbors + real code), path (BFS
    between two concepts, endpoints resolved by embedding). `label=False`
    skips the (cached, fail-open) LLM community labels."""
    from .graph import graph_root
    _maybe_reindex(root, reindex)
    return graph_root(root, mode=mode, node=node, source=source,
                      target=target, path_filter=path_filter, label=label)


def grep(root: Path, pattern: str, regex: bool = False,
         ignore_case: bool = False, path_filter: str | None = None,
         reindex: bool = True) -> dict:
    """Literal search that understands what it found: every match resolved to
    its enclosing symbol and classified (defines/reads/config/tests/docs),
    reads ranked by graph centrality with their incoming edges shown. Zero
    LLM, no vectors loaded — one pass over the indexed chunk text."""
    from .retrieval.grepx import grep_repo
    _maybe_reindex(root, reindex)
    return grep_repo(root, pattern, regex=regex, ignore_case=ignore_case,
                     path_filter=path_filter)


def get(root: Path, sub: str | None, file: str, symbol: str | None = None) -> str:
    """One file or symbol. Owns resolve+rel_join so a bare name under a scope
    resolves. NOTE: get does NOT auto-reindex — faithful to today's callers
    (CLI/MCP get skip the refresh; a raw file read doesn't need a fresh index)."""
    from .retrieval.files import get_code
    return get_code(root, rel_join(root, sub, file), symbol)


def chunks(root: Path, sub: str | None, file: str, query_str: str,
           path_filter: str | None = None, reindex: bool = True) -> dict:
    """Every chunk of one file, scored + selected flags."""
    from .retrieval.bundle import chunks_for_file_root
    _maybe_reindex(root, reindex)
    return chunks_for_file_root(root, rel_join(root, sub, file), query_str,
                                path_filter=path_filter)


def index(root: Path, force: bool = False, exclude=(),
          scan_filters: bool = False) -> dict:
    """Incremental index/update — returns stats; the caller renders them.
    `scan_filters` (opt-in) honors .gitignore + skips vendored/generated."""
    from .indexing.indexer import index_repo
    return index_repo(root, force=force, exclude=exclude, scan_filters=scan_filters)


def scan(root: Path) -> dict:
    """Index-intelligence census (no indexing): what WOULD index + every
    skipped candidate with its reason. Powers `megabrain scan` and GET /scan."""
    from .indexing.ignore import scan as _scan_census
    from .indexing.indexer import EXCLUDE_DIRS, load_ignore
    from .indexing.strategies import all_exts, build_registry, load_repo_strategies
    root = Path(root).resolve()
    reg = build_registry(root.name, extra=tuple(load_repo_strategies(root, root.name)))
    return _scan_census(root, all_exts(reg), exclude=load_ignore(root),
                        extra_names=EXCLUDE_DIRS)


def stats(root: Path) -> dict:
    """Index shape counts (no raw SQL in the frontend — Store owns it)."""
    from .storage.store import Store
    with Store(root) as s:
        return s.stats()


def flows_list(root: Path) -> dict:
    """The flow cache as a listing: every cached ask (id, question, cited
    files, when, size) WITHOUT the stored text — the light shape for a list
    view. `stale` marks flows whose cited files changed since caching (they
    survive until the next index prunes them)."""
    from .storage.flows import enabled, files_current
    from .storage.store import Store
    with Store(root) as s:
        metas, _, _ = s.load_flows()
    # staleness vs DISK, the same check the serve path makes — NOT the index's
    # shas (Store.stale_flows), which can lag disk by the 60 s refresh TTL and
    # would flag a perfectly serveable flow as doomed.
    return {"enabled": enabled(root),
            "flows": [{"id": m["id"], "question": m["question"],
                       "files": sorted(m["files"]), "created": m["created"],
                       "chars": len(m["text"]),
                       "stale": not files_current(root, m["files"])}
                      for m in reversed(metas)]}      # newest first


def flow_get(root: Path, flow_id: int) -> dict:
    """One cached flow in full — the stored walkthrough (prose + real code
    spliced at cache time) for the viewer."""
    from .errors import MegabrainError
    from .storage.store import Store
    with Store(root) as s:
        for m in s.load_flows()[0]:
            if m["id"] == flow_id:
                return {"id": m["id"], "question": m["question"],
                        "files": sorted(m["files"]), "created": m["created"],
                        "text": m["text"]}
    e = MegabrainError(f"no cached flow with id {flow_id}")
    e.http_status = 404
    raise e


def flow_delete(root: Path, flow_id: int) -> dict:
    from .storage.store import Store
    with Store(root) as s:
        s.delete_flow(flow_id)
        s.commit()
    return {"deleted": flow_id}


def example_queries(root: Path, limit: int = 6) -> dict:
    """Starter questions for the studio's Ask chips — EVERY indexed repo gets
    some, so a newcomer never faces a blank box. Three tiers, best first:

      "file"    <root>/.megabrainqueries — the repo AUTHORED its main
                workflows (one per line, `#` comments). Committed intent wins.
      "flows"   the questions already in the flow cache. These are the best
                fallback by far: each one's answer is CACHED, so clicking the
                chip serves instantly with no LLM and no rate-limit cost.
      "derived" deterministic, no-LLM questions over the repo's central files
                (see ask.warmup.derive_questions) — always something.

    `source` tells the UI which tier it got so it can label the row honestly
    (an already-answered question is worth advertising as instant)."""
    from .ask.warmup import authored_questions, derive_questions
    authored = authored_questions(root)
    if authored:
        return {"source": "file", "queries": authored[:limit]}
    try:
        from .storage.store import Store
        with Store(Path(root)) as s:
            cached = [m["question"] for m in reversed(s.load_flows()[0])]
        if cached:
            return {"source": "flows", "queries": cached[:limit]}
    except Exception:                       # noqa: BLE001 — never break the chips
        pass
    return {"source": "derived", "queries": derive_questions(root, limit)}
