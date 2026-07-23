"""megabrain read — the batch fetch that closes the map's loop.

The map names WHERE (files, spans, symbols); the host's Read then pays one
tool call per target, one turn per call, and a re-render of anything a
search already showed. This tool fetches EVERY target of the whole task in
ONE call, verbatim from disk with true line numbers, in three spec forms:

    path                     the whole file (capped — big files must narrow)
    path#symbol[,symbol2]    the symbol's exact line range, from the index
    path:START-END           an explicit line range (also path:START:END)

Deterministic, no LLM. The render is what the agent edits FROM — pair it
with megabrain_replace (exact-string ops) and the read->edit loop never
leaves megabrain.
"""

from __future__ import annotations

from pathlib import Path

from ..storage.store import Store

MAX_WHOLE_FILE = 1200      # lines; beyond this a whole-file spec must narrow


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve()) + "/"):
        raise ValueError(f"path escapes the repo: {rel!r}")
    return p


def _parse(spec: str) -> tuple[str, list[str], tuple[int, int] | None]:
    """-> (path, symbols, range). Range accepts 10-40, 10:40 and bare 10."""
    if "#" in spec:
        path, _, syms = spec.partition("#")
        return path, [s.strip() for s in syms.split(",") if s.strip()], None
    path, _, rng = spec.partition(":")
    if not rng:
        return spec, [], None
    a, _, b = rng.replace("-", ":").partition(":")
    try:
        lo = int(a)
        hi = int(b) if b else lo
    except ValueError:
        # a Windows-style or odd path with ':' — treat as plain path
        return spec, [], None
    return path, [], (min(lo, hi), max(lo, hi))


def read_specs(root: Path, specs: list[str]) -> dict:
    root = Path(root)
    # #symbol resolves line ranges from the INDEX — after an edit the file
    # shifts and a stale index hands back the wrong window (smoke run: the
    # attrs arena returned __init__'s body for #default). Same freshness
    # gate as search/ask: 60s TTL, fail-open.
    from ..indexing.indexer import maybe_reindex
    maybe_reindex(root)
    out: list[dict] = []
    with Store(root) as store:
        for spec in specs:
            path, syms, rng = _parse(str(spec))
            try:
                p = _safe(root, path)
            except ValueError as e:
                out.append({"spec": spec, "error": str(e)})
                continue
            if not p.is_file():
                out.append({"spec": spec, "error": f"no such file: {path}"})
                continue
            lines = p.read_text(encoding="utf-8",
                                errors="replace").splitlines()
            if syms:
                table = store.symbols_for(path)
                for sym in syms:
                    hits = [s for s in table
                            if s["name"] == sym or s["name"].endswith("." + sym)]
                    if not hits:
                        near = sorted({s["name"] for s in table},
                                      key=lambda n: (sym.lower() not in n.lower(), n))[:6]
                        out.append({"spec": f"{path}#{sym}",
                                    "error": f"symbol {sym!r} not found; "
                                             f"file has: {', '.join(near) or '(none)'}"})
                        continue
                    s = hits[0]
                    lo, hi = s["line"], s.get("end_line") or s["line"]
                    out.append({"spec": f"{path}#{sym}", "file": path,
                                "start": lo, "end": hi,
                                "lines": lines[lo - 1:hi]})
            elif rng:
                lo = max(1, rng[0])
                hi = min(len(lines), rng[1])
                out.append({"spec": spec, "file": path, "start": lo, "end": hi,
                            "lines": lines[lo - 1:hi]})
            else:
                if len(lines) > MAX_WHOLE_FILE:
                    sig = ", ".join(
                        f'{s["name"]} L{s["line"]}-{s.get("end_line") or s["line"]}'
                        for s in store.symbols_for(path)[:12])
                    out.append({"spec": spec,
                                "error": f"{path} is {len(lines)} lines — narrow "
                                         f"with #symbol or :start-end. Symbols: {sig}"})
                    continue
                out.append({"spec": spec, "file": path, "start": 1,
                            "end": len(lines), "lines": lines})
    return {"targets": out}


def render_read(res: dict) -> str:
    ok = [t for t in res["targets"] if "error" not in t]
    bad = [t for t in res["targets"] if "error" in t]
    L = [f'# megabrain read — {len(ok)} target(s)'
         + (f' · {len(bad)} FAILED' if bad else "")
         + ' · verbatim from disk, true line numbers']
    for t in bad:
        L.append(f'FAILED {t["spec"]} — {t["error"]}')
    for t in ok:
        L.append(f'\n## {t["spec"]}  L{t["start"]}-{t["end"]}')
        for i, ln in enumerate(t["lines"], t["start"]):
            L.append(f'{i:>5}→{ln}')
    return "\n".join(L)
