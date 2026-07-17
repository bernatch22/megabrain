"""Global registry of indexed repos: ~/.megabrain/registry.json.

Every successful index registers its repo here, so any frontend (CLI `repos`,
MCP `megabrain_index list=true`, studio `/repos`) can list EVERY repo indexed
on this machine — not just the one it was booted with. The per-repo truth
stays in each repo's own `.megabrain/db.sqlite`; this file is only a cheap,
rebuildable pointer list (path + last-index stats).

Fail-open everywhere: a corrupt/unwritable registry must never break an index
or a listing — indexing is the primary job, the registry is bookkeeping.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)


def registry_path() -> Path:
    """The registry file. `MEGABRAIN_REGISTRY` overrides (tests, sandboxes)."""
    env = os.environ.get("MEGABRAIN_REGISTRY")
    return Path(env).expanduser() if env else Path.home() / ".megabrain" / "registry.json"


def _read() -> dict:
    """{path: entry} or {} — tolerant of a missing/corrupt file."""
    f = registry_path()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(entries: dict) -> None:
    """Atomic write (tmp + replace) so a crash never truncates the registry."""
    f = registry_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(f)


def register(root: Path, stats: dict | None = None) -> None:
    """Upsert one repo after a successful index. Never raises."""
    try:
        root = Path(root).resolve()
        entries = _read()
        stats = stats or {}
        entries[root.as_posix()] = {
            "name": root.name,
            "path": root.as_posix(),
            "last_index": time.time(),
            "files": stats.get("files", 0),
            "chunks": stats.get("chunks", stats.get("new_chunks", 0)),
            "embed_model": stats.get("embed_model"),
        }
        _write(entries)
    except Exception:
        log.debug("registry register skipped", exc_info=True)


def unregister(root: Path) -> None:
    """Drop one repo from the registry. Never raises."""
    try:
        entries = _read()
        if entries.pop(Path(root).resolve().as_posix(), None) is not None:
            _write(entries)
    except Exception:
        log.debug("registry unregister skipped", exc_info=True)


def list_repos(validate: bool = True) -> list[dict]:
    """Registered repos, newest-indexed first. With `validate`, entries whose
    index no longer exists on disk are dropped (and the pruned list persisted)
    — the registry self-heals instead of accumulating dead pointers."""
    entries = _read()
    if validate:
        alive = {p: e for p, e in entries.items()
                 if (Path(p) / ".megabrain" / "db.sqlite").exists()}
        if len(alive) != len(entries):
            try:
                _write(alive)
            except Exception:
                log.debug("registry prune skipped", exc_info=True)
        entries = alive
    return sorted(entries.values(), key=lambda e: -(e.get("last_index") or 0))
