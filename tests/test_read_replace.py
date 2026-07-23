"""megabrain read + replace — the in-engine read->edit loop.

read: EVERY target in one call (path / path#symbol / path:start-end),
verbatim with true line numbers. replace: transactional exact-string batch —
validate all, write all or nothing. Together they kill the one-host-Read-per
-turn tax the duels measured."""

from megabrain.retrieval.readx import read_specs, render_read
from megabrain.retrieval.replacex import apply_ops, render_replace

# ------------------------------------------------------------------- read

def test_read_three_spec_forms_in_one_call(tiny_repo):
    res = read_specs(tiny_repo, ["util.py",
                                 "auth/login.py#check_password",
                                 "billing/invoice.py:1-2"])
    ok = [t for t in res["targets"] if "error" not in t]
    assert len(ok) == 3
    whole, sym, rng = ok
    assert whole["start"] == 1 and "def flatten(xs):" in whole["lines"][0]
    assert sym["file"] == "auth/login.py" and sym["start"] == 6
    assert any("hash(password)" in ln for ln in sym["lines"])
    assert rng["lines"][0].startswith("def create_invoice")
    out = render_read(res)
    assert "3 target(s)" in out and "true line numbers" in out
    assert "    6→def check_password" in out          # real line numbers


def test_read_symbol_not_found_lists_candidates(tiny_repo):
    res = read_specs(tiny_repo, ["auth/login.py#login_uzer"])
    t = res["targets"][0]
    assert "not found" in t["error"] and "login_user" in t["error"]
    assert "FAILED" in render_read(res)


def test_read_rejects_path_escape_and_missing_file(tiny_repo):
    res = read_specs(tiny_repo, ["../secrets.txt", "nope.py"])
    errs = [t["error"] for t in res["targets"]]
    assert "escapes the repo" in errs[0]
    assert "no such file" in errs[1]


def test_read_whole_file_cap_redirects_to_symbols(tiny_repo):
    big = tiny_repo / "big.py"
    big.write_text("\n".join(f"x{i} = {i}" for i in range(1500)) + "\n")
    res = read_specs(tiny_repo, ["big.py"])
    assert "narrow" in res["targets"][0]["error"]
    # an explicit range still works on the same file
    res = read_specs(tiny_repo, ["big.py:2-3"])
    assert res["targets"][0]["lines"] == ["x1 = 1", "x2 = 2"]


def test_read_oversized_batch_defers_tail_never_spills(tiny_repo):
    # A batch over the render budget must SPLIT: what fits renders now, the
    # rest comes back as a ready re-call — if the full render went out, the
    # MCP host would dump it to a file the agent has to Read back in host
    # chunks (the exact round-trips this tool exists to kill).
    res = read_specs(tiny_repo, ["util.py",
                                 "auth/login.py#check_password",
                                 "billing/invoice.py:1-2"])
    out = render_read(res, budget=len(render_read(res)) - 40)
    assert "NOT RENDERED" in out and "megabrain_read" in out
    assert '"billing/invoice.py:1-2"' in out       # the tail, verbatim spec
    assert "def flatten(xs):" in out               # the head DID render
    assert "create_invoice" not in out             # the tail did NOT


def test_read_first_target_over_budget_renders_prefix_with_continuation(tiny_repo):
    big = tiny_repo / "wide.py"
    big.write_text("\n".join(f"v{i} = {i}" for i in range(1, 201)) + "\n")
    res = read_specs(tiny_repo, ["wide.py:1-200"])
    out = render_read(res, budget=900)
    # never an empty result: the prefix renders, the remainder is an exact spec
    assert "    1→v1 = 1" in out
    assert "NOT RENDERED" in out and '"wide.py:' in out
    import re
    cont = re.search(r'"wide\.py:(\d+)-200"', out)
    assert cont and int(cont.group(1)) > 1


# ---------------------------------------------------------------- replace

def test_replace_batch_applies_and_reports(tiny_repo):
    res = apply_ops(tiny_repo, [
        {"file": "util.py", "find": "Flatten a nested list one level.",
         "replace": "Flatten ONE level of nesting."},
        {"file": "auth/login.py", "find": "% 7 == hash(name) % 7",
         "replace": "% 11 == hash(name) % 11"},
    ])
    assert res["ok"] and sorted(res["written"]) == ["auth/login.py", "util.py"]
    assert "ONE level" in (tiny_repo / "util.py").read_text()
    assert "% 11" in (tiny_repo / "auth" / "login.py").read_text()
    assert "Run the gates now." in render_replace(res)


def test_replace_is_transactional_nothing_written_on_any_failure(tiny_repo):
    before = (tiny_repo / "util.py").read_text()
    res = apply_ops(tiny_repo, [
        {"file": "util.py", "find": "Flatten a nested list one level.",
         "replace": "CHANGED"},
        {"file": "util.py", "find": "THIS TEXT DOES NOT EXIST",
         "replace": "x"},
    ])
    assert not res["ok"]
    assert (tiny_repo / "util.py").read_text() == before   # op 1 rolled back too
    out = render_replace(res)
    assert "NOTHING was written" in out and "occurs 0 time(s)" in out


def test_replace_not_found_names_the_nearest_line(tiny_repo):
    res = apply_ops(tiny_repo, [
        {"file": "util.py", "find": "def flaten(xs):", "replace": "def f():"}])
    assert "Nearest line: L1" in res["report"][0]["error"]


def test_replace_ambiguous_find_demands_uniqueness(tiny_repo):
    (tiny_repo / "dup.py").write_text("a = 1\nb = 2\na = 1\n")
    res = apply_ops(tiny_repo, [
        {"file": "dup.py", "find": "a = 1", "replace": "a = 9"}])
    assert not res["ok"] and "occurs 2 time(s), expected 1" in res["report"][0]["error"]
    res = apply_ops(tiny_repo, [
        {"file": "dup.py", "find": "a = 1", "replace": "a = 9", "count": 2}])
    assert res["ok"] and (tiny_repo / "dup.py").read_text().count("a = 9") == 2


def test_replace_same_file_ops_see_prior_result(tiny_repo):
    (tiny_repo / "seq.py").write_text("v = 1\n")
    res = apply_ops(tiny_repo, [
        {"file": "seq.py", "find": "v = 1", "replace": "v = 2"},
        {"file": "seq.py", "find": "v = 2", "replace": "v = 3"},
    ])
    assert res["ok"] and (tiny_repo / "seq.py").read_text() == "v = 3\n"


def test_replace_rejects_escape_and_new_files(tiny_repo):
    res = apply_ops(tiny_repo, [
        {"file": "../evil.py", "find": "x", "replace": "y"}])
    assert not res["ok"] and "escapes the repo" in res["report"][0]["error"]
    res = apply_ops(tiny_repo, [
        {"file": "brand_new.py", "find": "x", "replace": "y"}])
    assert not res["ok"] and "use Write for new ones" in res["report"][0]["error"]
    assert not (tiny_repo / "brand_new.py").exists()


def test_read_missing_file_suggests_the_real_one(tiny_repo):
    """Field run (click#3652): the agent guessed CHANGES.rst on a repo that
    uses CHANGES.md and burned a recovery turn — a wrong-extension miss must
    name the sibling with the same stem."""
    (tiny_repo / "CHANGES.md").write_text("# changes\n")
    res = read_specs(tiny_repo, ["CHANGES.rst:1-10"])
    assert "Did you mean: CHANGES.md?" in res["targets"][0]["error"]


def test_replace_tolerates_path_old_new_aliases(tiny_repo):
    """Field run (attrs#1549): the agent called replace with {path, old, new}
    and {edits: "<json>"} by habit — it failed cryptically as 'path escapes
    the repo'. The canonical names are file/find/replace, but the common
    aliases now work, and a missing file names the field."""
    res = apply_ops(tiny_repo, [
        {"path": "util.py", "old": "Flatten a nested list one level.",
         "new": "Flattened."}])
    assert res["ok"] and "Flattened." in (tiny_repo / "util.py").read_text()
    # and a genuinely missing file field is a clear error, not a path-escape
    res = apply_ops(tiny_repo, [{"find": "x", "replace": "y"}])
    assert not res["ok"] and "missing 'file'" in res["report"][0]["error"]


def test_replace_dispatch_accepts_edits_and_json_string(tiny_repo):
    from megabrain.server import mcp
    out = mcp.call_tool("megabrain_replace", {
        "repo_path": str(tiny_repo),
        "edits": '[{"path": "util.py", "old": "Flatten a nested list one level.", "new": "Z"}]'})
    assert "1 op(s) applied" in out
    assert "Z" in (tiny_repo / "util.py").read_text()
