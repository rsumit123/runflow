from datetime import datetime, timedelta
import fitness_projection as fp

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _run(days_ago, dist_m, pace_sec, rid=1):
    return {"id": rid, "distance": dist_m,
            "start_date": NOW - timedelta(days=days_ago),
            "average_speed": 1000.0 / pace_sec}


def test_estimate_current_5k_uses_fastest_recent():
    acts = [_run(5, 3000, 360, 1), _run(10, 3000, 400, 2), _run(3, 2000, 350, 3)]
    est = fp.estimate_current_5k(acts, NOW)
    # fastest is the 350s/km run -> Riegel up to 5k is slower than 350*5
    assert est["current_5k_sec"] > 350 * 5
    assert est["confidence"] in ("ok", "low")


def test_projections_targets_get_faster_with_more_weeks():
    acts = [_run(5, 3000, 360, 1)]
    p = fp.projections(acts, NOW)
    times = [h["target_time_sec"] for h in p["horizons"]]
    assert p["current_5k_sec"] is not None
    assert times == sorted(times, reverse=True)  # more weeks -> faster (smaller) time
    assert all(t < p["current_5k_sec"] for t in times)


def test_projections_no_data():
    assert fp.projections([], NOW)["current_5k_sec"] is None


def test_reality_check_flags_too_fast():
    acts = [_run(5, 3000, 360, 1)]
    cur = fp.estimate_current_5k(acts, NOW)["current_5k_sec"]
    # a target 30% faster than current is not trainable in 8 weeks
    r = fp.reality_check(acts, NOW, int(cur * 0.7), 8)
    assert r["realistic"] is False
    # a target slightly slower than current is fine
    assert fp.reality_check(acts, NOW, cur + 60, 8)["realistic"] is True
