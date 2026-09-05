from adaptsg.settings import Settings


def test_clean_settings_default_to_live_provider_mode() -> None:
    assert Settings(_env_file=None).adaptsg_mode == "live"