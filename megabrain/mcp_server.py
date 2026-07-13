"""Back-compat launcher — the MCP server lives in megabrain.frontends.mcp.
This path stays because `python3 -m megabrain.mcp_server` is registered in
users' MCP configs (claude mcp add megabrain -- python3 -m megabrain.mcp_server)."""

from .frontends.mcp import PROTOCOL, TOOLS, call_tool, main  # noqa: F401

if __name__ == "__main__":
    main()
