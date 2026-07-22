"""`packages/` classification: Linguist's NuGet rule says vendored, the JS
workspace convention says first-party — the root manifest decides. Field
case: the nx scan census proposed skipping 4,275 source files (the entire
packages/ tree, daemon and project-graph included) as "vendored"."""

from megabrain.indexing.ignore import _is_js_workspace, is_vendored, scan


def test_packages_is_code_under_a_js_workspace(tmp_path):
    _is_js_workspace.cache_clear()
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n")
    assert not is_vendored("packages/nx/src/daemon/server/watcher.ts", tmp_path)
    # the OTHER vendor rules still fire inside a workspace
    assert is_vendored("packages/nx/node_modules/x/index.js", tmp_path)
    assert is_vendored("packages/nx/dist/main.js", tmp_path)


def test_packages_stays_vendored_without_a_workspace(tmp_path):
    """A .NET-shaped repo: packages/ holds NuGet restore output."""
    _is_js_workspace.cache_clear()
    assert is_vendored("packages/Newtonsoft.Json.13.0.1/lib/net45/x.md", tmp_path)
    # legacy callers without a root keep the conservative behavior
    assert is_vendored("packages/anything/file.md")


def test_every_workspace_marker_counts(tmp_path):
    import json
    for marker, content in (("nx.json", "{}"), ("lerna.json", "{}"),
                            ("turbo.json", "{}"), ("rush.json", "{}")):
        _is_js_workspace.cache_clear()
        root = tmp_path / marker.replace(".", "_")
        root.mkdir()
        (root / marker).write_text(content)
        assert not is_vendored("packages/a/b.ts", root), marker
    # package.json "workspaces" (yarn/npm) also counts
    _is_js_workspace.cache_clear()
    root = tmp_path / "yarnws"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
    assert not is_vendored("packages/a/b.ts", root)
    # a plain package.json does NOT
    _is_js_workspace.cache_clear()
    root = tmp_path / "plain"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"name": "app"}))
    assert is_vendored("packages/a/b.ts", root)


def test_scan_census_keeps_workspace_packages(tmp_path):
    """The end-to-end shape of the nx bite: with the manifest present the
    census must count packages/ as indexable, not propose ignoring it."""
    _is_js_workspace.cache_clear()
    (tmp_path / "nx.json").write_text("{}")
    pkg = tmp_path / "packages" / "core" / "src"
    pkg.mkdir(parents=True)
    (pkg / "engine.ts").write_text("export function run() { return 1; }\n")
    rep = scan(tmp_path, exts=(".ts",))
    assert rep["would_index"] == 1
    assert not any(f["path"].startswith("packages/") for f in rep["flagged"])
    assert "packages/" not in rep["proposed_ignore"]
