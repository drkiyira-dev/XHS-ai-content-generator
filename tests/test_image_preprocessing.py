"""Tests for model-ready image normalization."""

import asyncio
import os
from pathlib import Path

from PIL import Image
import pytest

from backend.services.image import preprocessing
from backend.services.image import (
    ValidatedImage,
    delete_processed_image,
    preprocess_validated_image,
)
from backend.core.config import Settings
from tests.support import make_image_bytes


def build_settings(tmp_path: Path, *, max_edge: int = 3584) -> Settings:
    return Settings(
        SILICONFLOW_API_KEY="test-secret-key",
        SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
        VISION_MODEL_NAME="qwen-test-model",
        OCR_MODEL_NAME="paddle-test-model",
        UPLOAD_DIR=str(tmp_path),
        MODEL_MAX_IMAGE_EDGE=max_edge,
        _env_file=None,
    )


def write_source(
    tmp_path: Path,
    content: bytes,
    *,
    image_format: str,
    width: int,
    height: int,
) -> ValidatedImage:
    path = tmp_path / "validated.upload"
    path.write_bytes(content)
    return ValidatedImage(
        path=path,
        image_format=image_format,
        width=width,
        height=height,
        size_bytes=len(content),
    )


def test_corrects_exif_orientation_and_removes_exif(tmp_path: Path) -> None:
    source_path = tmp_path / "oriented.upload"
    exif = Image.Exif()
    exif[274] = 6
    with Image.new("RGB", (4, 2), color=(20, 40, 60)) as source:
        source.save(source_path, format="JPEG", exif=exif)
    validated = ValidatedImage(
        path=source_path,
        image_format="JPEG",
        width=4,
        height=2,
        size_bytes=source_path.stat().st_size,
    )

    processed = asyncio.run(
        preprocess_validated_image(validated, build_settings(tmp_path))
    )
    try:
        assert processed.exif_transposed is True
        assert (processed.width, processed.height) == (2, 4)
        with Image.open(processed.path) as result:
            assert result.mode == "RGB"
            assert result.getexif().get(274) is None
    finally:
        asyncio.run(delete_processed_image(processed))


def test_composites_transparency_onto_white_rgb_background(tmp_path: Path) -> None:
    source_path = tmp_path / "transparent.upload"
    with Image.new("RGBA", (2, 2), color=(255, 0, 0, 0)) as source:
        source.putpixel((1, 1), (0, 0, 255, 255))
        source.save(source_path, format="PNG")
    validated = ValidatedImage(
        path=source_path,
        image_format="PNG",
        width=2,
        height=2,
        size_bytes=source_path.stat().st_size,
    )

    processed = asyncio.run(
        preprocess_validated_image(validated, build_settings(tmp_path))
    )
    try:
        assert processed.alpha_composited is True
        with Image.open(processed.path) as result:
            assert result.mode == "RGB"
            assert result.getpixel((0, 0)) == (255, 255, 255)
            assert result.getpixel((1, 1)) == (0, 0, 255)
    finally:
        asyncio.run(delete_processed_image(processed))


def test_handles_png_trns_transparency_and_strips_metadata(tmp_path: Path) -> None:
    source_path = tmp_path / "trns.upload"
    transparent_color = (10, 20, 30)
    with Image.new("RGB", (2, 1), color=transparent_color) as source:
        source.putpixel((1, 0), (0, 0, 255))
        source.save(
            source_path,
            format="PNG",
            transparency=transparent_color,
            icc_profile=b"test-icc-profile",
        )
    validated = ValidatedImage(
        path=source_path,
        image_format="PNG",
        width=2,
        height=1,
        size_bytes=source_path.stat().st_size,
    )

    processed = asyncio.run(
        preprocess_validated_image(validated, build_settings(tmp_path))
    )
    try:
        assert processed.alpha_composited is True
        with Image.open(processed.path) as result:
            result.load()
            assert result.mode == "RGB"
            assert result.getpixel((0, 0)) == (255, 255, 255)
            assert result.getpixel((1, 0)) == (0, 0, 255)
            for metadata_key in ("exif", "icc_profile", "xmp", "transparency"):
                assert metadata_key not in result.info
    finally:
        asyncio.run(delete_processed_image(processed))


def test_preserves_trns_transparency_semantics_while_resizing(tmp_path: Path) -> None:
    source_path = tmp_path / "trns-resize.upload"
    transparent_green = (0, 255, 0)
    with Image.new("RGB", (4, 1), color=transparent_green) as source:
        source.putpixel((2, 0), (255, 0, 0))
        source.putpixel((3, 0), (255, 0, 0))
        source.save(
            source_path,
            format="PNG",
            transparency=transparent_green,
        )
    validated = ValidatedImage(
        path=source_path,
        image_format="PNG",
        width=4,
        height=1,
        size_bytes=source_path.stat().st_size,
    )

    processed = asyncio.run(
        preprocess_validated_image(
            validated,
            build_settings(tmp_path, max_edge=2),
        )
    )
    try:
        with Image.open(processed.path) as result:
            result.load()
            left, right = result.getpixel((0, 0)), result.getpixel((1, 0))
            assert left[0] >= 250
            assert right[0] >= 250
            assert abs(left[1] - left[2]) <= 1
            assert abs(right[1] - right[2]) <= 1
    finally:
        asyncio.run(delete_processed_image(processed))


def test_resizes_without_changing_aspect_ratio_or_upscaling(tmp_path: Path) -> None:
    large = make_image_bytes(size=(400, 200))
    validated = write_source(
        tmp_path,
        large,
        image_format="PNG",
        width=400,
        height=200,
    )
    settings = build_settings(tmp_path, max_edge=100)

    processed = asyncio.run(preprocess_validated_image(validated, settings))
    try:
        assert processed.resized is True
        assert (processed.width, processed.height) == (100, 50)
    finally:
        asyncio.run(delete_processed_image(processed))

    small = write_source(
        tmp_path,
        make_image_bytes(size=(40, 20)),
        image_format="PNG",
        width=40,
        height=20,
    )
    processed_small = asyncio.run(preprocess_validated_image(small, settings))
    try:
        assert processed_small.resized is False
        assert (processed_small.width, processed_small.height) == (40, 20)
    finally:
        asyncio.run(delete_processed_image(processed_small))


def test_resizes_before_creating_rgb_or_alpha_composite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "large-transparent.upload"
    with Image.new("RGBA", (400, 200), color=(255, 0, 0, 100)) as source:
        source.save(source_path, format="PNG")
    validated = ValidatedImage(
        path=source_path,
        image_format="PNG",
        width=400,
        height=200,
        size_bytes=source_path.stat().st_size,
    )
    observed_conversion_sizes: list[tuple[int, int]] = []
    original_to_rgb = preprocessing._to_rgb

    def record_conversion_size(
        image: Image.Image,
        has_alpha: bool,
    ) -> Image.Image:
        observed_conversion_sizes.append(image.size)
        return original_to_rgb(image, has_alpha)

    monkeypatch.setattr(preprocessing, "_to_rgb", record_conversion_size)
    processed = asyncio.run(
        preprocess_validated_image(
            validated,
            build_settings(tmp_path, max_edge=100),
        )
    )
    try:
        assert observed_conversion_sizes == [(100, 50)]
        assert (processed.width, processed.height) == (100, 50)
    finally:
        asyncio.run(delete_processed_image(processed))


def test_output_is_private_and_uses_a_new_uuid_name(tmp_path: Path) -> None:
    validated = write_source(
        tmp_path,
        make_image_bytes("WEBP"),
        image_format="WEBP",
        width=2,
        height=2,
    )

    processed = asyncio.run(
        preprocess_validated_image(validated, build_settings(tmp_path))
    )
    try:
        assert processed.path != validated.path
        assert processed.path.suffix == ".webp"
        assert len(processed.path.stem) == 32
        assert os.stat(processed.path).st_mode & 0o777 == 0o600
        with Image.open(processed.path) as result:
            result.load()
            assert result.format == "WEBP"
            assert result.mode == "RGB"
    finally:
        asyncio.run(delete_processed_image(processed))
