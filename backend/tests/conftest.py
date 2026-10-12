"""Test configuration.

Unit tests need no services. Integration tests need PostgreSQL and Redis:

  TEST_DATABASE_URL  PostgreSQL URL of a dedicated database whose name contains "test"
  TEST_REDIS_URL     Redis URL on a logical database numbered 10 or higher
                     (default: redis://127.0.0.1:6379/15)

scripts/setup-local.ps1 creates both. Integration tests are skipped when TEST_DATABASE_URL
is not set. The suite never touches the development database.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

# The environment must be prepared before any application module reads its settings.
os.environ["FORGE_ENV_FILE"] = str(Path(__file__).parent / "no-such.env")
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "t" * 48
os.environ["API_KEY_PEPPER"] = "p" * 48
for _name in [name for name in os.environ if name.startswith("NATLAS_")]:
    del os.environ[_name]

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["DATABASE_URL"] = (
    TEST_DATABASE_URL or "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"
)

import pytest  # noqa: E402
import redis as redis_sync  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


def make_settings(**overrides) -> Settings:
    """Hermetic settings: nothing leaks in from the developer's shell or .env file."""
    values = {
        "app_env": "test",
        "database_url": os.environ["DATABASE_URL"],
        "redis_url": TEST_REDIS_URL,
        "secret_key": "t" * 48,
        "api_key_pepper": "p" * 48,
        "cookie_secure": False,
        "log_level": "WARNING",
        "log_format": "console",
        "registration_enabled": True,
        "public_base_url": "http://localhost:5173",
        "cors_allowed_origins": "",
        "trusted_proxy_count": 0,
        "max_request_body_bytes": 1_048_576,
        "auth_rate_limit_per_minute": 1000,
        "gateway_rate_limit_per_minute": 1000,
        "login_max_failures": 5,
        "natlas_inference_url": None,
        "natlas_inference_token": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _require_safe_targets() -> None:
    db_name = urlsplit(TEST_DATABASE_URL).path.lstrip("/")
    if "test" not in db_name.lower():
        raise RuntimeError(
            f"Refusing to run tests: database name '{db_name}' does not contain 'test'."
        )
    redis_db = urlsplit(TEST_REDIS_URL).path.lstrip("/") or "0"
    if not redis_db.isdigit() or int(redis_db) < 10:
        raise RuntimeError(
            "Refusing to run tests: TEST_REDIS_URL must use a logical database of 10 or higher "
            "because the suite flushes it."
        )


@pytest.fixture(scope="session")
def migrated_database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set, so integration tests are skipped.")
    _require_safe_targets()
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    redis_sync.Redis.from_url(TEST_REDIS_URL).flushdb()
    yield


@pytest.fixture(scope="module")
def client(migrated_database):
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client
