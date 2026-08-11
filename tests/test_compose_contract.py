"""Static safety contract for the local Docker Compose topology."""

from pathlib import Path
import re
import stat

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "compose.yaml"
COMPOSE_CI_FILE = PROJECT_ROOT / "compose.ci.yaml"


def _load_compose() -> dict[str, object]:
    loaded = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _load_compose_ci() -> dict[str, object]:
    loaded = yaml.safe_load(COMPOSE_CI_FILE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_compose_uses_shared_loopback_without_publishing_mysql() -> None:
    compose = _load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    mysql = services["mysql"]
    backend = services["backend"]

    assert mysql["image"] == "mysql:8.4.11"
    assert backend["network_mode"] == "service:mysql"
    assert "--bind-address=127.0.0.1" in mysql["command"]
    assert "--mysqlx=OFF" in mysql["command"]
    assert mysql["ports"] == ["127.0.0.1:8000:8000"]
    assert all("3306" not in published for published in mysql["ports"])
    assert mysql["networks"] == ["runtime"]
    assert compose["networks"]["runtime"]["driver"] == "bridge"


def test_compose_grants_each_service_only_its_required_file_secrets() -> None:
    compose = _load_compose()
    services = compose["services"]
    mysql = services["mysql"]
    backend = services["backend"]

    assert mysql["environment"]["MYSQL_ROOT_PASSWORD_FILE"] == (
        "/run/secrets/MYSQL_ROOT_PASSWORD"
    )
    assert "MYSQL_USER" not in mysql["environment"]
    assert "MYSQL_PASSWORD_FILE" not in mysql["environment"]
    assert "MYSQL_PASSWORD" not in mysql["environment"]
    assert "MYSQL_ROOT_PASSWORD" not in mysql["environment"]
    assert backend["environment"] == {
        "DATABASE_ENABLED": "true",
        "UPLOAD_DIR": "/run/xhs/uploads",
    }

    mysql_targets = {item["target"] for item in mysql["secrets"]}
    backend_targets = {item["target"] for item in backend["secrets"]}
    assert mysql_targets == {"MYSQL_APP_PASSWORD", "MYSQL_ROOT_PASSWORD"}
    assert backend_targets == {"SILICONFLOW_API_KEY", "DATABASE_URL"}
    assert mysql_targets.isdisjoint(backend_targets)

    expected_files = {
        "siliconflow_api_key": "./.compose-secrets/SILICONFLOW_API_KEY",
        "database_url": "./.compose-secrets/DATABASE_URL",
        "mysql_app_password": "./.compose-secrets/MYSQL_APP_PASSWORD",
        "mysql_root_password": "./.compose-secrets/MYSQL_ROOT_PASSWORD",
    }
    assert {
        name: definition["file"]
        for name, definition in compose["secrets"].items()
    } == expected_files


def test_compose_hardens_both_services_and_persists_only_mysql_data() -> None:
    compose = _load_compose()
    services = compose["services"]
    for service_name in ("mysql", "backend"):
        service = services[service_name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges=true"]
        assert service["pids_limit"] > 0
        assert service["tmpfs"]

    assert services["mysql"]["user"] == "999:999"
    assert services["backend"]["user"] == "10001:10001"
    assert set(compose["volumes"]) == {"mysql_data"}
    mysql_volumes = services["mysql"]["volumes"]
    assert {
        (volume["source"], volume["target"])
        for volume in mysql_volumes
        if volume["type"] == "volume"
    } == {("mysql_data", "/var/lib/mysql")}
    assert not services["backend"].get("volumes")


def test_compose_initialization_is_scoped_and_permission_reducing() -> None:
    compose = _load_compose()
    mysql = compose["services"]["mysql"]
    bind_mounts = {
        volume["source"]: volume
        for volume in mysql["volumes"]
        if volume["type"] == "bind"
    }
    assert set(bind_mounts) == {
        "./migrations/001_generation_records.sql",
        "./migrations/002_create_app_user.sh",
    }
    assert all(volume["read_only"] is True for volume in bind_mounts.values())

    migration_path = PROJECT_ROOT / "migrations" / "002_create_app_user.sh"
    assert not stat.S_IMODE(migration_path.stat().st_mode) & stat.S_IXUSR
    migration = migration_path.read_text(encoding="utf-8")
    assert "docker_process_sql --database=xhs_ai" in migration
    assert ">/dev/null 2>&1" in migration
    assert "MYSQL_APP_PASSWORD" in migration
    assert "GRANT ALL" not in migration.upper()
    assert "DELETE" not in migration.upper()
    sql_block = migration.split("<<SQL\n", maxsplit=1)[1].split("\nSQL", maxsplit=1)[0]
    statements = [
        re.sub(r"\s+", " ", statement).strip().upper()
        for statement in sql_block.split(";")
        if statement.strip()
    ]
    assert statements == [
        "CREATE USER 'XHS_APP'@'%' IDENTIFIED BY '${APP_PASSWORD}'",
        (
            "GRANT SELECT, INSERT, UPDATE ON XHS_AI.* TO 'XHS_APP'@'%'"
        ),
    ]


def test_compose_source_never_contains_embedded_credentials() -> None:
    source = COMPOSE_FILE.read_text(encoding="utf-8")
    assert not re.search(r"mysql\+pymysql://", source, flags=re.IGNORECASE)
    assert not re.search(r"SILICONFLOW_API_KEY\s*:\s*\S+", source)
    assert not re.search(r"MYSQL_(?:ROOT_)?PASSWORD\s*:\s*\S+", source)
    assert "privileged:" not in source
    assert "cap_add:" not in source


def test_compose_ci_disables_restart_and_external_runtime_networking() -> None:
    compose_ci = _load_compose_ci()

    assert compose_ci["services"] == {
        "mysql": {"restart": "no"},
        "backend": {"restart": "no"},
    }
    assert compose_ci["networks"] == {"runtime": {"internal": True}}
