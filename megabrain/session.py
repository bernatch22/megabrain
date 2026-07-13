"""RepoSession — warm, self-invalidating retrieval state for one repo.

A long-running server (serve-api, or the MCP stdio process) loads the SQLite
matrices once and keeps them warm, so each query hits memory instead of
reloading. The Store connection is shared across worker threads
(check_same_thread=False), so a single lock guards every read; the state
reloads automatically when the index file (db mtime) changes on disk, so a
re-index or redeploy is picked up without a restart.

This used to live inside the serve-api handler (`_Repo`), which trapped warm
state to the HTTP transport while the MCP server paid a cold load per call.
Promoted here so both frontends share it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .docsearch import load_groups
from .retrieval.state import SearchState, load_state


class RepoSession:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.groups = load_groups(self.root)     # docsearch section names (optional)
        self.start = time.time()
        self._lock = threading.Lock()
        self._state: SearchState | None = None
        self._mtime = -1.0

    def _mtime_now(self) -> float:
        try:
            return (self.root / ".megabrain" / "db.sqlite").stat().st_mtime
        except OSError:
            return -1.0

    def with_state(self, fn):
        """Run fn(state) with the warm state, serialized. Reloads when the index
        file changes on disk. (The embedding network call runs under the lock
        too — fine for a docs search box / MCP query; revisit with per-thread
        connections if it ever needs heavy concurrency.)"""
        with self._lock:
            mt = self._mtime_now()
            if self._state is None or mt != self._mtime:
                if self._state is not None:
                    self._state.close()          # release the stale connection
                self._state = load_state(self.root, check_same_thread=False)
                self._mtime = mt
            return fn(self._state)
