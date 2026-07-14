"""Indexer: walk repo -> chunk -> embed -> store. Incremental by file sha256.
No daemon, no watcher: one command, runs in seconds on a warm cache."""

from __future__ import annotations

import hashlib
import logging
import time
from fnmatch import fnmatch
from pathlib import Path

from ..chunkers import embed_text, validate_partition
from ..providers.embeddings import Embedder
from ..storage.store import Store
from .strategies import all_exts, build_registry, load_repo_strategies, strategy_for

# Universal build/vendor/cache dirs only — anything project-specific belongs in
# the repo's own `.megabrainignore` (or `--exclude`), never baked in here.
EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist",
                "build", "coverage", ".next", ".nuxt", ".pytest_cache", ".tox",
                ".mypy_cache", ".ruff_cache", "target", "vendor", ".megabrain"}
AUTO_REFRESH_TTL = 60  # seconds; ask/query refresh a staler index before answering
MAX_FILE_BYTES = 600_000
IGNORE_FILE = ".megabrainignore"


def load_ignore(root: Path) -> list[str]:
    """User exclude patterns from `<root>/.megabrainignore` (one per line; blank
    lines and `#` comments skipped)."""
    f = root / IGNORE_FILE
    if not f.exists():
        return []
    out = []
    for ln in f.read_text(errors="replace").splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            out.append(ln)
    return out


def _split_patterns(patterns) -> tuple[set[str], list[str]]:
    """A bare token (no `/`, no glob char) matches any path SEGMENT; anything
    with `/` or a glob metachar is an fnmatch pattern on the repo-relative path."""
    names, globs = set(), []
    for p in patterns:
        p = p.strip().rstrip("/")
        if not p:
            continue
        if "/" in p or any(c in p for c in "*?["):
            globs.append(p)
        else:
            names.add(p)
    return names, globs


def _excluded(rel: str, names: set[str], globs: list[str]) -> bool:
    parts = rel.split("/")
    if names.intersection(parts):
        return True
    for g in globs:
        if rel == g or rel.startswith(g + "/") or fnmatch(rel, g) or fnmatch(rel, g + "/*"):
            return True
    return False


def discover(root: Path, exts: tuple[str, ...], exclude=()) -> list[Path]:
    names, globs = _split_patterns(exclude)
    names |= EXCLUDE_DIRS
    out = []
    for p in sorted(root.rglob("*")):
        if p.suffix not in exts or not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _excluded(rel, names, globs):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        out.append(p)
    return out


def maybe_reindex(root: Path, ttl: int = AUTO_REFRESH_TTL) -> bool:
    """Incrementally refresh the index when it's older than `ttl` seconds, so
    answers always match disk (CLI ask/query/chunks and the MCP server call
    this before retrieving). Fail-open: with no embedding credential or any
    index error, keep the existing index — a stale answer beats a crash."""
    from ..storage.store import Store
    try:
        with Store(Path(root)) as s:
            meta = s.get_meta("last_index")
        if meta and time.time() - meta["t"] <= ttl:
            return False
        index_repo(root)
        return True
    except Exception:
        logging.getLogger(__name__).debug("index auto-refresh skipped", exc_info=True)
        return False


def index_repo(root: Path, repo_name: str | None = None,
               force: bool = False, exclude=(), strategies=(),
               prune_flows: bool = True) -> dict:
    """Index/update a repo and RETURN the stats dict — the library never prints
    (rendering the result is the frontend's job). `strategies` injects custom
    ChunkStrategy instances (checked before the built-ins, so they can claim new
    extensions or override existing ones) — see the megabrain-examples repo.
    Trusted repo-local strategies (`.megabrain/strategies/*.py`) load
    automatically after them, so custom extensions survive every reindex."""
    root = Path(root).resolve()
    name = repo_name or root.name
    t0 = time.time()
    emb = Embedder()
    with Store(root) as store:
        return _index_into(store, emb, root, name, force=force,
                           exclude=exclude, strategies=strategies,
                           prune_flows=prune_flows, t0=t0)


def _index_into(store: Store, emb: Embedder, root: Path, name: str, *,
                force, exclude, strategies, prune_flows, t0) -> dict:
    """The indexing pipeline against an OPEN store — index_repo owns the
    connection lifecycle (with Store(...)), this owns the work."""
    registry = build_registry(name, extra=(*strategies,
                                           *load_repo_strategies(root, name)))
    # exclude = built-in dirs + `.megabrainignore` (persistent) + caller-supplied.
    excludes = [*load_ignore(root), *exclude]

    # A change of embedding model invalidates every stored vector (different
    # space/dims), so re-embed everything — not just sha-changed files. This
    # makes MEGABRAIN_EMBED_MODEL swaps safe: stale vectors never silently
    # linger. The instance's model is the truth (construction-time config).
    prev_model = store.get_meta("embed_model")
    if prev_model is not None and prev_model != emb.model:
        force = True
        logging.getLogger(__name__).info(
            "embed model changed (%s -> %s); re-embedding all", prev_model, emb.model)

    paths = discover(root, all_exts(registry), excludes)
    # POSIX relpaths everywhere: they're the DB keys and the engine matches
    # them with "/" (excludes, path filters, graph). str() yields "\" on
    # Windows — the source of cross-platform index corruption. Keep as_posix.
    rels = {p: p.relative_to(root).as_posix() for p in paths}
    sources = {rels[p]: p.read_text(errors="replace") for p in paths}

    # per-strategy whole-repo graph prepass (cheap; None for content with no graph)
    edge_ctx = {strat: strat.build_edge_ctx(sources, name) for strat in registry}

    changed, unchanged, removed = 0, 0, 0
    stats = {"chunks": 0, "violations": 0}
    for p in paths:
        rel = rels[p]
        src = sources[rel]
        strat = strategy_for(registry, rel)
        if strat is None:
            continue
        sha = hashlib.sha256(src.encode()).hexdigest()
        if not force and store.file_sha(rel) == sha:
            unchanged += 1
            continue
        store.delete_file(rel)
        r = strat.chunk_file(rel, src)
        if validate_partition(r):
            stats["violations"] += 1
        texts = [embed_text(c) for c in r.chunks]
        vecs = emb.embed(texts) if texts else None
        store.insert_chunks(r.chunks, vecs)
        store.insert_symbols(r.symbols)
        skel_vec = emb.embed([r.skeleton])[0] if r.skeleton else None
        store.upsert_file(rel, sha, r.skeleton, skel_vec)
        edges = strat.extract_edges(rel, src, edge_ctx[strat])
        if edges is not None:
            store.replace_edges(rel, edges)
        stats["chunks"] += len(r.chunks)
        changed += 1

    # orphans: indexed files no longer on disk — here incoming edges die too
    for gone in store.all_paths() - set(rels.values()):
        store.delete_file(gone, drop_incoming=True)
        removed += 1

    # flow invalidation: a cached ask synthesis dies with the code it cites —
    # any cited file whose sha changed (or vanished) drops the whole flow.
    # Store-level integrity (no import of the flows feature module — indexing
    # never depends upward). `prune_flows=False` keeps stale flows so
    # `flows --refresh` can re-ask them (it reindexes first to update shas,
    # then regenerates instead of dropping).
    stale_flows = store.prune_stale_flows() if prune_flows else 0

    store.set_meta("repo_name", name)
    store.set_meta("embed_model", emb.model)
    store.set_meta("last_index", {"t": time.time(), "files": len(paths)})
    store.commit()
    return {"files": len(paths), "changed": changed, "unchanged": unchanged,
            "removed": removed, "new_chunks": stats["chunks"],
            "partition_violations": stats["violations"],
            "stale_flows_pruned": stale_flows,
            "embed_tokens": emb.tokens, "embed_cost_usd": round(emb.cost, 6),
            "seconds": round(time.time() - t0, 2)}
