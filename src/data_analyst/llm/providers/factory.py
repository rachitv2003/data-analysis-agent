from data_analyst.llm.providers.base import LLMProvider


def create_llm_client() -> tuple["LLMProvider", str]:
    """Return (provider, provider_name).

    Provider is selected by DATA_ANALYST_LLM_PROVIDER. When that is empty,
    auto-detects: openrouter key set → openrouter; gemini key set → gemini; else stub.
    """
    from data_analyst.config.settings import get_settings
    settings = get_settings()

    explicit = settings.llm_provider.strip().lower()

    def _resolve() -> str:
        if explicit:
            return explicit
        if settings.openrouter_api_key:
            return "openrouter"
        if settings.gemini_api_key:
            return "gemini"
        return "stub"

    provider_name = _resolve()

    if provider_name == "openrouter":
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "DATA_ANALYST_LLM_PROVIDER=openrouter but DATA_ANALYST_OPENROUTER_API_KEY is not set."
            )
        from data_analyst.llm.providers.openrouter import OpenRouterProvider
        return OpenRouterProvider(settings.openrouter_api_key, settings.llm_model), "openrouter"

    if provider_name == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError(
                "DATA_ANALYST_LLM_PROVIDER=gemini but DATA_ANALYST_GEMINI_API_KEY is not set."
            )
        from data_analyst.llm.providers.gemini import GeminiProvider
        return GeminiProvider(settings.gemini_api_key, settings.llm_model), "gemini"

    from data_analyst.llm.providers.stub import StubLLMProvider
    return StubLLMProvider(), "stub"
