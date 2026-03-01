from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Required
    secret_key: str

    # Optional with defaults
    database_url: str = "sqlite+aiosqlite:///config/greateye.db"
    plex_client_id: str = ""
    log_level: str = "INFO"
    session_expiry_hours: int = 24
    metrics_retention_days: int = 30

    model_config = {"env_file": ".env", "env_prefix": "", "case_sensitive": False}


# Singleton — imported wherever needed
settings = Settings()
