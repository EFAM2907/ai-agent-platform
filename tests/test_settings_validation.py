"""Settings parsing and production fail-fast checks.

Every instance is built with _env_file=None so the real .env is never
read; required fields are passed explicitly (init kwargs also win over
the process environment).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import EXAMPLE_SECRET_KEYS, Settings

GOOD_SECRET = "s3cr3t-" + "x" * 40
VALID_PEM = "-----BEGIN PRIVATE KEY-----\nplaceholder\n-----END PRIVATE KEY-----"


def make_settings(**overrides) -> Settings:
    values = dict(
        database_url="postgresql+asyncpg://u:p@localhost:5433/db",
        secret_key=GOOD_SECRET,
        encryption_key="test-encryption-key",
        algorithm="HS256",
        access_token_expire_minutes=30,
        litellm_emergency_fallback_key="test-fallback-key",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_production(**overrides) -> Settings:
    values = dict(
        environment="production",
        sts_private_key=VALID_PEM,
        cors_origins=["https://app.example.com"],
    )
    values.update(overrides)
    return make_settings(**values)


def test_cors_origins_default_keeps_local_flow(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert make_settings().cors_origins == ["http://localhost:5173"]


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, https://app.example.com ,")
    assert make_settings().cors_origins == [
        "http://localhost:5173",
        "https://app.example.com",
    ]


def test_valid_production_config_is_accepted():
    assert make_production().environment == "production"


def test_development_allows_weak_config(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    settings = make_settings(secret_key="short", cors_origins=["*"])
    assert settings.environment == "development"


def test_production_rejects_short_secret_key_without_leaking_it():
    weak = "tiny-secret-value"
    with pytest.raises(ValidationError) as exc:
        make_production(secret_key=weak)
    assert "SECRET_KEY" in str(exc.value)
    assert weak not in str(exc.value)


def test_production_rejects_example_secret_key():
    example = next(iter(EXAMPLE_SECRET_KEYS))
    with pytest.raises(ValidationError) as exc:
        make_production(secret_key=example)
    assert "placeholder" in str(exc.value)
    assert example not in str(exc.value)


def test_production_requires_sts_private_key():
    with pytest.raises(ValidationError) as exc:
        make_production(sts_private_key=None)
    assert "STS_PRIVATE_KEY" in str(exc.value)


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValidationError) as exc:
        make_production(cors_origins=["*"])
    assert "CORS_ORIGINS" in str(exc.value)


def test_production_rejects_non_https_cors_origin():
    with pytest.raises(ValidationError) as exc:
        make_production(cors_origins=["https://app.example.com", "http://app.example.com"])
    assert "https" in str(exc.value)
    assert "http://app.example.com" not in str(exc.value)
