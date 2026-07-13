"""
Conversational run analyst — a bounded OpenRouter tool-calling loop.

The model answers questions about a run and the athlete's history, but every
number it states must come from a read-only tool call (grounding). We own the
tools and the data; the model only reasons and phrases. Fail-safe: any transport
error returns a friendly message rather than raising.

Provider is OpenRouter's OpenAI-compatible chat-completions API (same as
coach_llm) — tools use the OpenAI function-calling shape.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional

import httpx

import config

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

SYSTEM_PROMPT = (
    "You are a sharp, encouraging running analyst inside the RunFlow app. You are "
    "discussing one run (activity_id={activity_id}); today is {today}.\n"
    "RULES:\n"
    "- Ground EVERY numeric claim in data you fetched via a tool. Never invent paces, "
    "heart rates, distances, or comparisons. If a tool doesn't give you the number, say "
    "you don't have it.\n"
    "- Call get_run with no arguments first when the question is about 'this run'. Use "
    "find_runs / get_best_efforts / get_trend to compare against history.\n"
    "- Be concise and concrete: cite the actual numbers and what they mean. Prefer 2-5 "
    "sentences unless asked for detail.\n"
    "- You are not a doctor; do not give medical advice. Encourage, don't diagnose.\n"
    "- Paces are seconds per km; format them as M:SS/km in your answers."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "get_run",
        "description": "Full detail for one run: distance, pace, HR, cadence, splits, HR zones, "
                       "best efforts, and interval breakdown. Omit activity_id for the run in context.",
        "parameters": {"type": "object", "properties": {
            "activity_id": {"type": "integer", "description": "Run id; omit for the current run."}}},
    }},
    {"type": "function", "function": {
        "name": "find_runs",
        "description": "List the athlete's runs matching filters, newest first, with key stats "
                       "(date, distance, pace, avg HR). Use to compare this run against others.",
        "parameters": {"type": "object", "properties": {
            "since_days": {"type": "integer", "description": "Only runs within this many days (default 120)."},
            "min_km": {"type": "number"}, "max_km": {"type": "number"},
            "name_contains": {"type": "string"},
            "limit": {"type": "integer", "description": "Max runs (default 15, cap 25)."}}},
    }},
    {"type": "function", "function": {
        "name": "get_best_efforts",
        "description": "The athlete's fastest times (PRs) for standard distances (100,200,400,500,1000,2000 m), "
                       "with dates. Optionally filter to one distance in meters.",
        "parameters": {"type": "object", "properties": {
            "distance_m": {"type": "integer"}}},
    }},
    {"type": "function", "function": {
        "name": "get_trend",
        "description": "A weekly trend over recent weeks for a metric.",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": ["weekly_distance", "avg_pace", "avg_hr"]},
            "weeks": {"type": "integer", "description": "How many weeks back (default 12)."}},
            "required": ["metric"]},
    }},
    {"type": "function", "function": {
        "name": "get_active_plan",
        "description": "The athlete's active training plan (goal, target, next workout, adherence), or null.",
        "parameters": {"type": "object", "properties": {}},
    }},
]


async def _post(messages: list[dict[str, Any]], use_tools: bool) -> Optional[dict[str, Any]]:
    if not config.OPENROUTER_API_KEY:
        return None
    body: dict[str, Any] = {
        "model": config.OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.3,
    }
    if use_tools:
        body["tools"] = TOOLS
        body["tool_choice"] = "auto"
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{config.OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://runflow.skdev.one",
                    "X-Title": "RunFlow",
                },
                json=body,
            )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — never let chat crash the request
        logger.warning("run_chat OpenRouter call failed: %s", exc)
        return None


async def chat(activity_id: int, messages: list[dict[str, str]],
               execute_tool: ToolExecutor, today: str) -> dict[str, Any]:
    """Run the tool-calling loop. `messages` is the prior user/assistant turns
    (last must be the user's question). Returns {reply, ok}."""
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(activity_id=activity_id, today=today)}
    ] + list(messages)

    for _ in range(MAX_TOOL_ROUNDS):
        data = await _post(convo, use_tools=True)
        if data is None:
            return {"reply": "I couldn't reach the analysis engine just now — try again in a moment.", "ok": False}
        msg = data["choices"][0]["message"]
        convo.append(msg)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return {"reply": msg.get("content") or "", "ok": True}
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            try:
                result = await execute_tool(name, args)
            except Exception as exc:  # noqa: BLE001
                logger.warning("tool %s failed: %s", name, exc)
                result = {"error": f"tool '{name}' failed"}
            convo.append({
                "role": "tool", "tool_call_id": tc.get("id"),
                "content": json.dumps(result, default=str)[:6000],
            })

    # Ran out of tool rounds — force a final, tool-free answer.
    data = await _post(convo, use_tools=False)
    if data:
        return {"reply": data["choices"][0]["message"].get("content") or "", "ok": True}
    return {"reply": "I gathered a lot of data but couldn't quite wrap up — try asking a bit more specifically.", "ok": False}
