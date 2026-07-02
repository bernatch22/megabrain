"""Indexer: walk repo -> chunk -> embed -> store. Incremental by file sha256.
No daemon, no watcher: one command, runs in seconds on a warm cache."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .chunker import embed_text, validate_partition
from .embeddings import MODEL as EMBED_MODEL
from .embeddings import Embedder
from .store import Store
from .strategies import all_exts, build_registry, strategy_for

EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", "dist",
                "build", ".megabrain", "logs", "data", "src.bkp", ".brainbank",
                "coverage", ".next",
                # benchmark/eval scratch (checked-out foreign repos — not our source)
                "clones", "wt", "wt_ask", "wt_best", ".pytest_cache"}
MAX_FILE_BYTES = 600_000


def discover(root: Path, exts: tuple[str, ...]) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if p.suffix not in exts or not p.is_file():
            continue
        if EXCLUDE_DIRS & set(p.parts):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        out.append(p)
    return out


def index_repo(root: Path, repo_name: str | None = None, quiet: bool = False,
               force: bool = False) -> dict:
    root = Path(root).resolve()
    name = repo_name or root.name
    t0 = time.time()
    store = Store(root)
    emb = Embedder()
    registry = build_registry(name)

    # A change of embedding model invalidates every stored vector (different
    # space/dims), so re-embed everything — not just sha-changed files. This
    # makes MEGABRAIN_EMBED_MODEL swaps safe: stale vectors never silently linger.
    prev_model = store.get_meta("embed_model")
    if prev_model is not None and prev_model != EMBED_MODEL:
        force = True
        if not quiet:
            print(f"embed model changed ({prev_model} -> {EMBED_MODEL}); re-embedding all")

    paths = discover(root, all_exts(registry))
    rels = {p: str(p.relative_to(root)) for p in paths}
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
        rows = [(c.file, c.kind, c.name, c.part, c.start_line, c.end_line,
                 c.text, c.breadcrumb, vecs[i].astype("float32").tobytes())
                for i, c in enumerate(r.chunks)]
        store.insert_chunks(rows)
        store.insert_symbols([
            (s.file, s.name, s.kind, s.line, s.end_line, s.signature,
             json.dumps(s.decorators), s.doc) for s in r.symbols])
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

    store.set_meta("repo_name", name)
    store.set_meta("embed_model", EMBED_MODEL)
    store.set_meta("last_index", {"t": time.time(), "files": len(paths)})
    store.commit()
    result = {"files": len(paths), "changed": changed, "unchanged": unchanged,
              "removed": removed, "new_chunks": stats["chunks"],
              "partition_violations": stats["violations"],
              "embed_tokens": emb.tokens, "embed_cost_usd": round(emb.cost, 6),
              "seconds": round(time.time() - t0, 2)}
    if not quiet:
        print(json.dumps(result, indent=1))
    return result
