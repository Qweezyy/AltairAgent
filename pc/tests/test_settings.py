"""Settings (core/settings.py): what they must never give away."""


def test_the_secrets_never_show_in_the_settings_repr():
    """Found while testing the bodies: a failing assertion printed Settings(...) with the user's
    real API key and bridge token in the test output."""
    from core.settings import Settings

    s = Settings(_env_file=None, llm_api_key="sk-live-123", openrouter_api_key="or-456", tavily_api_key="tv-7",
                 brave_api_key="br-8", bridge_token="tok-9")
    shown = repr(s) + str(s)
    for secret in ("sk-live-123", "or-456", "tv-7", "br-8", "tok-9"):
        assert secret not in shown
    assert s.llm_api_key == "sk-live-123"            # still there for the code that needs it
