"""Behavior tests for server-side uploaded-image validation."""

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from tests.support import build_test_app, make_image_bytes, send_request


def assert_error(response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == code
    assert payload["error"]["retryable"] is False
    assert payload["error"]["message"]


@pytest.mark.parametrize(
    ("image_format", "filename", "content_type"),
    [
        ("JPEG", "sample.jpg", "image/jpeg"),
        ("JPEG", "sample.jpeg", "image/jpeg"),
        ("PNG", "sample.png", "image/png"),
        ("WEBP", "sample.webp", "image/webp"),
    ],
)
def test_accepts_supported_real_images_and_removes_temp_file(
    tmp_path: Path,
    image_format: str,
    filename: str,
    content_type: str,
) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={
                "image": (
                    filename,
                    make_image_bytes(image_format),
                    content_type,
                )
            },
        )
    )

    assert response.status_code == 200
    assert f"测试模型已识别 {image_format} 图片" in response.json()["image_summary"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("sample.heic", "image/heic"),
        ("sample.heif", "image/heif"),
    ],
)
def test_accepts_heif_and_converts_it_to_model_ready_jpeg(
    tmp_path: Path,
    filename: str,
    content_type: str,
) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={
                "image": (filename, make_image_bytes("HEIF"), content_type),
            },
        )
    )

    assert response.status_code == 200
    assert "测试模型已识别 JPEG 图片" in response.json()["image_summary"]
    assert list(tmp_path.iterdir()) == []


def test_rejects_an_empty_upload(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("empty.png", b"", "image/png")},
        )
    )

    assert_error(response, status_code=400, code="IMAGE_REQUIRED")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("sample.gif", "image/gif"),
        ("sample.png", "image/jpeg"),
        ("sample", "image/png"),
    ],
)
def test_rejects_unsupported_or_inconsistent_declared_types(
    tmp_path: Path,
    filename: str,
    content_type: str,
) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": (filename, make_image_bytes(), content_type)},
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_file_whose_magic_bytes_do_not_match(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={
                "image": ("disguised.jpg", make_image_bytes("PNG"), "image/jpeg")
            },
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_corrupted_image_after_magic_check(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("broken.jpg", b"\xff\xd8\xffbroken", "image/jpeg")},
        )
    )

    assert_error(response, status_code=422, code="IMAGE_DECODE_FAILED")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_corrupted_heic_after_ftyp_magic_check(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    corrupted_heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("broken.heic", corrupted_heic, "image/heic")},
        )
    )

    assert_error(response, status_code=422, code="IMAGE_DECODE_FAILED")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_non_heif_file_disguised_as_heic(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("disguised.heic", make_image_bytes("PNG"), "image/heic")},
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []


def test_rejects_an_avif_container_disguised_as_heic(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    avif_header = b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00mif1avif"
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("disguised.heic", avif_header, "image/heic")},
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []


def test_rejects_avif_hidden_in_a_generic_heif_compatible_brand(
    tmp_path: Path,
) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    disguised_avif = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00heicavif"
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("disguised.heif", disguised_avif, "image/heif")},
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_heif_sequence_brand(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    sequence_header = b"\x00\x00\x00\x18ftyphevc\x00\x00\x00\x00mif1hevc"
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("sequence.heic", sequence_header, "image/heic")},
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_multiframe_heic_even_with_a_single_image_major_brand(
    tmp_path: Path,
) -> None:
    output = BytesIO()
    with Image.new("RGB", (2, 2), color=(255, 0, 0)) as first:
        with Image.new("RGB", (2, 2), color=(0, 0, 255)) as second:
            first.save(
                output,
                format="HEIF",
                save_all=True,
                append_images=[second],
            )

    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("burst.heic", output.getvalue(), "image/heic")},
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_file_over_the_configured_size_limit(tmp_path: Path) -> None:
    application = build_test_app(
        UPLOAD_DIR=str(tmp_path),
        MAX_IMAGE_SIZE_MB=1,
    )
    oversized = b"\x89PNG\r\n\x1a\n" + (b"x" * (1024 * 1024))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("large.png", oversized, "image/png")},
        )
    )

    assert_error(response, status_code=413, code="IMAGE_TOO_LARGE")
    assert list(tmp_path.iterdir()) == []


def test_accepts_a_valid_image_exactly_at_the_size_limit(tmp_path: Path) -> None:
    application = build_test_app(
        UPLOAD_DIR=str(tmp_path),
        MAX_IMAGE_SIZE_MB=1,
    )
    image = make_image_bytes()
    exact_limit = image + (b"\x00" * ((1024 * 1024) - len(image)))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("exact-limit.png", exact_limit, "image/png")},
        )
    )

    assert response.status_code == 200
    assert list(tmp_path.iterdir()) == []


def test_rejects_an_image_over_the_configured_pixel_limit(tmp_path: Path) -> None:
    application = build_test_app(
        UPLOAD_DIR=str(tmp_path),
        MAX_IMAGE_PIXELS=3,
    )
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("four-pixels.png", make_image_bytes(), "image/png")},
        )
    )

    assert_error(response, status_code=422, code="INVALID_IMAGE_DIMENSIONS")
    assert list(tmp_path.iterdir()) == []


def test_rejects_a_heif_image_over_the_configured_pixel_limit(
    tmp_path: Path,
) -> None:
    application = build_test_app(
        UPLOAD_DIR=str(tmp_path),
        MAX_IMAGE_PIXELS=5,
    )
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={
                "image": (
                    "six-pixels.heic",
                    make_image_bytes("HEIF", size=(3, 2)),
                    "image/heic",
                )
            },
        )
    )

    assert_error(response, status_code=422, code="INVALID_IMAGE_DIMENSIONS")
    assert list(tmp_path.iterdir()) == []


def test_accepts_an_image_exactly_at_the_pixel_limit(tmp_path: Path) -> None:
    application = build_test_app(
        UPLOAD_DIR=str(tmp_path),
        MAX_IMAGE_PIXELS=4,
    )
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files={"image": ("four-pixels.png", make_image_bytes(), "image/png")},
        )
    )

    assert response.status_code == 200
    assert list(tmp_path.iterdir()) == []


def test_rejects_multiple_image_parts(tmp_path: Path) -> None:
    application = build_test_app(UPLOAD_DIR=str(tmp_path))
    response = asyncio.run(
        send_request(
            "POST",
            "/api/v1/generations",
            application=application,
            files=[
                ("image", ("one.png", make_image_bytes(), "image/png")),
                ("image", ("two.png", make_image_bytes(), "image/png")),
            ],
        )
    )

    assert_error(response, status_code=415, code="UNSUPPORTED_IMAGE_TYPE")
    assert list(tmp_path.iterdir()) == []
