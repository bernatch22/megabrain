"""`megabrain install` — the MCP registration table.

These run against a fake $HOME so they never touch the developer's real
assistant configs. The invariant that matters: we only ever own the `megabrain`
key — every other server the user configured must survive untouched.
"""

from __future__ import annotations

import json

import pytest

from megabrain.server import install as I


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(I.Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_detect_reports_not_installed_on_empty_home(home):
    rows = {r["platform"]: r for r in I.detect()}
    assert set(rows) == set(I.PLATFORMS)
    assert not any(r["installed"] for r in rows.values())
    assert not any(r["registered"] for r in rows.values())


def test_json_install_preserves_other_servers(home):
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({
        "mcpServers": {"mypry": {"command": "node", "args": ["x.js"]}},
        "projects": {"/some/repo": {"keep": True}},
    }))
    I.apply(platform="claude")
    d = json.loads(cfg.read_text())
    assert "megabrain" in d["mcpServers"]
    assert d["mcpServers"]["mypry"] == {"command": "node", "args": ["x.js"]}, \
        "must not clobber other MCP servers"
    assert d["projects"] == {"/some/repo": {"keep": True}}, \
        "must not touch unrelated top-level config"


def test_install_is_idempotent_and_repairs_a_stale_entry(home):
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"megabrain": {
        "command": "python3", "env": {"PYTHONPATH": "/old/checkout"}}}}))
    I.apply(platform="claude")
    entry = json.loads(cfg.read_text())["mcpServers"]["megabrain"]
    assert "env" not in entry, "a stale PYTHONPATH must be dropped, not merged into"
    assert entry["args"] == ["-m", "megabrain.mcp_server"]
    first = cfg.read_text()
    I.apply(platform="claude")
    assert cfg.read_text() == first, "re-running must be a no-op"


def test_toml_install_appends_and_replaces_only_our_section(home):
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('# my notes\n[mcp_servers.other]\ncommand = "node"\n')
    I.apply(platform="codex")
    text = cfg.read_text()
    assert "# my notes" in text, "comments must survive"
    assert '[mcp_servers.other]' in text, "other servers must survive"
    assert '[mcp_servers.megabrain]' in text
    # replace, not duplicate
    I.apply(platform="codex")
    assert cfg.read_text().count("[mcp_servers.megabrain]") == 1


def test_remove_drops_only_megabrain(home):
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"mypry": {"command": "node"}}}))
    I.apply(platform="claude")
    I.apply(platform="claude", remove=True)
    d = json.loads(cfg.read_text())
    assert "megabrain" not in d["mcpServers"]
    assert "mypry" in d["mcpServers"]


def test_toml_remove(home):
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[mcp_servers.other]\ncommand = "node"\n')
    I.apply(platform="codex")
    I.apply(platform="codex", remove=True)
    text = cfg.read_text()
    assert "[mcp_servers.megabrain]" not in text
    assert "[mcp_servers.other]" in text


def test_gemini_is_not_claimed_when_only_antigravity_exists(home):
    """~/.gemini exists for Antigravity too — Gemini CLI must not be claimed."""
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    rows = {r["platform"]: r for r in I.detect()}
    assert rows["antigravity"]["installed"] is True
    assert rows["gemini"]["installed"] is False


def test_unknown_platform_rejected(home):
    with pytest.raises(ValueError, match="unknown platform"):
        I.apply(platform="nope")


def test_auto_install_skips_platforms_that_are_absent(home):
    (home / ".codex").mkdir()
    res = {r["platform"]: r["action"] for r in I.apply()}
    assert res["codex"] == "registered"
    assert "skipped" in res["cursor"]
