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

# The MCP host truncates a tool result over its token ceiling (~25k tokens)
# into a FILE the agent must Read back in host chunks — the exact round-trips
# this tool exists to kill (field run: a 66k-char, 7-target batch cost 3 host
# turns of recovery). So the render self-splits BELOW the ceiling: targets
# render in caller order until the budget is spent; the tail comes back as a
# ready-to-paste re-call, never a spill. ~2.5 chars/token for guttered code
# puts 48k chars safely under 25k tokens.
MAX_RENDER_CHARS = 48_000


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
                # a wrong-extension guess is the common miss (field run:
                # CHANGES.rst / docs/options.rst on a repo that uses .md)
                # and it cost a whole recovery turn — name the real file.
                sugg = sorted(str(s.relative_to(root))
                              for s in p.parent.glob(p.stem + ".*")
                              if s.is_file()) if p.parent.is_dir() else []
                if not sugg:
                    import difflib
                    sugg = difflib.get_close_matches(
                        path, sorted(store.all_paths()), 3, 0.6)
                hint = f' Did you mean: {", ".join(sugg[:3])}?' if sugg else ""
                out.append({"spec": spec,
                            "error": f"no such file: {path}.{hint}"})
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


def render_read(res: dict, budget: int = MAX_RENDER_CHARS) -> str:
    ok = [t for t in res["targets"] if "error" not in t]
    bad = [t for t in res["targets"] if "error" in t]
    L = [f'# megabrain read — {len(ok)} target(s)'
         + (f' · {len(bad)} FAILED' if bad else "")
         + ' · verbatim from disk, true line numbers']
    for t in bad:
        L.append(f'FAILED {t["spec"]} — {t["error"]}')
    used = sum(len(x) + 1 for x in L)
    rendered = 0
    deferred: list[str] = []
    for t in ok:
        if deferred:              # budget already spent — keep caller order
            deferred.append(t["spec"])
            continue
        body = [f'\n## {t["spec"]}  L{t["start"]}-{t["end"]}']
        body += [f'{i:>5}→{ln}' for i, ln in enumerate(t["lines"], t["start"])]
        size = sum(len(x) + 1 for x in body)
        if used + size > budget:
            if rendered == 0:
                # the FIRST target alone busts the budget — render the prefix
                # that fits and defer the exact remaining range, so the call
                # is never empty and the continuation spec is precise.
                keep = [body[0]]
                room = used + len(body[0]) + 1
                cut = t["start"] - 1
                for line in body[1:]:
                    if room + len(line) + 1 > budget:
                        break
                    keep.append(line)
                    room += len(line) + 1
                    cut += 1
                if cut >= t["start"]:
                    L.extend(keep)
                    used = room
                    rendered += 1
                    if cut < t["end"]:
                        deferred.append(f'{t["file"]}:{cut + 1}-{t["end"]}')
                    continue
            deferred.append(t["spec"])
            continue
        L.extend(body)
        used += size
        rendered += 1
    if deferred:
        import json
        L.append(f'\n— NOT RENDERED ({len(deferred)} target(s)): the full batch '
                 'would exceed the MCP output ceiling and spill to a host file. '
                 'Fetch the rest with ONE more megabrain_read:')
        L.append(f'  targets: {json.dumps(deferred)}')
    return "\n".join(L)
