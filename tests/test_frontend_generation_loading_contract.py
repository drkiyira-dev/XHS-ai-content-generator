"""Static regression checks for generation, draft, and version state."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
GENERATE_VIEW = ROOT / "frontend" / "src" / "views" / "GenerateView.vue"
WORKSPACE_STATE = ROOT / "frontend" / "src" / "state" / "workspace.ts"
APP = ROOT / "frontend" / "src" / "App.vue"


def _source() -> str:
    return GENERATE_VIEW.read_text(encoding="utf-8")


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


def _initial_skeleton_block(source: str) -> str:
    marker = source.index('data-testid="initial-generation-skeleton"')
    start = source.rfind("<div", 0, marker)
    end = source.index('<div v-else-if="!currentVersion"', marker)
    return source[start:end]


def test_first_generation_uses_an_honest_complete_result_skeleton() -> None:
    source = _source()
    loading = _initial_skeleton_block(source)

    assert 'v-if="status === \'loading\' && !hasResult"' in loading
    assert 'data-testid="initial-generation-skeleton"' in loading
    assert 'class="result-skeleton-panel"' in loading
    assert 'role="status"' in loading
    assert 'aria-live="polite"' in loading
    assert 'aria-atomic="true"' in loading
    assert 'class="result-skeleton" aria-hidden="true"' in loading
    assert "正在分析图片并生成初稿" in loading
    assert "结果会在完成格式与内容检查后一次性展示" in loading

    for result_field in ("图片理解摘要", "标题", "正文", "话题标签"):
        assert result_field in loading
    assert loading.count('class="skeleton-shape skeleton-tag"') == 4

    for fake_progress_marker in (
        "<el-progress",
        "aria-valuenow",
        "生成进度",
        "已完成百分比",
    ):
        assert fake_progress_marker not in loading


def test_generation_disables_duplicate_submission_without_hiding_an_old_draft() -> None:
    view_source = _source()
    app_source = APP.read_text(encoding="utf-8")
    generate = _function_body(app_source, "handleGenerate")
    before_response = generate[: generate.index("const data = await generate(formData)")]

    assert "if (isGenerating.value)" in generate
    assert ':disabled="!imageFile || isGenerating"' in view_source
    assert 'v-if="status === \'loading\' && !hasResult"' in view_source
    assert 'v-if="isRegenerating"' in view_source
    assert 'class="regeneration-banner" role="status"' in view_source
    assert 'v-model="currentVersion.draft.title"' in view_source
    assert 'v-model="currentVersion.draft.body"' in view_source
    assert "@click=\"copyAll\"" in view_source

    assert "status.value = 'loading'" in before_response
    for destructive_write in (
        "clearGenerationResult()",
        "currentVersion.value =",
        "previousVersion.value =",
    ):
        assert destructive_write not in before_response


def test_success_rotates_versions_only_after_every_stale_response_guard() -> None:
    source = APP.read_text(encoding="utf-8")
    generate = _function_body(source, "handleGenerate")

    response = generate.index("const data = await generate(formData)")
    rotation = generate.index(
        "previousVersion.value = cloneWorkspaceVersion(currentVersion.value)",
        response,
    )
    install = generate.index(
        "currentVersion.value = createWorkspaceVersion(data, submitted, 'generated')",
        rotation,
    )

    assert response < rotation < install
    for stale_guard in (
        "requestId !== generationRequestId",
        "requestedAuthRevision !== authRevision.value",
        "requestedUserId !== (currentUser.value?.user_id ?? null)",
    ):
        assert response < generate.index(stale_guard, response) < rotation

    assert generate.index("const submitted = currentSubmittedConfig()") < response
    assert generate.index("status.value = 'success'", install) > install


def test_failed_regeneration_preserves_current_and_previous_versions() -> None:
    app_source = APP.read_text(encoding="utf-8")
    view_source = _source()
    generate = _function_body(app_source, "handleGenerate")
    catch_body = generate.split("} catch (err: unknown) {", 1)[1]

    assert "status.value = currentVersion.value === null ? 'error' : 'success'" in catch_body
    assert "errorMessage.value = err.message" in catch_body
    for destructive_write in (
        "clearGenerationResult()",
        "currentVersion.value = createWorkspaceVersion",
        "currentVersion.value = null",
        "previousVersion.value =",
    ):
        assert destructive_write not in catch_body

    assert 'class="generation-safety-note"' in view_source
    assert 'v-if="errorMessage"' in view_source


def test_server_snapshot_and_editable_draft_are_separate_contracts() -> None:
    app_source = APP.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")
    view_source = _source()
    create_version = _function_body(app_source, "createWorkspaceVersion")
    copy_all = _function_body(app_source, "copyAll")
    restore_draft = _function_body(app_source, "restoreGeneratedDraft")

    assert "export interface EditableGenerationDraft" in workspace_source
    assert "export interface WorkspaceVersion" in workspace_source
    assert "server: GenerationResponse" in workspace_source
    assert "draft: EditableGenerationDraft" in workspace_source

    assert "const server = cloneGenerationResponse(response)" in create_version
    for field in ("title", "body", "tags"):
        assert f"{field}: server.{field}" in create_version or (
            field == "tags" and "tags: [...server.tags]" in create_version
        )
    assert "void copyDraft(currentVersion.value.draft)" in copy_all
    assert "currentVersion.value.server.title" in restore_draft
    assert "currentVersion.value.server.body" in restore_draft
    assert "tags: [...currentVersion.value.server.tags]" in restore_draft

    assert "currentVersion.server.image_summary" in view_source
    assert "currentVersion.server.risk_assessment" in view_source
    assert 'v-model="currentVersion.draft.title"' in view_source
    assert 'v-model="currentVersion.draft.body"' in view_source
    assert 'v-model="currentVersion.draft.tags[index]"' in view_source
    assert 'v-if="riskSnapshotStale"' in view_source
    assert 'class="draft-stale-note"' in view_source


def test_submitted_config_snapshot_drives_clean_dirty_and_unknown_states() -> None:
    app_source = APP.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")
    view_source = _source()
    submitted_config = _function_body(app_source, "currentSubmittedConfig")
    compare_config = _function_body(app_source, "sameSubmittedConfig")
    generate = _function_body(app_source, "handleGenerate")

    assert "export interface SubmittedGenerationConfig extends WorkspaceForm" in workspace_source
    assert "imageToken: number" in workspace_source
    assert "submitted: SubmittedGenerationConfig | null" in workspace_source
    assert "export type GenerationConfigState = 'none' | 'clean' | 'dirty' | 'unknown'" in workspace_source

    for field in (
        "imageToken",
        "productName",
        "targetAudience",
        "tone",
        "emojiLevel",
        "relatedTags",
    ):
        assert field in submitted_config
        assert f"left.{field} === right.{field}" in compare_config

    snapshot = generate.index("const submitted = currentSubmittedConfig()")
    request = generate.index("const data = await generate(formData)")
    install = generate.index(
        "currentVersion.value = createWorkspaceVersion(data, submitted, 'generated')"
    )
    assert snapshot < request < install
    assert "currentVersion.value.submitted === null" in app_source
    assert "sameSubmittedConfig(" in app_source
    assert 'v-if="configState === \'dirty\'"' in view_source
    assert 'class="config-callout"' in view_source
    assert 'aria-live="polite"' in view_source
    assert 'v-else-if="configState === \'unknown\'"' in view_source


def test_clearing_or_replacing_an_image_invalidates_an_inflight_request() -> None:
    source = APP.read_text(encoding="utf-8")
    image_change = _function_body(source, "handleImageChange")
    clear_image = _function_body(source, "clearImage")

    assert "generationRequestId += 1" in image_change
    assert "generationRequestId += 1" in clear_image


def test_validated_result_reveal_respects_reduced_motion() -> None:
    source = _source()

    assert ".result--ready {" in source
    assert "animation: result-enter 320ms" in source
    assert "@keyframes result-enter" in source
    assert "@keyframes skeleton-pulse" in source

    reduced_start = source.index("@media (prefers-reduced-motion: reduce)")
    reduced_end = source.index("@media (max-width: 1180px)", reduced_start)
    reduced_motion = source[reduced_start:reduced_end]
    for animated_element in (
        ".result--ready",
        ".result-loading-mark",
        ".skeleton-shape",
    ):
        assert animated_element in reduced_motion
    assert "animation: none" in reduced_motion
