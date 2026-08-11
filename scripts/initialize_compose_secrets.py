"""Create local Docker Compose secrets without printing their values."""

from __future__ import annotations

from getpass import getpass
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Callable
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / ".compose-secrets"
SECRET_FILENAMES = (
    "SILICONFLOW_API_KEY",
    "MYSQL_APP_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "DATABASE_URL",
)


class ComposeSecretInitializationError(RuntimeError):
    """Safe setup failure that never contains a generated secret."""


def initialize_compose_secrets(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    api_key: str,
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> tuple[Path, ...]:
    """Create one complete, internally consistent set of Compose secrets."""
    normalized_api_key = api_key.strip()
    if not normalized_api_key or "\n" in normalized_api_key or "\r" in normalized_api_key:
        raise ComposeSecretInitializationError("invalid provider credential")

    created_directory = False
    cleanup_allowed = False
    created_files: list[Path] = []
    try:
        if output_dir.is_symlink():
            raise ComposeSecretInitializationError("unsafe secrets directory")
        if output_dir.exists():
            if not output_dir.is_dir() or any(output_dir.iterdir()):
                raise ComposeSecretInitializationError("secrets directory is not empty")
        else:
            output_dir.mkdir(mode=0o700, parents=False)
            created_directory = True
        cleanup_allowed = True
        os.chmod(output_dir, 0o700)

        app_password = _validated_generated_token(token_factory(36))
        root_password = _validated_generated_token(token_factory(36))
        if root_password == app_password:
            raise ComposeSecretInitializationError("secret generation failed")
        database_url = (
            "mysql+pymysql://xhs_app:"
            f"{quote(app_password, safe='')}@127.0.0.1:3306/xhs_ai"
        )
        values = {
            "SILICONFLOW_API_KEY": normalized_api_key,
            "MYSQL_APP_PASSWORD": app_password,
            "MYSQL_ROOT_PASSWORD": root_password,
            "DATABASE_URL": database_url,
        }

        for filename in SECRET_FILENAMES:
            path = output_dir / filename
            _write_secret_exclusively(path, values[filename])
            created_files.append(path)
    except ComposeSecretInitializationError:
        if cleanup_allowed:
            _clean_partial_output(output_dir, created_directory)
        raise
    except Exception:
        if cleanup_allowed:
            _clean_partial_output(output_dir, created_directory)
        raise ComposeSecretInitializationError("compose secret setup failed") from None
    except BaseException:
        if cleanup_allowed:
            _clean_partial_output(output_dir, created_directory)
        raise
    finally:
        normalized_api_key = ""

    return tuple(created_files)


def _validated_generated_token(value: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{32,}", value) is None
    ):
        raise ComposeSecretInitializationError("secret generation failed")
    return value


def _write_secret_exclusively(path: Path, value: str) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except Exception:
        raise ComposeSecretInitializationError("secret file creation failed") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        # File-backed Compose secrets are bind mounts, so Docker cannot remap
        # their ownership or mode for the non-root container users. The 0700
        # parent directory protects them on the host; the files themselves must
        # remain readable after being mounted read-only into the containers.
        os.chmod(path, 0o444)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _clean_partial_output(
    output_dir: Path,
    created_directory: bool,
) -> None:
    for filename in SECRET_FILENAMES:
        (output_dir / filename).unlink(missing_ok=True)
    if created_directory:
        try:
            output_dir.rmdir()
        except OSError:
            pass


def main() -> int:
    try:
        api_key = getpass("SiliconFlow API Key（输入不会显示）: ")
        created = initialize_compose_secrets(api_key=api_key)
    except (ComposeSecretInitializationError, EOFError, KeyboardInterrupt):
        print(
            "未创建 Compose secrets；请确认目录为空并重新运行。",
            file=sys.stderr,
        )
        return 1
    finally:
        api_key = ""

    print(f"已安全创建 {len(created)} 个本地 Compose secret 文件。")
    print("目录：.compose-secrets/（已被 Git 忽略）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
