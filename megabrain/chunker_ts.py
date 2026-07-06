"""Back-compat shim — moved to `megabrain.chunkers.treesitter`.
Import from `megabrain.chunkers`; this module goes away in a future release."""

from .chunkers.treesitter import (  # noqa: F401
                                  DEFAULT_BUDGET,
                                  GO_SPEC,
                                  PHP_SPEC,
                                  RUBY_SPEC,
                                  RUST_SPEC,
                                  TS_SPEC,
                                  LangSpec,
                                  TreeSitterChunker,
                                  TsChunker,
                                  _parser,
                                  _signature,
)
