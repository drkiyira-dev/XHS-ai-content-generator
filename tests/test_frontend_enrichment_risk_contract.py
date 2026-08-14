"""Static contracts for enrichment controls and bounded risk disclosures."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"
APP_VUE = FRONTEND / "App.vue"
GENERATION_SERVICE = FRONTEND / "services" / "generation.ts"
WORKSPACE_STATE = FRONTEND / "state" / "workspace.ts"
GENERATE_VIEW = FRONTEND / "views" / "GenerateView.vue"
HISTORY_VIEW = FRONTEND / "views" / "HistoryView.vue"


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


def test_request_controls_use_frozen_values_and_honest_labels() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")
    view_source = GENERATE_VIEW.read_text(encoding="utf-8")
    generate = _function_body(app_source, "handleGenerate")
    submitted_config = _function_body(app_source, "currentSubmittedConfig")

    assert "export type EmojiLevel = 'off' | 'light' | 'expressive'" in (
        GENERATION_SERVICE.read_text(encoding="utf-8")
    )
    assert "emojiLevel: EmojiLevel" in workspace_source
    assert "relatedTags: boolean" in workspace_source
    assert "emojiLevel: 'light'" in app_source
    assert "relatedTags: true" in app_source
    assert "const submitted = currentSubmittedConfig()" in generate
    assert "imageToken: imageToken.value" in submitted_config
    assert "productName: form.productName.trim()" in submitted_config
    assert "targetAudience: form.targetAudience.trim()" in submitted_config
    assert "tone: form.tone.trim()" in submitted_config
    assert "emojiLevel: form.emojiLevel" in submitted_config
    assert "relatedTags: form.relatedTags" in submitted_config
    assert "formData.append('emoji_level', submitted.emojiLevel)" in generate
    assert (
        "formData.append('related_tags', submitted.relatedTags ? 'true' : 'false')"
        in generate
    )

    assert "Emoji 风格" in view_source
    assert "本地词库相关标签" in view_source
    assert "不读取平台热门榜或实时趋势" in view_source
    for level in ("off", "light", "expressive"):
        assert f'value="{level}"' in view_source
    assert 'v-model="form.emojiLevel"' in view_source
    assert 'v-model="form.relatedTags"' in view_source
    assert 'aria-labelledby="emoji-style-label"' in view_source


def test_generation_and_history_risk_payloads_are_exact_and_bounded() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    parse_generation = _function_body(source, "parseGenerationResponse")
    parse_risk = _function_body(source, "parseRiskAssessment")
    parse_finding = _function_body(source, "parseRiskFinding")
    parse_history_item = _function_body(source, "parseHistoryItem")

    for public_contract in (
        "export interface RiskFinding",
        "export interface RiskAssessment",
        "risk_assessment: RiskAssessment",
        "severity: RiskSeverity",
        "field: RiskField",
    ):
        assert public_contract in source

    assert "hasExactKeys(value, GENERATION_RESPONSE_KEYS)" in parse_generation
    assert "hasExactKeys(value, RISK_ASSESSMENT_KEYS)" in parse_risk
    assert "isBoundedString(value.rule_version, 1, 128)" in parse_risk
    assert "value.findings.length > MAX_RISK_FINDINGS" in parse_risk
    assert "value.findings.map(finding => parseRiskFinding(finding))" in parse_risk
    assert "hasExactKeys(value, RISK_FINDING_KEYS)" in parse_finding
    assert "isBoundedString(value.code, 1, 128)" in parse_finding
    assert "isRiskSeverity(value.severity)" in parse_finding
    assert "isRiskField(value.field)" in parse_finding
    assert "isBoundedString(value.reason, 1, 1000)" in parse_finding
    assert "isBoundedString(value.suggestion, 1, 1000)" in parse_finding
    assert "hasExactKeys(value, GENERATION_HISTORY_ITEM_KEYS)" in parse_history_item
    assert "parseGenerationFields(value)" in parse_history_item

    for allowed in ("'low'", "'medium'", "'high'", "'title'", "'body'", "'tags'"):
        assert allowed in source


def test_workspace_versions_separate_server_snapshot_from_editable_draft() -> None:
    generation_source = GENERATION_SERVICE.read_text(encoding="utf-8")
    app_source = APP_VUE.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")
    reset = _function_body(app_source, "resetAccountScopedState")
    restore = _function_body(app_source, "restoreHistoryItem")
    clear_result = _function_body(app_source, "clearGenerationResult")
    create_version = _function_body(app_source, "createWorkspaceVersion")
    clone_server = _function_body(app_source, "cloneGenerationResponse")
    clone_draft = _function_body(app_source, "cloneDraft")

    assert "rule_version: 'mock-risk-v1'" in generation_source
    assert re.search(r"rule_version: 'mock-risk-v1',\s*findings: \[\]", generation_source)

    for contract in (
        "export interface EditableGenerationDraft",
        "export interface WorkspaceVersion",
        "server: GenerationResponse",
        "draft: EditableGenerationDraft",
        "submitted: SubmittedGenerationConfig | null",
        "source: 'generated' | 'history'",
    ):
        assert contract in workspace_source

    assert "const server = cloneGenerationResponse(response)" in create_version
    assert "title: server.title" in create_version
    assert "body: server.body" in create_version
    assert "tags: [...server.tags]" in create_version
    assert "submitted: submitted === null ? null : { ...submitted }" in create_version
    assert "risk_assessment:" in clone_server
    assert "value.risk_assessment.findings.map" in clone_server
    assert "image_summary" not in clone_draft
    assert "risk_assessment" not in clone_draft
    assert "const riskSnapshotStale = computed(() => draftDirty.value)" in app_source

    assert "currentVersion.value = null" in clear_result
    assert "previousVersion.value = null" in clear_result
    assert "form.emojiLevel = 'light'" in reset
    assert "form.relatedTags = true" in reset

    assert "generation.risk_assessment.rule_version" in restore
    assert "generation.risk_assessment.findings.map" in restore
    assert "currentVersion.value = createWorkspaceVersion({" in restore
    assert "}, null, 'history')" in restore
    assert restore.index("generationRequestId += 1") < restore.index(
        "risk_assessment:"
    )


def test_current_and_history_views_label_risk_as_advisory_only() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    generate_source = GENERATE_VIEW.read_text(encoding="utf-8")
    history_source = HISTORY_VIEW.read_text(encoding="utf-8")
    copy_all = _function_body(app_source, "copyAll")
    copy_draft = _function_body(app_source, "copyDraft")

    assert "发布前风险提示（非平台审核）" in generate_source
    assert "不能代表平台审核，也不能预测限流或处罚" in generate_source
    assert "currentVersion.server.risk_assessment.findings.length" in generate_source
    risk_position = generate_source.index('<section class="risk-overview"')
    copy_position = generate_source.index('@click="copyAll"', risk_position)
    editor_position = generate_source.index('id="draft-title"', copy_position)
    assert risk_position < copy_position < editor_position
    assert 'v-if="riskSnapshotStale"' in generate_source
    assert "风险提示仍对应生成时版本，编辑后的文字尚未重新检测" in generate_source
    assert "也不会自动写回历史记录" in generate_source
    for field in (
        "finding.code",
        "finding.severity",
        "finding.field",
        "finding.reason",
        "finding.suggestion",
    ):
        assert field in generate_source
    assert "v-html" not in generate_source

    assert "currentVersion.value.draft" in copy_all
    for publishable_field in ("draft.title", "draft.body", "draft.tags.join(' ')"):
        assert publishable_field in copy_draft
    assert "draft.image_summary" not in copy_draft
    assert "draft.risk_assessment" not in copy_draft

    assert "item.risk_assessment.findings.length" in history_source
    assert "发布前风险提示" in history_source
    assert "非平台审核" in history_source
    assert "@click=\"restoreHistoryItem(item)\"" in history_source


def test_new_contract_preserves_generation_and_account_stale_guards() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    generate = _function_body(app_source, "handleGenerate")

    assert generate.index("const requestId = ++generationRequestId") < generate.index(
        "const data = await generate(formData)"
    )
    assert "requestId !== generationRequestId" in generate
    assert "requestedAuthRevision !== authRevision.value" in generate
    assert "requestedUserId !== (currentUser.value?.user_id ?? null)" in generate
    assert "currentVersion.value = createWorkspaceVersion(data, submitted, 'generated')" in generate
    assert "envelope?.error?.message" not in (
        FRONTEND / "services" / "api.ts"
    ).read_text(encoding="utf-8")
