"""Application configuration using pydantic-settings."""

import base64
import secrets
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = Field(
        ...,
        description="Main database URL (Neon Postgres with SSL)",
    )
    TEST_DATABASE_URL: str = Field(
        ...,
        description="Test database URL (must be different from DATABASE_URL)",
    )

    # Groq API
    GROQ_API_KEY: str = Field(
        ...,
        description="Groq API key for LLM access",
    )
    GROQ_MODEL_PRIMARY: str = Field(
        default="llama-3.3-70b-versatile",
        description="Primary model for complex reasoning",
    )
    GROQ_MODEL_FAST: str = Field(
        default="llama-3.1-8b-instant",
        description="Fast model for simple tasks",
    )

    # Application
    PUBLIC_BASE_URL: str = Field(
        default="http://localhost:8000",
        description="Public base URL for webhook callbacks",
    )
    SIGNING_KEY_B64: str = Field(
        default="",
        description="Base64-encoded signing key (auto-generated if empty)",
    )
    UNIVERSE_TTL_DAYS: int = Field(
        default=7,
        description="Universe expiry in real days",
    )
    ENV: Literal["dev", "test", "prod"] = Field(
        default="dev",
        description="Environment",
    )

    # Internal cached properties
    _signing_key_bytes: bytes | None = None
    _sqlalchemy_url: str | None = None
    _raw_database_url: str | None = None

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure DATABASE_URL is provided."""
        if not v:
            raise ValueError(
                "DATABASE_URL is required. Please set it in your .env file with a valid "
                "Neon Postgres connection string."
            )
        return v

    def get_sqlalchemy_url(self) -> str:
        """
        Convert Neon URL to SQLAlchemy-compatible URL with psycopg driver.

        Neon URLs come as postgresql:// or postgres://
        SQLAlchemy 2.0 + psycopg3 needs postgresql+psycopg://
        """
        if self._sqlalchemy_url is None:
            url = self.DATABASE_URL
            # Normalize postgres:// to postgresql://
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            # Add psycopg driver
            if url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url[len("postgresql://"):]
            self._sqlalchemy_url = url
        return self._sqlalchemy_url

    def get_raw_database_url(self) -> str:
        """
        Get the raw Postgres URL for LangGraph checkpoint.

        LangGraph expects a plain postgresql:// URL without the driver suffix.
        """
        if self._raw_database_url is None:
            url = self.DATABASE_URL
            # Normalize to postgresql://
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            self._raw_database_url = url
        return self._raw_database_url

    def get_signing_key(self) -> bytes:
        """Get or generate the signing key for webhook signatures."""
        if self._signing_key_bytes is None:
            if self.SIGNING_KEY_B64:
                self._signing_key_bytes = base64.b64decode(self.SIGNING_KEY_B64)
            else:
                # Generate a new key and persist it in memory for this process lifetime
                self._signing_key_bytes = secrets.token_bytes(32)
        return self._signing_key_bytes


# Global settings instance
settings = Settings()
