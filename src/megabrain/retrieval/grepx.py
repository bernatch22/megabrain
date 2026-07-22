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
        return head + "\n(no matches in the indexed corpus — is the index fresh?)"
    parts = [head]
    titles = {"defines": "DEFINES", "reads": "READS (by graph centrality)",
              "config": "CONFIG/DATA", "tests": "TESTS", "docs": "DOCS"}
    for key, title in titles.items():
        ms = res[key]
        if not ms:
            continue
        shown = ms[:MAX_PER_SECTION]
        parts.append(f"\n{title} ({len(ms)})")
        parts += [_fmt(m, arrow=key in ("defines", "reads")) for m in shown]
        if len(ms) > len(shown):
            parts.append(f"  … +{len(ms) - len(shown)} more (narrow with a "
                         f"scope_path or a longer pattern)")
    return "\n".join(parts)
