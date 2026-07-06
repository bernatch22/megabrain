"""Back-compat shim — moved to `megabrain.chunkers.markdown`.
Import from `megabrain.chunkers`; this module goes away in a future release."""

from .chunkers.markdown import MarkdownChunker, qmd_cut  # noqa: F401
