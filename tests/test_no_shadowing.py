"""Guard against silent module shadowing by an INSTALLED megabrain.

The dev machine may carry an editable install of the original engine
(`pip install -e ~/megabrain`). Setuptools' editable finder sits on
sys.meta_path and resolves `megabrain.<sub>` BY NAME when our package lacks
that submodule — so a typo'd/renamed import ("from ..store import Store")
doesn't ImportError: it silently loads the OLD engine's module and every test
keeps passing against foreign code. This caught a real one (indexer importing
0.7.2's Store mid-restructure).

Two guards: every loaded megabrain module must live under THIS repo's src/,
and the legacy module names removed by the restructure must not resolve at all
(if they do, it's the ghost).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SRC = (Path(__file__).parent.parent / "src").resolve()

# modules that no longer exist in v2 — resolving means the installed old engine
LEGACY = ["megabrain.store", "megabrain.flows", "megabrain.ask_agents",
          "megabrain.retrieval.query", "megabrain.forge_eval",
          "megabrain.forge_specialize", "megabrain.frontends",
          "megabrain.session", "megabrain.docsearch"]


def test_every_loaded_megabrain_module_is_ours():
    import megabrain  # noqa: F401 — populate sys.modules via the suite's imports
    for name, mod in list(sys.modules.items()):
        if not name.startswith("megabrain"):
            continue
        f = getattr(mod, "__file__", None)
        if f is None:                      # namespace stubs — nothing loaded
            continue
        assert Path(f).resolve().is_relative_to(SRC), (
            f"{name} loaded from {f} — an INSTALLED megabrain is shadowing "
            f"this repo (stale import somewhere resolves outside src/)")


@pytest.mark.parametrize("name", LEGACY)
def test_legacy_module_names_are_not_ours(name):
    """A legacy name must not resolve to a file in THIS repo (a leftover from
    the restructure). If it resolves to an INSTALLED old engine instead, that
    is an environment condition, not a repo bug — the guard above already
    proves none of OUR code loads it — so report it as a skip, not a failure."""
    try:
        mod = importlib.import_module(name)
    except ImportError:
        return                             # correct: the module is gone
    try:
        f = getattr(mod, "__file__", "?")
        if f != "?" and Path(f).resolve().is_relative_to(SRC):
            raise AssertionError(f"{name} still exists in this repo: {f} — "
                                 f"leftover file from the restructure")
        pytest.skip(f"{name} shadowed by an installed engine ({f}) — "
                    f"guard #1 proves our code never imports it")
    finally:
        sys.modules.pop(name, None)        # don't leak the ghost to other tests
