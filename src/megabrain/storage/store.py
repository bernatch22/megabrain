"""SQLite storage: chunks, vectors, skeletons, symbols, graph edges, file hashes.

Single file per repo: <repo>/.megabrain/db.sqlite
Vectors stored as float32 blobs; loaded into one numpy matrix at query time
(brute-force cosine is < 1ms up to ~50K chunks; HNSW deferred until needed).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import numpy as np

from ..model import ChunkMeta

if TYPE_CHECKING:
    from ..chunkers import Chunk, Symbol


def resolve_root(path: Path) -> tuple[Path, str]:
    """Resolve a filesystem path to (repo_root, subpath).

    repo_root = the nearest ancestor (including `path` itself) that contains
    `.megabrain/db.sqlite`. subpath = `path` relative to that root as a POSIX
    string, or "" when `path` IS the root. Raises ValueError if no
    `.megabrain/db.sqlite` is found walking up.

    Enables PATH-SCOPE: `megabrain ask ~/repo/src/sub "…"` anchors the repo at
    ~/repo (where the index lives) and returns subpath "src/sub" to filter
    retrieval to files under it.
    """
    p = Path(path).expanduser().resolve()
    for anc in (p, *p.parents):
        if (anc / ".megabrain" / "db.sqlite").exists():
            rel = p.relative_to(anc).as_posix()
            return anc, ("" if rel == "." else rel)
    from ..errors import IndexNotFound
    raise IndexNotFound.at(p)


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha  TEXT NOT NULL,
    skeleton TEXT,
    skel_vec BLOB
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT NOT NULL,
    kind TEXT, name TEXT, part TEXT,
    start_line INTEGER, end_line INTEGER,
    text TEXT, breadcrumb TEXT,
    vec BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file);
CREATE TABLE IF NOT EXISTS symbols (
    file TEXT, name TEXT, kind TEXT,
    line INTEGER, end_line INTEGER,
    signature TEXT, decorators TEXT, doc TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT, dst TEXT, kind TEXT,        -- kind: import | call
    PRIMARY KEY (src, dst, kind)
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,               -- the ask that produced this flow
    text TEXT NOT NULL,                   -- the rendered walkthrough (prose+code)
    files TEXT NOT NULL,                  -- JSON {relpath: sha} of cited sources
    vec BLOB,                             -- question+prose embedding (ATTACH lane)
    qvec BLOB,                            -- question-only embedding (SERVE lane)
    created REAL                          -- unix time the flow was cached
);
"""


class Store:
    def __init__(self, repo_root: Path, check_same_thread: bool = True):
        # check_same_thread=False lets a long-running server (serve.py) read the
        # same connection from worker threads; the server serializes access with
        # a lock, so this stays safe. Default True keeps CLI/index behaviour.
        self.root = Path(repo_root)
        d = self.root / ".megabrain"
        d.mkdir(exist_ok=True)
        self.db = sqlite3.connect(d / "db.sqlite", check_same_thread=check_same_thread)
        self.db.executescript(SCHEMA)
        for col in ("qvec BLOB", "created REAL"):
            try:                    # migrate older flow tables in place
                self.db.execute(f"ALTER TABLE flows ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass                # column already exists

    def close(self):
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- files / incremental

    def file_sha(self, path: str) -> str | None:
        r = self.db.execute("SELECT sha FROM files WHERE path=?", (path,)).fetchone()
        return r[0] if r else None

    def delete_file(self, path: str, drop_incoming: bool = False):
        """Remove a file's rows before re-inserting (re-index) or for good (orphan).

        Outgoing edges (src=path) always go — they're rebuilt from the new source.
        Incoming edges (dst=path) drop ONLY for orphans (drop_incoming=True): on a
        normal re-index the importers' A->B edges are still valid, and deleting
        them here silently destroyed every edge whose src file happened to be
        processed before its dst in the same indexing pass."""
        self.db.execute("DELETE FROM chunks WHERE file=?", (path,))
        self.db.execute("DELETE FROM symbols WHERE file=?", (path,))
        self.db.execute("DELETE FROM edges WHERE src=?", (path,))
        if drop_incoming:
            self.db.execute("DELETE FROM edges WHERE dst=?", (path,))
        self.db.execute("DELETE FROM files WHERE path=?", (path,))

    def upsert_file(self, path: str, sha: str, skeleton: str, skel_vec: np.ndarray | None):
        blob = skel_vec.astype(np.float32).tobytes() if skel_vec is not None else None
        self.db.execute(
            "INSERT OR REPLACE INTO files(path, sha, skeleton, skel_vec) VALUES (?,?,?,?)",
            (path, sha, skeleton, blob))

    def insert_chunks(self, chunks: Sequence["Chunk"], vecs) -> None:
        """Persist chunker output. Row packing lives HERE — the column order
        exists in exactly one file (this one), matched by load_matrix below."""
        self.db.executemany(
            "INSERT INTO chunks(file,kind,name,part,start_line,end_line,text,breadcrumb,vec) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(c.file, c.kind, c.name, c.part, c.start_line, c.end_line,
              c.text, c.breadcrumb, vecs[i].astype(np.float32).tobytes())
             for i, c in enumerate(chunks)])

    def insert_symbols(self, symbols: Sequence["Symbol"]) -> None:
        """Persist chunker symbols; serialization policy (decorators as JSON)
        is the store's knowledge, not the indexer's."""
        self.db.executemany(
            "INSERT INTO symbols(file,name,kind,line,end_line,signature,decorators,doc) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(s.file, s.name, s.kind, s.line, s.end_line, s.signature,
              json.dumps(s.decorators), s.doc) for s in symbols])

    def replace_edges(self, src: str, edges: list[tuple[str, str]]):
        self.db.execute("DELETE FROM edges WHERE src=?", (src,))
        self.db.executemany("INSERT OR IGNORE INTO edges(src,dst,kind) VALUES (?,?,?)",
                            [(src, dst, kind) for dst, kind in edges])

    def all_paths(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT path FROM files")}

    def commit(self):
        self.db.commit()

    # ---- query-time loads

    def load_matrix(self) -> tuple[list[ChunkMeta], np.ndarray]:
        rows = self.db.execute(
            "SELECT id,file,kind,name,part,start_line,end_line,text,breadcrumb,vec "
            "FROM chunks WHERE vec IS NOT NULL ORDER BY id").fetchall()
        metas, vecs = [], []
        for r in rows:
            metas.append(ChunkMeta(id=r[0], file=r[1], kind=r[2], name=r[3],
                                   part=r[4], start_line=r[5], end_line=r[6],
                                   text=r[7], breadcrumb=r[8]))
            vecs.append(np.frombuffer(r[9], dtype=np.float32))
        M = np.stack(vecs) if vecs else np.zeros((0, 1))
        return metas, M

    def load_file_matrix(self) -> tuple[list[str], list[str], np.ndarray]:
        rows = self.db.execute(
            "SELECT path, skeleton, skel_vec FROM files WHERE skel_vec IS NOT NULL").fetchall()
        paths = [r[0] for r in rows]
        skels = [r[1] or "" for r in rows]
        M = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows]) if rows \
            else np.zeros((0, 1))
        return paths, skels, M

    def all_edges(self) -> list[tuple[str, str, str]]:
        """Every (src, dst, kind) edge — the graph feature's raw material."""
        return [(r[0], r[1], r[2]) for r in
                self.db.execute("SELECT src, dst, kind FROM edges")]

    def file_chunks(self, path: str) -> list[dict]:
        """Every chunk of one file in line order (no vectors) — the graph node
        view splices these verbatim, same anti-hallucination stance as ask."""
        rows = self.db.execute(
            "SELECT id,kind,name,part,start_line,end_line,text,breadcrumb "
            "FROM chunks WHERE file=? ORDER BY start_line", (path,)).fetchall()
        return [{"id": r[0], "kind": r[1], "name": r[2], "part": r[3],
                 "start_line": r[4], "end_line": r[5], "text": r[6],
                 "breadcrumb": r[7]} for r in rows]

    def neighbors(self, path: str) -> set[str]:
        out = set()
        for r in self.db.execute("SELECT dst FROM edges WHERE src=?", (path,)):
            out.add(r[0])
        for r in self.db.execute("SELECT src FROM edges WHERE dst=?", (path,)):
            out.add(r[0])
        return out

    def symbols_for(self, path: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT name,kind,line,end_line,signature,doc FROM symbols WHERE file=? ORDER BY line",
            (path,)).fetchall()
        return [{"name": r[0], "kind": r[1], "line": r[2], "end_line": r[3],
                 "signature": r[4], "doc": r[5]} for r in rows]

    def symbol_names(self) -> dict[str, int]:
        """BARE symbol name -> definition count, repo-wide. The navigator only
        links a name whose jump is UNAMBIGUOUS: defined in the current file,
        or count == 1 — a link that could land anywhere is worse than none."""
        counts: dict[str, int] = {}
        for (name,) in self.db.execute("SELECT name FROM symbols"):
            if name:
                bare = name.rsplit(".", 1)[-1]
                counts[bare] = counts.get(bare, 0) + 1
        return counts

    def find_symbols(self, name: str) -> list[dict]:
        """Definitions matching a bare symbol name repo-wide (exact, or the
        last segment of a qualified `Class.method`) — go-to-definition for the
        studio's code navigator. Uses idx_symbols_name."""
        rows = self.db.execute(
            "SELECT file,name,kind,line,end_line,signature FROM symbols "
            "WHERE name=? OR name LIKE '%.' || ? ORDER BY file, line",
            (name, name)).fetchall()
        return [{"file": r[0], "name": r[1], "kind": r[2], "line": r[3],
                 "end_line": r[4], "signature": r[5]} for r in rows]

    def stats(self) -> dict:
        """Index shape counts — the store's knowledge, so no frontend ever
        runs raw SQL against the schema."""
        count = lambda t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
        return {"files": count("files"), "chunks": count("chunks"),
                "symbols": count("symbols"), "edges": count("edges"),
                "last_index": self.get_meta("last_index")}

    def set_meta(self, k: str, v):
        self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", (k, json.dumps(v)))

    def get_meta(self, k: str):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None

    # ---- flow cache (self-caching workflow retrieval — see flows.py)

    def insert_flow(self, question: str, text: str, files: dict, vec: np.ndarray,
                    qvec: np.ndarray | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO flows(question,text,files,vec,qvec,created) VALUES (?,?,?,?,?,?)",
            (question, text, json.dumps(files, sort_keys=True),
             vec.astype(np.float32).tobytes(),
             qvec.astype(np.float32).tobytes() if qvec is not None else None,
             time.time()))
        return cur.lastrowid

    def delete_flow(self, flow_id: int):
        self.db.execute("DELETE FROM flows WHERE id=?", (flow_id,))

    def stale_flows(self) -> list[dict]:
        """Flows whose cited files changed sha or vanished from the index.
        Store-level INTEGRITY knowledge (pure SQL + sha compare, no LLM):
        a cached walkthrough is valid only while its sources are unchanged."""
        metas, _, _ = self.load_flows()
        current = {r[0]: r[1] for r in self.db.execute("SELECT path, sha FROM files")}
        return [m for m in metas
                if any(current.get(f) != sha for f, sha in m["files"].items())]

    def prune_stale_flows(self) -> int:
        """Drop stale flows (see stale_flows). Called by index_repo after every
        (re)index so flows always describe current code — the cheap, no-LLM
        default; flows.refresh_stale is the opt-in re-ask UPDATE instead."""
        stale = self.stale_flows()
        for m in stale:
            self.delete_flow(m["id"])
        return len(stale)

    def load_flows(self) -> tuple[list[dict], np.ndarray, np.ndarray]:
        """(metas, attach matrix, question-only matrix). Rows cached before the
        qvec migration get a zero qvec — they still attach, never serve."""
        rows = self.db.execute(
            "SELECT id,question,text,files,vec,qvec,created FROM flows WHERE vec IS NOT NULL "
            "ORDER BY id").fetchall()
        metas = [{"id": r[0], "question": r[1], "text": r[2],
                  "files": json.loads(r[3]), "created": r[6]} for r in rows]
        if not rows:
            return metas, np.zeros((0, 1)), np.zeros((0, 1))
        M = np.stack([np.frombuffer(r[4], dtype=np.float32) for r in rows])
        dims = M.shape[1]
        Q = np.stack([np.frombuffer(r[5], dtype=np.float32) if r[5] is not None
                      else np.zeros(dims, dtype=np.float32) for r in rows])
        return metas, M, Q
