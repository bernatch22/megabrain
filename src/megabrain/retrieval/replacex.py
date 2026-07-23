"""megabrain replace — transactional exact-string edits, the write half of
the read->edit loop.

The host's Edit requires a prior host Read of the same file — so every body
megabrain already rendered gets paid twice, which is the token leak the whole
map/read design exists to kill. This tool applies a BATCH of exact-string
operations in one call, two-phase:

    validate ALL ops in memory first (find must occur exactly `count` times,
    default 1, on the evolving text) -> only if every op passes, write.

Any failure means NOTHING is written and the report says exactly which op
failed and why (with the nearest line when the text was not found). The
engine never invents content: `find` and `replace` are the agent's own
strings, applied verbatim — same anti-hallucination stance as ask's splicing.
New files are out of scope (use the host's Write): replace edits what exists.
"""

from __future__ import annotations

import difflib
from pathlib import Path


def _field(op: dict, *names: str):
    """First present alias among *names* — tolerates agents that reach for
    path/old/new instead of the canonical file/find/replace."""
    for n in names:
        if op.get(n) is not None:
            return op[n]
    return None


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve()) + "/"):
        raise ValueError(f"path escapes the repo: {rel!r}")
    return p


def _nearest(text: str, find: str) -> str:
    """Best-matching line for a not-found `find` — the typo is usually
    whitespace or one identifier off, and seeing the real line unblocks the
    retry without another read."""
    probe = next((ln.strip() for ln in find.splitlines() if ln.strip()), "")
    if not probe:
        return ""
    lines = text.splitlines()
    hit = difflib.get_close_matches(probe, [ln.strip() for ln in lines], 1, 0.5)
    if not hit:
        return ""
    for i, ln in enumerate(lines, 1):
        if ln.strip() == hit[0]:
            return f' Nearest line: L{i} {ln.strip()[:80]!r}'
    return ""


def apply_ops(root: Path, operations: list[dict]) -> dict:
    root = Path(root)
    texts: dict[str, str] = {}
    report: list[dict] = []
    failed = False
    for i, op in enumerate(operations, 1):
        # accept the aliases agents reach for by habit (the host Edit uses
        # old_string/new_string; many tools use path/old/new) — a field-name
        # mismatch used to fail cryptically as "path escapes the repo: ''".
        rel = str(_field(op, "file", "path", "filename") or "")
        find = str(_field(op, "find", "old", "old_string", "search") or "")
        repl = str(_field(op, "replace", "new", "new_string", "with") or "")
        want = int(op.get("count", 1))
        row = {"op": i, "file": rel}
        report.append(row)
        if not rel:
            row["error"] = ("missing 'file'. Each operation is "
                            "{file, find, replace, count?}.")
            failed = True
            continue
        try:
            p = _safe(root, rel)
        except ValueError as e:
            row["error"] = str(e)
            failed = True
            continue
        if rel not in texts:
            if not p.is_file():
                row["error"] = f"no such file: {rel} (replace edits existing files; use Write for new ones)"
                failed = True
                continue
            texts[rel] = p.read_text(encoding="utf-8")
        if not find:
            row["error"] = "empty find"
            failed = True
            continue
        n = texts[rel].count(find)
        if n != want:
            row["error"] = (f"find occurs {n} time(s), expected {want}."
                            + (_nearest(texts[rel], find) if n == 0 else
                               " Add surrounding lines to make it unique, or pass count."))
            failed = True
            continue
        texts[rel] = texts[rel].replace(find, repl)
        row["replaced"] = want
    if failed:
        return {"ok": False, "report": report, "written": []}
    written = []
    for rel, text in texts.items():
        _safe(root, rel).write_text(text, encoding="utf-8")
        written.append(rel)
    return {"ok": True, "report": report, "written": written}


def render_replace(res: dict) -> str:
    if res["ok"]:
        L = [f'# megabrain replace — {len(res["report"])} op(s) applied, '
             f'{len(res["written"])} file(s) written. Run the gates now.']
        for r in res["report"]:
            L.append(f'  ok op {r["op"]} {r["file"]} — {r["replaced"]} replacement(s)')
        return "\n".join(L)
    L = ['# megabrain replace — FAILED, NOTHING was written (transactional).']
    for r in res["report"]:
        L.append(f'  {"FAIL" if "error" in r else "ok  "} op {r["op"]} {r["file"]}'
                 + (f' — {r["error"]}' if "error" in r else ""))
    return "\n".join(L)
