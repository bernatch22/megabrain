"""SQLite storage: chunks, vectors, skeletons, symbols, graph edges, file hashes.

Single file per repo: <repo>/.megabrain/db.sqlite
Vectors stored as float32 blobs; loaded into one numpy matrix at query time
(brute-force cosine is < 1ms up to ~50K chunks; HNSW deferred until needed).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

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
    raise ValueError(
        f"no megabrain index found at or above {p} — run `megabrain index` "
        f"on the repo root (looked for .megabrain/db.sqlite up the tree)")


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

    # ---- files / incremental

    def file_sha(self, path: str) -> str | None:
        r = self.db.execute("SELECT sha FROM files WHERE path=?", (path,)).fetchone()
        return r[0] if r else None

    def delete_file(self, path: str):
        self.db.execute("DELETE FROM chunks WHERE file=?", (path,))
        self.db.execute("DELETE FROM symbols WHERE file=?", (path,))
        self.db.execute("DELETE FROM edges WHERE src=? OR dst=?", (path, path))
        self.db.execute("DELETE FROM files WHERE path=?", (path,))

    def upsert_file(self, path: str, sha: str, skeleton: str, skel_vec: np.ndarray | None):
        blob = skel_vec.astype(np.float32).tobytes() if skel_vec is not None else None
        self.db.execute(
            "INSERT OR REPLACE INTO files(path, sha, skeleton, skel_vec) VALUES (?,?,?,?)",
            (path, sha, skeleton, blob))

    def insert_chunks(self, rows: list[tuple]):
        self.db.executemany(
            "INSERT INTO chunks(file,kind,name,part,start_line,end_line,text,breadcrumb,vec) "
            "VALUES (?,?,?,?,?,?,?,?,?)", rows)

    def insert_symbols(self, rows: list[tuple]):
        self.db.executemany(
            "INSERT INTO symbols(file,name,kind,line,end_line,signature,decorators,doc) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)

    def replace_edges(self, src: str, edges: list[tuple[str, str]]):
        self.db.execute("DELETE FROM edges WHERE src=?", (src,))
        self.db.executemany("INSERT OR IGNORE INTO edges(src,dst,kind) VALUES (?,?,?)",
                            [(src, dst, kind) for dst, kind in edges])

    def all_paths(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT path FROM files")}

    def commit(self):
        self.db.commit()

    # ---- query-time loads

    def load_matrix(self) -> tuple[list[dict], np.ndarray]:
        rows = self.db.execute(
            "SELECT id,file,kind,name,part,start_line,end_line,text,breadcrumb,vec "
            "FROM chunks WHERE vec IS NOT NULL ORDER BY id").fetchall()
        metas, vecs = [], []
        for r in rows:
            metas.append({"id": r[0], "file": r[1], "kind": r[2], "name": r[3],
                          "part": r[4], "start_line": r[5], "end_line": r[6],
                          "text": r[7], "breadcrumb": r[8]})
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

    def set_meta(self, k: str, v):
        self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", (k, json.dumps(v)))

    def get_meta(self, k: str):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None
