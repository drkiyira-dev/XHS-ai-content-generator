"""Deterministic checks for unsupported claims and clear OCR conflicts."""

from bisect import bisect_left
from collections.abc import Iterator
import re
import unicodedata

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
        "subjective_experience",
        re.compile(
            r"(?:质地|肤感|触感|手感).{0,6}"
            r"(?:轻盈|轻薄|丝滑|顺滑|柔滑|细腻|清爽|舒服|舒适|"
            r"好推开|易推开)|"
            r"(?:轻盈|轻薄|丝滑|顺滑|柔滑|细腻|清爽).{0,6}"
            r"(?:质地|肤感|触感|手感)|"
            r"(?:用起来|使用起来|用着|使用时|上手|拿起来|拿着|"
            r"拿在手里|握着).{0,8}"
            r"(?:顺手|好用|省心|方便|轻松|舒服|舒适)"
        ),
    ),
    (
        "price_claim",
        re.compile(
            r"平价好物|平价(?:之选|单品|宝藏)?|高性价比|"
            r"性价比(?:高|拉满)|物美价廉|物超所值|"
            r"(?:价格|售价).{0,4}(?:实惠|亲民|便宜|友好)|"
            r"白菜价|(?:很|超)?划算|超值(?:入手|之选)?"
        ),
    ),
    (
        "cosmetic_efficacy",
        re.compile(
            r"美白|焕白|提亮肤色|淡斑|祛斑|祛痘|抗痘|抗衰|抗老|"
            r"淡纹|祛皱|去皱|提拉(?!米苏)|抗氧化|"
            r"(?:肌肤|皮肤|轮廓).{0,6}紧致|"
            r"紧致.{0,6}(?:肌肤|皮肤|轮廓)|"
            r"修复屏障|修护屏障|修护受损|舒缓敏感|改善敏感|退红|"
            r"补水|保湿|锁水|控油|收缩毛孔|去黑头|不闷痘|不致痘"
        ),
    ),
    (
        "medical_claim",
        re.compile(
            r"治疗|治療|治愈|治癒|疗愈|療癒|根治|疗效|療效|"
            r"消炎|抗炎|抑菌|抗菌|杀菌|殺菌|药用|藥用|"
            r"医美级|醫美級|医学级|醫學級|"
            r"临床验证|臨床驗證|医生推荐|醫生推薦|"
            r"皮肤科推荐|皮膚科推薦"
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

# Strict matching removes punctuation and symbols, so it must use only atomic
# words or adjacent phrases. Reusing distance-based rules here could join two
# unrelated sentences and create false positives.
_STRICT_CLAIM_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "skin_tolerance",
        re.compile(
            r"温和不刺激|不刺激|无刺激|零刺激|低敏|不致敏|不过敏|亲肤|不伤肤"
        ),
    ),
    (
        "skin_suitability",
        re.compile(
            r"(?:敏感肌|敏肌|敏皮|敏感肤质|脆弱肌)"
            r"(?:可用|可以用|适用|适合|友好|放心|安心|推荐|首选|必备)|"
            r"(?:适合|适用|推荐给)(?:敏感肌|敏肌|敏皮|敏感肤质|脆弱肌)|"
            r"(?:孕妇|儿童|婴幼儿)"
            r"(?:可用|可以用|适用|适合(?:用|使用)?)"
            r"(?:这款|这个|该|本款|本)?(?:产品|商品|护肤品)|"
            r"(?:孕妇|儿童|婴幼儿)(?:可用|适用|适合)"
        ),
    ),
    (
        "skin_feel",
        re.compile(
            r"不紧绷|无紧绷|不拔干|不干涩|不干燥|不油腻|不黏腻|不粘腻"
        ),
    ),
    (
        "subjective_experience",
        re.compile(
            r"(?:质地|肤感|触感|手感)"
            r"(?:轻盈|轻薄|丝滑|顺滑|柔滑|细腻|清爽|舒服|舒适|好推开|易推开)|"
            r"(?:轻盈|轻薄|丝滑|顺滑|柔滑|细腻|清爽)"
            r"(?:质地|肤感|触感|手感)|"
            r"(?:用起来|使用起来|用着|使用时|上手|拿起来|拿着|拿在手里|握着)"
            r"(?:顺手|好用|省心|方便|轻松|舒服|舒适)"
        ),
    ),
    (
        "price_claim",
        re.compile(
            r"平价好物|平价之选|平价单品|平价宝藏|高性价比|性价比高|"
            r"性价比拉满|物美价廉|物超所值|白菜价|很划算|超划算|划算|"
            r"超值入手|超值之选"
        ),
    ),
    (
        "cosmetic_efficacy",
        re.compile(
            r"美白|焕白|提亮肤色|淡斑|祛斑|祛痘|抗痘|抗衰|抗老|"
            r"淡纹|祛皱|去皱|提拉(?!米苏)|抗氧化|修复屏障|修护屏障|"
            r"修护受损|舒缓敏感|改善敏感|退红|补水|保湿|锁水|控油|"
            r"收缩毛孔|去黑头|不闷痘|不致痘|"
            r"(?:肌肤|皮肤|轮廓)紧致|紧致(?:肌肤|皮肤|轮廓)"
        ),
    ),
    (
        "medical_claim",
        re.compile(
            r"治疗|治療|治愈|治癒|疗愈|療癒|根治|疗效|療效|"
            r"消炎|抗炎|抑菌|抗菌|杀菌|殺菌|药用|藥用|"
            r"医美级|醫美級|医学级|醫學級|"
            r"临床验证|臨床驗證|医生推荐|醫生推薦|"
            r"皮肤科推荐|皮膚科推薦"
        ),
    ),
    (
        "performance_claim",
        re.compile(
            r"深层清洁|彻底清洁|彻底卸除|卸得干净|清洁力强|"
            r"立刻见效|即时见效|快速见效|显著改善|明显改善|有效改善"
        ),
    ),
    (
        "composition_claim",
        re.compile(
            r"无添加|零添加|不含酒精|不含香精|不含防腐剂|"
            r"纯天然|全天然|有机认证"
        ),
    ),
    (
        "unsupported_reassurance",
        re.compile(r"用起来安心|用起来放心|安心使用|放心使用"),
    ),
    (
        "english_claim",
        re.compile(
            r"hypoallergenic|nonirritating|whitening|brightening|antiaging|"
            r"antiacne|moisturizing|hydrating|clinicallyproven|"
            r"dermatologisttested|dermatologistrecommended"
        ),
    ),
)

_SENSITIVE_SKIN_TAG = re.compile(r"敏感肌|敏肌|敏皮|敏感肤质|脆弱肌")
_CLAIM_RULE_NAMES = frozenset(
    rule_name for rule_name, _pattern in _CLAIM_RULES
)
_CLAIM_RULE_DIAGNOSTIC_LABELS = _CLAIM_RULE_NAMES | frozenset(
    {"product_category_conflict", "sensitive_skin_tag"}
)
_LOCAL_FALLBACK_RULES = _CLAIM_RULE_NAMES | frozenset({"sensitive_skin_tag"})
_LOCAL_FALLBACK_PATTERNS = _CLAIM_RULES
_LOCAL_FALLBACK_TAGS = ("#图片记录", "#图文分享", "#内容分享")
_CLAUSE_SEPARATOR = re.compile(r"[，,。.!！？?；;：:\n]+|但是|不过|然而|但")
_SANITIZE_CLAUSE_SEPARATOR = re.compile(r"[，,。.!！？?；;：:\n]+")
_STRONG_CLAIM_SEPARATOR = re.compile(r"[，,。.!！？?；;：:\n]")
_BOUNDARY_CLAIM_CONTEXT_BEFORE: dict[str, re.Pattern[str]] = {
    "cosmetic_efficacy": re.compile(
        r"(?:能|可|可以|会|具有|主打|声称|帮助|实现|达到|让|"
        r"(?:这款|这个|该|本款|本)?(?:产品|商品|护肤品|精华|乳液|面霜))$"
    ),
    "medical_claim": re.compile(
        r"(?:能|可|可以|会|具有|用于|主打|声称|帮助|实现|达到|"
        r"(?:这款|这个|该|本款|本)?(?:产品|商品|护肤品|精华|乳液|面霜))$"
    ),
    "skin_suitability": re.compile(r"(?:适合|适用|推荐给)$"),
}
_BOUNDARY_CLAIM_CONTEXT_AFTER: dict[str, re.Pattern[str]] = {
    "cosmetic_efficacy": re.compile(
        r"^(?:效果|功效|作用|肌肤|皮肤|提亮|淡化)"
    ),
    "medical_claim": re.compile(
        r"^(?:作用|效果|功效|疾病|症状|痘痘|皮肤|感冒|系统性疾病)"
    ),
    "skin_suitability": re.compile(
        r"^(?:可用|适用|适合|友好|放心|安心|推荐|人群)"
    ),
}
_NON_ASSERTIVE_TAIL = (
    r"(?:这款|该|本款)?(?:产品|商品)?(?:它|其)?"
    r"(?:实际|具体|真正|官方说明|使用后|用后|是否能|是否可以|"
    r"是否|能否|会否|有无|有没有)*"
)
_NON_ASSERTIVE_PREFIX = re.compile(
    rf"(?:无法|不能|未)(?:确认|证实|保证|判断){_NON_ASSERTIVE_TAIL}$|"
    rf"不代表{_NON_ASSERTIVE_TAIL}$|"
    rf"(?:请|建议)(?:先|再)?核对{_NON_ASSERTIVE_TAIL}$"
)
_NON_ASSERTIVE_CONTINUATION = re.compile(r"(?:和|及|与|、|或|以及)?")
_CATEGORY_NEGATION_BEFORE = re.compile(
    r"(?:(?:不是|并非)(?:一瓶|这款|这个|该|本款|本)?"
    r"(?:产品|商品|护肤品)?|非|没有|没|并未|未|无|未见|"
    r"看不见|看不到|不可见|不含|"
    r"(?:没有|没|并未|未|并非|不是)(?:看到|展示|出现|发现|标注|标有|"
    r"标明|写着|写|显示|识别)(?:为)?)$"
)

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
                if _has_assertive_match(rule_name, pattern, clause):
                    return rule_name

    for tag in copy.tags:
        normalized_tag = _normalize_for_matching(tag)
        if _SENSITIVE_SKIN_TAG.search(normalized_tag):
            return "sensitive_skin_tag"
    return None


def find_strict_unsupported_claim_rule(
    copy: GeneratedCopy,
    *,
    ocr_text: str | None = None,
) -> str | None:
    """Fail closed on symbol-obfuscated high-risk claims."""
    text_fields = (
        copy.image_summary,
        copy.title,
        copy.body,
    )
    if _has_strict_ocr_category_conflict(
        text_fields,
        copy.tags,
        ocr_text,
    ):
        return "product_category_conflict"

    for value in text_fields:
        for rule_name, pattern in _STRICT_CLAIM_RULES:
            if _has_strict_assertive_match(rule_name, pattern, value):
                return rule_name

    for tag in copy.tags:
        compact_tag = _normalize_for_strict_matching(tag)
        for rule_name, pattern in _STRICT_CLAIM_RULES:
            if pattern.search(compact_tag):
                return rule_name
        if _contains_sensitive_skin_tag(tag):
            return "sensitive_skin_tag"
    return None


def claim_rule_diagnostic_label(rule: str | None) -> str:
    """Return only a fixed rule label that is safe to include in logs."""
    if rule is None:
        return "not_applicable"
    if rule in _CLAIM_RULE_DIAGNOSTIC_LABELS:
        return rule
    return "unknown"


def can_apply_local_evidence_fallback(rule: str | None) -> bool:
    """Return whether a fixed unsupported-claim type can be removed locally."""
    return rule in _LOCAL_FALLBACK_RULES


def build_local_evidence_fallback(copy: GeneratedCopy) -> GeneratedCopy:
    """Remove all unsupported-claim clauses and tags, then restore the schema."""
    image_summary = _join_safe_clauses(copy.image_summary)
    title = "".join(_safe_clauses(copy.title))
    body = _join_safe_clauses(copy.body)

    safe_tags = [
        tag
        for tag in copy.tags
        if not _contains_local_fallback_claim(tag)
        and not _contains_sensitive_skin_tag(tag)
    ]
    seen_tags = set(safe_tags)
    for neutral_tag in _LOCAL_FALLBACK_TAGS:
        if len(safe_tags) >= 3:
            break
        if neutral_tag not in seen_tags:
            safe_tags.append(neutral_tag)
            seen_tags.add(neutral_tag)

    return GeneratedCopy(
        image_summary=image_summary or "图片中可见上传内容中的主体。",
        title=title or "图片内容观察",
        body=body or "仅记录图片可见内容，其他属性无法从图片确认。",
        tags=tuple(safe_tags[:5]),
    )


def _join_safe_clauses(value: str) -> str:
    return "。".join(_safe_clauses(value))


def _safe_clauses(value: str) -> list[str]:
    return [
        clause
        for raw_clause in _SANITIZE_CLAUSE_SEPARATOR.split(value)
        if (clause := raw_clause.strip())
        and not _contains_local_fallback_claim(clause)
    ]


def _contains_local_fallback_claim(value: str) -> bool:
    for clause in _iter_assertive_clauses(value):
        for rule_name, pattern in _LOCAL_FALLBACK_PATTERNS:
            if _has_assertive_match(rule_name, pattern, clause):
                return True
        for rule_name, pattern in _STRICT_CLAIM_RULES:
            if _has_strict_assertive_match(rule_name, pattern, clause):
                return True
    return False


def _contains_sensitive_skin_tag(value: str) -> bool:
    if _SENSITIVE_SKIN_TAG.search(_normalize_for_matching(value)):
        return True
    return bool(
        _SENSITIVE_SKIN_TAG.search(_normalize_for_strict_matching(value))
    )


def _has_assertive_match(
    rule_name: str,
    pattern: re.Pattern[str],
    clause: str,
) -> bool:
    previous_non_assertive_end: int | None = None
    for match in pattern.finditer(clause):
        is_non_assertive = _is_non_assertive_match(
            rule_name,
            clause,
            match.start(),
        )
        if not is_non_assertive and previous_non_assertive_end is not None:
            gap = clause[previous_non_assertive_end : match.start()]
            is_non_assertive = bool(_NON_ASSERTIVE_CONTINUATION.fullmatch(gap))
        if not is_non_assertive:
            return True
        previous_non_assertive_end = match.end()
    return False


def _is_non_assertive_match(
    rule_name: str,
    clause: str,
    match_start: int,
) -> bool:
    return _is_non_assertive_prefix(rule_name, clause[:match_start])


def _is_non_assertive_prefix(rule_name: str, prefix: str) -> bool:
    prefix = _normalize_for_matching(prefix)
    if _NON_ASSERTIVE_PREFIX.search(prefix):
        return True
    return rule_name == "skin_suitability" and prefix.endswith("不")


def _has_strict_assertive_match(
    rule_name: str,
    pattern: re.Pattern[str],
    value: str,
) -> bool:
    """Match symbol-obfuscated claims without losing disclaimer boundaries."""
    compact, source_positions = _strict_text_with_source_positions(value)
    boundary_starts, boundary_ends = _strong_boundary_positions(value)
    previous_non_assertive_end: int | None = None
    for match in pattern.finditer(compact):
        source_start = source_positions[match.start()]
        source_end = source_positions[match.end() - 1] + 1
        source_span = value[source_start:source_end]
        if (
            _STRONG_CLAIM_SEPARATOR.search(source_span)
            and not _has_boundary_split_claim_context(
                rule_name,
                value,
                source_start,
                source_end,
                boundary_starts,
                boundary_ends,
            )
        ):
            continue
        is_non_assertive = _is_non_assertive_prefix(
            rule_name,
            value[:source_start],
        )
        if not is_non_assertive and previous_non_assertive_end is not None:
            gap = _normalize_for_matching(
                value[previous_non_assertive_end:source_start]
            )
            is_non_assertive = bool(_NON_ASSERTIVE_CONTINUATION.fullmatch(gap))
        if not is_non_assertive:
            return True
        previous_non_assertive_end = source_end
    return False


def _has_boundary_split_claim_context(
    rule_name: str,
    value: str,
    source_start: int,
    source_end: int,
    boundary_starts: tuple[int, ...],
    boundary_ends: tuple[int, ...],
) -> bool:
    source_span = value[source_start:source_end]
    if rule_name == "skin_suitability":
        compact_span = _normalize_for_strict_matching(source_span)
        if _SENSITIVE_SKIN_TAG.search(compact_span) or re.search(
            r"(?:孕妇|儿童|婴幼儿)"
            r"(?:可用|可以用|适用|适合(?:用|使用)?)"
            r"(?:这款|这个|该|本款|本)?(?:产品|商品|护肤品)",
            compact_span,
        ):
            return True
    if _is_standalone_boundary_claim(
        value,
        source_start,
        source_end,
        boundary_starts,
        boundary_ends,
    ):
        return True
    before_pattern = _BOUNDARY_CLAIM_CONTEXT_BEFORE.get(rule_name)
    after_pattern = _BOUNDARY_CLAIM_CONTEXT_AFTER.get(rule_name)
    if before_pattern is None and after_pattern is None:
        return False
    prefix = _normalize_for_strict_matching(
        value[max(0, source_start - 12) : source_start]
    )
    suffix = _normalize_for_strict_matching(value[source_end : source_end + 12])
    return bool(
        (before_pattern is not None and before_pattern.search(prefix))
        or (after_pattern is not None and after_pattern.search(suffix))
    )


def _is_standalone_boundary_claim(
    value: str,
    source_start: int,
    source_end: int,
    boundary_starts: tuple[int, ...],
    boundary_ends: tuple[int, ...],
) -> bool:
    previous_index = bisect_left(boundary_starts, source_start) - 1
    clause_start = boundary_ends[previous_index] if previous_index >= 0 else 0
    next_index = bisect_left(boundary_starts, source_end)
    clause_end = (
        boundary_starts[next_index]
        if next_index < len(boundary_starts)
        else len(value)
    )
    left_context = _normalize_for_strict_matching(
        value[clause_start:source_start]
    )
    right_context = _normalize_for_strict_matching(
        value[source_end:clause_end]
    )
    return not left_context and not right_context


def _strong_boundary_positions(
    value: str,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    boundaries = tuple(_STRONG_CLAIM_SEPARATOR.finditer(value))
    return (
        tuple(boundary.start() for boundary in boundaries),
        tuple(boundary.end() for boundary in boundaries),
    )


def _has_strict_ocr_category_conflict(
    text_fields: tuple[str, ...],
    tags: tuple[str, ...],
    ocr_text: str | None,
) -> bool:
    if not ocr_text:
        return False
    ocr_categories = _find_strict_categories(ocr_text)
    if len(ocr_categories) != 1:
        return False
    output_categories: set[str] = set()
    for value in text_fields:
        output_categories.update(_find_strict_asserted_categories(value))
    for tag in tags:
        output_categories.update(
            _find_categories(_normalize_for_strict_matching(tag))
        )
    return bool(output_categories - ocr_categories)


def _find_strict_categories(value: str) -> set[str]:
    categories: set[str] = set()
    for raw_clause in _SANITIZE_CLAUSE_SEPARATOR.split(value):
        compact_clause = _normalize_for_strict_matching(raw_clause)
        categories.update(_find_categories(compact_clause))
    return categories


def _find_strict_asserted_categories(value: str) -> set[str]:
    compact, source_positions = _strict_text_with_source_positions(value)
    boundary_starts, boundary_ends = _strong_boundary_positions(value)
    categories: set[str] = set()
    for category, pattern in _CATEGORY_PATTERNS.items():
        for match in pattern.finditer(compact):
            source_start = source_positions[match.start()]
            source_end = source_positions[match.end() - 1] + 1
            prefix = _normalize_for_strict_matching(
                value[max(0, source_start - 12) : source_start]
            )
            if _CATEGORY_NEGATION_BEFORE.search(prefix):
                continue
            source_span = value[source_start:source_end]
            if _STRONG_CLAIM_SEPARATOR.search(source_span):
                suffix = _normalize_for_strict_matching(
                    value[source_end : source_end + 8]
                )
                has_category_context = bool(
                    re.search(
                        r"(?:是|为|一瓶|产品|商品|标签|品类|这是|这款|"
                        r"可见|看到|展示|出现|标注|标有|写着|显示)$",
                        prefix,
                    )
                    or re.match(r"(?:产品|包装|瓶|品类)", suffix)
                )
                if not (
                    _is_standalone_boundary_claim(
                        value,
                        source_start,
                        source_end,
                        boundary_starts,
                        boundary_ends,
                    )
                    or has_category_context
                ):
                    continue
            categories.add(category)
    return categories


def _strict_text_with_source_positions(value: str) -> tuple[str, tuple[int, ...]]:
    compact_characters: list[str] = []
    source_positions: list[int] = []
    for source_index, character in enumerate(value):
        normalized = unicodedata.normalize("NFKC", character).casefold()
        for normalized_character in normalized:
            if unicodedata.category(normalized_character)[0] in {"L", "N"}:
                compact_characters.append(normalized_character)
                source_positions.append(source_index)
    return "".join(compact_characters), tuple(source_positions)


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
                prefix = clause[max(0, match.start() - 12) : match.start()]
                if _CATEGORY_NEGATION_BEFORE.search(prefix):
                    continue
                categories.add(category)
    return categories


def _iter_assertive_clauses(value: str) -> Iterator[str]:
    normalized = _normalize_for_matching(value)
    for clause in _CLAUSE_SEPARATOR.split(normalized):
        if not clause:
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


def _normalize_for_strict_matching(value: str) -> str:
    compact, _source_positions = _strict_text_with_source_positions(value)
    return compact
