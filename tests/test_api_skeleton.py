"""Tests for the first runnable FastAPI generation endpoint."""

import asyncio
from datetime import datetime
from uuid import UUID

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from tests.support import build_test_app, make_image_bytes, send_request


def test_generation_endpoint_accepts_frozen_multipart_contract() -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            files={"image": ("sample.jpg", make_image_bytes("JPEG"), "image/jpeg")},
            data={
                "product_name": "测试商品",
                "target_audience": "学生",
                "tone": "自然",
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "generation_id",
        "image_summary",
        "title",
        "body",
        "tags",
        "created_at",
    }
    assert str(UUID(payload["generation_id"])) == payload["generation_id"]
    assert datetime.fromisoformat(payload["created_at"]).tzinfo is not None
    assert payload["image_summary"].startswith("测试模型已识别 JPEG 图片")
    assert payload["title"] == "测试生成标题"
    assert payload["tags"] == ["#接口测试", "#模型测试", "#结构化输出"]


def test_openapi_marks_image_as_a_binary_multipart_file() -> None:
    schema = build_test_app().openapi()
    operation = schema["paths"]["/api/v1/generations"]["post"]
    multipart_schema = operation["requestBody"]["content"]["multipart/form-data"]
    reference = multipart_schema["schema"]["$ref"].rsplit("/", 1)[-1]
    body_schema = schema["components"]["schemas"][reference]
    image_schema = body_schema["properties"]["image"]

    assert "image" in body_schema["required"]
    assert image_schema["type"] == "string"
    assert image_schema["format"] == "binary"
    error_schema_reference = operation["responses"]["502"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert error_schema_reference.endswith("/ErrorResponse")


def test_text_part_is_not_accepted_as_an_uploaded_image() -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            files={"image": (None, "not-a-file")},
        )
    )

    assert response.status_code == 415
    assert response.json() == {
        "error": {
            "code": "UNSUPPORTED_IMAGE_TYPE",
            "message": "图片必须通过文件上传控件提交。",
            "retryable": False,
        }
    }


def test_generation_endpoint_requires_an_image() -> None:
    response = asyncio.run(send_request("POST", "/api/v1/generations"))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMAGE_REQUIRED"


def test_optional_form_fields_can_all_be_omitted() -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 200


@pytest.mark.parametrize("field_name", ["product_name", "target_audience", "tone"])
def test_oversized_optional_form_field_uses_frozen_error_envelope(
    field_name: str,
) -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
            data={field_name: "x" * (1024 * 1024 + 1)},
        )
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "FORM_FIELD_TOO_LARGE",
            "message": "表单文本字段过大，请缩短后重试。",
            "retryable": False,
        }
    }
    assert "detail" not in response.json()


def test_optional_form_field_at_parser_limit_is_still_accepted() -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
            data={"product_name": "x" * (1024 * 1024)},
        )
    )

    assert response.status_code == 200


def test_unrelated_http_exception_keeps_fastapi_default_response() -> None:
    response = asyncio.run(send_request("GET", "/missing-route"))

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_method_not_allowed_keeps_default_response_and_allow_header() -> None:
    response = asyncio.run(send_request("GET", "/api/v1/generations"))

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}
    assert response.headers["allow"] == "POST"


def test_same_http_detail_without_parser_context_is_not_reclassified() -> None:
    application = build_test_app()

    @application.post("/synthetic-http-error")
    async def synthetic_http_error() -> None:
        raise StarletteHTTPException(
            status_code=400,
            detail="Part exceeded maximum size of 1024KB.",
        )

    response = asyncio.run(
        send_request(
            "POST",
            "/synthetic-http-error",
            application=application,
        )
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Part exceeded maximum size of 1024KB."
    }


def test_other_multipart_parser_error_is_not_reclassified() -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            headers={"Content-Type": "multipart/form-data"},
            content=b"",
        )
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Missing boundary in multipart."}


def test_configured_frontend_origin_passes_cors_preflight() -> None:
    response = asyncio.run(
        send_request(
            "OPTIONS",
            "/api/v1/generations",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_actual_post_from_configured_origin_gets_cors_permission_header() -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            headers={"Origin": "http://localhost:5173"},
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_actual_post_from_unconfigured_origin_gets_no_cors_permission_header() -> None:
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            headers={"Origin": "https://untrusted.example.com"},
            files={"image": ("sample.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
