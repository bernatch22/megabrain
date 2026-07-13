"""MCP tool schemas are a PUBLIC contract: registered clients (claude mcp add)
and the demo depend on the exact names, property names/types, required lists
and descriptions. This pins them byte-for-byte against a committed golden so a
frontend refactor can never silently drift the wire schema.

Regenerate ONLY on an intended schema change:
    python3 -c "import json;from megabrain.frontends.mcp import TOOLS;\
json.dump(TOOLS,open('tests/goldens/mcp_tools.json','w'),indent=1,sort_keys=True)"
"""

import json
from pathlib import Path

from megabrain.frontends.mcp import TOOLS

GOLDEN = Path(__file__).parent / "goldens" / "mcp_tools.json"


def test_mcp_tools_byte_identical():
    got = json.dumps(TOOLS, indent=1, sort_keys=True)
    assert got == GOLDEN.read_text(), (
        "MCP tool schema drifted — if intended, regenerate the golden "
        "(see this file's docstring)")
