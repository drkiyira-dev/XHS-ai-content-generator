"""Tests for local, non-printing Compose secret initialization."""

from pathlib import Path
import os
import stat

import pytest
from sqlalchemy.engine import make_url

from scripts.initialize_compose_secrets import (
    ComposeSecretInitializationError,
    SECRET_FILENAMES,
    initialize_compose_secrets,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_creates_consistent_secrets_in_a_private_host_directory(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "compose-secrets"
    app_password = "app-password-with-safe-characters-123456789"
    root_password = "root-password-abcdefghijklmnopqrstuvwxyz-123456"
    generated = iter((app_password, root_password))

    created = initialize_compose_secrets(
        output_dir=output_dir,
        api_key="provider-test-key",
        token_factory=lambda _: next(generated),
    )

    assert tuple(path.name for path in created) == SECRET_FILENAMES
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o444 for path in created)
    assert (output_dir / "SILICONFLOW_API_KEY").read_text(
        encoding="utf-8"
    ) == "provider-test-key"
    assert (output_dir / "MYSQL_APP_PASSWORD").read_text(
        encoding="utf-8"
    ) == app_password
    assert (output_dir / "MYSQL_ROOT_PASSWORD").read_text(
        encoding="utf-8"
    ) == root_password

    database_url = make_url(
        (output_dir / "DATABASE_URL").read_text(encoding="utf-8")
    )
    assert database_url.drivername == "mysql+pymysql"
    assert database_url.username == "xhs_app"
    assert database_url.password == app_password
    assert database_url.host == "127.0.0.1"
    assert database_url.port == 3306
    assert database_url.database == "xhs_ai"


def test_refuses_to_overwrite_any_existing_secret(tmp_path: Path) -> None:
    output_dir = tmp_path / "compose-secrets"
    output_dir.mkdir()
    existing = output_dir / "SILICONFLOW_API_KEY"
    existing.write_text("must-remain", encoding="utf-8")

    with pytest.raises(ComposeSecretInitializationError) as caught:
        initialize_compose_secrets(
            output_dir=output_dir,
            api_key="new-private-provider-key",
        )

    assert existing.read_text(encoding="utf-8") == "must-remain"
    assert "new-private-provider-key" not in str(caught.value)
    assert set(path.name for path in output_dir.iterdir()) == {
        "SILICONFLOW_API_KEY"
    }


@pytest.mark.parametrize("api_key", ["", "   ", "private\nsecond-line"])
def test_rejects_invalid_provider_key_without_creating_files(
    tmp_path: Path,
    api_key: str,
) -> None:
    output_dir = tmp_path / "compose-secrets"

    with pytest.raises(ComposeSecretInitializationError) as caught:
        initialize_compose_secrets(output_dir=output_dir, api_key=api_key)

    assert not output_dir.exists()
    if api_key.strip():
        assert api_key.strip() not in str(caught.value)


def test_cleans_partial_files_when_secret_generation_fails(tmp_path: Path) -> None:
    output_dir = tmp_path / "compose-secrets"
    generated = iter(("a" * 48, "too-short"))

    with pytest.raises(ComposeSecretInitializationError):
        initialize_compose_secrets(
            output_dir=output_dir,
            api_key="provider-test-key",
            token_factory=lambda _: next(generated),
        )

    assert not output_dir.exists()


@pytest.mark.parametrize(
    "unsafe_token",
    [
        "a" * 31,
        "a" * 31 + " ",
        "a" * 31 + "'",
        "a" * 31 + "$",
        "a" * 31 + "\n",
    ],
)
def test_rejects_generated_tokens_that_the_mysql_initializer_cannot_quote(
    tmp_path: Path,
    unsafe_token: str,
) -> None:
    output_dir = tmp_path / "compose-secrets"

    with pytest.raises(ComposeSecretInitializationError):
        initialize_compose_secrets(
            output_dir=output_dir,
            api_key="provider-test-key",
            token_factory=lambda _: unsafe_token,
        )

    assert not output_dir.exists()


def test_keyboard_interrupt_cleans_the_current_and_completed_secret_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "compose-secrets"
    real_fsync = os.fsync
    calls = 0

    def interrupt_second_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", interrupt_second_fsync)

    with pytest.raises(KeyboardInterrupt):
        initialize_compose_secrets(
            output_dir=output_dir,
            api_key="provider-test-key",
        )

    assert not output_dir.exists()


def test_local_compose_secret_directory_is_ignored_by_git() -> None:
    ignored_patterns = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".compose-secrets/" in ignored_patterns.splitlines()
