"""Pure-function tests for the MCP presentation layer."""
import mcp_views as mv


def test_find_pauses_detects_a_gap_with_no_distance_gained():
    time = [0, 1, 2, 120, 121]
    dist = [0.0, 3.0, 6.0, 6.0, 9.0]
    hr = [150.0, 160.0, 190.0, 148.0, 150.0]

    pauses = mv.find_pauses(time, dist, hr)

    assert len(pauses) == 1
    assert pauses[0]["at_km"] == 0.006
    assert pauses[0]["seconds"] == 118
    assert pauses[0]["hr_in"] == 190.0
    assert pauses[0]["hr_out"] == 148.0


def test_find_pauses_ignores_a_gap_where_distance_advanced():
    # A sparse sample is not a pause if the runner covered ground across it.
    time = [0, 1, 2, 120]
    dist = [0.0, 3.0, 6.0, 400.0]

    assert mv.find_pauses(time, dist, None) == []


def test_find_pauses_ignores_short_gaps():
    time = [0, 1, 2, 5, 6]
    dist = [0.0, 3.0, 6.0, 6.0, 9.0]

    assert mv.find_pauses(time, dist, None) == []


def test_moving_time_axis_excludes_paused_seconds():
    time = [0, 1, 2, 120, 121]
    dist = [0.0, 3.0, 6.0, 6.0, 9.0]

    moving = mv.moving_time_axis(time, dist)

    assert moving == [0, 1, 2, 3, 4]


def test_splits_use_moving_time_not_wall_clock():
    # 1 m/s steady, with a 600 s standing pause at the 600 m mark.
    time, dist, hr = [], [], []
    t = 0
    for metre in range(0, 1001):
        if metre == 600:
            t += 600  # the pause
        time.append(t)
        dist.append(float(metre))
        hr.append(180.0)
        t += 1

    splits = mv.split_table(dist, time, hr, metres=500)

    assert len(splits) == 2
    # 500 m at 1 m/s = 1000 s/km, and the pause must not leak into split 2.
    assert splits[0]["pace_sec_per_km"] == 1000
    assert splits[1]["pace_sec_per_km"] == 1000
