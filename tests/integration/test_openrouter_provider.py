"""Real-key integration test for the OpenRouter provider (C21).

Verifies that setting AGENT_LLM_PROVIDER=openrouter with AGENT_OPENROUTER_API_KEY
routes to OpenRouter and returns a real, non-empty response.

Requires AGENT_OPENROUTER_API_KEY to be set in .env.  The test is skipped
automatically when the key is absent so the offline gate stays green.

Run explicitly:
    uv run pytest tests/integration/test_openrouter_provider.py -v
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- #
# Skip guard — specific to OpenRouter (not the generic _require_llm_key)
# --------------------------------------------------------------------------- #


@pytest.fixture
def _require_openrouter_key():
    """Skip if AGENT_OPENROUTER_API_KEY is not set in .env."""
    from config.settings import get_settings

    s = get_settings()
    if not s.openrouter_api_key:
        pytest.skip(
            "AGENT_OPENROUTER_API_KEY not set in .env — skipping OpenRouter real-key test"
        )


# --------------------------------------------------------------------------- #
# Real-key tests
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("_require_openrouter_key")
def test_openrouter_provider_real_call(monkeypatch):
    """Setting AGENT_LLM_PROVIDER=openrouter routes to OpenRouter and returns a
    real, non-empty string response from the live API.

    Requires AGENT_OPENROUTER_API_KEY in .env.
    """
    # Force the provider selection to openrouter explicitly.
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openrouter")

    # Re-resolve after env change (the autouse _reset_settings_singleton fixture
    # already cleared _settings; monkeypatch applies before get_settings() runs).
    from config.settings import get_settings

    s = get_settings()
    assert s.llm_provider == "openrouter", "provider must be forced to openrouter"
    assert s.openrouter_api_key, "key must be present (skip guard should have caught this)"

    from llm.providers.openrouter import OpenRouterProvider

    provider = OpenRouterProvider(
        api_key=s.openrouter_api_key,
        model=s.llm_model or OpenRouterProvider.DEFAULT_MODEL,
    )

    # Minimal completion — keep token cost negligible.
    response = provider.complete("Say hello in one word")

    assert response is not None, "expected an LLMResponse, got None"
    text = response.text
    assert isinstance(text, str), f"response.text must be a str, got {type(text)}"
    assert text.strip(), "response text must be non-empty"
    assert "[stub]" not in text, f"got a stub reply — real provider not used: {text!r}"

    print(f"\n[openrouter] response={text!r} tokens_in={response.tokens_input} tokens_out={response.tokens_output}")


@pytest.mark.usefixtures("_require_openrouter_key")
def test_openrouter_call_model_returns_str(monkeypatch):
    """call_model() is the thin wrapper around complete(); verify it also returns
    a non-empty string so the LLMClient interface contract is satisfied end-to-end.

    Requires AGENT_OPENROUTER_API_KEY in .env.
    """
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openrouter")

    from config.settings import get_settings
    from llm.providers.openrouter import OpenRouterProvider

    s = get_settings()

    provider = OpenRouterProvider(
        api_key=s.openrouter_api_key,
        model=s.llm_model or OpenRouterProvider.DEFAULT_MODEL,
    )

    result = provider.call_model("Say hello in one word")

    assert isinstance(result, str), f"call_model must return str, got {type(result)}"
    assert result.strip(), "call_model result must be non-empty"
    assert "[stub]" not in result, f"got stub text — real API not reached: {result!r}"

    print(f"\n[openrouter] call_model result={result!r}")


@pytest.mark.usefixtures("_require_openrouter_key")
def test_openrouter_via_llm_client(monkeypatch):
    """LLMClient auto-selects OpenRouter when AGENT_LLM_PROVIDER=openrouter.

    Verifies the full wiring from client.py through to the OpenRouter HTTP
    endpoint: provider name resolves correctly and a real completion is returned.

    Requires AGENT_OPENROUTER_API_KEY in .env.
    """
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openrouter")

    from llm.client import LLMClient

    client = LLMClient()

    assert client.provider == "openrouter", (
        f"LLMClient.provider must be 'openrouter', got {client.provider!r}"
    )

    response = client.complete("Say hello in one word")

    assert response is not None
    text = response.text
    assert isinstance(text, str)
    assert text.strip(), "LLMClient.complete() via OpenRouter must return non-empty text"
    assert "[stub]" not in text, f"got stub response — OpenRouter not reached: {text!r}"

    print(f"\n[openrouter] LLMClient.complete() text={text!r}")
