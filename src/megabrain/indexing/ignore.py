"""scan — index intelligence: what a repo SHOULD index, and what to skip.

Three deterministic, curated layers on top of the indexer's fixed EXCLUDE_DIRS
(no LLM — the best OSS tools here are deterministic, not learned):

1. `.gitignore` — the repo author already declared their noise. A minimal
   stdlib matcher (hierarchical, `!` negation, `**`/`*`/`?`, anchoring,
   trailing-`/` dir-only) covering ~95% of real patterns, keeping the engine's
   zero-dependency stance.
2. Linguist-style detectors — VENDORED (a curated path/glob list:
   node_modules, vendor, minified bundles, lockfiles…) and GENERATED (read the
   first ~2 KB for `@generated` / `DO NOT EDIT` markers, protobuf/codegen names,
   and minified-by-line-length).
3. too-big (> MAX_FILE_BYTES).

The hard rule (asymmetry): over-INCLUDING noise is cheap (it ranks low, is
ext- and size-capped). Auto-EXCLUDING source is a silent recall bug. So these
deterministic layers may skip (conservative + industry-standard), `scan`
ALWAYS reports what it skipped and WHY, and nothing here ever removes a file
from an existing index without the user confirming the proposed ignore.
"""

from __future__ import annotations

import re
from pathlib import Path

# ── .gitignore matcher (stdlib, hierarchical) ──────────────────────────────


def _translate(pat: str) -> str:
    """Translate a gitignore glob body (leading `!`/`/` and trailing `/`
    already stripped) into a regex fragment matching a POSIX relative path.
    `**` crosses directories, `*`/`?` do not."""
    out, i, n = [], 0, len(pat)
    while i < n:
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")          # zero or more leading dirs
            i += 3
        elif pat.startswith("/**", i):
            out.append("/.*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[":                 # char class — copy through the ]
            j = i + 1
            if j < n and pat[j] in "!^":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            klass = pat[i:j + 1]
            if klass.startswith("[!"):
                klass = "[^" + klass[2:]
            out.append(klass)
            i = j + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return "".join(out)


class _Rule:
    __slots__ = ("base", "negate", "regex")

    def __init__(self, base: str, line: str):
        self.negate = line.startswith("!")
        if self.negate:
            line = line[1:]
        dir_only = line.endswith("/")
        body = line.rstrip("/")
        anchored = body.startswith("/") or ("/" in body)
        body = body.lstrip("/")
        core = _translate(body)
        prefix = "^" if anchored else "^(?:.*/)?"
        # dir-only excludes a directory's CONTENTS (we only ever test files, so
        # require a `/…` tail); otherwise match the path itself OR its children.
        suffix = "/.*$" if dir_only else "(?:/.*)?$"
        self.base = base
        self.regex = re.compile(prefix + core + suffix)

    def match(self, rel: str) -> bool:
        if self.base:
            if not rel.startswith(self.base + "/"):
                return False
            rel = rel[len(self.base) + 1:]
        return bool(self.regex.match(rel))


class GitignoreMatcher:
    """Hierarchical `.gitignore` evaluation. Rules from shallower files come
    first, deeper files (more specific) later; within a file, line order is
    kept — so LAST match wins and a later `!negation` can re-include, matching
    git's own precedence closely enough for a census."""

    def __init__(self, rules: list[_Rule]):
        self._rules = rules

    @classmethod
    def load(cls, root: Path) -> "GitignoreMatcher":
        root = Path(root)
        files = sorted(root.rglob(".gitignore"),
                       key=lambda p: len(p.relative_to(root).parts))
        rules: list[_Rule] = []
        for gi in files:
            if ".git/" in gi.as_posix():
                continue
            base = gi.parent.relative_to(root).as_posix()
            base = "" if base == "." else base
            try:
                text = gi.read_text(errors="replace")
            except OSError:
                continue
            for raw in text.splitlines():
                ln = raw.rstrip()
                if not ln or ln.lstrip().startswith("#"):
                    continue
                if ln.strip() != ln and ln.endswith(" ") and not ln.endswith("\\ "):
                    ln = ln.rstrip()
                rules.append(_Rule(base, ln))
        return cls(rules)

    def ignored(self, rel: str) -> bool:
        """Is this repo-relative POSIX path git-ignored? Last matching rule
        wins; a negation rule re-includes."""
        state = False
        for r in self._rules:
            if r.match(rel):
                state = not r.negate
        return state


# ── Linguist-style vendored / generated detectors ──────────────────────────

# Curated vendored path/glob patterns — the essentials of Linguist's vendor.yml.
# Matched against the POSIX repo-relative path.
_VENDOR = [re.compile(p) for p in (
    r"(^|/)(node_modules|bower_components|jspm_packages)/",
    r"(^|/)(vendor|third[_-]?party|thirdparty|deps|Godeps|packages)/",
    r"(^|/)(dist|build|out|target|\.next|\.nuxt)/",
    r"(^|/)(jquery|bootstrap|angular|react|vue|d3|lodash|underscore|moment)"
    r"([.-][\w.]*)?\.js$",
    r"\.min\.(js|css)$",
    r"[.-](min|bundle)\.js$",
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|composer\.lock|"
    r"Gemfile\.lock|Cargo\.lock|poetry\.lock|Pipfile\.lock)$",
    r"(^|/)(\.yarn|\.pnp)\b",
)]

# Generated by NAME (fast path, no read).
_GEN_NAME = [re.compile(p) for p in (
    r"_pb2(_grpc)?\.pyi?$", r"\.pb\.go$", r"_pb\.js$", r"\.pb\.cc$",
    r"\.g\.dart$", r"\.g\.cs$", r"\.generated\.[\w]+$", r"_generated\.[\w]+$",
    r"\.designer\.cs$",
)]

# Generated CONTENT markers — checked in the first ~2 KB.
_GEN_MARKERS = re.compile(
    r"@generated\b|DO NOT EDIT|Code generated by|autogenerated|auto-generated|"
    r"machine[- ]generated|Generated by the protocol buffer compiler|"
    r"This file was automatically generated", re.I)


def is_vendored(rel: str) -> bool:
    return any(rx.search(rel) for rx in _VENDOR)


def is_generated(path: Path, rel: str) -> bool:
    """Name-based (protobuf/codegen), then a first-2 KB marker read, then a
    minified-by-line-length heuristic — cheapest checks first."""
    if any(rx.search(rel) for rx in _GEN_NAME):
        return True
    try:
        head = path.read_bytes()[:2048].decode("utf-8", "replace")
    except OSError:
        return False
    if _GEN_MARKERS.search(head):
        return True
    # minified: very long average line (bundlers strip newlines)
    nl = head.count("\n")
    if len(head) >= 2000 and nl <= 2:
        return True
    return nl and (len(head) / (nl + 1)) > 400


# ── the census ──────────────────────────────────────────────────────────────

MAX_FILE_BYTES = 600_000


def scan(root: Path, exts: tuple[str, ...], exclude=(),
         extra_names: set[str] | None = None) -> dict:
    """Census WITHOUT indexing: what WOULD be indexed, and every candidate
    skipped with the reason (gitignored / vendored / generated / too-big /
    excluded). `exts` is the set of known extensions (from the strategy
    registry); `extra_names` are the indexer's built-in EXCLUDE_DIRS so the
    census matches what indexing would actually do. Returns the UI's add-repo
    contract: {would_index, by_ext, top_dirs, flagged, proposed_ignore}."""
    root = Path(root).resolve()
    gi = GitignoreMatcher.load(root)
    excl_names = set(extra_names or ()) | {p.rstrip("/") for p in exclude
                                           if "/" not in p and not any(c in p for c in "*?[")}

    would = 0
    by_ext: dict[str, int] = {}
    dir_files: dict[str, int] = {}
    dir_bytes: dict[str, int] = {}
    flagged: list[dict] = []
    skip_dirs: set[str] = set()      # for the proposed ignore

    for p in sorted(root.rglob("*")):
        if p.suffix not in exts or not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        parts = rel.split("/")
        top = parts[0] if len(parts) > 1 else "."

        # precedence: excluded-dir -> gitignore -> vendored -> generated -> too-big
        reason = None
        if excl_names.intersection(parts):
            reason = "excluded"
        elif gi.ignored(rel):
            reason = "gitignored"
        elif is_vendored(rel):
            reason = "vendored"
        elif is_generated(p, rel):
            reason = "generated"
        elif p.stat().st_size > MAX_FILE_BYTES:
            reason = "too-big"

        if reason:
            flagged.append({"path": rel, "reason": reason})
            if reason in ("gitignored", "vendored", "generated") and len(parts) > 1:
                skip_dirs.add(top)
            continue

        would += 1
        by_ext[p.suffix] = by_ext.get(p.suffix, 0) + 1
        dir_files[top] = dir_files.get(top, 0) + 1
        try:
            dir_bytes[top] = dir_bytes.get(top, 0) + p.stat().st_size
        except OSError:
            pass

    top_dirs = sorted(({"dir": d, "files": n, "bytes": dir_bytes.get(d, 0)}
                       for d, n in dir_files.items()),
                      key=lambda x: -x["files"])[:10]
    proposed = _proposed_ignore(skip_dirs, flagged)
    return {
        "would_index": would,
        "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
        "top_dirs": top_dirs,
        "flagged": flagged,
        "proposed_ignore": proposed,
    }


def _proposed_ignore(skip_dirs: set[str], flagged: list[dict]) -> str:
    """A `.megabrainignore` proposal from the deterministic skips — one line
    per top-level dir that only held gitignored/vendored/generated files, with
    a reason comment. The user reviews it in the add-repo textarea before
    anything is written (nothing here auto-applies)."""
    if not skip_dirs:
        return ""
    reason_of: dict[str, str] = {}
    for f in flagged:
        top = f["path"].split("/", 1)[0]
        if top in skip_dirs and top not in reason_of:
            reason_of[top] = f["reason"]
    lines = ["# proposed by `megabrain scan` — review before indexing"]
    for d in sorted(skip_dirs):
        lines.append(f"{d}/    # {reason_of.get(d, 'noise')}")
    return "\n".join(lines) + "\n"
