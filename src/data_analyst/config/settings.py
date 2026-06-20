from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATA_ANALYST_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(default="sqlite:///data_analyst.db")
    llm_provider: str = Field(default="")          # gemini | openrouter | stub | "" (auto)
    gemini_api_key: str = Field(default="")
    openrouter_api_key: str = Field(default="")
    llm_model: str = Field(default="gemini-3.1-flash-lite")
    max_iterations: int = Field(default=6)
    log_level: str = Field(default="INFO")
    upload_dir: str = Field(default="uploads")
    cache_limit_mb: int = Field(default=1024)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
