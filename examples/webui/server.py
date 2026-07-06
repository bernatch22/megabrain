#!/usr/bin/env python3
"""megabrain web demo — search a repo and watch chunk selection in real time.

    python examples/webui/server.py [repo ...]

With no arguments it serves the bundled `legacy-php-app` sample (a faithful
2003-style procedural PHP app — business logic buried in HTML/SQL noise),
indexing it on first run. Any extra repo paths on the command line are loaded
too, and the UI can load more at runtime: pick "Other…", type an absolute repo
path, and it's indexed on demand.

Flow in the UI: type a question -> the real engine ranks the bundle files
(CORE/RELATED) -> click a file -> every chunk of it appears scored, with the
chunks the retrieval actually SELECTED highlighted and the noise dimmed.
Nothing is canned: each query runs `search_with_state` + `chunks_for_file`,
the same code path agents use.

Single port, stdlib only. State stays warm per repo (matrices loaded once,
auto-reload when an index changes on disk). Queries need an embedding key —
OPENROUTER_API_KEY, or a local endpoint via MEGABRAIN_EMBED_BASE_URL.

Local demo: binds 127.0.0.1 and indexes whatever local path you give it, so
run it only on repos you trust on your own machine.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from megabrain.indexer import index_repo
from megabrain.query import chunks_for_file, search_with_state
from megabrain.serve import _Repo

PORT = 8688
HERE = Path(__file__).parent
SAMPLE = HERE / "legacy-php-app"

# Suggested queries for the bundled sample (known ground truth). User-loaded
# repos get none — you type your own.
SUGGESTED = {
    "legacy-php-app": [
        "how is the invoice total computed, with tax and discount?",
        "where is login validated and the permission level checked?",
        "where is stock decremented when an order is confirmed?",
        "where is the sales report per customer generated?",
        "how is the client category discount computed?",
    ],
}

_repos: dict[str, _Repo] = {}
_lock = threading.Lock()


def add_repo(path_str: str) -> str:
    """Index (if needed) and register a repo, returning its display name.
    Idempotent: a path that's already loaded returns its existing name."""
    root = Path(path_str).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    with _lock:
        for name, r in _repos.items():
            if r.root == root:
                return name
    if not (root / ".megabrain" / "db.sqlite").exists():
        print(f"indexing {root} (first run)…", flush=True)
        stats = index_repo(root, quiet=True)
        if stats["files"] == 0:
            raise ValueError(f"no indexable source files under {root}")
    with _lock:
        name = root.name
        while name in _repos:                     # disambiguate same basename
            name += "·"
        _repos[name] = _Repo(root)
        return name


def repo_meta(name: str) -> dict:
    r = _repos[name]
    return {
        "name": name,
        "files": r.with_state(lambda st: sorted(st.fpaths)),
        "chunks": r.with_state(lambda st: len(st.metas)),
        "suggested": SUGGESTED.get(name, []),
    }


def _slim_search(res: dict) -> dict:
    """The file-ranking view only needs names/scores — chunk text stays in /api/chunks."""
    return {
        "query": res["query"], "repo": res["repo"], "ms": res["ms"],
        "tier1": [{"file": t["file"], "score": t["score"],
                   "chunks": len(t["chunks"]),
                   "matched": [c["name"] for c in t["chunks"] if c["name"]][:4]}
                  for t in res["tier1"]],
        "tier2": [{"file": t["file"], "score": t["score"],
                   "via_graph": t["via_graph"], "matched": t["matched"][:4]}
                  for t in res["tier2"]],
    }


def make_handler():
    ui = (HERE / "ui" / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json"):
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload):
            self._send(code, json.dumps(payload).encode())

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            arg = lambda k: (qs.get(k) or [""])[0].strip()  # noqa: E731
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, ui, "text/html")
                if u.path == "/api/meta":
                    return self._json(200, {"repos": [repo_meta(n) for n in _repos]})
                if u.path == "/api/add":
                    p = arg("path")
                    if not p:
                        return self._json(400, {"error": "missing path"})
                    try:
                        name = add_repo(p)
                    except Exception as e:
                        return self._json(400, {"error": str(e)})
                    return self._json(200, repo_meta(name))
                repo = _repos.get(arg("repo") or (next(iter(_repos)) if _repos else ""))
                if repo is None:
                    return self._json(404, {"error": "unknown repo"})
                if u.path == "/api/search":
                    q = arg("q")
                    if not q:
                        return self._json(400, {"error": "missing q"})
                    res = repo.with_state(lambda st: search_with_state(st, q))
                    return self._json(200, _slim_search(res))
                if u.path == "/api/chunks":
                    f, q = arg("file"), arg("q")
                    if not f or not q:
                        return self._json(400, {"error": "missing file or q"})
                    return self._json(200, repo.with_state(
                        lambda st: chunks_for_file(st, f, q)))
                return self._json(404, {"error": "not found"})
            except Exception as e:              # surface engine errors to the UI
                return self._json(500, {"error": str(e)})

    return Handler


def main():
    for p in sys.argv[1:] or [str(SAMPLE)]:
        name = add_repo(p)
        n = _repos[name].with_state(lambda st: len(st.metas))
        print(f"  {name}: {n} chunks warm", flush=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler())
    httpd.daemon_threads = True
    print(f"megabrain web demo → http://localhost:{PORT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
