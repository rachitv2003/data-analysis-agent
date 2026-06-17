from data_analyst.llm.providers.base import LLMProvider


def create_llm_client() -> tuple["LLMProvider", str]:
    """Return (provider, provider_name). Auto-selects real Gemini when API key is set."""
    from data_analyst.config.settings import get_settings
    settings = get_settings()

    if settings.gemini_api_key:
        from data_analyst.llm.providers.gemini import GeminiProvider
        return GeminiProvider(settings.gemini_api_key, settings.llm_model), "gemini"

    from data_analyst.llm.providers.stub import StubLLMProvider
    return StubLLMProvider(), "stub"
