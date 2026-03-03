from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.config import Settings
from app.pipeline import progress as prog

from .definitions import get_prompts
from .tools import tavily_search_parallel

logger = logging.getLogger(__name__)


@dataclass
class CrewResult:
    raw_output: str
    classification: dict
    search_results: list[dict]
    agent_logs: list[dict]


def _stage_params(settings: Settings, stage: str) -> tuple[float, int]:
    """Return (temperature, max_tokens) for a given stage, with per-stage overrides."""
    overrides = settings.llm.stage_overrides.get(stage, {})
    temperature = overrides.get("temperature", settings.llm.temperature)
    max_tokens = overrides.get("max_tokens", settings.llm.max_tokens)
    return float(temperature), int(max_tokens)


def _llm_call(
    model: str,
    system: str,
    user: str,
    settings: Settings,
    stage: str = "",
    api_key: Optional[str] = None,
) -> str:
    """
    Single blocking LLM call via litellm with timeout and retry (A1, A3).
    - Respects per-stage temperature/max_tokens (A3).
    - Retries up to settings.llm.max_retries times on timeout or empty response (A1, A4).
    - Passes api_key directly to litellm instead of via env var (B2).
    """
    import litellm

    temperature, max_tokens = _stage_params(settings, stage)
    timeout = settings.llm.timeout_seconds
    max_retries = max(1, settings.llm.max_retries)

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            kwargs: dict = dict(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if api_key:
                kwargs["api_key"] = api_key
            if settings.llm.provider == "ollama":
                kwargs["api_base"] = settings.llm.ollama_base_url

            response = litellm.completion(**kwargs)
            text = response.choices[0].message.content or ""
            text = text.strip()

            if text:
                return text

            # Empty response — retry (A4)
            logger.warning("Empty LLM response for stage %r (attempt %d/%d)", stage, attempt + 1, max_retries)

        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    "LLM call failed for stage %r (attempt %d/%d, retrying in %ds): %s",
                    stage, attempt + 1, max_retries, wait, e,
                )
                time.sleep(wait)
            else:
                logger.error("LLM call failed for stage %r after %d attempts: %s", stage, max_retries, e)
                raise

    # All retries returned empty — raise a clear error
    raise RuntimeError(
        f"LLM returned empty response for stage {stage!r} after {max_retries} attempts"
        + (f": {last_exc}" if last_exc else "")
    )


def _parse_classification(text: str) -> dict:
    try:
        match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            # Validate required keys are present and non-empty
            if result.get("type") and result.get("title"):
                return result
    except (json.JSONDecodeError, AttributeError):
        pass
    logger.warning("Could not parse classification JSON, using defaults")
    return {"type": "unknown", "confidence": 0.5, "title": "Untitled Note", "key_themes": []}


def _parse_queries(text: str) -> list[str]:
    try:
        match = re.search(r"\[.*?\]", text, re.DOTALL)
        if match:
            queries = json.loads(match.group())
            if isinstance(queries, list):
                return [str(q) for q in queries if q][:8]
    except (json.JSONDecodeError, AttributeError):
        pass
    # Fallback: treat non-empty lines as queries
    lines = [ln.strip().strip('"').strip("'").lstrip("- ") for ln in text.splitlines()]
    return [ln for ln in lines if len(ln) > 5][:6]


def build_and_run_crew(
    raw_text: str,
    settings: Settings,
    note_id: str = "",
    model_override: Optional[str] = None,
    api_key: Optional[str] = None,
    pipeline_flags: Optional[dict] = None,
) -> CrewResult:
    """
    Sequential 5-stage pipeline using litellm directly.
    Stages: classify → query_gen → research → analyse → format

    Called via asyncio.to_thread() so it can block without affecting the event loop.

    Args:
        model_override: use this litellm model string instead of the configured default.
        api_key: explicit API key (B2 fix — avoids global os.environ mutation).
        pipeline_flags: dict of behaviour flags from tags (1b):
            skip_search (bool): skip web search even if Tavily configured
            force_type (str|None): override classifier project_type
            no_format (bool): skip formatter, return analyst output directly
    """
    flags = pipeline_flags or {}
    model = model_override or settings.llm_model_string
    prompts = get_prompts()
    agent_logs: list[dict] = []
    all_search_results: list[dict] = []
    today = date.today().isoformat()

    logger.info("Pipeline starting (model=%s)", model)
    t_total = time.monotonic()

    # ── Stage 1: Classify ────────────────────────────────────────────────
    t0 = time.monotonic()
    prog.start(note_id, "classify")
    classify_output = _llm_call(model, prompts["classify"], raw_text, settings, stage="classify", api_key=api_key)
    classification = _parse_classification(classify_output)
    project_type = classification.get("type", "unknown")
    title = classification.get("title", "Untitled Note")
    confidence = classification.get("confidence", 0)
    # Apply force_type flag (#market / #tech) — override classifier result (1b)
    if flags.get("force_type"):
        project_type = flags["force_type"]
        classification["type"] = project_type
    logger.info("Classified: type=%r confidence=%.2f", project_type, confidence)
    prog.complete(
        note_id, "classify",
        f"Type: {project_type} · Confidence: {confidence:.0%} · Title: {title}",
    )
    agent_logs.append({
        "agent_name": "Classifier",
        "task_name": "classifier",
        "input_text": raw_text[:500],
        "output_text": classify_output[:2000],
        "duration_seconds": time.monotonic() - t0,
    })

    # ── Stage 2: Generate search queries + execute (parallel, A5) ────────
    search_text = ""
    # #quick flag skips web search even when Tavily is configured (1b)
    if settings.search.enabled and settings.tavily_api_key and not flags.get("skip_search"):
        t0 = time.monotonic()
        prog.start(note_id, "query_gen")
        query_prompt = (
            f"Project idea: {raw_text}\n"
            f"Type: {project_type}\n"
            f"Title: {title}\n"
            f"Key themes: {', '.join(classification.get('key_themes', []))}"
        )
        query_output = _llm_call(model, prompts["query_gen"], query_prompt, settings, stage="query_gen", api_key=api_key)
        queries = _parse_queries(query_output)
        logger.info("Generated %d search queries", len(queries))

        # Run all searches in parallel (A5)
        search_text, all_search_results = tavily_search_parallel(
            queries, settings.tavily_api_key, settings.search.max_results
        )
        logger.info("Collected %d search results total", len(all_search_results))

        prog.complete(
            note_id, "query_gen",
            f"{len(queries)} queries · {len(all_search_results)} results collected",
        )
        agent_logs.append({
            "agent_name": "Query Generator",
            "task_name": "query_generator",
            "input_text": query_prompt[:500],
            "output_text": f"{len(queries)} queries generated; {len(all_search_results)} results fetched",
            "duration_seconds": time.monotonic() - t0,
        })
    else:
        reason = "#quick flag" if flags.get("skip_search") else "Search disabled or no API key"
        logger.info(
            "Search skipped (enabled=%s, has_key=%s, skip_search=%s)",
            settings.search.enabled, bool(settings.tavily_api_key), flags.get("skip_search"),
        )
        prog.skip(note_id, "query_gen", reason)

    # ── Stage 3: Research synthesis ───────────────────────────────────────
    t0 = time.monotonic()
    prog.start(note_id, "research")
    research_prompt = (
        f"Project idea: {raw_text}\n\n"
        f"Classification: {json.dumps(classification)}\n\n"
        f"Search results:\n{search_text or '[No search results — summarise from general knowledge]'}"
    )
    research_output = _llm_call(model, prompts["research"], research_prompt, settings, stage="research", api_key=api_key)
    prog.complete(note_id, "research", research_output[:300])
    agent_logs.append({
        "agent_name": "Researcher",
        "task_name": "researcher",
        "input_text": research_prompt[:500],
        "output_text": research_output[:2000],
        "duration_seconds": time.monotonic() - t0,
    })

    # ── Stage 4: Analysis ─────────────────────────────────────────────────
    t0 = time.monotonic()
    prog.start(note_id, "analyse")
    analysis_prompt = (
        f"Project idea: {raw_text}\n\n"
        f"Classification: {json.dumps(classification)}\n\n"
        f"Research findings:\n{research_output}"
    )
    analysis_output = _llm_call(model, prompts["analyse"], analysis_prompt, settings, stage="analyse", api_key=api_key)
    prog.complete(note_id, "analyse", analysis_output[:300])
    agent_logs.append({
        "agent_name": "Analyst",
        "task_name": "analyst",
        "input_text": analysis_prompt[:500],
        "output_text": analysis_output[:2000],
        "duration_seconds": time.monotonic() - t0,
    })

    # ── Stage 5: Format (skipped when #noformat flag set) ────────────────
    if flags.get("no_format"):
        # Return analyst output directly without polished markdown template (1b)
        prog.skip(note_id, "format", "#noformat — returning raw analyst output")
        final_output = analysis_output
        logger.info("Format stage skipped (#noformat flag)")
    else:
        t0 = time.monotonic()
        prog.start(note_id, "format")
        sources_section = "\n".join(
            f"- [{r['title']}]({r['url']})"
            for r in all_search_results
            if r.get("url") and r.get("title")
        )
        format_prompt = (
            f"Project idea: {raw_text}\n"
            f"Today's date: {today}\n\n"
            f"Classification: {json.dumps(classification)}\n\n"
            f"Research findings:\n{research_output}\n\n"
            f"Analysis:\n{analysis_output}\n\n"
            f"Sources to include:\n{sources_section or '[none]'}"
        )
        final_output = _llm_call(model, prompts["format"], format_prompt, settings, stage="format", api_key=api_key)
        prog.complete(note_id, "format", "Report formatted successfully")
        agent_logs.append({
            "agent_name": "Formatter",
            "task_name": "formatter",
            "input_text": format_prompt[:500],
            "output_text": final_output[:2000],
            "duration_seconds": time.monotonic() - t0,
        })

    logger.info("Pipeline complete in %.1fs", time.monotonic() - t_total)

    return CrewResult(
        raw_output=final_output,
        classification=classification,
        search_results=all_search_results,
        agent_logs=agent_logs,
    )


def compare_notes(
    notes_data: list[dict],
    settings: Settings,
    model_override: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    """
    Compare multiple notes and return a short analysis (2b).

    notes_data: list of dicts with keys: title, type, confidence, report_markdown
    Returns a markdown comparison string.
    """
    model = model_override or settings.llm_model_string

    summaries = []
    for i, n in enumerate(notes_data, 1):
        # Truncate each report to keep context manageable
        report_excerpt = (n.get("report_markdown") or "")[:1500]
        summaries.append(
            f"## Note {i}: {n.get('title', 'Untitled')}\n"
            f"Type: {n.get('type', 'unknown')} | Confidence: {n.get('confidence', 0):.0%}\n\n"
            f"{report_excerpt}"
        )

    user_prompt = "\n\n---\n\n".join(summaries)

    system_prompt = (
        "You are a project analyst comparing multiple research notes. "
        "Given the summaries below, write a concise comparison (max 400 words) covering:\n"
        "1. Which project is most technically feasible and why\n"
        "2. Which has the highest potential value/impact\n"
        "3. Key trade-offs between the projects\n"
        "4. A recommended priority order with one-line justification for each.\n"
        "Be direct and specific. Use markdown."
    )

    return _llm_call(model, system_prompt, user_prompt, settings, stage="format", api_key=api_key)


def followup_note(
    original_report: str,
    original_title: str,
    followup_question: str,
    settings: Settings,
    model_override: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    """
    Run a focused follow-up research session on an existing report (2c).

    Returns a markdown answer to the follow-up question, grounded in the
    original report context.
    """
    model = model_override or settings.llm_model_string

    system_prompt = (
        "You are a research assistant helping to dig deeper into a specific aspect of "
        "an existing research report. The user has a follow-up question about their project. "
        "Use the provided report as context, and answer the follow-up question thoroughly. "
        "If you need information not in the report, draw on general knowledge. "
        "Format your answer in clear markdown with headings and bullet points."
    )

    user_prompt = (
        f"## Original report: {original_title}\n\n"
        f"{original_report[:3000]}\n\n"
        f"---\n\n"
        f"## Follow-up question\n\n{followup_question}"
    )

    return _llm_call(model, system_prompt, user_prompt, settings, stage="research", api_key=api_key)
