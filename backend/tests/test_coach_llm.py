import pytest
import coach_llm


@pytest.mark.asyncio
async def test_narrative_parses_fenced_json(monkeypatch):
    async def fake_chat(messages, max_tokens=700):
        return '```json\n{"overview":"Great comeback plan.","weekly":[{"week":1,"focus":"ease in"}]}\n```'
    monkeypatch.setattr(coach_llm, "_chat", fake_chat)
    out = await coach_llm.generate_plan_narrative({"target_str": "27:22", "weeks": 8}, [{"week": 1}])
    assert out["overview"] == "Great comeback plan."
    assert out["weekly"][0]["week"] == 1


@pytest.mark.asyncio
async def test_narrative_failsafe_on_none(monkeypatch):
    async def fake_chat(messages, max_tokens=700):
        return None  # simulates missing key / network error
    monkeypatch.setattr(coach_llm, "_chat", fake_chat)
    assert await coach_llm.generate_plan_narrative({"target_str": "27:22", "weeks": 8}, []) is None


@pytest.mark.asyncio
async def test_narrative_failsafe_on_garbage(monkeypatch):
    async def fake_chat(messages, max_tokens=700):
        return "sorry, I can't do that"  # no JSON object
    monkeypatch.setattr(coach_llm, "_chat", fake_chat)
    assert await coach_llm.generate_plan_narrative({"target_str": "27:22", "weeks": 8}, []) is None


def test_extract_json_tolerates_prose():
    assert coach_llm._extract_json('here you go: {"overview":"x"} thanks')["overview"] == "x"
    assert coach_llm._extract_json("no json here") is None
