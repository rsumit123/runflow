import warmup_cooldown as wc


def _run_with_phases(warm_s, main_s, cool_s, warm_pace=8.0, main_pace=5.0, cool_pace=8.0):
    """Build distance+time streams (1 Hz): slow warm-up, steady main, slow cool-down.
    pace args are min/km -> m/s = 1000/(pace*60)."""
    d, t, dist = [], [], 0.0
    clock = 0
    for secs, pace in ((warm_s, warm_pace), (main_s, main_pace), (cool_s, cool_pace)):
        mps = 1000.0 / (pace * 60)
        for _ in range(secs):
            dist += mps
            clock += 1
            d.append(dist)
            t.append(clock)
    return d, t


def test_detects_warmup_and_cooldown():
    d, t = _run_with_phases(120, 1200, 120)  # 2 min slow / 20 min main / 2 min slow
    r = wc.detect_warmup_cooldown(d, t)
    assert r["has_warmup"] and r["has_cooldown"]
    assert 90 <= r["warmup_sec"] <= 180
    assert 90 <= r["cooldown_sec"] <= 180
    assert r["main_pace_sec"] and 280 <= r["main_pace_sec"] <= 320  # ~5:00/km


def test_even_paced_run_reports_no_phase():
    d, t = _run_with_phases(0, 1500, 0, main_pace=5.0)  # all one pace
    r = wc.detect_warmup_cooldown(d, t)
    assert not r["has_warmup"] and not r["has_cooldown"]
    assert r["warmup_sec"] == 0 and r["cooldown_sec"] == 0


def test_too_short_run_returns_empty():
    d, t = _run_with_phases(30, 60, 30)  # 2 min total
    r = wc.detect_warmup_cooldown(d, t)
    assert not r["has_warmup"] and not r["has_cooldown"]
    assert r["main_pace_sec"] is None


def test_missing_streams():
    assert wc.detect_warmup_cooldown(None, None)["has_warmup"] is False
    assert wc.detect_warmup_cooldown([], [])["has_cooldown"] is False


def test_warmup_only():
    d, t = _run_with_phases(150, 1200, 0)  # warm-up, no cool-down
    r = wc.detect_warmup_cooldown(d, t)
    assert r["has_warmup"] and not r["has_cooldown"]
