"""Structured engine errors: a small taxonomy every boundary maps ONCE.

Errors are data, not strings: each carries a stable machine `code` (for MCP
payloads / logs) and an `http_status` (for serve-api). Frontends translate the
TYPE in exactly one catch site each — CLI prints one line and exits 2 (raw
traceback only under MEGABRAIN_DEBUG), HTTP maps to a status without leaking
internals, MCP returns `error (<code>): …` with isError.

Compatibility by construction: each subclass ALSO inherits the builtin type it
replaced (resolve_root raised ValueError, providers raised RuntimeError), so
pre-existing `except ValueError/RuntimeError` callers keep working — the same
trick as json.JSONDecodeError(ValueError).
"""

from __future__ import annotations


class MegabrainError(Exception):
    """Base engine error. `code` is a stable machine-readable slug;
    `http_status` is what serve-api answers with."""

    code = "error"
    http_status = 500


class IndexNotFound(MegabrainError, ValueError):
    """No .megabrain/db.sqlite at or above the given path."""

    code = "index_not_found"
    http_status = 404

    @classmethod
    def at(cls, path) -> "IndexNotFound":
        return cls(f"no megabrain index found at or above {path} — run "
                   f"`megabrain index` on the repo root (looked for "
                   f".megabrain/db.sqlite up the tree)")


class EmptyIndex(MegabrainError, RuntimeError):
    """The index exists but holds no chunks."""

    code = "empty_index"
    http_status = 404

    @classmethod
    def at(cls, path=None) -> "EmptyIndex":
        where = f" at {path}" if path else ""
        return cls(f"index{where} is empty — run: megabrain index")


class MissingAPIKey(MegabrainError, RuntimeError):
    """A required provider credential is not configured."""

    code = "missing_api_key"
    http_status = 503

    @classmethod
    def named(cls, name: str) -> "MissingAPIKey":
        return cls(f"{name} not set (env or ~/.zshrc)")


class ProviderError(MegabrainError, RuntimeError):
    """An upstream LLM/embedding endpoint failed after retries."""

    code = "provider_error"
    http_status = 502

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class UnknownTool(MegabrainError, ValueError):
    """An MCP tools/call named a tool this server does not expose."""

    code = "unknown_tool"
    http_status = 404
