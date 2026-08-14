"""Focused contracts for advisory pre-publication content risk hints."""

from dataclasses import FrozenInstanceError

import pytest

from backend.services.model.content_risk import (
    CONTENT_RISK_RULE_VERSION,
    ContentIndustry,
    ContentRiskScanner,
    ContentScenario,
    scan_content_risks,
)
from backend.services.model.types import GeneratedCopy


def _copy(
    *,
    image_summary: str = "图片中可见一件日常物品。",
    title: str = "日常记录",
    body: str = "记录图片中可见的颜色和外观。",
    tags: tuple[str, ...] = ("#日常记录", "#图片分享", "#生活观察"),
) -> GeneratedCopy:
    return GeneratedCopy(
        image_summary=image_summary,
        title=title,
        body=body,
        tags=tags,
    )


def test_model_package_exports_public_scanner_contract() -> None:
    from backend.services import model

    assert model.CONTENT_RISK_RULE_VERSION == CONTENT_RISK_RULE_VERSION
    assert model.ContentRiskScanner is ContentRiskScanner
    assert model.scan_content_risks is scan_content_risks


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ("这是全网第一的产品。", "absolute_or_superiority_claim"),
        ("保证百分之百有效。", "guaranteed_outcome_claim"),
        ("这款产品可以根治问题。", "medical_or_health_claim"),
        ("想了解详情就加微信。", "off_platform_diversion"),
        ("微信号abc123，备注来意。", "off_platform_diversion"),
        ("想看更多就+V。", "off_platform_diversion"),
        ("主页有微。", "off_platform_diversion"),
        ("头像有微，欢迎来聊。", "off_platform_diversion"),
        ("主页联系我。", "off_platform_diversion"),
        ("这是最佳选择。", "absolute_or_superiority_claim"),
        ("采用最高级技术。", "absolute_or_superiority_claim"),
        ("国家级产品。", "absolute_or_superiority_claim"),
        ("使用起来绝对安全。", "guaranteed_outcome_claim"),
        ("号称零副作用。", "guaranteed_outcome_claim"),
        ("可以改善睡眠。", "medical_or_health_claim"),
        ("帮助快速减脂瘦身。", "medical_or_health_claim"),
        ("点赞后即可领取资料。", "engagement_inducement"),
        (
            "这是小红书官方推荐的产品。",
            "unverified_authority_or_platform_endorsement",
        ),
        ("其他品牌都是垃圾。", "competitor_disparagement"),
    ],
)
def test_scanner_covers_initial_advisory_rule_families(
    body: str,
    expected_code: str,
) -> None:
    copy = _copy(body=body)

    findings = scan_content_risks(copy)

    assert expected_code in {finding.code for finding in findings}
    finding = next(item for item in findings if item.code == expected_code)
    assert finding.field == "body"
    assert 0 <= finding.start < finding.end <= len(body)
    assert finding.rule_version == CONTENT_RISK_RULE_VERSION
    assert body[finding.start : finding.end]


def test_generic_traffic_tag_is_a_low_advisory_hint() -> None:
    copy = _copy(tags=("#湖边散步", "#小红书爆款", "#周末记录"))

    findings = scan_content_risks(copy)

    assert findings == (
        next(
            finding
            for finding in findings
            if finding.code == "generic_traffic_tag"
        ),
    )
    finding = findings[0]
    assert finding.severity == "low"
    assert finding.field == "tags"
    tag_start = len("#湖边散步 ")
    assert (finding.start, finding.end) == (
        tag_start,
        tag_start + len("#小红书爆款"),
    )


def test_findings_are_frozen_and_returned_as_a_tuple() -> None:
    findings = scan_content_risks(_copy(body="保证一定成功。"))

    assert isinstance(findings, tuple)
    with pytest.raises(FrozenInstanceError):
        findings[0].severity = "low"  # type: ignore[misc]


def test_scanner_never_mutates_generated_copy() -> None:
    copy = _copy(body="点赞后领取资料。")
    before = copy.model_dump()

    scan_content_risks(copy)

    assert copy.model_dump() == before


def test_image_summary_is_evidence_only_and_is_not_scanned_for_publish_risk() -> None:
    copy = _copy(image_summary="图片文字写着全网第一并保证有效。")

    assert scan_content_risks(copy) == ()


def test_findings_do_not_copy_private_matched_content() -> None:
    private_email = "private-person-42@example.com"
    findings = scan_content_risks(_copy(body=f"详情请联系 {private_email}"))

    finding = next(
        item for item in findings if item.code == "off_platform_diversion"
    )
    assert private_email not in repr(finding)
    assert private_email not in finding.reason
    assert private_email not in finding.suggestion
    assert (finding.start, finding.end) == (
        len("详情请联系 "),
        len("详情请联系 ") + len(private_email),
    )


@pytest.mark.parametrize(
    "body",
    [
        "这是我最喜欢的一张湖景照片。",
        "图片无法确认是否具有治疗效果。",
        "本产品不能治疗疾病。",
        "欢迎分享你的真实体验。",
        "请参考小红书社区规范后再发布。",
        "详情见 https://www.xiaohongshu.com/explore/example",
        "请勿通过微信联系。",
        "谨防有人叫你加微信。",
        "不要相信小红书官方推荐的说法。",
        "请举报其他品牌都是垃圾的言论。",
        "这个项目使用 Vue 3 + Vite。",
        "今天拍了一段 +Vlog。",
        "示例代码包含 C++ vector。",
    ],
)
def test_scanner_avoids_common_context_free_false_positives(body: str) -> None:
    assert scan_content_risks(_copy(body=body)) == ()


@pytest.mark.parametrize(
    "url",
    [
        "https://notxiaohongshu.com/example",
        "https://evil.example/?next=xiaohongshu.com",
    ],
)
def test_lookalike_or_query_urls_are_not_treated_as_xhs_hosts(url: str) -> None:
    findings = scan_content_risks(_copy(body=f"详情见 {url}"))

    assert any(item.code == "off_platform_diversion" for item in findings)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.xiaohongshu.com/explore/example",
        "https://sns-img-qc.xhscdn.com/example.webp",
    ],
)
def test_exact_xhs_hosts_and_subdomains_are_not_flagged_as_off_platform(url: str) -> None:
    findings = scan_content_risks(_copy(body=f"站内内容链接：{url}"))

    assert not any(item.code == "off_platform_diversion" for item in findings)


def test_commercial_and_sensitive_contexts_raise_advisory_severity() -> None:
    copy = _copy(body="这是行业第一，并且具有抑菌效果。")

    ordinary = scan_content_risks(copy)
    commercial = scan_content_risks(
        copy,
        scenario=ContentScenario.BRAND_COLLABORATION,
        industry=ContentIndustry.BEAUTY_PERSONAL_CARE,
    )

    ordinary_by_code = {finding.code: finding for finding in ordinary}
    commercial_by_code = {finding.code: finding for finding in commercial}
    assert ordinary_by_code["absolute_or_superiority_claim"].severity == "medium"
    assert ordinary_by_code["medical_or_health_claim"].severity == "medium"
    assert commercial_by_code["absolute_or_superiority_claim"].severity == "high"
    assert commercial_by_code["medical_or_health_claim"].severity == "high"


def test_default_context_is_conservative_and_explicitly_unspecified() -> None:
    scanner = ContentRiskScanner()
    copy = _copy(body="保证有效。")

    implicit = scanner.scan(copy)
    explicit = scanner.scan(
        copy,
        scenario="unspecified",
        industry="unspecified",
    )

    assert implicit == explicit
    assert scanner.rule_version == CONTENT_RISK_RULE_VERSION


@pytest.mark.parametrize(
    ("keyword", "message"),
    [
        ("private-scenario-value", "unsupported content scenario"),
        ("private-industry-value", "unsupported content industry"),
    ],
)
def test_invalid_context_errors_do_not_echo_the_supplied_value(
    keyword: str,
    message: str,
) -> None:
    scanner = ContentRiskScanner()
    copy = _copy()

    with pytest.raises(ValueError) as error:
        if "scenario" in message:
            scanner.scan(copy, scenario=keyword)
        else:
            scanner.scan(copy, industry=keyword)

    assert str(error.value) == message
    assert keyword not in str(error.value)


def test_normalization_preserves_original_offsets_for_obfuscated_text() -> None:
    body = "详情请加 Ｖ\u200b信 联系。"

    findings = scan_content_risks(_copy(body=body))

    finding = next(
        item for item in findings if item.code == "off_platform_diversion"
    )
    assert finding.field == "body"
    assert body[finding.start : finding.end] == "加 Ｖ\u200b信"


def test_results_are_deterministic_sorted_and_deduplicated() -> None:
    copy = _copy(
        image_summary="图片文字写着全网第一。",
        title="保证一定成功",
        body="加微信，点赞后领取资料。",
        tags=("#日常", "#爆款", "#分享"),
    )

    first = scan_content_risks(copy)
    second = scan_content_risks(copy)

    assert first == second
    assert [finding.field for finding in first] == [
        "title",
        "body",
        "body",
        "tags",
    ]
    identities = {
        (finding.code, finding.field, finding.start, finding.end)
        for finding in first
    }
    assert len(identities) == len(first)
