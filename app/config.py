"""DispatchGuard Configuration module."""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "DispatchGuard"
    APP_ENV: str = "production"
    DEBUG: bool = False
    SECRET_KEY: str = "dispatchguard-manufacturing-secret-key-2026-production"
    DATABASE_URL: str = "sqlite:///./dispatchguard.db"

    @property
    def normalized_database_url(self) -> str:
        url = self.DATABASE_URL
        # Render and Heroku PostgreSQL URLs start with postgres://
        # SQLAlchemy 2.0 requires postgresql:// or postgresql+psycopg2://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url


settings = Settings()
