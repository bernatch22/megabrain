"""`megabrain grep` — literal search that understands what it found.

grep gives you lines; this gives you ROLES. Every match is resolved against
the index the engine already built: the enclosing symbol (symbols table)
says whether the line DEFINES the name, READS it inside real code, or just
mentions it in config/data; the edges table says who reaches the reading
file (import/call edges INTO it — the dependents a plain grep cannot see);
and reads are ordered by in-degree so the core site outranks the preset.
Field case (nx#35656): 17 raw matches for `analyzeSourceFiles` collapse
into 1 definition + 2 reads + config + tests, and the read site's
`reached-from` list is half the diagnosis — the daemon NOT appearing in it
IS the bug.

Zero LLM, no vectors loaded: one pass over `chunks.text` in SQLite (chunks
are a line partition, so every indexed line is seen exactly once), plus two
indexed lookups. Same completeness stance as the CORE/RELATED map — nothing
is hidden, everything is structured: tests and docs land in their own
labeled sections, never dropped.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..storage.store import Store
from .scoring import _is_test_path

# Per-section listing caps: grep output must stay readable in an agent's
# context. Overflow is COUNTED and said out loud, never silent (house rule:
# a bounded listing that reads as complete is a lie).
MAX_PER_SECTION = 40
MAX_REACHED_FROM = 4
# The wire cap for `GET /grep`. Higher than the text one because a UI SCROLLS
# — the 40-line ceiling exists to protect an agent's context, not a viewport.
API_MAX_PER_SECTION = 200

_DOC_EXT = {".md", ".rst", ".txt"}
_DATA_EXT = {".json", ".yaml", ".yml", ".toml", ".xml"}


def _compile(pattern: str, regex: bool, ignore_case: bool) -> re.Pattern:
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(pattern if regex else re.escape(pattern), flags)


def _enclosing(symbols: list[dict], line: int) -> dict | None:
    """The innermost symbol whose span contains `line` — deepest def line
    wins, ties broken by the tighter (larger) start."""
    best = None
    for s in symbols:
        end = s.get("end_line") or s["line"]
        if s["line"] <= line <= end and \
                (best is None or s["line"] >= best["line"]):
            best = s
    return best


def _classify(file: str, line: int, sym: dict | None, pattern: str,
              regex: bool) -> str:
    ext = "." + file.rsplit(".", 1)[-1] if "." in file else ""
    if _is_test_path(file):
        return "tests"
    if ext in _DOC_EXT:
        return "docs"
    if ext in _DATA_EXT or sym is None:
        return "config"
    bare = (sym["name"] or "").rsplit(".", 1)[-1]
    if sym["line"] == line and not regex and pattern in bare:
        return "defines"
    return "reads"


def grep_repo(root: Path, pattern: str, regex: bool = False,
              ignore_case: bool = False,
              path_filter: str | None = None) -> dict:
    """Scan every indexed chunk for `pattern` (literal by default) and
    resolve each match against symbols + edges. Returns the role-grouped
    result; `render_grep` turns it into the CLI/MCP text."""
    rx = _compile(pattern, regex, ignore_case)
    root = Path(root)
    with Store(root) as store:
        rows = store.db.execute(
            "SELECT file, start_line, text FROM chunks ORDER BY file, start_line"
        ).fetchall()
        in_deg = dict(store.db.execute(
            "SELECT dst, COUNT(DISTINCT src) FROM edges GROUP BY dst"))

        sections: dict[str, list[dict]] = {
            k: [] for k in ("defines", "reads", "config", "tests", "docs")}
        syms_cache: dict[str, list[dict]] = {}
        files_hit: set[str] = set()
        total = 0
        for file, start, text in rows:
            if path_filter and not (file == path_filter
                                    or file.startswith(path_filter.rstrip("/") + "/")):
                continue
            if not rx.search(text):          # cheap whole-chunk gate first
                continue
            if file not in syms_cache:
                syms_cache[file] = store.symbols_for(file)
            for off, ln in enumerate(text.splitlines()):
                if not rx.search(ln):
                    continue
                total += 1
                files_hit.add(file)
                line = start + off
                sym = _enclosing(syms_cache[file], line)
                role = _classify(file, line, sym, pattern, regex)
                sections[role].append({
                    "file": file, "line": line, "text": ln.strip()[:160],
                    "symbol": (sym or {}).get("name"),
                    "kind": (sym or {}).get("kind"),
                    "in_deg": int(in_deg.get(file, 0)),
                })

        # reads: core first — the site more of the repo depends on outranks
        # the leaf. defines/config/tests keep file order (stable, scannable).
        sections["reads"].sort(key=lambda m: (-m["in_deg"], m["file"], m["line"]))

        # who reaches the defining/reading files — the dependents grep can't see
        reached: dict[str, list[str]] = {}
        for m in sections["defines"] + sections["reads"]:
            f = m["file"]
            if f not in reached:
                reached[f] = [r[0] for r in store.db.execute(
                    "SELECT DISTINCT src FROM edges WHERE dst=? LIMIT ?",
                    (f, MAX_REACHED_FROM))]
            m["reached_from"] = reached[f]

    return {"pattern": pattern, "regex": regex, "ignore_case": ignore_case,
            "matches": total, "files": len(files_hit),
            **{k: v for k, v in sections.items()}}


SECTIONS = ("defines", "reads", "config", "tests", "docs")


def grep_payload(res: dict, limit: int = API_MAX_PER_SECTION) -> dict:
    """The JSON shape of a grep result — for a client that DRAWS it (`GET
    /grep`, the studio) instead of reading a string. Same roles, same order,
    same ranking as `render_grep`; the difference is that the sections stay
    lists of records so the caller can lay out file/symbol/in-degree/
    reached-from as UI instead of re-parsing text.

    Sections are capped for the wire, and `counts` carries the TRUE totals —
    the same rule the text view follows: a bounded listing that reads as
    complete is a lie, so a client can always say "200 of 1240" and mean it."""
    return {**{k: v for k, v in res.items() if k not in SECTIONS},
            "counts": {k: len(res[k]) for k in SECTIONS},
            "limit": limit,
            **{k: res[k][:limit] for k in SECTIONS}}


def _fmt(m: dict, arrow: bool = False) -> str:
    where = f'{m["file"]}:{m["line"]}'
    sym = f' · {m["kind"]} {m["symbol"]}' if m.get("symbol") else ""
    out = f"  {where}{sym}\n    {m['text']}"
    if arrow and m.get("reached_from"):
        out += "\n    ← reached from: " + ", ".join(m["reached_from"])
    return out


def render_grep(res: dict) -> str:
    """The grouped text view. Sections in fixed order; overflow counted."""
    head = (f'# megabrain grep "{res["pattern"]}" · '
            f'{res["matches"]} match(es) in {res["files"]} file(s)')
    if res["matches"] == 0:
        # Zero is often THE answer (a flag nobody sets inherits its default —
        # proof of absence), and the entry paths refresh a stale index before
        # searching, so hedging here undermined the one result that mattered
        # (field report: "esa línea siembra duda"). State it as evidence, with
        # the honest scope: the INDEXED corpus — lockfiles, config JSON and
        # other files the scan skips are not covered.
        return head + ("\n(0 matches — verified absence over the indexed "
                       "corpus, refreshed before this search. Files the index "
                       "skips — lockfiles, config JSON, binaries — are not "
                       "covered; plain grep those.)")
    parts = [head]
    titles = {"defines": "DEFINES", "reads": "READS (by graph centrality)",
              "config": "CONFIG/DATA", "tests": "TESTS", "docs": "DOCS"}
    # a reached-from list repeats verbatim across matches of the same core
    # module (click field run: the identical 4-file list printed 20 times) —
    # print each distinct list ONCE; repeats add zero information.
    seen_arrows: set[tuple] = set()
    for key, title in titles.items():
        ms = res[key]
        if not ms:
            continue
        parts.append(f"\n{title} ({len(ms)})")
        if key in ("defines", "reads"):
            shown = ms[:MAX_PER_SECTION]
            for m in shown:
                rf = tuple(m.get("reached_from") or ())
                arrow = bool(rf) and rf not in seen_arrows
                seen_arrows.add(rf)
                parts.append(_fmt(m, arrow=arrow))
            if len(ms) > len(shown):
                parts.append(f"  … +{len(ms) - len(shown)} more (narrow with "
                             f"a scope_path or a longer pattern)")
        else:
            # tests/docs/config matter as LOCATIONS, not as 26 quoted lines
            # (click field run: 'multiple=True' listed every test verbatim —
            # a wall). One line per file: count + the line numbers.
            by_file: dict[str, list[int]] = {}
            for m in ms:
                by_file.setdefault(m["file"], []).append(m["line"])
            for f, lns in list(by_file.items())[:MAX_PER_SECTION]:
                nums = " ".join(f"L{n}" for n in lns[:8])
                extra = f" +{len(lns) - 8} more" if len(lns) > 8 else ""
                parts.append(f"  {f} ×{len(lns)} · {nums}{extra}")
            if len(by_file) > MAX_PER_SECTION:
                parts.append(f"  … +{len(by_file) - MAX_PER_SECTION} more "
                             f"file(s)")
    return "\n".join(parts)
