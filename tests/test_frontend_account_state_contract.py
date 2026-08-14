"""Static regression checks for routed account state and cross-user isolation."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"
APP_VUE = FRONTEND_SRC / "App.vue"
AUTH_DIALOG = FRONTEND_SRC / "components" / "AuthDialog.vue"
AUTH_STATE = FRONTEND_SRC / "state" / "auth.ts"
WORKSPACE_STATE = FRONTEND_SRC / "state" / "workspace.ts"
GENERATE_VIEW = FRONTEND_SRC / "views" / "GenerateView.vue"


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"^(?P<indent>[ \t]*)(?:async\s+)?function {function_name}"
        rf"\([^)]*\)(?:\s*:\s*[^{{\n]+)?\s*\{{"
        rf"(?P<body>.*?)^(?P=indent)\}}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, function_name
    return match.group("body")


def test_app_consumes_the_central_auth_session_and_workspace_context() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    auth_source = AUTH_STATE.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")

    assert "export type AuthStatus" in auth_source
    for status in ("disabled", "restoring", "anonymous", "authenticated", "unavailable"):
        assert f"'{status}'" in auth_source
    assert "export const authSession = createAuthSession()" in auth_source
    assert "const authStatus = authSession.status" in app_source
    assert "const currentUser = authSession.user" in app_source
    assert "void authSession.ensureRestored()" in app_source

    assert "export interface WorkspaceContext" in workspace_source
    assert "provide(WORKSPACE_KEY" in app_source
    assert "<RouterView />" in app_source
    assert "activeView" not in app_source
    assert 'v-show="activeView' not in app_source


def test_account_revision_synchronously_clears_every_private_workspace_value() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    auth_source = AUTH_STATE.read_text(encoding="utf-8")
    reset = _function_body(app_source, "resetAccountScopedState")
    clear_result = _function_body(app_source, "clearGenerationResult")

    for required_clear in (
        "generationRequestId += 1",
        "historyRequestId += 1",
        "historyRevision += 1",
        "clearImage()",
        "clearGenerationResult()",
        "form.productName = ''",
        "form.targetAudience = ''",
        "form.tone = ''",
        "form.emojiLevel = 'light'",
        "form.relatedTags = true",
        "historyItems.value = []",
        "historyCount.value = 0",
        "historyError.value = ''",
        "historyLoaded.value = false",
        "historyDeleteTokens.clear()",
        "historyDeletingIds.value = new Set()",
        "historyStatus.value = 'idle'",
    ):
        assert required_clear in reset

    assert "currentVersion.value = null" in clear_result
    assert "previousVersion.value = null" in clear_result
    assert "status.value = 'idle'" in clear_result
    assert reset.index("generationRequestId += 1") < reset.index("clearImage()")
    assert reset.index("clearImage()") < reset.index("clearGenerationResult()")

    assert "watch(authRevision, resetAccountScopedState, { flush: 'sync' })" in app_source
    assert app_source.count("resetAccountScopedState") == 2

    install_user = _function_body(auth_source, "installUser")
    install_anonymous = _function_body(auth_source, "installAnonymous")
    expire = _function_body(auth_source, "expire")
    assert "user.value?.user_id !== nextUser.user_id" in install_user
    assert "revision.value += 1" in install_user
    assert "user.value !== null" in install_anonymous
    assert "revision.value += 1" in install_anonymous
    assert "revision.value += 1" in expire


def test_workspace_keeps_server_snapshots_and_local_drafts_account_scoped() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")
    clear_result = _function_body(app_source, "clearGenerationResult")
    copy_all = _function_body(app_source, "copyAll")

    for contract in (
        "export interface EditableGenerationDraft",
        "export interface SubmittedGenerationConfig",
        "export interface WorkspaceVersion",
        "server: GenerationResponse",
        "draft: EditableGenerationDraft",
        "currentVersion: Ref<WorkspaceVersion | null>",
        "previousVersion: Ref<WorkspaceVersion | null>",
        "configState: ComputedRef<GenerationConfigState>",
        "draftDirty: ComputedRef<boolean>",
    ):
        assert contract in workspace_source

    assert "currentVersion.value = null" in clear_result
    assert "previousVersion.value = null" in clear_result
    assert "void copyDraft(currentVersion.value.draft)" in copy_all


def test_delayed_generation_and_history_responses_are_session_versioned() -> None:
    source = APP_VUE.read_text(encoding="utf-8")
    generation = _function_body(source, "handleGenerate")
    history = _function_body(source, "loadHistory")

    for body, request_id in (
        (generation, "generationRequestId"),
        (history, "historyRequestId"),
    ):
        assert "requestedAuthRevision = authRevision.value" in body
        assert "requestedUserId = currentUser.value?.user_id ?? null" in body
        assert request_id in body
        assert "requestedAuthRevision !== authRevision.value" in body
        assert "requestedUserId !== (currentUser.value?.user_id ?? null)" in body

    stale_guard = history.index("requestId !== historyRequestId")
    revision_guard = history.index("requestedRevision !== historyRevision")
    assert stale_guard < revision_guard

    deletion = _function_body(source, "deleteHistoryItem")
    assert "requestedAuthRevision = authRevision.value" in deletion
    assert "requestedUserId = currentUser.value?.user_id ?? null" in deletion
    assert "historyDeleteTokens.get(generationId) !== token" in deletion
    assert "requestedAuthRevision !== authRevision.value" in deletion
    assert "requestedUserId !== (currentUser.value?.user_id ?? null)" in deletion


def test_delayed_file_reader_and_upload_queue_cannot_restore_old_account_data() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    view_source = GENERATE_VIEW.read_text(encoding="utf-8")
    image_change = _function_body(app_source, "handleImageChange")
    clear_image = _function_body(app_source, "clearImage")

    assert "let imageSelectionId = 0" in app_source
    assert "const selectionId = ++imageSelectionId" in image_change
    assert image_change.index("await detectImageFormat(raw)") < image_change.index(
        "if (selectionId !== imageSelectionId)"
    ) < image_change.index("imageFile.value = raw")
    assert "imageSelectionId += 1" in clear_image
    assert "imageResetEpoch.value += 1" in clear_image
    assert "clearImage()" in _function_body(app_source, "resetAccountScopedState")
    assert "uploadRef.value?.clearFiles()" in view_source

    unmount = re.search(
        r"onBeforeUnmount\(\(\) => \{(.*?)\n\}\)", app_source, flags=re.DOTALL
    )
    assert unmount is not None
    assert "imageSelectionId += 1" in unmount.group(1)


def test_session_expiry_invalidates_actions_and_releases_loading_before_revision() -> None:
    source = AUTH_STATE.read_text(encoding="utf-8")
    expired = _function_body(source, "expire")

    assert expired.index("actionId += 1") < expired.index(
        "busy.value = false"
    ) < expired.index("revision.value += 1")
    assert "user.value = null" in expired
    assert "status.value = 'anonymous'" in expired


def test_logout_changes_identity_only_after_server_confirms_revocation() -> None:
    source = AUTH_STATE.read_text(encoding="utf-8")
    logout = _function_body(source, "logout")

    assert logout.index("await logoutAccount()") < logout.index("installAnonymous()")
    catch_body = logout.split("} catch", 1)[1].split("} finally", 1)[0]
    assert "status.value = 'authenticated'" in catch_body
    assert "installAnonymous()" not in catch_body


def test_auth_dialog_keeps_password_ephemeral_and_describes_demo_email() -> None:
    source = AUTH_DIALOG.read_text(encoding="utf-8")

    assert 'type="password"' in source
    assert "'current-password'" in source
    assert 'autocomplete="new-password"' in source
    assert 'autocomplete="email"' in source
    assert "clearPasswords()" in source
    assert "form.email = ''" in source
    assert "邮箱仅作为本地登录标识" in source
    assert "不会发送激活邮件" in source
    assert "不会把账号标记为已验证" in source
    assert "v-html" not in source


def test_frontend_never_reads_cookie_or_persists_auth_secrets() -> None:
    frontend_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FRONTEND_SRC.rglob("*")
        if path.suffix in {".ts", ".vue"}
    )

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "Authorization",
        "raw_token",
    ):
        assert forbidden not in frontend_sources

    template = APP_VUE.read_text(encoding="utf-8").split("<template>", 1)[1]
    assert "currentUser.user_id" not in template
