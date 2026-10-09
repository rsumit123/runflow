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


def test_cardiac_drift_is_second_half_mean_minus_first_half_mean():
    hr = [150.0] * 10 + [170.0] * 10

    assert mv.cardiac_drift(hr) == 20.0


def test_cardiac_drift_is_none_without_enough_samples():
    assert mv.cardiac_drift([150.0]) is None
    assert mv.cardiac_drift(None) is None


def test_seconds_to_cross_returns_first_crossing():
    hr = [140.0, 150.0, 170.0, 195.0]
    time = [0, 10, 20, 30]

    assert mv.seconds_to_cross(hr, time, 168) == 20
    assert mv.seconds_to_cross(hr, time, 189) == 30


def test_seconds_to_cross_returns_none_when_never_crossed():
    assert mv.seconds_to_cross([140.0, 150.0], [0, 1], 189) is None


def test_zone_shares_are_percentages_of_recorded_time():
    zones = [
        {"zone": 1, "secs": 0.0, "low_bpm": 105},
        {"zone": 2, "secs": 25.0, "low_bpm": 126},
        {"zone": 3, "secs": 25.0, "low_bpm": 147},
        {"zone": 4, "secs": 25.0, "low_bpm": 168},
        {"zone": 5, "secs": 25.0, "low_bpm": 189},
    ]

    shares = mv.zone_shares(zones)

    assert shares[5] == 25.0
    assert shares[1] == 0.0
    assert sum(shares.values()) == 100.0


def test_zone_shares_handles_missing_zones():
    assert mv.zone_shares(None) == {}
    assert mv.zone_shares([]) == {}


def test_strip_heavy_removes_polylines_and_streams():
    payload = {
        "activities": [
            {"id": 1, "map_summary_polyline": "sm_jCajmmOVE", "distance": 3000.0},
        ],
        "streams": [{"stream_type": "heartrate", "data": [150.0] * 1200}],
    }

    pruned = mv.strip_heavy(payload)

    assert "map_summary_polyline" not in pruned["activities"][0]
    assert pruned["activities"][0]["distance"] == 3000.0
    assert "streams" not in pruned


def test_strip_heavy_leaves_light_payloads_alone():
    payload = {"best_1km_split": {"time": 276}}

    assert mv.strip_heavy(payload) == payload


def test_downsample_keeps_first_and_last_and_reports_true_length():
    data = list(range(1000))

    out = mv.downsample(data, max_points=10)

    assert out["original_samples"] == 1000
    assert len(out["data"]) <= 10
    assert out["data"][0] == 0
    assert out["data"][-1] == 999


def test_downsample_leaves_short_series_untouched():
    out = mv.downsample([1, 2, 3], max_points=10)

    assert out["data"] == [1, 2, 3]
    assert out["original_samples"] == 3


def test_cap_text_truncates_with_an_explicit_marker():
    out = mv.cap_text("x" * 100, limit=50)

    assert len(out) <= 120
    assert "truncated" in out.lower()


def test_cap_text_leaves_short_text_alone():
    assert mv.cap_text("short", limit=50) == "short"
