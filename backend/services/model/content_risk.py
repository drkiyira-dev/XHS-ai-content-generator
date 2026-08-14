"""Advisory, source-versioned risk hints for generated publishable copy.

This module deliberately does not predict platform distribution, reproduce an
official moderation system, or certify legal compliance.  It returns immutable
findings for human review and never mutates the generated copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Literal
import unicodedata
from urllib.parse import urlsplit

from backend.services.model.types import GeneratedCopy


CONTENT_RISK_RULE_VERSION = "content-risk-hints-2026-08-14.2"

RiskSeverity = Literal["low", "medium", "high"]
ContentRiskField = Literal["title", "body", "tags"]


class ContentScenario(StrEnum):
    """Publishing context used only to tune advisory severity."""

    UNSPECIFIED = "unspecified"
    ORGANIC_NOTE = "organic_note"
    BRAND_COLLABORATION = "brand_collaboration"
    PAID_AD = "paid_ad"


class ContentIndustry(StrEnum):
    """Content category used only to tune advisory severity."""

    UNSPECIFIED = "unspecified"
    GENERAL = "general"
    BEAUTY_PERSONAL_CARE = "beauty_personal_care"
    FOOD_HEALTH = "food_health"
    MEDICAL_HEALTH = "medical_health"
    EDUCATION = "education"
    FINANCE = "finance"


@dataclass(frozen=True, slots=True)
class ContentRiskFinding:
    """One immutable, non-authoritative risk hint.

    ``start`` and ``end`` are offsets into ``field``'s original value.  The
    ``tags`` value is the tags joined with one ASCII space in their original
    order.  The finding intentionally excludes the matched text so it can be
    logged without copying user content.
    """

    code: str
    severity: RiskSeverity
    field: ContentRiskField
    start: int
    end: int
    reason: str
    suggestion: str
    rule_version: str


@dataclass(frozen=True, slots=True)
class _Rule:
    code: str
    severity: RiskSeverity
    patterns: tuple[re.Pattern[str], ...]
    reason: str
    suggestion: str


_COMMON_RULES: tuple[_Rule, ...] = (
    _Rule(
        code="absolute_or_superiority_claim",
        severity="medium",
        patterns=(
            re.compile(
                r"(?:全网|全国|全球|行业|品类)"
                r"(?:第一|最好|最佳|最强|首选|顶级)"
            ),
            re.compile(r"(?:销量|口碑)(?:第一|冠军)"),
            re.compile(
                r"(?:唯一|独一无二|无可替代)"
                r"(?:选择|首选|产品|品牌|方案)"
            ),
            re.compile(r"(?:产品|品牌|方案)(?:最好|最佳|最强|顶级)"),
            re.compile(r"(?:最佳|首选)(?:选择|方案)"),
            re.compile(r"最高级(?:技术|工艺|品质|标准)"),
            re.compile(r"(?:国家级|世界级)(?:产品|品牌|技术|品质|标准)"),
            re.compile(r"(?:100%|百分之百)(?:有效|安全|成功|满意|无误)"),
        ),
        reason="绝对化或市场地位表述需要结合语境和可验证依据人工判断。",
        suggestion="改为有范围、有条件的客观描述，并核对可证明的依据。",
    ),
    _Rule(
        code="guaranteed_outcome_claim",
        severity="high",
        patterns=(
            re.compile(
                r"(?:保证|确保|承诺)(?:百分之百|100%|一定|绝对)?"
                r"(?:有效|见效|成功|通过|改善|治愈|赚钱|收益|满意|无副作用)"
            ),
            re.compile(
                r"包过|包治|稳赚不赔|保本稳赚|零风险|无风险回报|"
                r"永久有效|永不反弹|绝对安全|零副作用"
            ),
        ),
        reason="结果保证或零风险承诺可能构成夸大、误导性表达。",
        suggestion="删除保证性承诺，只保留能够证明的条件、事实和适用范围。",
    ),
    _Rule(
        code="medical_or_health_claim",
        severity="medium",
        patterns=(
            re.compile(
                r"治疗|治愈|根治|疗效|消炎|抗炎|抑菌|抗菌|杀菌|"
                r"药用|医美级|医学级|临床验证|皮肤科推荐|"
                r"提高免疫力|增强免疫力|降血压|降血糖|防癌|抗癌|"
                r"改善睡眠|减脂瘦身|快速瘦身|药到病除|立竿见影"
            ),
        ),
        reason="医疗、保健或功效表达通常需要资质、品类和证据支持。",
        suggestion="避免将产品与治疗或健康结果直接关联，并交由人工核对资质。",
    ),
    _Rule(
        code="off_platform_diversion",
        severity="high",
        patterns=(
            re.compile(
                r"(?:加|联系|私信|dd|滴滴|戳)(?:我|客服)?"
                r"(?:微信|vx|v信|qq|企鹅|电话|手机号|邮箱)"
            ),
            re.compile(
                r"(?:微信|vx|v信|qq)(?:联系|咨询|下单|购买)"
            ),
            re.compile(
                r"(?:主页|头像|简介)(?:有|看|找|联系)"
                r"(?:微信|vx|v信|联系方式|二维码)"
            ),
            re.compile(r"(?:主页|头像|简介)(?:有|看|找|联系)微(?:$|号|信)"),
            re.compile(r"(?:主页|头像|简介)(?:找|联系)我(?:$|咨询|下单|购买)"),
            re.compile(r"(?:扫码|扫描二维码)(?:加|联系|咨询|下单|购买)"),
            re.compile(r"加v(?:我|信|微信)?(?![a-z0-9])"),
            re.compile(r"\+v(?![a-z0-9])"),
            re.compile(r"(?:微信号|vx号|v信号)(?:是)?[a-z0-9]{3,}"),
        ),
        reason="站外联系方式或交易导向可能触发平台导流规则。",
        suggestion="删除站外联系方式和交易指令，改用平台允许的沟通方式。",
    ),
    _Rule(
        code="engagement_inducement",
        severity="medium",
        patterns=(
            re.compile(
                r"(?:点赞|收藏|关注|评论|转发).{0,8}"
                r"(?:送|领取|获取|获得|解锁|抽奖|返现|有惊喜|福利|资料)"
            ),
            re.compile(
                r"(?:送|领取|获取|福利|资料).{0,8}"
                r"(?:点赞|收藏|关注|评论|转发)"
            ),
            re.compile(
                r"双击有惊喜|评论(?:1|一|关键词).{0,4}(?:领取|获取)|"
                r"互粉|互赞|互关|集赞|点赞收藏不迷路"
            ),
        ),
        reason="用奖励、资源或暗示交换互动可能属于诱导互动。",
        suggestion="改为不附带交换条件的自然邀请，避免承诺奖励或制造暗示。",
    ),
    _Rule(
        code="unverified_authority_or_platform_endorsement",
        severity="high",
        patterns=(
            re.compile(
                r"(?:小红书|平台)(?:官方)?"
                r"(?:认证|推荐|严选|指定|背书|认可)"
            ),
            re.compile(
                r"(?:央视|cctv|国家机关|政府|专家|权威机构|医生|院士)"
                r"(?:认证|推荐|背书|指定|认可)"
            ),
            re.compile(r"(?:国家|国际|全球)(?:免检|认证)"),
            re.compile(r"(?:官方|权威)(?:认证|推荐|背书)"),
        ),
        reason="平台、机构或专业人士背书必须真实且具备可核验授权。",
        suggestion="没有完整证明时删除背书表述，改为可验证的产品事实。",
    ),
    _Rule(
        code="competitor_disparagement",
        severity="medium",
        patterns=(
            re.compile(
                r"(?:竞品|同行|其他品牌|某品牌|品牌)"
                r"(?:就是|都是|简直是|很|非常)?"
                r"(?:垃圾|劣质|坑人|骗钱|智商税|不行|差劲)"
            ),
            re.compile(
                r"(?:吊打|碾压|完胜)(?:所有|全部|一众)?"
                r"(?:竞品|同行|其他品牌|品牌)"
            ),
        ),
        reason="缺少客观依据的竞品贬损或不公正比较具有内容风险。",
        suggestion="删除攻击性判断；如需比较，使用同口径事实并注明来源。",
    ),
)

_DIRECT_VALUE_PATTERNS: tuple[tuple[_Rule, re.Pattern[str]], ...] = (
    (
        next(rule for rule in _COMMON_RULES if rule.code == "off_platform_diversion"),
        re.compile(
            r"(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@"
            r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
            r"(?![A-Za-z0-9_.-])"
        ),
    ),
    (
        next(rule for rule in _COMMON_RULES if rule.code == "off_platform_diversion"),
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ),
    (
        next(rule for rule in _COMMON_RULES if rule.code == "off_platform_diversion"),
        re.compile(r"https?://[^\s，。；！？]+", re.IGNORECASE),
    ),
)

_GENERIC_TRAFFIC_TAGS = frozenset(
    {
        "#小红书爆款",
        "#小红书热门",
        "#爆款",
        "#爆款笔记",
        "#上热门",
        "#热门推荐",
        "#流量密码",
        "#必火",
    }
)

_GENERIC_TAG_RULE = _Rule(
    code="generic_traffic_tag",
    severity="low",
    patterns=(),
    reason="泛流量标签与具体内容关联不足，并可能暗示未经证实的热度。",
    suggestion="换成能够由图片或正文支持的具体主题标签。",
)

_SEGMENT_SEPARATOR = re.compile(r"[，,。.!！？?；;：:\n]+")
_CAUTIONARY_PREFIX = re.compile(
    r"(?:无法|不能|未)(?:确认|证实|保证|判断)"
    r"(?:这款|该|本款)?(?:产品|商品)?"
    r"(?:是否|能否|有无|有没有|是否具有|具有)?$|"
    r"(?:不|并不|不能|无法|不可|未|没有|并未|不代表|禁止|避免|"
    r"请勿|切勿|谨防|不要)$|"
    r"(?:请勿|切勿|不要|禁止)(?:通过|使用|相信)?$|"
    r"谨防(?:有人)?(?:叫你|让你)?$|请举报$|"
    r"不要相信(?:小红书|平台)?$"
)
_PUBLISHABLE_FIELD_ORDER = {
    "title": 0,
    "body": 1,
    "tags": 2,
}


class ContentRiskScanner:
    """Deterministic pre-publication hints with no automatic rewriting."""

    rule_version = CONTENT_RISK_RULE_VERSION

    def scan(
        self,
        copy: GeneratedCopy,
        *,
        scenario: ContentScenario | str = ContentScenario.UNSPECIFIED,
        industry: ContentIndustry | str = ContentIndustry.UNSPECIFIED,
    ) -> tuple[ContentRiskFinding, ...]:
        """Return sorted immutable findings without changing ``copy``."""
        normalized_scenario = _content_scenario(scenario)
        normalized_industry = _content_industry(industry)
        findings: list[ContentRiskFinding] = []

        values: tuple[tuple[ContentRiskField, str], ...] = (
            ("title", copy.title),
            ("body", copy.body),
        )
        for field, value in values:
            findings.extend(
                self._scan_value(
                    field,
                    value,
                    scenario=normalized_scenario,
                    industry=normalized_industry,
                )
            )

        tag_offset = 0
        for tag in copy.tags:
            findings.extend(
                self._scan_value(
                    "tags",
                    tag,
                    base_offset=tag_offset,
                    scenario=normalized_scenario,
                    industry=normalized_industry,
                )
            )
            if _normalize_tag(tag) in _GENERIC_TRAFFIC_TAGS:
                findings.append(
                    _finding(
                        _GENERIC_TAG_RULE,
                        field="tags",
                        start=tag_offset,
                        end=tag_offset + len(tag),
                        scenario=normalized_scenario,
                        industry=normalized_industry,
                    )
                )
            tag_offset += len(tag) + 1

        unique = {
            (finding.code, finding.field, finding.start, finding.end): finding
            for finding in findings
        }
        return tuple(sorted(unique.values(), key=_finding_sort_key))

    def _scan_value(
        self,
        field: ContentRiskField,
        value: str,
        *,
        base_offset: int = 0,
        scenario: ContentScenario,
        industry: ContentIndustry,
    ) -> list[ContentRiskFinding]:
        findings: list[ContentRiskFinding] = []
        for segment, offset in _iter_segments(value):
            compact, source_positions = _compact_with_positions(segment)
            if not compact:
                continue
            for rule in _COMMON_RULES:
                for pattern in rule.patterns:
                    for match in pattern.finditer(compact):
                        local_start = offset + source_positions[match.start()]
                        local_end = offset + source_positions[match.end() - 1] + 1
                        if _is_cautionary(value, local_start):
                            continue
                        findings.append(
                            _finding(
                                rule,
                                field=field,
                                start=base_offset + local_start,
                                end=base_offset + local_end,
                                scenario=scenario,
                                industry=industry,
                            )
                        )

        for rule, pattern in _DIRECT_VALUE_PATTERNS:
            for match in pattern.finditer(value):
                if match.re.flags & re.IGNORECASE and _is_xhs_url(match.group(0)):
                    continue
                findings.append(
                    _finding(
                        rule,
                        field=field,
                        start=base_offset + match.start(),
                        end=base_offset + match.end(),
                        scenario=scenario,
                        industry=industry,
                    )
                )
        return findings


DEFAULT_CONTENT_RISK_SCANNER = ContentRiskScanner()


def scan_content_risks(
    copy: GeneratedCopy,
    *,
    scenario: ContentScenario | str = ContentScenario.UNSPECIFIED,
    industry: ContentIndustry | str = ContentIndustry.UNSPECIFIED,
) -> tuple[ContentRiskFinding, ...]:
    """Scan generated copy for advisory risks using the frozen default rules."""
    return DEFAULT_CONTENT_RISK_SCANNER.scan(
        copy,
        scenario=scenario,
        industry=industry,
    )


def _finding(
    rule: _Rule,
    *,
    field: ContentRiskField,
    start: int,
    end: int,
    scenario: ContentScenario,
    industry: ContentIndustry,
) -> ContentRiskFinding:
    return ContentRiskFinding(
        code=rule.code,
        severity=_severity(rule, scenario=scenario, industry=industry),
        field=field,
        start=start,
        end=end,
        reason=rule.reason,
        suggestion=rule.suggestion,
        rule_version=CONTENT_RISK_RULE_VERSION,
    )


def _severity(
    rule: _Rule,
    *,
    scenario: ContentScenario,
    industry: ContentIndustry,
) -> RiskSeverity:
    commercial = scenario in {
        ContentScenario.BRAND_COLLABORATION,
        ContentScenario.PAID_AD,
    }
    if rule.code == "absolute_or_superiority_claim" and commercial:
        return "high"
    if rule.code == "competitor_disparagement" and commercial:
        return "high"
    if rule.code == "medical_or_health_claim" and (
        commercial
        or industry
        in {
            ContentIndustry.BEAUTY_PERSONAL_CARE,
            ContentIndustry.FOOD_HEALTH,
            ContentIndustry.MEDICAL_HEALTH,
        }
    ):
        return "high"
    return rule.severity


def _iter_segments(value: str) -> tuple[tuple[str, int], ...]:
    segments: list[tuple[str, int]] = []
    start = 0
    for separator in _SEGMENT_SEPARATOR.finditer(value):
        if separator.start() > start:
            segments.append((value[start : separator.start()], start))
        start = separator.end()
    if start < len(value):
        segments.append((value[start:], start))
    return tuple(segments)


def _compact_with_positions(value: str) -> tuple[str, tuple[int, ...]]:
    characters: list[str] = []
    positions: list[int] = []
    for source_index, character in enumerate(value):
        normalized = unicodedata.normalize("NFKC", character).casefold()
        for normalized_character in normalized:
            if (
                unicodedata.category(normalized_character)[0] in {"L", "N"}
                or normalized_character in {"%", "+"}
            ):
                characters.append(normalized_character)
                positions.append(source_index)
    return "".join(characters), tuple(positions)


def _compact(value: str) -> str:
    compact, _positions = _compact_with_positions(value)
    return compact


def _is_cautionary(value: str, source_start: int) -> bool:
    prefix = _compact(value[max(0, source_start - 16) : source_start])
    return bool(_CAUTIONARY_PREFIX.search(prefix))


def _normalize_tag(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def _is_xhs_url(value: str) -> bool:
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    normalized = hostname.rstrip(".").casefold()
    return any(
        normalized == domain or normalized.endswith(f".{domain}")
        for domain in ("xiaohongshu.com", "xhscdn.com")
    )


def _content_scenario(value: ContentScenario | str) -> ContentScenario:
    try:
        return ContentScenario(value)
    except (TypeError, ValueError):
        raise ValueError("unsupported content scenario") from None


def _content_industry(value: ContentIndustry | str) -> ContentIndustry:
    try:
        return ContentIndustry(value)
    except (TypeError, ValueError):
        raise ValueError("unsupported content industry") from None


def _finding_sort_key(finding: ContentRiskFinding) -> tuple[int, int, str]:
    field_order = _PUBLISHABLE_FIELD_ORDER.get(finding.field, 100)
    return field_order, finding.start, finding.code


__all__ = [
    "CONTENT_RISK_RULE_VERSION",
    "ContentIndustry",
    "ContentRiskField",
    "ContentRiskFinding",
    "ContentRiskScanner",
    "ContentScenario",
    "DEFAULT_CONTENT_RISK_SCANNER",
    "scan_content_risks",
]
