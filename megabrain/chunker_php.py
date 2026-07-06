"""Back-compat shim — moved to `megabrain.chunkers.php`.
Import from `megabrain.chunkers.php`; this module goes away in a future release."""

from .chunkers.php import LegacyPhpChunker, PhpChunker, looks_legacy  # noqa: F401
