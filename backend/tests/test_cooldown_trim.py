"""A walked cool-down at the end of a structured workout is not part of the run.

The watch records it as COOLDOWN laps; left in, its walking drags the run's
average pace down. A cool-down actually run (the July fixture) stays.
"""
import copy
import json
import pathlib

import garmin_transform as gt

FIX = pathlib.Path(__file__).parent / "fixtures"
summary = json.loads((FIX / "garmin_summary.json").read_text())
splits = json.loads((FIX / "garmin_splits.json").read_text())
details = json.loads((FIX / "garmin_details.json").read_text())


def _walked_cooldown():
    sp = copy.deepcopy(splits)
    for lap in sp["lapDTOs"]:
        if lap["intensityType"] == "COOLDOWN":
            lap["averageSpeed"] = 1.0
    return sp


def _trim(sp):
    fields = gt.summary_to_activity_fields(summary)
    streams = gt.details_to_streams(details)
    return gt.trim_walked_cooldown(fields, sp, streams)


def test_a_run_cooldown_is_kept():
    fields, sp, streams, trimmed = _trim(copy.deepcopy(splits))
    assert trimmed is False
    assert fields["distance"] == summary["distance"]
    assert len(sp["lapDTOs"]) == len(splits["lapDTOs"])
    assert len(streams["time"]) == len(gt.details_to_streams(details)["time"])


def test_a_walked_cooldown_is_cut_from_laps_totals_and_streams():
    fields, sp, streams, trimmed = _trim(_walked_cooldown())
    kept = [l for l in splits["lapDTOs"] if l["intensityType"] != "COOLDOWN"]
    assert trimmed is True
    assert [l["intensityType"] for l in sp["lapDTOs"]] == [l["intensityType"] for l in kept]
    assert fields["distance"] == sum(l["distance"] for l in kept)
    assert fields["moving_time"] == round(sum(l["movingDuration"] for l in kept))
    assert fields["average_speed"] == fields["distance"] / fields["moving_time"]
    # The cool-down began 1021 s after the start (01:29:29 -> 01:46:30).
    assert fields["elapsed_time"] == 1021
    assert max(streams["time"]) < 1021
    assert len({len(v) for v in streams.values()}) == 1, "streams stay aligned"
    assert fields["end_latlng"] == streams["latlng"][-1]
    assert fields["max_heartrate"] == max(l["maxHR"] for l in kept)
    secs = sum(l["duration"] for l in kept)
    assert abs(fields["average_heartrate"]
               - sum(l["averageHR"] * l["duration"] for l in kept) / secs) < 1e-6


def test_only_trailing_walked_laps_go():
    sp = _walked_cooldown()
    sp["lapDTOs"][-2]["averageSpeed"] = 2.5   # ran the first cool-down lap, walked the last
    fields, out, _, trimmed = _trim(sp)
    assert trimmed is True
    assert len(out["lapDTOs"]) == len(splits["lapDTOs"]) - 1


def test_a_free_run_with_no_lap_types_is_untouched():
    sp = copy.deepcopy(splits)
    for lap in sp["lapDTOs"]:
        lap["intensityType"] = "INTERVAL"
        lap["averageSpeed"] = 1.0
    assert _trim(sp)[3] is False


def test_an_all_cooldown_activity_is_untouched():
    sp = _walked_cooldown()
    sp["lapDTOs"] = [l for l in sp["lapDTOs"] if l["intensityType"] == "COOLDOWN"]
    assert _trim(sp)[3] is False
