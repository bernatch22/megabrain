"""megabrain install — register the MCP server with your AI coding assistants.

megabrain speaks MCP, and MCP is portable: the SAME stdio server works in Claude
Code, Codex, Antigravity, Cursor, Windsurf and Gemini CLI. Only the config file
differs (path + format + key), so this module keeps that one table and writes the
entry for whichever platforms are actually installed on the machine.

The entry always points at THIS interpreter (`sys.executable -m
megabrain.mcp_server`), so an install can never leave a stale PYTHONPATH aimed at
some other checkout — re-running it repairs a drifted config in place.

Merge semantics: only the `megabrain` key is written; every other server the user
configured is preserved. JSON configs round-trip through json; codex's TOML gets a
targeted section replace/append so hand-written comments survive (no TOML writer in
the stdlib, and megabrain takes no dependencies).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SERVER_NAME = "megabrain"

# path is relative to $HOME. `dir_hint` is what proves the platform is installed
# even before it has ever written an MCP config (e.g. a fresh codex).
PLATFORMS: dict[str, dict] = {
    "claude": {"path": ".claude.json", "fmt": "json", "key": "mcpServers",
               "dir_hint": ".claude", "label": "Claude Code"},
    "codex": {"path": ".codex/config.toml", "fmt": "toml", "key": "mcp_servers",
              "dir_hint": ".codex", "label": "Codex"},
    "antigravity": {"path": ".gemini/antigravity/mcp_config.json", "fmt": "json",
                    "key": "mcpServers", "dir_hint": ".gemini/antigravity",
                    "label": "Antigravity"},
    "cursor": {"path": ".cursor/mcp.json", "fmt": "json", "key": "mcpServers",
               "dir_hint": ".cursor", "label": "Cursor"},
    "windsurf": {"path": ".codeium/windsurf/mcp_config.json", "fmt": "json",
                 "key": "mcpServers", "dir_hint": ".codeium/windsurf",
                 "label": "Windsurf"},
    # NOT ".gemini" — that dir also exists for Antigravity, which nests under it;
    # keying off the settings file avoids claiming Gemini CLI is installed when
    # only Antigravity is.
    "gemini": {"path": ".gemini/settings.json", "fmt": "json", "key": "mcpServers",
               "dir_hint": ".gemini/settings.json", "label": "Gemini CLI"},
}


def _entry() -> dict:
    """The MCP server entry — pinned to the interpreter megabrain is installed in,
    so it keeps working regardless of the caller's cwd/PATH."""
    return {"command": sys.executable, "args": ["-m", "megabrain.mcp_server"]}


def detect() -> list[dict]:
    """Which platforms are on this machine, and is megabrain registered yet."""
    home = Path.home()
    out = []
    for name, cfg in PLATFORMS.items():
        path = home / cfg["path"]
        installed = (home / cfg["dir_hint"]).exists() or path.exists()
        out.append({"platform": name, "label": cfg["label"], "path": path,
                    "installed": installed, "registered": _is_registered(path, cfg)})
    return out


def _is_registered(path: Path, cfg: dict) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if cfg["fmt"] == "toml":
        return bool(re.search(rf"^\[{cfg['key']}\.{SERVER_NAME}\]", text, re.M))
    try:
        return SERVER_NAME in (json.loads(text or "{}").get(cfg["key"]) or {})
    except json.JSONDecodeError:
        return False


def _write_json(path: Path, key: str, entry: dict | None) -> None:
    """Merge (or drop) the megabrain key, leaving every other server untouched."""
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{path} is not valid JSON — fix it first ({e})") from e
    servers = data.setdefault(key, {})
    if entry is None:
        servers.pop(SERVER_NAME, None)
    else:
        servers[SERVER_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


_TOML_BLOCK = re.compile(
    rf"^\[{{key}}\.{SERVER_NAME}\]\n(?:(?!^\[).*\n?)*", re.M)


def _write_toml(path: Path, key: str, entry: dict | None) -> None:
    """Targeted section replace/append — the stdlib has no TOML writer, and a
    naive rewrite would eat the user's comments and other servers."""
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    block = _TOML_BLOCK.pattern.replace("{key}", re.escape(key))
    pat = re.compile(block, re.M)
    if entry is None:
        new = pat.sub("", text)
    else:
        args = ", ".join(json.dumps(a) for a in entry["args"])
        section = (f"[{key}.{SERVER_NAME}]\n"
                   f"command = {json.dumps(entry['command'])}\n"
                   f"args = [{args}]\n")
        new = pat.sub(section, text) if pat.search(text) else \
            (text.rstrip("\n") + "\n\n" + section if text.strip() else section)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")


def apply(platform: str | None = None, remove: bool = False) -> list[dict]:
    """Register (or with remove=True, unregister) megabrain. Default: every
    platform detected on this machine. Returns one result row per platform."""
    targets = [platform] if platform else None
    if platform and platform not in PLATFORMS:
        raise ValueError(f"unknown platform '{platform}' — choose from: "
                         f"{', '.join(PLATFORMS)}")
    entry = None if remove else _entry()
    results = []
    for row in detect():
        name = row["platform"]
        if targets and name not in targets:
            continue
        if not targets and not row["installed"]:
            results.append({**row, "action": "skipped (not installed)"})
            continue
        path, cfg = row["path"], PLATFORMS[name]
        try:
            if cfg["fmt"] == "toml":
                _write_toml(path, cfg["key"], entry)
            else:
                _write_json(path, cfg["key"], entry)
            results.append({**row, "action": "removed" if remove else "registered"})
        except Exception as e:  # noqa: BLE001 — report per platform, keep going
            results.append({**row, "action": f"FAILED: {e}"})
    return results


def render(results: list[dict], remove: bool = False) -> str:
    verb = "Unregistered" if remove else "Registered"
    lines = []
    for r in results:
        mark = "✓" if r["action"] in ("registered", "removed") else "·"
        lines.append(f"  {mark} {r['label']:<12} {r['action']:<24} {r['path']}")
    done = [r for r in results if r["action"] in ("registered", "removed")]
    head = f"{verb} megabrain in {len(done)} platform(s):" if done else \
        "No platforms touched."
    tail = ("\n\nRestart your assistant to pick it up. Tools: megabrain_ask, "
            "megabrain_query, megabrain_index, megabrain_forge, megabrain_flows."
            if done and not remove else "")
    return head + "\n" + "\n".join(lines) + tail
