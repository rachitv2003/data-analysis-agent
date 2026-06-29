from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(...)
    log_level: str = Field(default="INFO")

    # LLM provider — auto-detected from whichever key is set if left blank
    llm_provider: str = Field(default="")   # auto | gemini | openrouter | stub
    llm_model: str = Field(default="")      # uses provider default when blank

    # Provider keys — set exactly one
    anthropic_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    openrouter_api_key: str = Field(default="")

    # ReAct loop cap — max plan/execute iterations per run. 12 gives heavy
    # multi-step tasks (e.g. merge -> aggregate -> encode -> scale -> fit a model
    # -> summarise) room to finish in one turn; simple questions still stop early.
    # Tunable per-instance via Settings (the `max_iterations` DB override).
    max_iterations: int = Field(default=12)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
