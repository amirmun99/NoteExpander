from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


def tavily_search(
    query: str,
    api_key: str,
    max_results: int = 5,
) -> tuple[str, list[dict]]:
    """Execute a single Tavily search. Returns (formatted_text_for_llm, raw_result_dicts)."""
    if not api_key:
        return f"[Search disabled — no TAVILY_API_KEY for query: {query}]", []

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(query=query, max_results=max_results, include_answer=False)
        results = response.get("results", [])

        raw = [
            {
                "query": query,
                "title": r.get("title"),
                "url": r.get("url"),
                "snippet": r.get("content"),
                "score": r.get("score"),
            }
            for r in results
        ]

        if not results:
            return f"No results found for: {query}", []

        lines = [f"Search: {query}"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. {r.get('title', 'No title')} — {r.get('url', '')}\n"
                f"   {r.get('content', '')[:350]}"
            )
        return "\n".join(lines), raw

    except Exception as e:
        logger.error("Tavily search error for %r: %s", query, e)
        return f"Search failed for '{query}': {e}", []


def tavily_search_parallel(
    queries: list[str],
    api_key: str,
    max_results: int = 5,
    max_workers: int = 4,
) -> tuple[str, list[dict]]:
    """
    Execute multiple Tavily searches concurrently (A5).
    Returns (combined_text_for_llm, all_raw_results).
    """
    if not queries:
        return "", []

    search_blocks: list[tuple[int, str]] = []  # (original_index, text)
    all_raw: list[dict] = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(queries))) as pool:
        futures = {
            pool.submit(tavily_search, q, api_key, max_results): i
            for i, q in enumerate(queries)
        }
        results_by_index: dict[int, tuple[str, list[dict]]] = {}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                block, raw = future.result()
                results_by_index[idx] = (block, raw)
            except Exception as e:
                logger.error("Parallel search failed for query index %d: %s", idx, e)
                results_by_index[idx] = (f"Search failed: {e}", [])

    # Reassemble in original query order
    for i in range(len(queries)):
        block, raw = results_by_index.get(i, ("", []))
        search_blocks.append(block)
        all_raw.extend(raw)

    return "\n\n".join(search_blocks), all_raw
