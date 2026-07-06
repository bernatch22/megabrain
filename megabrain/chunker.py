"""Back-compat shim — the Python chunker moved to `megabrain.chunkers`
(data model in `chunkers.base`, CastChunker in `chunkers.python`).
Import from `megabrain.chunkers`; this module goes away in a future release."""

from .chunkers.base import (DEFAULT_BUDGET, Chunk, FileResult,  # noqa: F401
                            Symbol, embed_text, nws, validate_partition)
from .chunkers.python import CastChunker  # noqa: F401
