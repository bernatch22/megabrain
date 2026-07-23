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
