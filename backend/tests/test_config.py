"""Tests for configuration validation."""

from app.config import Settings


def test_database_url_conversion() -> None:
    """Test DATABASE_URL is converted to SQLAlchemy format."""
    config = Settings()
    sa_url = config.get_sqlalchemy_url()

    assert sa_url.startswith("postgresql+psycopg://")


def test_postgres_url_conversion(monkeypatch) -> None:
    """Test old postgres:// URLs are normalized."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host/db")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@host/test")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    config = Settings()
    sa_url = config.get_sqlalchemy_url()

    assert sa_url.startswith("postgresql+psycopg://")


def test_test_url_never_equals_production() -> None:
    """Test that conftest prevents TEST_DATABASE_URL == DATABASE_URL."""
    # This is tested indirectly in conftest.py, but we verify the config allows both to be set
    config = Settings()
    assert config.DATABASE_URL != config.TEST_DATABASE_URL


def test_signing_key_generation(monkeypatch) -> None:
    """Test signing key is auto-generated if empty."""
    monkeypatch.setenv("SIGNING_KEY_B64", "")

    config = Settings()
    key1 = config.get_signing_key()
    key2 = config.get_signing_key()  # Should be cached

    assert len(key1) == 32
    assert key1 == key2  # Same instance
