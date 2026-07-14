"""megabrain ask — the LLM walkthrough layer (the ONLY layer that narrates).

    narrator   single-agent ask: cite-only prompt -> [[k]] citations spliced
               with verbatim code from disk; render_ask / stream_ask sinks
    agents     ask v2: classify -> plan -> parallel tool-using sub-agents ->
               synthesis; stream_events is the one event driver every surface
               (CLI, MCP, SSE, webui) sinks from
    warmup     flow-cache warm/refresh orchestration (drives the narrator;
               the cache mechanics live DOWN in storage.flows)

The documented API is `from megabrain.ask import ask, render_ask, stream_ask`
— this package interface is that surface.
"""

from .narrator import ask, cited_files, render_ask, stream_ask

__all__ = ["ask", "render_ask", "stream_ask", "cited_files"]
