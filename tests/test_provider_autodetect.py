"""Default chat provider is AUTO: Claude when the SDK is importable, else
OpenRouter; an explicit MEGABRAIN_CHAT_PROVIDER always wins. These tests set
the env explicitly (the autouse conftest fixture pins openrouter otherwise)."""

import importlib.util

from megabrain import providers


def test_explicit_env_wins_both_ways(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "claude")
    assert providers.chat_provider() == "claude"
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "OpenRouter")   # case-insensitive
    assert providers.chat_provider() == "openrouter"


def test_auto_picks_claude_when_sdk_present(monkeypatch):
    monkeypatch.delenv("MEGABRAIN_CHAT_PROVIDER", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert providers.chat_provider() == "claude"


def test_auto_falls_back_to_openrouter_without_sdk(monkeypatch):
    monkeypatch.delenv("MEGABRAIN_CHAT_PROVIDER", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert providers.chat_provider() == "openrouter"
    assert providers.ask_model() == "google/gemini-3-flash-preview"
