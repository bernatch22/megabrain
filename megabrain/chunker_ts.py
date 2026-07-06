"""Back-compat shim — moved to `megabrain.chunkers.treesitter`.
Import from `megabrain.chunkers`; this module goes away in a future release."""

from .chunkers.treesitter import (DEFAULT_BUDGET, GO_SPEC,  # noqa: F401
                                  PHP_SPEC, RUBY_SPEC, RUST_SPEC, TS_SPEC,
                                  LangSpec, TreeSitterChunker, TsChunker,
                                  _parser, _signature)
