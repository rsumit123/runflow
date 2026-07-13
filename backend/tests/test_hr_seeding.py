"""Unit tests for HR extraction from Strava-export files (bulk_import)."""
import bulk_import as bi


def test_clean_hr_filters_dropouts_and_noise():
    # 0 and 300 are dropout/noise; the rest valid. Need >= HR_MIN_SAMPLES.
    vals = [0.0, 300.0] + [150.0] * 12
    out = bi.clean_hr_values(vals)
    assert out["valid_count"] == 12
    assert out["avg"] == 150.0
    assert out["max"] == 150.0
    assert 0.0 not in out["clean"] and 300.0 not in out["clean"]


def test_clean_hr_too_few_samples_returns_none():
    out = bi.clean_hr_values([150.0, 151.0])  # below HR_MIN_SAMPLES
    assert out["avg"] is None and out["max"] is None


def test_clean_hr_all_dropouts_returns_none():
    out = bi.clean_hr_values([0.0] * 50)  # loose-GW4 style: all zeros
    assert out["avg"] is None
    assert out["valid_count"] == 0


def test_extract_hr_from_gpx(tmp_path):
    gpx = """<?xml version="1.0"?>
<gpx xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk><trkseg>
    <trkpt lat="1" lon="2"><extensions><gpxtpx:TrackPointExtension>
      <gpxtpx:hr>120</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
    <trkpt lat="1" lon="2"><extensions><gpxtpx:TrackPointExtension>
      <gpxtpx:hr>140</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
  </trkseg></trk>
</gpx>"""
    p = tmp_path / "a.gpx"
    p.write_text(gpx)
    assert bi._extract_hr_from_gpx(str(p)) == [120.0, 140.0]


def test_extract_hr_from_tcx_leading_whitespace_and_lap_exclusion(tmp_path):
    # 10 leading spaces before <?xml (the real Strava-export quirk) +
    # lap-level Average/Maximum summaries that must NOT be counted.
    tcx = ("          <?xml version='1.0' encoding='UTF-8'?>"
           "<TrainingCenterDatabase xmlns='http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'>"
           "<Activities><Activity><Lap>"
           "<AverageHeartRateBpm><Value>185</Value></AverageHeartRateBpm>"
           "<MaximumHeartRateBpm><Value>203</Value></MaximumHeartRateBpm>"
           "<Track>"
           "<Trackpoint><HeartRateBpm><Value>99</Value></HeartRateBpm></Trackpoint>"
           "<Trackpoint><Position><LatitudeDegrees>1</LatitudeDegrees></Position></Trackpoint>"
           "<Trackpoint><HeartRateBpm><Value>101</Value></HeartRateBpm></Trackpoint>"
           "</Track></Lap></Activity></Activities></TrainingCenterDatabase>")
    p = tmp_path / "a.tcx"
    p.write_text(tcx)
    # only the two trackpoint HRs, not the lap 185/203 summaries
    assert bi._extract_hr_from_tcx(str(p)) == [99.0, 101.0]
