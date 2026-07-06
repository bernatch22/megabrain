"""serve.py — long-running JSON API over one indexed repo.

    megabrain serve-api ~/repo --port 2134

Keeps the retrieval state warm (the embedding matrix is loaded once at boot,
not per request) so each query skips the SQLite load. Pure stdlib
`http.server` — no framework — matching the engine's no-dependency stance
(numpy + urllib only). Serving docs needs only numpy + the db; the chunk text
lives in the db, so `/docsearch` answers without the source files on disk.

Endpoints:
    GET  /health                       -> {ok, repo, files, chunks, uptime}
    POST /search    {query, max?}      -> raw bundle {tier1, tier2, repo, ms}
    GET  /docsearch ?q=&limit=         -> [{title, slug, snippet, context, score, group}]
                                          docs-site search shape, section-level semantic hits
    GET  /chunks    ?file=&q=          -> every chunk of one file: span, score, selected flag
    POST /ask       {question, docs?}  -> {text, retrieval_ms, llm_ms, repo}
    GET  /get       ?file=&symbol=     -> {code}
    POST /index     {force?}           -> index stats (needs source files on disk)

Optional auth: --token / MEGABRAIN_API_TOKEN requires `Authorization: Bearer
<token>` on every route except /health — set it whenever binding off-localhost.

Single repo, pinned at boot. State auto-reloads when the index (db mtime)
changes, so a re-index or redeploy is picked up without a restart.

Query embedding and /ask both go through OpenRouter, so the process needs
OPENROUTER_API_KEY in its environment. CORS is off by default
(localhost / behind a reverse proxy); pass --cors <origin> for a browser origin.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .query import SearchState, load_state, search_with_state

# ── docsearch adapter ─────────────────────────────────────────────────────
# Section-level semantic hits shaped for a docs-site search box. Result groups
# (sidebar sections) are per-deployment config, not engine knowledge:
#   <repo>/.megabrain/docsearch.json   {"api/": "SDK API", "guides/": "Guides"}
#   MEGABRAIN_DOCSEARCH_GROUPS         same JSON object, env fallback
# Slug prefixes are matched in declaration order; no match -> "Docs".


def _load_groups(root: Path) -> tuple[tuple[str, str], ...]:
    raw = None
    cfg = root / ".megabrain" / "docsearch.json"
    if cfg.exists():
        raw = cfg.read_text(errors="replace")
    elif os.environ.get("MEGABRAIN_DOCSEARCH_GROUPS"):
        raw = os.environ["MEGABRAIN_DOCSEARCH_GROUPS"]
    if not raw:
        return ()
    try:
        d = json.loads(raw)
        return tuple((str(k), str(v)) for k, v in d.items())
    except (json.JSONDecodeError, AttributeError):
        return ()

# Markdown chunk text is raw (YAML frontmatter, '#' headings, fences, backticks).
# Clean it for display so the snippet reads as prose and the title has no markup.
_FM = re.compile(r"\A﻿?---[ \t]*\n.*?\n---[ \t]*\n+", re.S)


def _strip_fm(text: str) -> str:
    return _FM.sub("", text, count=1)


def _clean_inline(t: str) -> str:
    t = re.sub(r"`([^`]+)`", r"\1", t)                 # `code` -> code
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)     # [text](url) -> text
    t = re.sub(r"[*_~]", "", t)                         # bold / italic / strike
    return t.strip()


def _snippet(text: str, n: int = 160) -> str:
    """Frontmatter + markdown stripped to readable prose (keeps code text)."""
    t = _strip_fm(text)
    t = re.sub(r"```+[A-Za-z0-9_-]*\n?", " ", t)        # fence markers out, code stays
    t = re.sub(r"^[ \t]*#{1,6}\s+.*$", "", t, flags=re.M)  # heading lines out
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"[*_~>#|]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:n].rstrip() + "…") if len(t) > n else t


def _context(text: str, n: int = 2000) -> str:
    """Frontmatter + leading H1 stripped; markdown kept (the preview renders it)."""
    t = _strip_fm(text)
    t = re.sub(r"\A#\s+.+\n+", "", t)                  # drop leading H1 (shown as title)
    t = t.strip()
    return (t[:n].rstrip() + "\n\n…") if len(t) > n else t


def _group(slug: str, groups: tuple[tuple[str, str], ...]) -> str:
    s = slug.lstrip("/")
    for prefix, name in groups:
        if s.startswith(prefix):
            return name
    return "Docs"


def _slug(relpath: str) -> str:
    """docs/foo/bar.md -> /foo/bar ; index.md -> / (matches build-search-index)."""
    rel = relpath
    for ext in (".md", ".markdown", ".mdx"):
        if rel.endswith(ext):
            rel = rel[: -len(ext)]
            break
    if rel.startswith("docs/"):       # repo root may sit above the docs dir
        rel = rel[len("docs/"):]
    if rel in ("index", ""):
        return "/"
    return "/" + rel


def _title(relpath: str, chunk: dict) -> str:
    bc = (chunk.get("breadcrumb") or "").strip()
    if bc:
        # breadcrumb separator is ' > ' (markdown.py _crumb); path crumbs and
        # heading text may contain '/', so split ONLY on ' > '. Keep the heading
        # path after the '<file>.md' crumb — the rest duplicates the slug.
        segs = [s.strip() for s in bc.split(" > ") if s.strip()]
        cut = -1
        for i, s in enumerate(segs):
            if s.endswith((".md", ".markdown", ".mdx")):
                cut = i
        headings = segs[cut + 1:] if cut >= 0 else segs[-1:]
        headings = [h for h in (_clean_inline(s.lstrip("#").strip()) for s in headings) if h]
        if headings:
            return " › ".join(headings[:3])
    nm = _clean_inline((chunk.get("name") or "").lstrip("#").strip())
    if nm:
        return nm
    tail = _slug(relpath).rstrip("/").rsplit("/", 1)[-1] or "Overview"
    return tail.replace("-", " ")


def docsearch(state: SearchState, q: str, limit: int = 15,
              groups: tuple[tuple[str, str], ...] = ()) -> list[dict]:
    """Flatten retrieval to section-level hits in docs-web's SearchResult shape,
    deduped to the best hit per page (slug)."""
    res = search_with_state(state, q)
    hits: list[tuple[str, dict, float]] = []
    for t in res["tier1"]:
        for c in t["chunks"]:
            hits.append((t["file"], c, float(c.get("score", t["score"]))))
    for t in res["tier2"]:
        bc = t.get("best_chunk")
        if bc:
            hits.append((t["file"], bc, float(t.get("score", 0))))
    if not hits:
        return []
    top = max(h[2] for h in hits) or 1.0
    best_by_slug: dict[str, dict] = {}
    for relpath, chunk, score in hits:
        slug = _slug(relpath)
        raw = chunk.get("text") or ""
        entry = {
            "title": _title(relpath, chunk),
            "slug": slug,
            "snippet": _snippet(raw),
            "context": _context(raw),
            "score": round(score / top * 100),
            "group": _group(slug, groups),
        }
        prev = best_by_slug.get(slug)
        if prev is None or entry["score"] > prev["score"]:
            best_by_slug[slug] = entry
    return sorted(best_by_slug.values(), key=lambda e: -e["score"])[:limit]


# ── warm state, one repo ──────────────────────────────────────────────────

class _Repo:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.groups = _load_groups(self.root)   # docsearch section names (optional)
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
        """Run fn(state) with the warm state, serialized. The Store connection
        is shared across worker threads (check_same_thread=False), so a single
        lock guards every read. Reloads when the index file changes on disk.
        (The embedding network call runs under the lock too — fine for a docs
        search box; revisit with per-thread connections if it ever needs heavy
        concurrency.)"""
        with self._lock:
            mt = self._mtime_now()
            if self._state is None or mt != self._mtime:
                self._state = load_state(self.root, check_same_thread=False)
                self._mtime = mt
            return fn(self._state)


# ── HTTP ──────────────────────────────────────────────────────────────────

def _make_handler(repo: _Repo, cors: str | None, enable_llm: bool,
                  token: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):       # silence default per-request stderr noise
            pass

        def _authed(self, path: str) -> bool:
            """When a token is configured, every route except /health requires
            `Authorization: Bearer <token>`. No token -> open (localhost use)."""
            if not token or path == "/health":
                return True
            return self.headers.get("Authorization") == f"Bearer {token}"

        def _send(self, code: int, payload) -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code: int, msg: str) -> None:
            self._send(code, {"error": msg})

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return {}

        def do_OPTIONS(self):
            self.send_response(204)
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            path = u.path.rstrip("/") or "/"
            if not self._authed(path):
                return self._err(401, "unauthorized")
            try:
                if path == "/health":
                    return self._send(200, repo.with_state(lambda st: {
                        "ok": True, "repo": st.repo, "files": len(st.fpaths),
                        "chunks": len(st.metas), "uptime": int(time.time() - repo.start),
                    }))
                if path == "/docsearch":
                    q = (qs.get("q") or [""])[0].strip()
                    limit = max(1, min(50, int((qs.get("limit") or ["15"])[0])))
                    if len(q) < 2:
                        return self._send(200, [])
                    return self._send(200, repo.with_state(
                        lambda st: docsearch(st, q, limit, repo.groups)))
                if path == "/get":
                    rel = (qs.get("file") or [""])[0]
                    if not rel:
                        return self._err(400, "missing file")
                    from .query import get_code
                    sym = (qs.get("symbol") or [None])[0]
                    return self._send(200, {"code": get_code(repo.root, rel, sym)})
                if path == "/chunks":
                    rel = (qs.get("file") or [""])[0]
                    q = (qs.get("q") or qs.get("query") or [""])[0].strip()
                    if not rel or not q:
                        return self._err(400, "missing file or q")
                    from .query import chunks_for_file
                    return self._send(200, repo.with_state(
                        lambda st: chunks_for_file(st, rel, q)))
                return self._err(404, "not found")
            except Exception as e:       # noqa: BLE001 — surface any engine error as 500
                return self._err(500, str(e))

        def do_POST(self):
            path = (urllib.parse.urlparse(self.path).path).rstrip("/") or "/"
            if not self._authed(path):
                return self._err(401, "unauthorized")
            body = self._read_json()
            try:
                if path == "/search":
                    q = (body.get("query") or body.get("task") or "").strip()
                    if not q:
                        return self._err(400, "missing query")
                    res = repo.with_state(lambda st: search_with_state(st, q))
                    mx = int(body.get("max") or 0)
                    if mx:
                        res["tier1"] = res["tier1"][:mx]
                    return self._send(200, res)
                if path == "/ask":
                    if not enable_llm:
                        return self._err(503, "llm disabled (--no-llm)")
                    q = (body.get("question") or "").strip()
                    if not q:
                        return self._err(400, "missing question")
                    # ask() builds its own state/connection in this thread — no
                    # shared lock, so the slow LLM stream never blocks /search.
                    from .ask import ask
                    out = ask(repo.root, q, docs_only=bool(body.get("docs")),
                              include_docs=bool(body.get("include_docs")))
                    for k in ("result", "cands", "file_syms"):
                        out.pop(k, None)
                    return self._send(200, out)
                if path == "/index":
                    from .indexer import index_repo
                    return self._send(200, index_repo(repo.root, quiet=True,
                                                      force=bool(body.get("force"))))
                return self._err(404, "not found")
            except Exception as e:       # noqa: BLE001
                return self._err(500, str(e))

    return Handler


def serve(root, port: int = 2134, host: str = "127.0.0.1",
          cors: str | None = None, enable_llm: bool = True,
          token: str | None = None) -> None:
    repo = _Repo(Path(root))
    chunks = repo.with_state(lambda st: len(st.metas))   # warm up + validate index
    if chunks == 0:
        print(f"⚠  index at {repo.root}/.megabrain is empty — POST /index or run "
              f"`megabrain index {repo.root}` first")
    if not token and host not in ("127.0.0.1", "localhost", "::1"):
        print("⚠  binding beyond localhost with no --token / MEGABRAIN_API_TOKEN — "
              "every endpoint (including POST /index) is open to the network")

    httpd = ThreadingHTTPServer((host, port), _make_handler(repo, cors, enable_llm, token))
    httpd.daemon_threads = True
    print(f"megabrain serve-api → http://{host}:{port}  repo={repo.root.name} "
          f"chunks={chunks} cors={cors or 'off'} llm={'on' if enable_llm else 'off'} "
          f"auth={'bearer' if token else 'off'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.shutdown()
