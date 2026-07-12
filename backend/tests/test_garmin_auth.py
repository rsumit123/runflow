import pytest
import garmin_auth

def test_missing_token_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(garmin_auth.config, "GARMIN_TOKENSTORE", str(tmp_path / "nope"))
    garmin_auth._client = None
    with pytest.raises(RuntimeError) as e:
        garmin_auth.get_garmin()
    assert "token" in str(e.value).lower()
