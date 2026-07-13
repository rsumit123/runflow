import tempfile
from datetime import datetime, timedelta
import pytest

import run_chat as rchat


@pytest.mark.asyncio
async def test_chat_loop_executes_tools_then_answers(monkeypatch):
    # Fake OpenRouter: round 1 -> a tool call; round 2 -> final text.
    calls = {"n": 0}

    async def fake_post(messages, use_tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "get_run", "arguments": "{}"}}]}}]}
        return {"choices": [{"message": {"role": "assistant",
                "content": "Your run was 5.0 km at 5:00/km — solid."}}]}

    monkeypatch.setattr(rchat, "_post", fake_post)

    executed = []

    async def execute_tool(name, args):
        executed.append((name, args))
        return {"distance_km": 5.0, "pace_sec_per_km": 300}

    out = await rchat.chat(42, [{"role": "user", "content": "how was this run?"}], execute_tool, "2026-07-14")
    assert out["ok"] is True
    assert "5.0 km" in out["reply"]
    assert executed == [("get_run", {})]


@pytest.mark.asyncio
async def test_chat_handles_dead_engine(monkeypatch):
    async def dead_post(messages, use_tools):
        return None
    monkeypatch.setattr(rchat, "_post", dead_post)

    async def execute_tool(name, args):
        return {}
    out = await rchat.chat(1, [{"role": "user", "content": "hi"}], execute_tool, "2026-07-14")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_chat_caps_tool_rounds(monkeypatch):
    # Always return a tool call -> must stop after MAX_TOOL_ROUNDS and give a final answer.
    async def always_tool(messages, use_tools):
        if not use_tools:  # forced final answer
            return {"choices": [{"message": {"role": "assistant", "content": "Best I can do."}}]}
        return {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "x", "type": "function",
                                "function": {"name": "find_runs", "arguments": "{}"}}]}}]}
    monkeypatch.setattr(rchat, "_post", always_tool)

    count = {"n": 0}

    async def execute_tool(name, args):
        count["n"] += 1
        return {"runs": []}

    out = await rchat.chat(1, [{"role": "user", "content": "compare"}], execute_tool, "2026-07-14")
    assert count["n"] == rchat.MAX_TOOL_ROUNDS
    assert out["reply"] == "Best I can do."


@pytest.mark.asyncio
async def test_tools_over_real_db(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Split, BestEffort
    import main
    importlib.reload(main)

    now = datetime(2026, 7, 14, 12, 0, 0)
    async with database.async_session() as s:
        a = Activity(id=700, name="Morning Run", distance=5000.0, moving_time=1500,
                     start_date=now - timedelta(days=3), average_heartrate=150.0, max_heartrate=170.0,
                     sport_type="Run")
        s.add(a)
        s.add(Split(activity_id=700, split_number=1, distance=1000.0, moving_time=300, average_heartrate=148.0))
        s.add(BestEffort(activity_id=700, distance_target=1000, time_seconds=300.0))
        # an older run for comparison
        s.add(Activity(id=701, name="Old Run", distance=3000.0, moving_time=1080,
                       start_date=now - timedelta(days=20), average_heartrate=145.0, sport_type="Run"))
        await s.commit()

        run = await main._run_chat_tool(s, 700, "get_run", {})
        assert run["distance_km"] == 5.0 and run["pace_sec_per_km"] == 300
        assert run["splits"][0]["pace_sec_per_km"] == 300

        found = await main._run_chat_tool(s, 700, "find_runs", {"since_days": 60})
        assert found["count"] == 2

        prs = await main._run_chat_tool(s, 700, "get_best_efforts", {})
        assert any(b["distance_m"] == 1000 and b["best_time_sec"] == 300.0 for b in prs["best_efforts"])

        trend = await main._run_chat_tool(s, 700, "get_trend", {"metric": "weekly_distance", "weeks": 8})
        assert trend["metric"] == "weekly_distance" and len(trend["series"]) >= 1

        assert (await main._run_chat_tool(s, 700, "bogus", {})).get("error")
