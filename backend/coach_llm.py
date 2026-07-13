"""
Coaching narrative via OpenRouter (OpenAI-compatible chat completions).

The LLM only *phrases* the plan — it never sets the safety-critical numbers,
which come entirely from the deterministic plan generator. Fail-safe: any error
returns None and the plan remains fully usable without a narrative.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an encouraging, plain-spoken running coach. You explain a training "
    "plan that was already built by a deterministic engine. NEVER change, invent, "
    "or contradict any numbers — paces, distances, heart rates, dates, or week "
    "counts. Only describe the plan in a warm, motivating, human voice."
)


async def _chat(messages: list[dict[str, str]], max_tokens: int = 700) -> Optional[str]:
    """POST to OpenRouter chat completions; return the message text or None."""
    if not config.OPENROUTER_API_KEY:
        logger.info("OPENROUTER_API_KEY not set — skipping coaching narrative.")
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://runflow.skdev.one",
                    "X-Title": "RunFlow",
                },
                json={
                    "model": config.OPENROUTER_MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
            )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 — never let the narrative break plan creation
        logger.warning("OpenRouter chat failed: %s", exc)
        return None


def _extract_json(text: Optional[str]) -> Optional[dict[str, Any]]:
    """Parse a JSON object from model output, tolerating ``` fences / prose."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


async def generate_plan_narrative(
    goal: dict[str, Any], weeks_overview: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Return {overview, weekly: [{week, focus}]} or None (fail-safe).

    `goal` = {target_str, weeks}; `weeks_overview` = compact per-week facts the
    model phrases (it does not compute anything).
    """
    user = (
        "Here is a runner's 5K training plan built by our engine. Respond with a "
        "JSON object only, no prose outside it:\n"
        '  "overview": 2-3 warm sentences on the plan and how it should feel.\n'
        '  "weekly": a list of {"week": <int>, "focus": "<one short line>"} — one '
        "per week.\n"
        "Do NOT change any numbers.\n\n"
        f"Goal: 5K in {goal.get('target_str')} across {goal.get('weeks')} weeks.\n"
        f"Weeks: {json.dumps(weeks_overview)}"
    )
    content = await _chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    )
    data = _extract_json(content)
    if not isinstance(data, dict) or "overview" not in data:
        return None
    weekly = data.get("weekly")
    if not isinstance(weekly, list):
        weekly = []
    return {"overview": str(data["overview"]), "weekly": weekly}
