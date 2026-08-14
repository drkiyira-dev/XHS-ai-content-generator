"""Integration contracts for deterministic enhancement and advisory scanning."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.services.image import ProcessedImage
from backend.services.model import GeneratedCopy
from backend.schemas import RiskAssessmentSnapshot, RiskFindingSnapshot
from backend.services.persistence import (
    DeletedGeneration,
    FailedGeneration,
    PendingGeneration,
    StoredGeneration,
    StoredImagePreview,
    SuccessfulGeneration,
)
from tests.support import build_test_app, make_image_bytes, send_request


@dataclass
class RecordingPersistence:
    successes: list[SuccessfulGeneration] = field(default_factory=list)
    records: tuple[StoredGeneration, ...] = ()

    async def create_pending(self, record: PendingGeneration) -> None:
        _ = record

    async def mark_success(self, record: SuccessfulGeneration) -> None:
        self.successes.append(record)

    async def mark_failed(self, record: FailedGeneration) -> None:
        _ = record

    async def list_successful(
        self,
        *,
        user_id: int | None,
        limit: int,
    ) -> tuple[StoredGeneration, ...]:
        _ = user_id
        return self.records[:limit]

    async def get_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredGeneration | None:
        _ = generation_id, user_id
        return None

    async def get_image_preview(
        self,
        *,
        generation_id: str,
        user_id: int | None,
    ) -> StoredImagePreview | None:
        _ = generation_id, user_id
        return None

    async def delete_successful(
        self,
        *,
        generation_id: str,
        user_id: int | None,
        deleted_at: datetime,
    ) -> DeletedGeneration | None:
        _ = generation_id, user_id, deleted_at
        return None


@dataclass
class FixedModelService:
    copy: GeneratedCopy
    calls: int = 0

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        _ = image, product_name, target_audience, tone
        self.calls += 1
        return self.copy


def _safe_copy() -> GeneratedCopy:
    return GeneratedCopy(
        image_summary="图片中可见湖面、树林和山体倒影。",
        title="湖畔散步记录",
        body="湖面映着树林，远处可以看到山体轮廓。",
        tags=("#湖景", "#自然风光", "#旅行记录"),
    )


def _post(
    *,
    model_service: FixedModelService,
    persistence: RecordingPersistence,
    data: dict[str, str] | None = None,
):
    return asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=build_test_app(
                model_service=model_service,
                generation_persistence=persistence,
            ),
            files={"image": ("lake.png", make_image_bytes(), "image/png")},
            data=data or {},
        )
    )


def test_default_request_preserves_validated_copy_and_returns_empty_hints() -> None:
    model = FixedModelService(_safe_copy())
    persistence = RecordingPersistence()

    response = _post(model_service=model, persistence=persistence)

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "湖畔散步记录"
    assert payload["body"] == "湖面映着树林，远处可以看到山体轮廓。"
    assert payload["tags"] == ["#湖景", "#自然风光", "#旅行记录"]
    assert payload["risk_assessment"]["findings"] == []
    assert persistence.successes[0].title == payload["title"]
    assert list(persistence.successes[0].tags) == payload["tags"]
    assert (
        persistence.successes[0].risk_assessment.model_dump(mode="json")
        == payload["risk_assessment"]
    )


def test_opt_in_enhancement_is_bounded_and_persisted_exactly() -> None:
    model = FixedModelService(_safe_copy())
    persistence = RecordingPersistence()

    response = _post(
        model_service=model,
        persistence=persistence,
        data={"emoji_level": "light", "related_tags": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["image_summary"] == _safe_copy().image_summary
    assert len(payload["title"]) <= 20
    assert any(emoji in payload["title"] for emoji in {"🌿", "📷", "✨"})
    assert 3 <= len(payload["tags"]) <= 5
    assert "#小红书爆款" not in payload["tags"]
    stored = persistence.successes[0]
    assert stored.title == payload["title"]
    assert stored.body == payload["body"]
    assert list(stored.tags) == payload["tags"]


@pytest.mark.parametrize(
    "data",
    [
        {"emoji_level": "maximum"},
        {"related_tags": "yes"},
    ],
)
def test_invalid_enhancement_options_fail_before_model_or_persistence(
    data: dict[str, str],
) -> None:
    model = FixedModelService(_safe_copy())
    persistence = RecordingPersistence()

    response = _post(
        model_service=model,
        persistence=persistence,
        data=data,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FORM_DATA"
    assert model.calls == 0
    assert persistence.successes == []


def test_risk_scanner_warns_without_rewriting_or_claiming_platform_review() -> None:
    risky = GeneratedCopy(
        image_summary="图片中可见一个普通纸盒。",
        title="日常分享",
        body="点赞后即可领取资料，想了解详情就加微信。",
        tags=("#纸盒", "#小红书爆款", "#图片记录"),
    )
    persistence = RecordingPersistence()

    response = _post(
        model_service=FixedModelService(risky),
        persistence=persistence,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["body"] == risky.body
    assert payload["tags"] == list(risky.tags)
    findings = payload["risk_assessment"]["findings"]
    assert {finding["code"] for finding in findings} == {
        "engagement_inducement",
        "off_platform_diversion",
        "generic_traffic_tag",
    }
    assert {finding["field"] for finding in findings} == {"body", "tags"}
    assert all(
        set(finding) == {"code", "severity", "field", "reason", "suggestion"}
        for finding in findings
    )
    assert all("matched_text" not in finding for finding in findings)


def test_history_returns_generation_time_snapshot_without_rescanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    risky = _safe_copy().model_copy(
        update={"body": "这是小红书官方推荐的内容。"}
    )
    snapshot = RiskAssessmentSnapshot(
        rule_version="content-risk-hints-legacy-test",
        findings=(
            RiskFindingSnapshot(
                code="snapshot_at_generation",
                severity="low",
                field="tags",
                reason="生成时规则留下的测试提示。",
                suggestion="按生成时提示人工复核。",
            ),
        ),
    )
    persistence = RecordingPersistence(
        records=(
            StoredGeneration(
                generation_id="00000000-0000-4000-8000-000000000001",
                user_id=None,
                image_summary=risky.image_summary,
                title=risky.title,
                body=risky.body,
                tags=risky.tags,
                created_at=datetime.now(UTC),
                risk_assessment=snapshot,
            ),
        )
    )
    monkeypatch.setattr(
        "backend.api.v1.generations.scan_content_risks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("history must not rescan")
        ),
    )
    application = build_test_app(generation_persistence=persistence)

    response = asyncio.run(
        send_request("GET", "/api/v1/generations", application=application)
    )

    assert response.status_code == 200
    assessment = response.json()["items"][0]["risk_assessment"]
    assert assessment == snapshot.model_dump(mode="json")


def test_legacy_history_returns_explicit_unavailable_warning_without_rescanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copy = _safe_copy()
    persistence = RecordingPersistence(
        records=(
            StoredGeneration(
                generation_id="00000000-0000-4000-8000-000000000002",
                user_id=None,
                image_summary=copy.image_summary,
                title=copy.title,
                body=copy.body,
                tags=copy.tags,
                created_at=datetime.now(UTC),
                risk_assessment=None,
            ),
        )
    )
    monkeypatch.setattr(
        "backend.api.v1.generations.scan_content_risks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy history must not rescan")
        ),
    )

    response = asyncio.run(
        send_request(
            "GET",
            "/api/v1/generations",
            application=build_test_app(generation_persistence=persistence),
        )
    )

    assert response.status_code == 200
    assessment = response.json()["items"][0]["risk_assessment"]
    assert assessment["rule_version"] == "legacy-no-risk-snapshot"
    assert [finding["code"] for finding in assessment["findings"]] == [
        "legacy_risk_snapshot_unavailable"
    ]


def test_enhancement_is_rejected_if_it_would_reintroduce_an_unsupported_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _safe_copy()

    def unsafe_enhancer(*_args: Any, **_kwargs: Any) -> GeneratedCopy:
        return original.model_copy(update={"body": "保证有效。"})

    monkeypatch.setattr(
        "backend.api.v1.generations.enhance_generated_copy",
        unsafe_enhancer,
    )
    persistence = RecordingPersistence()

    response = _post(
        model_service=FixedModelService(original),
        persistence=persistence,
        data={"emoji_level": "light"},
    )

    assert response.status_code == 200
    assert response.json()["body"] == original.body
    assert persistence.successes[0].body == original.body


def test_advisory_scanner_failure_does_not_strand_a_pending_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_scanner(*_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        raise RuntimeError("private scanner implementation detail")

    monkeypatch.setattr(
        "backend.api.v1.generations.scan_content_risks",
        unavailable_scanner,
    )
    persistence = RecordingPersistence()

    response = _post(
        model_service=FixedModelService(_safe_copy()),
        persistence=persistence,
    )

    assert response.status_code == 200
    assert len(persistence.successes) == 1
    assert (
        persistence.successes[0].risk_assessment.model_dump(mode="json")
        == response.json()["risk_assessment"]
    )
    assert response.json()["risk_assessment"]["findings"] == [
        {
            "code": "risk_scan_unavailable",
            "severity": "high",
            "field": "body",
            "reason": "本地发布风险扫描暂时不可用，当前结果尚未完成该项检查。",
            "suggestion": "发布前请完整人工核对正文与话题标签，稍后可重新生成以再次检查。",
        }
    ]
