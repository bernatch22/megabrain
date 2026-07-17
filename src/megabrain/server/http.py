"""serve.py — long-running JSON API over indexed repos (+ the studio web UI).

    megabrain serve     ~/repo --port 2134   # UI + API; open http://localhost:2134/
    megabrain serve-api ~/repo --port 2134   # JSON API only, no UI

Both drive serve() below; `serve` mounts the studio (serve_ui=True), `serve-api`
does not. One repo is pinned at boot (warm state — the embedding matrix loads
once, not per request), but the server holds a REGISTRY of repos so the studio
can switch between several and add new ones at runtime; every route accepts an
optional `?repo=`/`"repo"` (absent = the boot repo). Pure stdlib `http.server` —
no framework — matching the engine's no-dependency stance.

Endpoints:
    GET  /                             -> studio UI (index.html; `serve` only)
    GET  /ui/*                         -> UI static assets (`serve` only)
    GET  /health        ?repo=         -> {ok, repo, files, chunks, uptime}
    GET  /repos                        -> [{name, root, files, chunks, active}]
    GET  /providers                    -> what can narrate here (settings panel)
    GET  /scan          ?path=         -> add-repo census: would_index, flagged, …
    GET  /docsearch     ?q=&limit=&repo=  -> docs-site hits
    GET  /get           ?file=&symbol=&repo= -> {code}
    GET  /chunks        ?file=&q=&repo=   -> every chunk of one file (heatmap)
    GET  /prune         ?q=&rerank=&repo= -> {chunks(signal), noise, kept, pruned, …}
    GET  /graph         ?mode=&node=&source=&target=&repo= -> knowledge graph
    POST /search    {query, max?, repo?}      -> raw bundle {tier1, tier2, ms}
    POST /ask       {question, model?, agents?, repo?, …} -> buffered answer
    POST /ask/stream {same}            -> SSE multi-agent live view
    POST /index     {force?, repo?}    -> index stats (blocking)
    POST /index/stream {repo?|path?, force?, scan_filters?} -> SSE per-file progress
    POST /repos/add {path, ignore?}    -> register a repo (+ write .megabrainignore)

Optional auth: --token / MEGABRAIN_API_TOKEN requires `Authorization: Bearer
<token>` on every route except /health and the UI. CORS off by default; pass
--cors <origin> for a cross-origin browser client.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..errors import MegabrainError
from ..retrieval.bundle import search_with_state
from ..retrieval.docsearch import docsearch
from .session import RepoSession

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent / "ui"      # Claude Design's static bundle
_CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
           ".css": "text/css", ".json": "application/json",
           ".svg": "image/svg+xml", ".ico": "image/x-icon",
           ".png": "image/png", ".woff2": "font/woff2", ".map": "application/json"}


def _http_error(msg: str, status: int) -> MegabrainError:
    """A MegabrainError carrying a per-instance http_status (the class default
    is 500) — for the ad-hoc 400/404 bad-request cases the routes raise."""
    e = MegabrainError(msg)
    e.http_status = status
    return e


class Registry:
    """The set of served repos, keyed by name. The boot repo is the default
    target when a request omits `repo`. Adding a repo at runtime (studio
    add-repo flow) registers a new RepoSession; a re-index is picked up by
    RepoSession's own mtime invalidation, no restart needed."""

    def __init__(self, boot: RepoSession):
        self._by_name: dict[str, RepoSession] = {boot.root.name: boot}
        self.boot_name = boot.root.name
        self._lock = threading.Lock()

    def get(self, name: str | None) -> RepoSession:
        with self._lock:
            s = self._by_name.get(name or self.boot_name)
        if s is None:
            raise _http_error(f"unknown repo: {name}", 404)
        return s

    def add(self, root: Path) -> RepoSession:
        root = Path(root).resolve()
        with self._lock:
            s = self._by_name.get(root.name)
            if s is None or s.root != root:
                s = RepoSession(root)
                self._by_name[root.name] = s
            return s

    def list(self) -> list[RepoSession]:
        with self._lock:
            return list(self._by_name.values())


def _all_repos(reg: Registry) -> list[dict]:
    """/repos = the repos THIS server holds warm (loaded: true) + every other
    repo in the machine-global registry (~/.megabrain/registry.json,
    loaded: false). Selecting an unloaded one in the studio goes through the
    existing POST /repos/add — the server never eagerly loads N repos at boot."""
    loaded = [{**_repo_stats(s), "loaded": True} for s in reg.list()]
    have = {r["root"] for r in loaded}
    try:
        from ..storage.registry import list_repos
        for e in list_repos():
            if e["path"] not in have:
                loaded.append({"name": e["name"], "root": e["path"],
                               "files": e.get("files", 0),
                               "chunks": e.get("chunks", 0),
                               "embed_model": e.get("embed_model"),
                               "loaded": False})
    except Exception:                     # registry is bookkeeping, never a 500
        log.debug("global registry merge skipped", exc_info=True)
    return loaded


def _repo_stats(s: RepoSession) -> dict:
    """name/root/files/chunks for /repos and /health — tolerant of a repo that
    isn't indexed yet (freshly added, before its first /index). Which repo is
    'active' is the client's selection, tracked in the UI — not a server fact."""
    base = {"name": s.root.name, "root": str(s.root)}
    try:
        return {**base, **s.with_state(lambda st: {
            "files": len(st.fpaths), "chunks": len(st.metas),
            "embed_model": st.store.get_meta("embed_model")})}
    except Exception:
        return {**base, "files": 0, "chunks": 0, "embed_model": None}


# ── HTTP ──────────────────────────────────────────────────────────────────

def _make_handler(reg: Registry, cors: str | None, enable_llm: bool,
                  token: str | None = None, serve_ui: bool = True):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):       # silence default per-request stderr noise
            pass

        def _authed(self, path: str) -> bool:
            """When a token is configured, every route except /health and the
            static UI requires `Authorization: Bearer <token>`."""
            if not token or path == "/health" or path == "/" or path.startswith("/ui"):
                return True
            return self.headers.get("Authorization") == f"Bearer {token}"

        def _send(self, code: int, payload, ctype: str | None = None) -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype or "application/json; charset=utf-8")
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

        def _sse_open(self):
            """Shared SSE preamble (/ask/stream + /index/stream). Returns an
            `sse(event_dict)` writer; caller runs the stream and handles the
            client-hung-up case."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")   # nginx: don't buffer SSE
            self.send_header("Connection", "close")
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.close_connection = True

            def sse(ev: dict):
                self.wfile.write(f'event: {ev["type"]}\ndata: '
                                 f'{json.dumps(ev)}\n\n'.encode())
                self.wfile.flush()
            return sse

        def do_OPTIONS(self):
            self.send_response(204)
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ── static UI ──────────────────────────────────────────────────────
        def _serve_ui(self, path: str) -> bool:
            """Serve the studio bundle. `/` → index.html; `/ui/<f>` and a
            top-level `/<f>` (index.html links siblings as ./api.js) both map
            into UI_DIR. Returns False (fall through to the JSON API) when the
            path names no UI file — so /repos, /search etc. still route."""
            if not serve_ui:
                return False
            if path == "/":
                rel = "index.html"
            elif path.startswith("/ui/"):
                rel = path[len("/ui/"):]
            else:
                rel = path.lstrip("/")
            if not rel:
                return False
            target = (UI_DIR / rel).resolve()
            try:                              # containment: no traversal out of UI_DIR
                target.relative_to(UI_DIR.resolve())
            except ValueError:
                return False
            if not target.is_file():
                if path == "/":               # bundle not installed yet
                    self._send(200, b"<h1>megabrain studio</h1><p>UI bundle not "
                               b"found. Build it into server/ui/.</p>",
                               "text/html; charset=utf-8")
                    return True
                return False                  # not a UI file -> let the API route it
            ctype = _CTYPES.get(target.suffix) or \
                (mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)
            return True

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            path = u.path.rstrip("/") or "/"
            if self.command == "GET" and self._serve_ui(u.path.rstrip("/") or "/"):
                return
            if not self._authed(path):
                return self._err(401, "unauthorized")
            repo_name = (qs.get("repo") or [None])[0]
            try:
                if path == "/health":
                    return self._send(200, reg.get(repo_name).with_state(lambda st: {
                        "ok": True, "repo": st.repo, "files": len(st.fpaths),
                        "chunks": len(st.metas),
                        "embed_model": st.store.get_meta("embed_model"),
                        "uptime": int(time.time() - reg.get(repo_name).start),
                    }))
                if path == "/repos":
                    return self._send(200, _all_repos(reg))
                if path == "/providers":
                    from .. import providers
                    return self._send(200, providers.detect())
                if path == "/scan":
                    p = (qs.get("path") or [""])[0].strip()
                    if not p:
                        return self._err(400, "missing path")
                    return self._send(200, _scan_report(p))
                if path == "/fs/pick":
                    return self._send(200, _native_pick_folder())
                if path == "/docsearch":
                    repo = reg.get(repo_name)
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
                    from ..retrieval.files import get_code
                    sym = (qs.get("symbol") or [None])[0]
                    return self._send(200, {"code": get_code(reg.get(repo_name).root, rel, sym)})
                if path == "/chunks":
                    rel = (qs.get("file") or [""])[0]
                    q = (qs.get("q") or qs.get("query") or [""])[0].strip()
                    if not rel or not q:
                        return self._err(400, "missing file or q")
                    from ..retrieval.bundle import chunks_for_file
                    return self._send(200, reg.get(repo_name).with_state(
                        lambda st: chunks_for_file(st, rel, q)))
                if path == "/graph":
                    from ..graph import graph_map, graph_node, graph_path
                    mode = (qs.get("mode") or ["map"])[0]
                    repo = reg.get(repo_name)
                    if mode == "node":
                        node = (qs.get("node") or [""])[0].strip()
                        if not node:
                            return self._err(400, "missing node")
                        return self._send(200, repo.with_state(
                            lambda st: graph_node(st, node)))
                    if mode == "path":
                        src = (qs.get("source") or [""])[0].strip()
                        dst = (qs.get("target") or [""])[0].strip()
                        if not (src and dst):
                            return self._err(400, "missing source/target")
                        return self._send(200, repo.with_state(
                            lambda st: graph_path(st, src, dst)))
                    return self._send(200, repo.with_state(
                        lambda st: graph_map(st)))
                if path == "/prune":
                    q = (qs.get("q") or qs.get("query") or [""])[0].strip()
                    if not q:
                        return self._err(400, "missing q")
                    from ..retrieval.bundle import prune_search
                    res = reg.get(repo_name).with_state(
                        lambda st: prune_search(st, q, include_pruned=True))
                    if (qs.get("rerank") or ["0"])[0] in ("1", "true"):
                        from ..retrieval.rerank import llm_rerank
                        res = llm_rerank(res, q)
                    return self._send(200, res)
                return self._err(404, "not found")
            except MegabrainError as e:  # typed engine error -> mapped status
                return self._err(e.http_status, str(e))
            except Exception:            # noqa: BLE001 — never leak internals
                log.exception("GET %s failed", path)
                return self._err(500, "internal error")

        def do_POST(self):
            path = (urllib.parse.urlparse(self.path).path).rstrip("/") or "/"
            if not self._authed(path):
                return self._err(401, "unauthorized")
            body = self._read_json()
            repo_name = body.get("repo")
            try:
                if path == "/search":
                    q = (body.get("query") or body.get("task") or "").strip()
                    if not q:
                        return self._err(400, "missing query")
                    res = reg.get(repo_name).with_state(lambda st: search_with_state(st, q))
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
                    ag = body.get("agents", "auto")
                    model = _valid_model(body.get("model"))
                    from ..ask import ask
                    out = ask(reg.get(repo_name).root, q,
                              docs_only=bool(body.get("docs")),
                              include_docs=bool(body.get("include_docs")),
                              agents=None if ag in (None, "auto") else bool(ag),
                              model=model)
                    for k in ("result", "cands", "file_syms"):
                        out.pop(k, None)
                    return self._send(200, out)
                if path == "/ask/stream":
                    if not enable_llm:
                        return self._err(503, "llm disabled (--no-llm)")
                    q = (body.get("question") or "").strip()
                    if not q:
                        return self._err(400, "missing question")
                    ag = body.get("agents", "auto")
                    model = _valid_model(body.get("model"))
                    from ..ask.agents import stream_events
                    sse = self._sse_open()
                    try:
                        stream_events(reg.get(repo_name).root, q, sse,
                                      agents=None if ag in (None, "auto") else bool(ag),
                                      docs_only=bool(body.get("docs")),
                                      include_docs=bool(body.get("include_docs")),
                                      model=model)
                    except (BrokenPipeError, ConnectionResetError):
                        pass              # client went away mid-stream
                    except Exception as e:  # noqa: BLE001 — headers already sent
                        try:
                            sse({"type": "error", "stage": "fatal", "msg": str(e)})
                        except OSError:
                            pass
                    return
                if path == "/index":
                    from ..indexing.indexer import index_repo
                    return self._send(200, index_repo(reg.get(repo_name).root,
                                                      force=bool(body.get("force"))))
                if path == "/providers/select":
                    from .. import providers
                    return self._send(200, providers.select(
                        str(body.get("provider") or ""),
                        (str(body.get("model")).strip() or None) if body.get("model") else None))
                if path == "/providers/ollama/serve":
                    from .. import providers
                    return self._send(200, providers.start_ollama())
                if path == "/index/stream":
                    return self._index_stream(body)
                if path == "/repos/add":
                    return self._repos_add(body)
                return self._err(404, "not found")
            except MegabrainError as e:  # typed engine error -> mapped status
                return self._err(e.http_status, str(e))
            except Exception:            # noqa: BLE001 — never leak internals
                log.exception("POST %s failed", path)
                return self._err(500, "internal error")

        # ── repo management ────────────────────────────────────────────────
        def _repos_add(self, body: dict):
            """Register a repo path (studio add-repo). Optionally merge the
            user-confirmed ignore lines into <path>/.megabrainignore FIRST, so
            the follow-up /index/stream honors them. Non-blocking: indexing is
            the separate streamed call."""
            p = str(body.get("path") or "").strip()
            if not p:
                return self._err(400, "missing path")
            root = Path(p).expanduser().resolve()
            if not root.is_dir():
                return self._err(400, f"not a directory: {root}")
            ignore = str(body.get("ignore") or "").strip()
            if ignore:
                _merge_ignore(root, ignore)
            s = reg.add(root)
            return self._send(200, _repo_stats(s))

        def _index_stream(self, body: dict):
            """SSE per-file indexing progress. Target by registered `repo`
            name, or by absolute `path` (auto-registers — the add-repo flow).
            `scan_filters` (default true here) honors .gitignore + skips
            vendored/generated; a plain re-index passes false to stay
            byte-identical."""
            from .. import providers
            from ..indexing.indexer import index_repo
            if body.get("path"):
                root = Path(str(body["path"])).expanduser().resolve()
                reg.add(root)
            else:
                root = reg.get(body.get("repo")).root
            scan_filters = body.get("scan_filters", True)
            force = bool(body.get("force"))
            # switch the EMBEDDING for this (re)index: set the model (+ base for
            # a local endpoint) so both this index AND subsequent query
            # embedding use it — index_repo re-embeds all on a model change.
            em = str(body.get("embed_model") or "").strip()
            if em:
                import os
                os.environ["MEGABRAIN_EMBED_MODEL"] = em
                eb = str(body.get("embed_base") or "").strip()
                if eb:
                    providers.EMBED_BASE_URL = eb.rstrip("/")
                    os.environ["MEGABRAIN_EMBED_BASE_URL"] = providers.EMBED_BASE_URL
                force = True
            sse = self._sse_open()

            def on_progress(ev: dict):
                sse({"type": "file", **ev})
            try:
                stats = index_repo(root, force=force,
                                   scan_filters=bool(scan_filters),
                                   on_progress=on_progress)
                sse({"type": "done", **stats})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa: BLE001 — headers already sent
                try:
                    sse({"type": "error", "msg": str(e)})
                except OSError:
                    pass

    return Handler


def _valid_model(model) -> str | None:
    """The UI sends `model` per ask. Guard the documented gotcha: the Claude
    provider only accepts its aliases (haiku/sonnet/opus…), never a
    provider/slug id — a slug with '/' on claude is silently ignored (fall back
    to the provider default) rather than failing the request."""
    m = (str(model).strip() if model else "")
    if not m:
        return None
    from .. import providers
    if providers.resolve().name == "claude" and "/" in m:
        return None
    return m


def _native_pick_folder() -> dict:
    """`GET /fs/pick` — open the OPERATING SYSTEM's own native folder dialog on
    the machine serve-api runs on, restricted to folders, and return the chosen
    absolute path. This is the real system chooser (Finder on macOS, GTK/KDE on
    Linux), not an HTML re-creation — and it's the only way to get an absolute
    path a browser will never hand over. Blocks until the user picks or cancels.
    `{path}` on pick · `{cancelled: true}` on cancel. Raises 400 with a clear
    message on a headless box (no display / no picker) so the UI falls back to
    the manual path field."""
    import shutil
    import subprocess
    import sys
    try:
        if sys.platform == "darwin":
            script = ('POSIX path of (choose folder with prompt '
                      '"Select a repository to index with megabrain")')
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:                 # user hit Cancel (or no GUI)
                if "User canceled" in (r.stderr or ""):
                    return {"cancelled": True}
                raise _http_error("no native folder dialog available on this host", 400)
            return {"path": r.stdout.strip().rstrip("/")}
        for cmd in (["zenity", "--file-selection", "--directory",
                     "--title=Select a repository to index"],
                    ["kdialog", "--getexistingdirectory", str(Path.home())]):
            if shutil.which(cmd[0]):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    return {"cancelled": True}
                return {"path": r.stdout.strip().rstrip("/")}
        raise _http_error("no native folder picker found (install zenity/kdialog, "
                          "or paste the path)", 400)
    except FileNotFoundError as e:
        raise _http_error("native folder picker not available — paste the path", 400) from e
    except subprocess.TimeoutExpired:
        return {"cancelled": True}


def _scan_report(path: str) -> dict:
    """`GET /scan?path=` — the add-repo census (no indexing), via app.scan."""
    from .. import app
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise _http_error(f"not a directory: {root}", 400)
    return {"path": str(root), "name": root.name, **app.scan(root)}


def _merge_ignore(root: Path, ignore: str) -> None:
    """Merge the user-confirmed ignore lines into <root>/.megabrainignore,
    APPENDING only lines not already present (never clobber existing content)."""
    f = root / ".megabrainignore"
    existing = f.read_text(encoding="utf-8", errors="replace").splitlines() if f.exists() else []
    have = {ln.strip() for ln in existing}
    add = [ln for ln in ignore.splitlines() if ln.strip() and ln.strip() not in have]
    if not add:
        return
    out = existing + ([""] if existing and existing[-1].strip() else []) + add
    f.write_text("\n".join(out) + "\n", encoding="utf-8")


def serve(root, port: int = 2134, host: str = "127.0.0.1",
          cors: str | None = None, enable_llm: bool = True,
          token: str | None = None, serve_ui: bool = True) -> None:
    import os

    from .. import providers
    # Studio default: if OPENROUTER_API_KEY is available (env or ~/.zshrc) and
    # the provider isn't explicitly pinned, narrate through OpenRouter — the
    # user asked for openrouter-by-default whenever the key is present. An
    # explicit MEGABRAIN_CHAT_PROVIDER still wins.
    if not os.environ.get("MEGABRAIN_CHAT_PROVIDER") and providers.find_key(required=False):
        providers.select("openrouter")

    boot = RepoSession(Path(root))
    chunks = boot.with_state(lambda st: len(st.metas))   # warm up + validate index
    if chunks == 0:
        print(f"⚠  index at {boot.root}/.megabrain is empty — POST /index or run "
              f"`megabrain index {boot.root}` first")
    if not token and host not in ("127.0.0.1", "localhost", "::1"):
        print("⚠  binding beyond localhost with no --token / MEGABRAIN_API_TOKEN — "
              "every endpoint (including POST /index) is open to the network")

    reg = Registry(boot)
    httpd = ThreadingHTTPServer((host, port),
                                _make_handler(reg, cors, enable_llm, token, serve_ui))
    httpd.daemon_threads = True
    ui = "on" if (serve_ui and (UI_DIR / "index.html").is_file()) else "off"
    verb = "serve" if serve_ui else "serve-api"
    print(f"megabrain {verb} → http://{host}:{port}  repo={boot.root.name} "
          f"chunks={chunks} cors={cors or 'off'} llm={'on' if enable_llm else 'off'} "
          f"auth={'bearer' if token else 'off'} ui={ui}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.shutdown()
