"""Unit tests for sprint_projection (pure projection math + reality check)."""
from __future__ import annotations

from datetime import datetime

from sprint_projection import CAP, PER_WEEK, sprint_projections, sprint_reality_check

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _target(current: float, weeks: int) -> float:
    imp = min(CAP, PER_WEEK * weeks)
    return round(current * (1 - imp), 1)


def test_projections_shape_and_current():
    out = sprint_projections(19.0, NOW)
    assert out["current_100m_sec"] == 19.0
    assert [h["weeks"] for h in out["horizons"]] == [8, 10, 12]


def test_projections_targets_for_19s():
    out = sprint_projections(19.0, NOW)
    by_weeks = {h["weeks"]: h for h in out["horizons"]}
    # Values computed directly from PER_WEEK=0.009, CAP=0.12:
    #  8wk: imp=0.072 -> 19*(1-0.072)=17.632 -> 17.6
    # 10wk: imp=0.090 -> 19*(1-0.090)=17.29  -> 17.3
    # 12wk: imp=0.108 -> 19*(1-0.108)=16.948 -> 16.9
    assert by_weeks[8]["target_100m_sec"] == 17.6
    assert by_weeks[10]["target_100m_sec"] == 17.3
    assert by_weeks[12]["target_100m_sec"] == 16.9


def test_improvement_pct_values():
    out = sprint_projections(19.0, NOW)
    by_weeks = {h["weeks"]: h for h in out["horizons"]}
    assert by_weeks[8]["improvement_pct"] == 7.2
    assert by_weeks[10]["improvement_pct"] == 9.0
    assert by_weeks[12]["improvement_pct"] == 10.8


def test_improvement_pct_monotonic_and_capped():
    # Include a long horizon that hits the CAP.
    out = sprint_projections(19.0, NOW, horizons=(2, 8, 10, 12, 14, 20))
    pcts = [h["improvement_pct"] for h in out["horizons"]]
    # Non-decreasing with weeks.
    assert pcts == sorted(pcts)
    # Capped at CAP*100 = 12.0.
    assert max(pcts) == 12.0
    assert all(p <= 12.0 + 1e-9 for p in pcts)
    # 14 weeks -> 0.009*14=0.126 > CAP=0.12, so capped.
    by_weeks = {h["weeks"]: h for h in out["horizons"]}
    assert by_weeks[14]["improvement_pct"] == 12.0
    assert by_weeks[20]["improvement_pct"] == 12.0


def test_targets_match_formula_for_various_currents():
    for current in (19.0, 15.5, 22.3, 12.8):
        out = sprint_projections(current, NOW)
        for h in out["horizons"]:
            assert h["target_100m_sec"] == _target(current, h["weeks"])


def test_reality_check_realistic_when_target_equals_floor():
    floor = _target(19.0, 8)
    res = sprint_reality_check(19.0, floor, 8)
    assert res["floor_100m_sec"] == floor
    assert res["realistic"] is True
    assert isinstance(res["note"], str) and res["note"]


def test_reality_check_realistic_when_slower_than_floor():
    floor = _target(19.0, 8)
    res = sprint_reality_check(19.0, floor + 0.5, 8)
    assert res["realistic"] is True


def test_reality_check_not_realistic_when_target_below_floor():
    # Sub-15s in 8 weeks from 19s is far below the realistic floor (~17.6).
    res = sprint_reality_check(19.0, 14.9, 8)
    assert res["realistic"] is False
    assert res["floor_100m_sec"] == _target(19.0, 8)
    # Note should reference the floor / suggest a realistic target.
    assert isinstance(res["note"], str) and res["note"]


def test_reality_check_boundary_epsilon():
    floor = _target(19.0, 10)
    # Just barely faster than floor (beyond epsilon) is not realistic.
    res = sprint_reality_check(19.0, floor - 0.01, 10)
    assert res["realistic"] is False
