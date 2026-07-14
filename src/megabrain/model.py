"""The read-side domain model: typed records for what the Store returns.

chunkers.base owns the WRITE-side entities (Chunk/Symbol/FileResult — what a
strategy produces); this module owns the READ side: what a stored chunk row
looks like to retrieval. One frozen record instead of the raw dict that used
to cross every layer — a typo'd key is now an AttributeError mypy catches,
and the column order lives in exactly one file (store.py packs and unpacks).

The BUNDLE (tier1/tier2 dicts) deliberately stays plain dicts/JSON: it is the
serialized public contract of /search, the MCP tools and the demo server.
ChunkMeta.to_dict() is the one conversion point where a record enters it.

Symbols remain dicts by decision: they are display-only rows fanned into
renders and prompts; typing them bought nothing measurable. Revisit if a
symbol key ever bites.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkMeta:
    """One indexed chunk as retrieval sees it (row of `chunks`, minus vec)."""

    id: int
    file: str
    kind: str | None
    name: str | None
    part: str | None
    start_line: int
    end_line: int
    text: str
    breadcrumb: str | None

    def to_dict(self) -> dict:
        """The bundle/JSON shape (public contract of /search, MCP, the demo)."""
        return {"id": self.id, "file": self.file, "kind": self.kind,
                "name": self.name, "part": self.part,
                "start_line": self.start_line, "end_line": self.end_line,
                "text": self.text, "breadcrumb": self.breadcrumb}
