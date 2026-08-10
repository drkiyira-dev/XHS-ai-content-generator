"""Static safety contract for the backend container definition."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_a_minimal_non_root_runtime() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim-bookworm AS builder" in dockerfile
    assert "FROM python:3.12-slim-bookworm AS runtime" in dockerfile
    assert "COPY pyproject.toml ./" in dockerfile
    assert "COPY backend ./backend" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "--chown" not in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "TMPDIR=/run/xhs/uploads" in dockerfile
    assert "UPLOAD_DIR=/run/xhs/uploads" in dockerfile
    assert 'CMD ["python", "-m", "uvicorn"' in dockerfile
    assert '"--no-server-header"' in dockerfile
    assert "/api/health" in dockerfile
    assert "VOLUME" not in dockerfile


def test_dockerfile_never_bakes_runtime_secrets() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "SILICONFLOW_API_KEY" not in dockerfile
    assert "DATABASE_URL" not in dockerfile
    assert "ARG " not in dockerfile


def test_docker_context_is_an_explicit_allowlist() -> None:
    entries = [
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert entries == [
        "**",
        "!pyproject.toml",
        "!backend/",
        "!backend/**/",
        "!backend/*.py",
        "!backend/**/*.py",
    ]


def test_ci_runtime_has_no_network_and_never_pushes_an_image() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    assert "backend-container-smoke:" in workflow
    assert "docker build" in workflow
    assert "--network none" in workflow
    assert "--read-only" in workflow
    assert "--tmpfs /run/xhs/uploads:" in workflow
    assert "--cap-drop ALL" in workflow
    assert "--security-opt no-new-privileges=true" in workflow
    assert "--env DATABASE_ENABLED=false" in workflow
    assert "/api/health" in workflow
    assert "docker push" not in workflow
