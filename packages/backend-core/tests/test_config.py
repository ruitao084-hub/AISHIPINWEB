"""Settings behaviour, especially secret handling (taskbook §63)."""

from __future__ import annotations

import pytest

from backend_core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_secrets_are_not_exposed_by_repr() -> None:
    """§63 forbids logging credentials. `repr` is how they usually escape."""
    settings = Settings(
        s3_access_key_id="AKIAREALLOOKINGKEY",  # type: ignore[arg-type]
        s3_secret_access_key="super-secret-value",  # type: ignore[arg-type]
        _env_file=None,
    )

    rendered = f"{settings!r} {settings!s} {settings.model_dump()}"
    assert "AKIAREALLOOKINGKEY" not in rendered
    assert "super-secret-value" not in rendered


def test_secret_value_is_still_retrievable_deliberately() -> None:
    settings = Settings(
        s3_secret_access_key="super-secret-value",  # type: ignore[arg-type]
        _env_file=None,
    )
    assert settings.s3_secret_access_key.get_secret_value() == "super-secret-value"


def test_mock_providers_are_the_default() -> None:
    """§170 makes a key-free local run a hard requirement."""
    settings = Settings(_env_file=None)
    assert settings.use_mock_providers is True
    assert settings.enable_credits is False


def test_invalid_app_env_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(app_env="prod", _env_file=None)  # type: ignore[arg-type]


def test_signed_url_ttl_is_bounded() -> None:
    """§110 wants short-lived URLs; S3 caps presigned validity at 7 days."""
    with pytest.raises(ValueError):
        Settings(s3_signed_url_ttl_seconds=10, _env_file=None)
    with pytest.raises(ValueError):
        Settings(s3_signed_url_ttl_seconds=999_999, _env_file=None)


def test_settings_are_cached() -> None:
    assert get_settings() is get_settings()
