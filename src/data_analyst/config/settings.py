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
    gemini_api_key: str = Field(default="")
    llm_model: str = Field(default="gemini-2.5-flash")
    max_iterations: int = Field(default=10)
    log_level: str = Field(default="INFO")
    upload_dir: str = Field(default="uploads")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
