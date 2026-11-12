"""Web intelligence: search providers, page extraction and comparison.

Providers (auto-selected):
  1. Brave Search API  — if BRAVE_SEARCH_API_KEY is set (fast, structured).
  2. DuckDuckGo HTML   — no key required.
  3. Bing/Google HTML  — last resort scraping fallback.

Fails honestly when there is no internet connection.
"""
from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .. import config

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 12.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet,
                "source": self.source}


@dataclass
class PageContent:
    url: str
    title: str = ""
    text: str = ""
    links: List[Dict[str, str]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"url": self.url, "title": self.title, "text": self.text,
                "links": self.links[:30], "error": self.error}


class SearchUnavailable(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT,
                                 "Accept-Language": "en-US,en;q=0.9"},
                        timeout=TIMEOUT, follow_redirects=True)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def _brave(query: str, count: int) -> List[SearchResult]:
    key = config.secret("BRAVE_SEARCH_API_KEY")
    if not key:
        return []
    with _client() as client:
        resp = client.get("https://api.search.brave.com/res/v1/web/search",
                          params={"q": query, "count": count},
                          headers={"X-Subscription-Token": key,
                                   "Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    results = []
    for item in data.get("web", {}).get("results", [])[:count]:
        results.append(SearchResult(
            title=BeautifulSoup(item.get("title", ""), "html.parser").get_text(),
            url=item.get("url", ""),
            snippet=BeautifulSoup(item.get("description", ""), "html.parser").get_text(),
            source=urlparse(item.get("url", "")).netloc,
        ))
    return results


def _duckduckgo(query: str, count: int) -> List[SearchResult]:
    with _client() as client:
        resp = client.post("https://html.duckduckgo.com/html/",
                           data={"q": query, "kl": "wt-wt"})
        resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    results: List[SearchResult] = []
    for block in soup.select("div.result"):
        link = block.select_one("a.result__a")
        if not link:
            continue
        href = link.get("href", "")
        if href.startswith("//duckduckgo.com/l/"):
            match = re.search(r"uddg=([^&]+)", href)
            if match:
                from urllib.parse import unquote
                href = unquote(match.group(1))
        snippet_el = block.select_one(".result__snippet")
        results.append(SearchResult(
            title=link.get_text(" ", strip=True),
            url=href,
            snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
            source=urlparse(href).netloc,
        ))
        if len(results) >= count:
            break
    return results


def _duckduckgo_package(query: str, count: int) -> List[SearchResult]:
    """Optional: use the ddgs / duckduckgo_search package if installed."""
    DDGS = None
    try:  # the package was renamed; prefer the new name
        from ddgs import DDGS  # type: ignore
    except ImportError:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            return []
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=count))
        return [SearchResult(title=r.get("title", ""), url=r.get("href", ""),
                             snippet=r.get("body", ""),
                             source=urlparse(r.get("href", "")).netloc)
                for r in raw]
    except Exception as exc:
        log.debug("duckduckgo_search failed: %s", exc)
        return []


def _bing(query: str, count: int) -> List[SearchResult]:
    with _client() as client:
        resp = client.get("https://www.bing.com/search", params={"q": query})
        resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    results: List[SearchResult] = []
    for item in soup.select("li.b_algo"):
        link = item.select_one("h2 a")
        if not link:
            continue
        href = link.get("href", "")
        snippet_el = item.select_one(".b_caption p")
        results.append(SearchResult(title=link.get_text(" ", strip=True), url=href,
                                    snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
                                    source=urlparse(href).netloc))
        if len(results) >= count:
            break
    return results


def search(query: str, count: int = 6) -> List[SearchResult]:
    """Try providers in order; raise SearchUnavailable if all fail."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Empty search query")
    errors: List[str] = []
    for name, fn in (("brave", _brave), ("duckduckgo", _duckduckgo_package),
                     ("duckduckgo-html", _duckduckgo), ("bing", _bing)):
        try:
            results = fn(query, count)
            if results:
                log.info("Search via %s returned %d results", name, len(results))
                return results
        except (httpx.HTTPError, OSError) as exc:
            errors.append(f"{name}: {type(exc).__name__}")
        except Exception as exc:  # parsing changes are not fatal
            errors.append(f"{name}: {type(exc).__name__}")
    raise SearchUnavailable(
        "No search provider responded (" + ", ".join(errors) +
        "). Check your internet connection."
    )


# ---------------------------------------------------------------------------
# Page extraction
# ---------------------------------------------------------------------------
def fetch_page(url: str, max_chars: int = 6000) -> PageContent:
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http(s) URLs can be fetched")
    try:
        with _client() as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return PageContent(url=url, error=f"{type(exc).__name__}: {exc}")

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else urlparse(url).netloc
    main = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in main.find_all(["p", "h1", "h2", "h3", "li"])]
    text = "\n".join(p for p in paragraphs if len(p) > 40)
    if len(text) < 200:  # fall back to raw text for JS-light pages
        text = main.get_text(" ", strip=True)
    text = re.sub(r"[ \t]+", " ", text)[:max_chars]
    links = [{"text": a.get_text(" ", strip=True)[:80], "href": urljoin(url, a.get("href", ""))}
             for a in main.find_all("a", href=True)][:30]
    return PageContent(url=url, title=title, text=text, links=links)


def research(query: str, depth: int = 3) -> Dict[str, Any]:
    """Search then extract the top pages so the model can synthesise an answer."""
    try:
        results = search(query, count=max(3, min(depth + 2, 8)))
    except SearchUnavailable as exc:
        return {"ok": False, "message": str(exc), "data": {"results": []}}

    sources: List[Dict[str, Any]] = []
    for result in results[:depth]:
        page = fetch_page(result.url)
        sources.append({
            "title": result.title or page.title,
            "url": result.url,
            "domain": urlparse(result.url).netloc,
            "snippet": result.snippet,
            "text": page.text[:2500] if page.text else "",
            "error": page.error,
        })
    usable = [s for s in sources if s["text"]]
    domains = ", ".join(sorted({s["domain"] for s in usable})) or "no sources"
    return {
        "ok": bool(usable),
        "verified": bool(usable),
        "message": (f"Researched '{query}' across {len(usable)} source(s): {domains}."
                    if usable else "Search returned results but the pages could not be read."),
        "data": {"query": query, "sources": sources},
    }


def compare(query: str) -> Dict[str, Any]:
    """Gather multi-source material for a comparison request."""
    return research(query, depth=4)
