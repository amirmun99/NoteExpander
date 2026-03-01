from __future__ import annotations

"""
URL fetcher for Discord messages (1a).

When a user includes a URL in their note, we fetch the page and extract the
main text content so the pipeline can analyse it in context.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Matches http(s) URLs — deliberately greedy to grab the full URL
_URL_PATTERN = re.compile(
    r"https?://[^\s\)\]\>\"']+",
    re.IGNORECASE,
)

# Tags whose text we skip when extracting body content
_SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}

_MAX_CONTENT_CHARS = 4000  # cap to avoid bloating the LLM context


def find_urls(text: str) -> list[str]:
    """Return all HTTP(S) URLs found in text."""
    return _URL_PATTERN.findall(text)


async def fetch_url_text(url: str, timeout: float = 15.0) -> Optional[str]:
    """
    Fetch a URL and return its main text content.

    Returns None if the fetch fails or content is not HTML/text.
    Requires httpx and beautifulsoup4 to be installed.
    """
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("httpx and/or beautifulsoup4 not installed — URL fetching disabled")
        return None

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; NoteExpander/1.0; "
                "+https://github.com/local/noteexpander)"
            )
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                logger.info("URL %s has non-HTML content type %r — skipping", url, content_type)
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # Remove noisy tags
            for tag in soup(list(_SKIP_TAGS)):
                tag.decompose()

            # Try to find the main content block
            main = (
                soup.find("main")
                or soup.find("article")
                or soup.find(id="content")
                or soup.find(id="main-content")
                or soup.find(class_="post-content")
                or soup.find(class_="entry-content")
                or soup.body
            )

            text = (main or soup).get_text(separator="\n", strip=True)

            # Collapse excessive blank lines
            lines = [ln for ln in text.splitlines() if ln.strip()]
            cleaned = "\n".join(lines)

            if len(cleaned) > _MAX_CONTENT_CHARS:
                cleaned = cleaned[:_MAX_CONTENT_CHARS] + "\n[…content truncated…]"

            logger.info("Fetched %d chars from %s", len(cleaned), url)
            return cleaned

    except Exception as e:
        logger.warning("Failed to fetch URL %s: %s", url, e)
        return None


async def enrich_text_with_urls(text: str) -> tuple[str, list[str]]:
    """
    Find URLs in text, fetch their content, and prepend it as context.

    Returns (enriched_text, list_of_fetched_urls).
    If no URLs or all fetches fail, returns the original text unchanged.
    """
    urls = find_urls(text)
    if not urls:
        return text, []

    fetched: list[str] = []
    context_parts: list[str] = []

    for url in urls[:3]:  # limit to first 3 URLs to avoid runaway fetching
        content = await fetch_url_text(url)
        if content:
            context_parts.append(
                f"--- Content from {url} ---\n{content}\n--- End of URL content ---"
            )
            fetched.append(url)

    if not context_parts:
        return text, []

    enriched = "\n\n".join(context_parts) + "\n\n" + text
    return enriched, fetched
