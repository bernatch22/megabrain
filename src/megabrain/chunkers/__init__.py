"""Chunkers — one per content type, all emitting the same FileResult contract
(see base.py). PHP lives in .php and is imported lazily by strategies.py so the
optional tree_sitter_php grammar is only touched when installed."""

from .base import DEFAULT_BUDGET, Chunk, FileResult, Symbol, embed_text, nws, validate_partition
from .markdown import MarkdownChunker, qmd_cut
from .python import CastChunker
from .treesitter import (
                   GO_SPEC,
                   PHP_SPEC,
                   RUBY_SPEC,
                   RUST_SPEC,
                   TS_SPEC,
                   LangSpec,
                   TreeChunkerOps,
                   TreeSitterChunker,
                   TsChunker,
                   parser_for,
)

__all__ = [
    "DEFAULT_BUDGET", "Chunk", "FileResult", "Symbol", "embed_text", "nws",
    "validate_partition", "CastChunker", "MarkdownChunker", "qmd_cut",
    "TreeSitterChunker", "TsChunker", "LangSpec", "TreeChunkerOps", "parser_for",
    "TS_SPEC", "RUBY_SPEC", "GO_SPEC", "RUST_SPEC", "PHP_SPEC",
]
