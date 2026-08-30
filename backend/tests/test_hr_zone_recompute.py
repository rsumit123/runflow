"""Time-in-zone must be comparable across the whole archive.

Garmin restates its own zone boundaries whenever it revises its max-HR
estimate, so the stored payload says "Z5" about 171 bpm on one run and about
186 bpm on another. Any zone trend drawn across that boundary is meaningless.
These tests pin the recomputation that puts every run on one set of
boundaries derived from the athlete's observed max.
"""
import fitness_model as fm


def test_zone_boundaries_are_pct_of_max_hr():
    assert fm.zone_boundaries(207) == [104, 124, 145, 166, 186]


def test_zone_boundaries_track_a_different_max():
    # The same 50/60/70/80/90 rule against the older observed max of 190.
    assert fm.zone_boundaries(190) == [95, 114, 133, 152, 171]


def test_time_in_zones_counts_seconds_per_zone():
    # 1 s per sample: 3 s in Z2 (124-144), 2 s in Z4 (166-185), 1 s in Z5.
    hr = [130.0, 135.0, 140.0, 170.0, 180.0, 190.0]
    time = [0, 1, 2, 3, 4, 5]
    zones = fm.time_in_zones(hr, time, fm.zone_boundaries(207))

    assert [z["zone"] for z in zones] == [1, 2, 3, 4, 5]
    assert [z["low_bpm"] for z in zones] == [104, 124, 145, 166, 186]
    secs = {z["zone"]: z["secs"] for z in zones}
    assert secs == {1: 0.0, 2: 3.0, 3: 0.0, 4: 2.0, 5: 1.0}


def test_time_in_zones_honours_irregular_sample_gaps():
    # A 10 s gap between samples is 10 s spent at that HR, not one sample.
    hr = [130.0, 190.0]
    time = [0, 10]
    secs = {z["zone"]: z["secs"] for z in fm.time_in_zones(hr, time, fm.zone_boundaries(207))}

    assert secs[2] == 10.0
    assert secs[5] == 1.0


def test_time_in_zones_ignores_samples_below_zone_one():
    hr = [60.0, 130.0]
    time = [0, 1]
    secs = {z["zone"]: z["secs"] for z in fm.time_in_zones(hr, time, fm.zone_boundaries(207))}

    assert sum(secs.values()) == 1.0


def test_time_in_zones_returns_none_without_usable_streams():
    assert fm.time_in_zones(None, None, fm.zone_boundaries(207)) is None
    assert fm.time_in_zones([], [], fm.zone_boundaries(207)) is None
