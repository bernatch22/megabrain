"""get_code must never escape the repo root — it is exposed to untrusted input
via serve.py `GET /get?file=` and the MCP `megabrain_get` tool."""

from megabrain.retrieval.query import get_code


def test_relative_traversal_blocked(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "inside.py").write_text("x = 1\n")
    (tmp_path / "secret.txt").write_text("s3cr3t")
    out = get_code(root, "../secret.txt")
    assert out.startswith("not found")
    assert "s3cr3t" not in out


def test_deep_traversal_blocked(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert get_code(root, "a/../../../../etc/passwd").startswith("not found")


def test_absolute_path_blocked(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    # Path(root) / "/etc/passwd" resolves to the absolute path itself
    assert get_code(root, "/etc/passwd").startswith("not found")


def test_normal_read_still_works(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "inside.py").write_text("x = 1\n")
    assert "x = 1" in get_code(root, "src/inside.py")
