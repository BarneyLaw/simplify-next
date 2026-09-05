import pytest

from adaptsg.settings import Settings


def test_clean_settings_default_to_live_provider_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAPTSG_MODE", raising=False)
    assert Settings(_env_file=None).adaptsg_mode == "live"
