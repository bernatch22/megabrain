"""Global repo registry (~/.megabrain/registry.json) — register/list/heal."""

import json

import pytest

from megabrain.storage import registry


@pytest.fixture
def reg_file(tmp_path, monkeypatch):
    f = tmp_path / "reg" / "registry.json"
    monkeypatch.setenv("MEGABRAIN_REGISTRY", str(f))
    return f


def _fake_repo(tmp_path, name="repoA"):
    root = tmp_path / name
    (root / ".megabrain").mkdir(parents=True)
    (root / ".megabrain" / "db.sqlite").write_bytes(b"")
    return root


def test_register_and_list(reg_file, tmp_path):
    root = _fake_repo(tmp_path)
    registry.register(root, {"files": 12, "chunks": 99, "embed_model": "m1"})
    rows = registry.list_repos()
    assert len(rows) == 1
    e = rows[0]
    assert e["name"] == "repoA" and e["path"] == root.resolve().as_posix()
    assert e["files"] == 12 and e["chunks"] == 99 and e["embed_model"] == "m1"
    assert e["last_index"] > 0


def test_register_upserts_not_duplicates(reg_file, tmp_path):
    root = _fake_repo(tmp_path)
    registry.register(root, {"files": 1, "chunks": 1})
    registry.register(root, {"files": 2, "chunks": 5})
    rows = registry.list_repos()
    assert len(rows) == 1 and rows[0]["files"] == 2 and rows[0]["chunks"] == 5


def test_list_orders_newest_first(reg_file, tmp_path):
    a, b = _fake_repo(tmp_path, "a"), _fake_repo(tmp_path, "b")
    registry.register(a, {})
    registry.register(b, {})     # registered later -> larger last_index
    data = json.loads(reg_file.read_text())
    data[a.resolve().as_posix()]["last_index"] -= 100   # force a strict gap
    reg_file.write_text(json.dumps(data))
    assert [e["name"] for e in registry.list_repos()] == ["b", "a"]


def test_validate_drops_dead_entries_and_persists(reg_file, tmp_path):
    alive, dead = _fake_repo(tmp_path, "alive"), _fake_repo(tmp_path, "dead")
    registry.register(alive, {})
    registry.register(dead, {})
    (dead / ".megabrain" / "db.sqlite").unlink()        # index vanished
    rows = registry.list_repos()
    assert [e["name"] for e in rows] == ["alive"]
    # the prune persisted — the dead pointer is gone from the file too
    assert list(json.loads(reg_file.read_text())) == [alive.resolve().as_posix()]


def test_unregister(reg_file, tmp_path):
    root = _fake_repo(tmp_path)
    registry.register(root, {})
    registry.unregister(root)
    assert registry.list_repos() == []


def test_corrupt_registry_fails_open(reg_file, tmp_path):
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    reg_file.write_text("{not json!!")
    assert registry.list_repos() == []                  # read tolerates garbage
    root = _fake_repo(tmp_path)
    registry.register(root, {"files": 3})               # write replaces garbage
    assert [e["files"] for e in registry.list_repos()] == [3]


def test_register_never_raises(tmp_path, monkeypatch):
    # point the registry at an unwritable location: register must swallow it
    monkeypatch.setenv("MEGABRAIN_REGISTRY", "/dev/null/nope/registry.json")
    registry.register(tmp_path, {"files": 1})           # no exception
    assert registry.list_repos() == []
