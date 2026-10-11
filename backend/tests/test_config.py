"""Configuration validation. No services required."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = {
    "database_url": "postgresql://user:pass@localhost:5432/forge_dev",
    "redis_url": "redis://localhost:6379/0",
    "secret_key": "s" * 40,
    "api_key_pepper": "k" * 40,
}


def build(**overrides) -> Settings:
    return Settings(_env_file=None, **{**BASE, "app_env": "development", **overrides})


def test_database_url_is_normalised_to_asyncpg():
    assert build().database_url == "postgresql+asyncpg://user:pass@localhost:5432/forge_dev"


def test_non_postgres_database_url_is_rejected():
    with pytest.raises(ValidationError):
        build(database_url="mysql://user:pass@localhost/db")


def test_placeholder_secrets_are_rejected_outside_tests():
    with pytest.raises(ValidationError):
        build(secret_key="CHANGE_ME_" + "x" * 30)
    assert build(app_env="test", secret_key="CHANGE_ME_" + "x" * 30)


def test_short_secrets_are_rejected():
    with pytest.raises(ValidationError):
        build(api_key_pepper="short")


def test_validation_errors_do_not_echo_secrets():
    with pytest.raises(ValidationError) as error:
        build(database_url="mysql://user:SuperSecretPw@localhost/db")
    assert "SuperSecretPw" not in str(error.value)


def test_production_requires_secure_cookies_and_https():
    good = {"app_env": "production", "cookie_secure": True, "public_base_url": "https://forge.example.com"}
    assert build(**good).is_production
    with pytest.raises(ValidationError):
        build(**{**good, "cookie_secure": False})
    with pytest.raises(ValidationError):
        build(**{**good, "public_base_url": "http://forge.example.com"})


def test_provider_url_and_token_must_be_set_together():
    with pytest.raises(ValidationError):
        build(natlas_inference_url="http://127.0.0.1:8081")
    with pytest.raises(ValidationError):
        build(natlas_inference_token="t" * 40)
    configured = build(natlas_inference_url="http://127.0.0.1:8081/", natlas_inference_token="t" * 40)
    assert configured.natlas_configured
    assert configured.natlas_inference_url == "http://127.0.0.1:8081"


def test_blank_provider_values_are_treated_as_unset():
    settings = build(natlas_inference_url="   ", natlas_inference_token="")
    assert settings.natlas_inference_url is None
    assert not settings.natlas_configured


def test_provider_url_must_not_embed_credentials_or_queries():
    for url in ("http://user:pw@worker:8081", "http://worker:8081/?x=1", "ftp://worker"):
        with pytest.raises(ValidationError):
            build(natlas_inference_url=url, natlas_inference_token="t" * 40)


def test_production_rejects_plaintext_remote_provider_but_allows_loopback():
    production = {
        "app_env": "production",
        "cookie_secure": True,
        "public_base_url": "https://forge.example.com",
        "natlas_inference_token": "t" * 40,
    }
    with pytest.raises(ValidationError):
        build(**production, natlas_inference_url="http://10.0.0.5:8081")
    assert build(**production, natlas_inference_url="http://127.0.0.1:8081")
    assert build(**production, natlas_inference_url="https://worker.example.com")
    assert build(
        **production,
        natlas_inference_url="http://10.0.0.5:8081",
        natlas_allow_plaintext_remote=True,
    )


def test_cors_origins_are_normalised_and_wildcards_rejected():
    settings = build(cors_allowed_origins="https://App.example.com/, http://localhost:3000")
    assert settings.cors_origins_list == ["https://app.example.com", "http://localhost:3000"]
    assert "https://app.example.com" in settings.allowed_origins
    with pytest.raises(ValidationError):
        build(cors_allowed_origins="*")


def test_default_max_tokens_cannot_exceed_the_limit():
    with pytest.raises(ValidationError):
        build(natlas_default_max_tokens=4096, natlas_max_tokens_limit=2048)
