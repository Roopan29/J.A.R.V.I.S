"""Web search and research tools."""
from __future__ import annotations

from ...database.models import ActionResult
from ...intelligence import web_search
from ..permissions import Capability
from ..tool_registry import ToolSpec, registry


def _wrap_search(query: str, count: int = 6) -> ActionResult:
    try:
        results = web_search.search(query, count=count)
    except web_search.SearchUnavailable as exc:
        return ActionResult(ok=False, message=str(exc))
    except (ValueError, OSError) as exc:
        return ActionResult(ok=False, message=f"Search failed: {exc}")
    listing = "; ".join(f"{r.title} ({r.source})" for r in results[:6])
    return ActionResult(ok=True, verified=True,
                        message=f"Top results for '{query}': {listing}",
                        data={"results": [r.to_dict() for r in results]})


def _wrap_research(query: str, depth: int = 3) -> ActionResult:
    result = web_search.research(query, depth)
    return ActionResult(ok=bool(result.get("ok")), message=result.get("message", ""),
                        data=result.get("data", {}), verified=bool(result.get("verified")))


registry.register(ToolSpec(
    name="web_search",
    description="Search the web and return titles, snippets and source URLs.",
    parameters={"type": "object",
                "properties": {"query": {"type": "string"},
                               "count": {"type": "integer", "default": 6}},
                "required": ["query"]},
    handler=lambda query, count=6: _wrap_search(query, count),
    category="web", capability=Capability.NETWORK,
))

registry.register(ToolSpec(
    name="research_topic",
    description="Search the web AND read the top pages so you can summarise or compare "
                "with real source material. Use for 'research', 'explain', 'compare', "
                "'what's the latest' questions.",
    parameters={"type": "object",
                "properties": {"query": {"type": "string"},
                               "depth": {"type": "integer", "default": 3,
                                         "description": "How many pages to read"}},
                "required": ["query"]},
    handler=lambda query, depth=3: _wrap_research(query, depth),
    category="web", capability=Capability.NETWORK,
))

registry.register(ToolSpec(
    name="read_webpage",
    description="Fetch a single web page and extract its readable text.",
    parameters={"type": "object", "properties": {"url": {"type": "string"}},
                "required": ["url"]},
    handler=lambda url: _read_page(url),
    category="web", capability=Capability.NETWORK,
))


def _read_page(url: str) -> ActionResult:
    try:
        page = web_search.fetch_page(url)
    except (ValueError, OSError) as exc:
        return ActionResult(ok=False, message=str(exc))
    if page.error:
        return ActionResult(ok=False, message=f"Could not read page: {page.error}")
    return ActionResult(ok=True, verified=True,
                        message=f"Read '{page.title}' ({len(page.text)} characters).",
                        data=page.to_dict())
