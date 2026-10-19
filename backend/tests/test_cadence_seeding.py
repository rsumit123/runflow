"""Cadence from a Strava export, for runs imported without it.

Files and Strava store cadence per foot; RunFlow stores a run's average in
steps per minute, the unit Garmin uses, so old and new runs compare directly.
The per-sample stream stays per foot, matching Garmin's cadence stream.
"""
import pytest
from sqlalchemy import select

import bulk_import as bi

TCX = ("  <?xml version='1.0' encoding='UTF-8'?>"
       "<TrainingCenterDatabase xmlns='http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'>"
       "<Activities><Activity><Lap><Cadence>99</Cadence><Track>"
       "<Trackpoint><Cadence>80</Cadence></Trackpoint>"
       "<Trackpoint><Extensions><TPX xmlns='http://www.garmin.com/xmlschemas/ActivityExtension/v2'>"
       "<RunCadence>84</RunCadence></TPX></Extensions></Trackpoint>"
       "<Trackpoint><Position><LatitudeDegrees>1</LatitudeDegrees></Position></Trackpoint>"
       "</Track></Lap></Activity></Activities></TrainingCenterDatabase>")


def test_tcx_cadence_is_per_trackpoint_only(tmp_path):
    p = tmp_path / "a.tcx"
    p.write_text(TCX)
    assert bi._extract_cadence_from_tcx(str(p)) == [80.0, 84.0]   # not the lap's 99


def test_clean_cadence_drops_stops_and_doubles_the_average():
    out = bi.clean_cadence_values([0.0, 0.0] + [80.0] * 6 + [90.0] * 6)
    assert out["clean"] == [80.0] * 6 + [90.0] * 6
    assert out["avg_spm"] == 170.0


def test_clean_cadence_needs_enough_samples():
    assert bi.clean_cadence_values([80.0] * 3)["avg_spm"] is None
    assert bi.clean_cadence_values([0.0] * 50)["avg_spm"] is None


def _export(tmp_path, rows):
    (tmp_path / "activities").mkdir()
    lines = ["Activity ID,Activity Date,Activity Name,Activity Type,Filename,Average Cadence"]
    for aid, fname, content, csv_cad in rows:
        if content is not None:
            (tmp_path / "activities" / fname).write_text(content)
        lines.append(f"{aid},\"Apr 1, 2022, 1:00:00 AM\",Run,Run,activities/{fname},{csv_cad}")
    (tmp_path / "activities.csv").write_text("\n".join(lines) + "\n")
    return str(tmp_path)


def _tcx(n, cad):
    pts = "".join(f"<Trackpoint><Cadence>{cad}</Cadence></Trackpoint>" for _ in range(n))
    return TCX.replace("<Trackpoint><Cadence>80</Cadence></Trackpoint>", pts)


@pytest.mark.asyncio
async def test_enrich_fills_only_missing_cadence(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Stream
    import sys
    sys.modules.pop("bulk_import", None)
    bi = importlib.import_module("bulk_import")   # bound to the fresh models

    (tmp_path / "exp").mkdir()
    exp = _export(tmp_path / "exp", [
        (1, "1.tcx", _tcx(20, 80), ""),     # from the file: 80/foot -> ~160 spm
        (2, "2.tcx", _tcx(20, 85), ""),     # already has cadence: untouched
        (3, "3.gpx", "<gpx/>", "83.0"),     # no samples in file: CSV average, doubled
        (4, "4.gpx", "<gpx/>", ""),         # nothing anywhere
    ])
    async with database.async_session() as s:
        for i in (1, 2, 3, 4):
            s.add(Activity(id=i, name="r", source="strava",
                           average_cadence=150.0 if i == 2 else None))
        await s.commit()
        out = await bi.enrich_cadence_from_export(s, exp)
        a = {i: await s.get(Activity, i) for i in (1, 2, 3, 4)}
        assert a[1].average_cadence == 160.4   # (20×80 + the 84 RunCadence) / 21, doubled
        assert a[2].average_cadence == 150.0
        assert a[3].average_cadence == 166.0
        assert a[4].average_cadence is None
        streams = (await s.execute(select(Stream).where(Stream.stream_type == "cadence"))).scalars().all()
        assert [st.activity_id for st in streams] == [1]
        assert out["from_file"] == 1 and out["from_csv"] == 1 and out["already_had"] == 1
        # Idempotent: a second pass changes nothing.
        again = await bi.enrich_cadence_from_export(s, exp)
        assert again["from_file"] == 0 and again["from_csv"] == 0
