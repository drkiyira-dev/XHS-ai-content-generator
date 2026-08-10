"""Deterministic checks for unsupported claims and clear OCR conflicts."""

import re
import unicodedata
from collections.abc import Iterator

from backend.services.model.types import GeneratedCopy


_CLAIM_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "skin_tolerance",
        re.compile(
            r"温和不刺激|不刺激|无刺激|零刺激|低敏|不致敏|不过敏|亲肤|"
            r"不会.{0,4}刺激(?:肌肤|皮肤)?|"
            r"刺激性(?:较低|低)|"
            r"温和(?:配方|清洁|卸妆|洁净|呵护)|"
            r"(?:配方|成分|质地|肤感|清洁|卸妆).{0,4}温和|"
            r"(?:肌肤|皮肤|敏感肌|敏肌).{0,8}温和|"
            r"温和.{0,8}(?:肌肤|皮肤|敏感肌|敏肌)|"
            r"温和.{0,4}(?:不伤肤|呵护肌肤)"
        ),
    ),
    (
        "skin_suitability",
        re.compile(
            r"(?:敏感肌|敏肌|敏皮|敏感肤质|脆弱肌).{0,10}"
            r"(?:可(?:以)?(?:用|试试)|(?:也)?能(?:用|尝试)|适用|适合|友好|"
            r"放心|安心|推荐|首选|必备)|"
            r"(?:适合|适用|推荐给).{0,10}"
            r"(?:敏感肌|敏肌|敏皮|敏感肤质|脆弱肌)|"
            r"(?:孕妇|儿童|婴幼儿).{0,8}(?:可用|适用|适合)"
        ),
    ),
    (
        "skin_feel",
        re.compile(
            r"不紧绷|无紧绷|不会(?:觉得|感到|感觉)?紧绷|"
            r"不拔干|不干涩|不干燥|不油腻|不黏腻|不粘腻|"
            r"洗(?:完|后)(?:脸)?[^，,。.!！？?；;]{0,4}不会干(?!净)|"
            r"(?:精华|乳液|面霜|护肤品|质地).{0,6}(?:好吸收|易吸收)|"
            r"(?:好吸收|易吸收).{0,6}(?:精华|乳液|面霜|护肤品|质地)|"
            r"(?:洗后|用后|使用后).{0,8}(?:水润|柔软|清爽|舒服|舒适|滑嫩)"
        ),
    ),
    (
        "cosmetic_efficacy",
        re.compile(
            r"美白|焕白|提亮肤色|淡斑|祛斑|祛痘|抗痘|抗衰|抗老|"
            r"淡纹|祛皱|去皱|提拉|抗氧化|"
            r"(?:肌肤|皮肤|轮廓).{0,6}紧致|"
            r"紧致.{0,6}(?:肌肤|皮肤|轮廓)|"
            r"修复屏障|修护屏障|修护受损|舒缓敏感|改善敏感|退红|"
            r"补水|保湿|锁水|控油|收缩毛孔|去黑头|不闷痘|不致痘"
        ),
    ),
    (
        "medical_claim",
        re.compile(
            r"治疗|治愈|消炎|抗炎|杀菌|药用|医美级|医学级|"
            r"临床验证|医生推荐|皮肤科推荐"
        ),
    ),
    (
        "performance_claim",
        re.compile(
            r"深层清洁|彻底清洁|彻底卸除|卸得干净|清洁力强|"
            r"(?:立刻|即时|快速|显著|明显|有效).{0,6}"
            r"(?:见效|改善|淡化|消除|去除)|"
            r"(?:保证|确保).{0,8}(?:有效|改善|见效)"
        ),
    ),
    (
        "composition_claim",
        re.compile(
            r"无添加|零添加|不含(?:酒精|香精|防腐剂)|纯天然|全天然|"
            r"有机认证"
        ),
    ),
    (
        "unsupported_reassurance",
        re.compile(r"(?:用起来|使用|可以).{0,4}(?:安心|放心)|安心使用|放心使用"),
    ),
    (
        "english_claim",
        re.compile(
            r"hypoallergenic|non-?irritating|whitening|brightening|"
            r"anti-?aging|anti-?acne|moisturizing|hydrating|"
            r"clinicallyproven|dermatologisttested"
        ),
    ),
)

_SENSITIVE_SKIN_TAG = re.compile(r"敏感肌|敏肌|敏皮|敏感肤质|脆弱肌")
_CLAUSE_SEPARATOR = re.compile(r"[，,。.!！？?；;：:\n]+|但是|不过|然而|但")
_NON_ASSERTIVE_CONTEXT = re.compile(
    r"无法(?:确认|证实|判断)|不能(?:确认|证实|保证|判断)|"
    r"未(?:确认|证实)|不代表|"
    r"不(?:适合|适用|推荐)(?:敏感肌|敏肌|敏皮|敏感肤质|脆弱肌)|"
    r"(?:请|建议)(?:先|再)?核对"
)
_CATEGORY_NEGATION_BEFORE = re.compile(r"(?:不是|并非|非)$")

_CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "cleansing_oil": re.compile(r"cleansingoil|卸妆油|洁颜油|清洁油"),
    "body_lotion": re.compile(
        r"bodylotion|bodymilk|身体乳(?:液)?|润肤乳"
    ),
}


def find_unsupported_claim_rule(
    copy: GeneratedCopy,
    *,
    ocr_text: str | None = None,
) -> str | None:
    """Return a fixed violation identifier without exposing model text."""
    fields = (
        copy.image_summary,
        copy.title,
        copy.body,
        *copy.tags,
    )

    if _has_ocr_category_conflict(fields, ocr_text):
        return "product_category_conflict"

    for value in fields:
        for clause in _iter_assertive_clauses(value):
            for rule_name, pattern in _CLAIM_RULES:
                if pattern.search(clause):
                    return rule_name

    for tag in copy.tags:
        normalized_tag = _normalize_for_matching(tag)
        if _SENSITIVE_SKIN_TAG.search(normalized_tag):
            return "sensitive_skin_tag"
    return None


def _has_ocr_category_conflict(
    fields: tuple[str, ...],
    ocr_text: str | None,
) -> bool:
    if not ocr_text:
        return False

    ocr_categories = _find_categories(ocr_text)
    if len(ocr_categories) != 1:
        return False

    output_categories: set[str] = set()
    for value in fields:
        output_categories.update(_find_asserted_categories(value))
    return bool(output_categories - ocr_categories)


def _find_categories(value: str) -> set[str]:
    normalized = _normalize_for_matching(value)
    return {
        category
        for category, pattern in _CATEGORY_PATTERNS.items()
        if pattern.search(normalized)
    }


def _find_asserted_categories(value: str) -> set[str]:
    normalized = _normalize_for_matching(value)
    categories: set[str] = set()
    for clause in _CLAUSE_SEPARATOR.split(normalized):
        for category, pattern in _CATEGORY_PATTERNS.items():
            for match in pattern.finditer(clause):
                prefix = clause[max(0, match.start() - 4) : match.start()]
                if _CATEGORY_NEGATION_BEFORE.search(prefix):
                    continue
                categories.add(category)
    return categories


def _iter_assertive_clauses(value: str) -> Iterator[str]:
    normalized = _normalize_for_matching(value)
    for clause in _CLAUSE_SEPARATOR.split(normalized):
        if not clause:
            continue
        if _NON_ASSERTIVE_CONTEXT.search(clause):
            continue
        yield clause


def _normalize_for_matching(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith("C")
    )
