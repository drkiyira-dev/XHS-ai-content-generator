"""Static contracts for Vue Router paths, guards, and workspace wiring."""

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
APP_VUE = FRONTEND / "src" / "App.vue"
MAIN_TS = FRONTEND / "src" / "main.ts"
ROUTER_TS = FRONTEND / "src" / "router.ts"
GENERATE_VIEW = FRONTEND / "src" / "views" / "GenerateView.vue"
HISTORY_VIEW = FRONTEND / "src" / "views" / "HistoryView.vue"
HOME_VIEW = FRONTEND / "src" / "views" / "HomeView.vue"
LANDING_VIEW = FRONTEND / "src" / "components" / "LandingPage.vue"


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"^(?P<indent>[ \t]*)(?:export\s+)?(?:async\s+)?function {function_name}"
        rf"\([^)]*\)(?:\s*:\s*[^{{\n]+)?\s*\{{"
        rf"(?P<body>.*?)^(?P=indent)\}}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, function_name
    return match.group("body")


def _route_block(source: str, path: str) -> str:
    match = re.search(
        rf"  \{{\n    path: {re.escape(repr(path))},(?P<body>.*?)\n  \}}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, path
    return match.group("body")


def test_vue_router_is_a_locked_runtime_dependency_and_is_installed_before_mount() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    main_source = MAIN_TS.read_text(encoding="utf-8")

    declared = package["dependencies"]["vue-router"]
    assert declared.lstrip("^~").startswith("4.")
    assert "vue-router" not in package.get("devDependencies", {})
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["dependencies"]["vue-router"] == declared
    assert lock["packages"]["node_modules/vue-router"]["version"].startswith("4.")

    assert "import router from './router'" in main_source
    assert main_source.index("app.use(router)") < main_source.index("app.mount('#app')")


def test_router_exposes_exact_public_and_private_page_paths() -> None:
    source = ROUTER_TS.read_text(encoding="utf-8")

    declared_paths = re.findall(r"^    path: '([^']+)'", source, flags=re.MULTILINE)
    assert declared_paths == [
        "/",
        "/app/generate",
        "/app/history",
        "/login",
        "/register",
        "/:pathMatch(.*)*",
    ]

    home = _route_block(source, "/")
    generate = _route_block(source, "/app/generate")
    history = _route_block(source, "/app/history")
    assert "name: 'home'" in home and "component: HomeView" in home
    assert "requiresAuth" not in home
    assert "name: 'generate'" in generate
    assert "import('./views/GenerateView.vue')" in generate
    assert "meta: { requiresAuth: true }" in generate
    assert "name: 'history'" in history
    assert "import('./views/HistoryView.vue')" in history
    assert "meta: { requiresAuth: true }" in history
    assert "history: createWebHistory(import.meta.env.BASE_URL)" in source
    assert "redirect: '/'" in _route_block(source, "/:pathMatch(.*)*")


def test_router_guard_is_opt_in_awaits_restore_and_uses_only_safe_destinations() -> None:
    source = ROUTER_TS.read_text(encoding="utf-8")
    safe_next = _function_body(source, "readSafeNext")
    guard_match = re.search(
        r"router\.beforeEach\(async \(to\) => \{(?P<body>.*?)\n\}\)",
        source,
        flags=re.DOTALL,
    )
    assert guard_match is not None
    guard = guard_match.group("body")

    assert "value === 'generate' || value === 'history'" in safe_next
    assert "if (!AUTH_ENABLED)" in guard
    assert guard.index("if (!AUTH_ENABLED)") < guard.index("if (to.meta.requiresAuth)")
    protected = guard.split("if (to.meta.requiresAuth)", 1)[1].split(
        "if (isAuthRoute)", 1
    )[0]
    assert protected.index("await authSession.ensureRestored()") < protected.index(
        "authSession.status.value === 'authenticated'"
    )
    assert "name: 'login'" in protected
    assert "query: { next }" in protected
    assert "readSafeNext(to.query.next) ?? 'generate'" in guard
    assert "void authSession.ensureRestored()" in guard

    for forbidden in ("to.fullPath", "user_id", "localStorage", "sessionStorage"):
        assert forbidden not in source


def test_app_shell_routes_without_the_removed_active_view_switch() -> None:
    source = APP_VUE.read_text(encoding="utf-8")
    template = source.split("<template>", 1)[1]

    assert "<RouterView />" in template
    for name in ("home", "generate", "history", "login", "register"):
        assert f":to=\"{{ name: '{name}' }}\"" in template
    assert "activeView" not in source
    assert 'v-show="activeView' not in source
    assert "watch(authRevision, resetAccountScopedState, { flush: 'sync' })" in source


def test_successful_auth_response_cannot_leave_an_authenticated_dialog_stranded() -> None:
    source = APP_VUE.read_text(encoding="utf-8")
    submit = _function_body(source, "handleAuthSubmit")

    assert "route.fullPath !== submittedRoute" in submit
    changed_route = submit.split("route.fullPath !== submittedRoute", 1)[1]
    assert "route.name === 'login' || route.name === 'register'" in changed_route
    assert "router.replace({ name: authDestination() })" in changed_route
    assert changed_route.index("route.name === 'login'") < changed_route.index(
        "router.replace({ name: authDestination() })"
    )


def test_dialog_close_event_cannot_override_a_successful_private_route() -> None:
    source = APP_VUE.read_text(encoding="utf-8")
    close_dialog = _function_body(source, "closeAuthDialog")

    route_guard = "route.name !== 'login' && route.name !== 'register'"
    assert route_guard in close_dialog
    assert close_dialog.index(route_guard) < close_dialog.index(
        "router.replace({ name: 'home' })"
    )


def test_each_routed_page_has_one_programmatically_focusable_title() -> None:
    sources = {
        "home": LANDING_VIEW.read_text(encoding="utf-8"),
        "generate": GENERATE_VIEW.read_text(encoding="utf-8"),
        "history": HISTORY_VIEW.read_text(encoding="utf-8"),
    }
    home_wrapper = HOME_VIEW.read_text(encoding="utf-8")

    assert '<LandingPage @start="startGeneration" />' in home_wrapper
    for name, source in sources.items():
        assert source.count("<h1") == 1, name
        assert '<h1 id="page-title"' in source, name
        assert 'tabindex="-1"' in source, name
        assert 'aria-labelledby="page-title"' in source, name

    app_source = APP_VUE.read_text(encoding="utf-8")
    assert 'href="#page-title"' in app_source
    assert "document.getElementById('page-title')?.focus" in app_source
    assert "previousRouteName !== undefined && previousRouteName !== name" in app_source
    assert "if (shouldFocusPageTitle && name !== 'login' && name !== 'register')" in app_source
