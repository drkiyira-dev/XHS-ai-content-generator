"""Static contract for the opt-in Docker Compose integration job."""

import ast
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
import yaml

from backend.validation import validate_copy
from scripts import compose_runtime_smoke


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
SMOKE_FILE = PROJECT_ROOT / "scripts" / "compose_runtime_smoke.py"


def _load_workflow() -> tuple[dict[str, object], str]:
    source = WORKFLOW_FILE.read_text(encoding="utf-8")
    loaded = yaml.safe_load(source)
    assert isinstance(loaded, dict)
    return loaded, source


def _compose_job_script() -> str:
    workflow, _ = _load_workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["compose-integration"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    run_steps = [
        step["run"]
        for step in steps
        if isinstance(step, dict) and "run" in step
    ]
    assert len(run_steps) == 1
    script = run_steps[0]
    assert isinstance(script, str)
    return script


def test_compose_ci_has_read_only_permissions_and_no_repository_secrets() -> None:
    workflow, source = _load_workflow()
    jobs = workflow["jobs"]
    compose_job = jobs["compose-integration"]

    assert workflow["permissions"] == {"contents": "read"}
    assert compose_job["runs-on"] == "ubuntu-24.04"
    assert compose_job["timeout-minutes"] == 15
    run_step = next(step for step in compose_job["steps"] if "run" in step)
    assert run_step["env"] == {
        "COMPOSE_PROJECT_NAME": (
            "xhs-compose-ci-${{ github.run_id }}-${{ github.run_attempt }}"
        )
    }
    checkout_step = compose_job["steps"][0]
    assert checkout_step["uses"].startswith("actions/checkout@")
    checkout_revision = checkout_step["uses"].split("@", maxsplit=1)[1]
    assert len(checkout_revision) == 40
    assert all(character in "0123456789abcdef" for character in checkout_revision)
    assert checkout_step["with"] == {"persist-credentials": False}
    assert "pull_request_target" not in source
    assert "github.event" not in source
    assert "${{ secrets." not in source
    assert "persist-credentials: false" in source
    assert "docker push" not in source
    assert "docker system prune" not in source
    assert "docker volume prune" not in source
    assert "rm -rf" not in source


def test_compose_ci_shell_is_valid_and_installs_cleanup_before_startup() -> None:
    script = _compose_job_script()
    result = subprocess.run(
        ["bash", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    trap_position = script.index("trap cleanup EXIT")
    initializer_position = script.index("initialize_compose_secrets")
    first_up_position = script.index("up --build --detach --wait")
    write_position = script.index("python - write")
    recreate_position = script.index("rm --force --stop backend mysql")
    second_up_position = script.index(
        "up --detach --wait --wait-timeout 240",
        recreate_position,
    )
    read_position = script.index('python - read "$generation_id"')
    down_position = script.rindex("down --volumes --remove-orphans")
    assert (
        trap_position
        < initializer_position
        < first_up_position
        < write_position
        < recreate_position
        < second_up_position
        < read_position
        < down_position
    )


def test_compose_ci_uses_an_internal_ephemeral_stack() -> None:
    script = _compose_job_script()

    assert "--file compose.yaml" in script
    assert "--file compose.ci.yaml" in script
    assert '--project-name "$COMPOSE_PROJECT_NAME"' in script
    assert 'config --quiet' in script
    assert 'up --build --detach --wait --wait-timeout 240' in script
    assert "ci-placeholder-not-a-secret" in script
    assert "com.docker.compose.network=runtime" in script
    assert "docker network inspect --format '{{.Internal}}'" in script
    assert 'down --volumes --remove-orphans' in script
    assert "com.docker.compose.project=$COMPOSE_PROJECT_NAME" in script
    assert "test -z \"$remaining_volumes\"" in script
    assert "test -z \"$remaining_containers\"" in script
    assert "test -z \"$remaining_networks\"" in script


def test_compose_ci_checks_container_hardening_and_secret_boundaries() -> None:
    script = _compose_job_script()

    assert 'exec -T mysql id -u' in script
    assert 'exec -T backend id -u' in script
    assert 'ReadonlyRootfs' in script
    assert 'HostConfig.CapDrop' in script
    assert "SILICONFLOW_API_KEY|DATABASE_URL" in script
    assert "MYSQL_[A-Z0-9_]*PASSWORD" in script
    assert 'HostConfig.PortBindings' in script
    assert 'bindings == {"8000/tcp"' in script
    assert '"HostIp":"127.0.0.1"' in script
    assert '"HostPort":"8000"' in script
    assert 'compose[@]}" port' not in script


def test_compose_ci_verifies_persistence_without_calling_the_model_api() -> None:
    script = _compose_job_script()

    assert "/api/health" in script
    assert "/api/v1/generations?limit=1" in script
    assert 'method="GET"' in script
    assert 'response.headers.get("Cache-Control") == "no-store"' in script
    assert "compose_runtime_smoke.py" in script
    assert "python - write" in script
    assert 'python - read "$generation_id"' in script
    assert script.count('verify_history "$generation_id"') == 2
    assert 'rm --force --stop backend mysql' in script
    assert 'test "$recreated_mysql_id" != "$mysql_id"' in script
    assert 'test "$recreated_backend_id" != "$backend_id"' in script
    assert "curl " not in script
    assert "SiliconFlow" not in script
    assert "POST" not in script


def test_persistence_smoke_checks_only_fixed_local_database_invariants() -> None:
    source = SMOKE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert 'EXPECTED_SCHEMA_PRIVILEGES = {"SELECT", "INSERT", "UPDATE"}' in source
    assert "EXPECTED_GRANTS = {" in source
    assert 'assert current_user == "xhs_app@%"' in source
    assert '"SELECT TABLE_SCHEMA, PRIVILEGE_TYPE "' in source
    assert 'text("SHOW GRANTS")' in source
    assert 'assert grants == EXPECTED_GRANTS' in source
    assert 'assert global_privileges <= {"USAGE"}' in source
    assert '"SELECT ROLE_NAME, ROLE_HOST "' in source
    assert '"FROM information_schema.APPLICABLE_ROLES"' in source
    assert 'assert applicable_roles == ()' in source
    assert '"TABLE_PRIVILEGES"' in source
    assert '"COLUMN_PRIVILEGES"' in source
    assert '"ROUTINE_PRIVILEGES"' in source
    assert 'await persistence.create_pending(' in source
    assert 'await persistence.mark_success(' in source
    assert 'await persistence.list_successful(limit=50)' in source
    assert 'Path("/run/xhs/uploads")' in source
    assert imported_modules == {
        "__future__",
        "asyncio",
        "datetime",
        "pathlib",
        "sys",
        "uuid",
        "anyio",
        "sqlalchemy",
        "backend.core.config",
        "backend.services.persistence",
        "backend.services.persistence.runtime",
    }
    called_builtins = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called_builtins.intersection({"__import__", "eval", "exec"})

    run_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run"
    )
    run_names = {
        node.id
        for node in ast.walk(run_node)
        if isinstance(node, ast.Name)
    }
    run_attributes = {
        node.attr
        for node in ast.walk(run_node)
        if isinstance(node, ast.Attribute)
    }
    assert {
        "_verify_identity_and_privileges",
        "_assert_expected_record",
        "_assert_upload_directory_empty",
    } <= run_names
    assert {
        "startup",
        "aclose",
        "create_pending",
        "mark_success",
        "list_successful",
    } <= run_attributes
    assert "model_service" not in source
    assert "SiliconFlow" not in source
    assert "urllib" not in source
    assert "httpx" not in source


def test_persistence_smoke_payload_is_valid_before_ci_reaches_mysql() -> None:
    normalized = validate_copy(
        image_summary=compose_runtime_smoke.EXPECTED_IMAGE_SUMMARY,
        title=compose_runtime_smoke.EXPECTED_TITLE,
        body=compose_runtime_smoke.EXPECTED_BODY,
        tags=compose_runtime_smoke.EXPECTED_TAGS,
    )

    assert normalized == (
        compose_runtime_smoke.EXPECTED_IMAGE_SUMMARY,
        compose_runtime_smoke.EXPECTED_TITLE,
        compose_runtime_smoke.EXPECTED_BODY,
        list(compose_runtime_smoke.EXPECTED_TAGS),
    )


def test_persistence_smoke_cli_write_and_read_protocol(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    generation_id = str(uuid4())
    calls: list[tuple[str, str | None]] = []

    async def fake_run(mode: str, received_id: str | None) -> str | None:
        calls.append((mode, received_id))
        return generation_id if mode == "write" else None

    monkeypatch.setattr(compose_runtime_smoke, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["compose_runtime_smoke.py", "write"])
    assert compose_runtime_smoke.main() == 0
    assert capsys.readouterr().out.strip() == generation_id

    monkeypatch.setattr(
        sys,
        "argv",
        ["compose_runtime_smoke.py", "read", generation_id],
    )
    assert compose_runtime_smoke.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert calls == [("write", None), ("read", generation_id)]
    assert str(UUID(generation_id)) == generation_id


def test_persistence_smoke_cli_rejects_mismatched_arguments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def forbidden_run(mode: str, generation_id: str | None) -> None:
        raise AssertionError("invalid arguments must be rejected before runtime setup")

    monkeypatch.setattr(compose_runtime_smoke, "_run", forbidden_run)
    for argv in (
        ["compose_runtime_smoke.py", "write", str(uuid4())],
        ["compose_runtime_smoke.py", "read"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        assert compose_runtime_smoke.main() == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("invalid smoke-test arguments") == 2
