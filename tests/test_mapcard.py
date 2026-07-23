"""megabrain map — the structure card that replaces body-renders on implement
tasks: files ranked, match spans, RELEVANT symbol outline, edges both ways,
def sites, pinning tests. Never a code body (the host requires Read before
Edit — a body of an edit target is paid twice)."""

import pytest

from megabrain.retrieval.mapcard import map_repo, render_map


@pytest.fixture
def mapped(tiny_repo):
    return map_repo(tiny_repo, "how is a user login password checked")


def test_map_has_no_code_bodies(mapped):
    out = render_map(mapped)
    assert "```" not in out
    assert "def login_user(name, password):" not in out.replace(
        "def login_user(name, password)", "")  # signature ok, body never
    assert "return check_password" not in out                # body line
    assert "NO code bodies" in out


def test_map_ranks_files_with_spans_and_outline(mapped):
    files = [f["file"] for f in mapped["files"]]
    assert "auth/login.py" in files
    top = next(f for f in mapped["files"] if f["file"] == "auth/login.py")
    assert top["spans"] and top["spans"][0]["start_line"] >= 1
    sigs = " ".join(s["signature"] for s in top["outline"])
    assert "login_user" in sigs or "check_password" in sigs


def test_map_defines_lane_resolves_exact_identifiers(tiny_repo):
    res = map_repo(tiny_repo, "where is check_password defined")
    assert any(d["token"] == "check_password" and d["file"] == "auth/login.py"
               for d in res["defines"])


def test_map_render_is_grep_priced(mapped):
    assert len(render_map(mapped)) < 4000     # structure, not a dump
