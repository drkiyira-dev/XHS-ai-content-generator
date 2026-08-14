"""Keep browser E2E isolated from developer services, data and credentials."""

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_playwright_dependency_and_script_are_locked() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))

    playwright_version = package["devDependencies"]["@playwright/test"]
    assert playwright_version.startswith("^1.")
    assert package["scripts"]["test:e2e"] == "playwright test"
    assert lock["packages"][""]["devDependencies"]["@playwright/test"] == playwright_version
    assert "node_modules/@playwright/test" in lock["packages"]


def test_playwright_uses_dedicated_loopback_ports_and_never_reuses_servers() -> None:
    source = (FRONTEND / "playwright.config.ts").read_text(encoding="utf-8")

    assert "baseURL: 'http://127.0.0.1:15173'" in source
    assert "cd .. && exec ${pythonCommand} -m uvicorn" in source
    assert "--host 127.0.0.1 --port 18080" in source
    assert "--host 127.0.0.1 --port 15173" in source
    assert "VITE_API_BASE_URL=http://127.0.0.1:18080" in source
    assert "VITE_AUTH_ENABLED=true" in source
    assert "VITE_USE_MOCK=false" in source
    assert source.count("reuseExistingServer: false") == 2
    assert "gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 }" in source
    assert "workers: 1" in source


def test_e2e_backend_is_explicitly_injected_and_uses_temporary_sqlite() -> None:
    source = (ROOT / "tests/e2e_server.py").read_text(encoding="utf-8")

    assert "_env_file=None" in source
    assert "DATABASE_URL=None" in source
    assert "DATABASE_TLS_CA=None" in source
    assert "sqlite+pysqlite:///" in source
    assert "tempfile.mkdtemp" in source
    assert "PRAGMA foreign_keys=ON" in source
    assert "Base.metadata.create_all(engine)" in source
    assert "generation_persistence=SQLAlchemyGenerationPersistence(" in source
    assert "auth_service=AuthService(" in source
    assert "model_service=DelayedE2EModelService()" in source
    assert "engine.dispose()" in source
    assert "shutil.rmtree(temporary_root" in source

    for forbidden in (
        "from backend.main import",
        "import backend.main",
        "get_settings(",
        "create_sqlalchemy_persistence_runtime(",
    ):
        assert forbidden not in source


def test_browser_flow_covers_cookie_loading_risk_history_and_deletion() -> None:
    source = (FRONTEND / "e2e/core-flow.spec.ts").read_text(encoding="utf-8")

    for evidence in (
        "httpOnly: true",
        "sameSite: 'Lax'",
        "正在分析图片并生成初稿",
        "发布前风险提示（非平台审核）",
        "image.naturalWidth",
        "已载入历史生成结果",
        "重新上传",
        "删除历史记录",
        "暂无历史记录",
        "退出",
    ):
        assert evidence in source


def test_browser_flow_covers_local_draft_versioning_and_history_isolation() -> None:
    source = (FRONTEND / "e2e/core-flow.spec.ts").read_text(encoding="utf-8")

    for evidence in (
        "getByLabel('标题', { exact: true })",
        "内容已在本地修改",
        "复制全部文案",
        "sessionStorage.getItem('e2e-copied-text')",
        "正在生成新版本",
        "当前稿件仍可查看和复制",
        "#previous-version-drawer",
        "复制上一版",
        "expect(historyTitles).not.toContain(editedTitle)",
    ):
        assert evidence in source

    assert ".title-text" not in source
    assert "toHaveValue(editedTitle)" in source
    assert "toContain(`标题：${editedTitle}`)" in source


def test_ci_runs_the_isolated_browser_suite() -> None:
    source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = source[source.index("  browser-e2e:"):source.index("  backend-container-smoke:")]

    assert "Browser E2E (Chrome, isolated SQLite)" in job
    assert 'python-version: "3.12"' in job
    assert 'node-version: "24"' in job
    assert "python -m pip install -e \".[dev]\"" in job
    assert "google-chrome --version" in job
    assert "E2E_PYTHON: python" in job
    assert "npm run test:e2e" in job
