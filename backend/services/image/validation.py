"""Secure validation for one uploaded JPEG, PNG, or WebP image."""

from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4

from anyio import CancelScope, to_thread
from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from backend.api.errors import APIError
from backend.core.config import Settings


READ_CHUNK_BYTES = 1024 * 1024
HEADER_BYTES = 16

FORMAT_BY_EXTENSION = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}
FORMAT_BY_MIME = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    """Trusted image metadata and its temporary local path."""

    path: Path
    image_format: str
    width: int
    height: int
    size_bytes: int


async def validate_uploaded_image(
    upload: UploadFile,
    settings: Settings,
) -> ValidatedImage:
    """Stream one upload to disk, then validate its declared and real format."""
    try:
        return await _validate_uploaded_image(upload, settings)
    finally:
        with CancelScope(shield=True):
            await upload.close()


async def _validate_uploaded_image(
    upload: UploadFile,
    settings: Settings,
) -> ValidatedImage:
    expected_format = _validate_declared_type(upload)
    max_size_bytes = settings.max_image_size_mb * 1024 * 1024

    if upload.size is not None and upload.size > max_size_bytes:
        raise _image_too_large(settings.max_image_size_mb)

    temp_path, size_bytes, header = await _stream_to_private_temp_file(
        upload,
        upload_dir=settings.resolved_upload_dir,
        max_size_bytes=max_size_bytes,
        max_size_mb=settings.max_image_size_mb,
    )

    try:
        magic_format = _detect_magic_format(header)
        if magic_format != expected_format:
            raise _unsupported_image(
                "图片扩展名、MIME 类型与实际文件内容不一致。"
            )

        image_format, width, height = await to_thread.run_sync(
            _decode_and_inspect,
            temp_path,
            expected_format,
            settings.max_image_pixels,
        )
        return ValidatedImage(
            path=temp_path,
            image_format=image_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
        )
    except BaseException:
        with CancelScope(shield=True):
            await _delete_path(temp_path)
        raise


async def delete_validated_image(image: ValidatedImage) -> None:
    """Remove a validated temporary upload after the request finishes."""
    with CancelScope(shield=True):
        await _delete_path(image.path)


def _validate_declared_type(upload: UploadFile) -> str:
    filename = (upload.filename or "").strip()
    if not filename:
        raise APIError(
            code="IMAGE_REQUIRED",
            message="请上传一张带文件名的图片。",
            status_code=400,
        )

    extension_format = FORMAT_BY_EXTENSION.get(Path(filename).suffix.casefold())
    content_type = (upload.content_type or "").partition(";")[0].strip().casefold()
    mime_format = FORMAT_BY_MIME.get(content_type)
    if extension_format is None or mime_format is None:
        raise _unsupported_image("仅支持 JPG、JPEG、PNG 和 WebP 图片。")
    if extension_format != mime_format:
        raise _unsupported_image("图片扩展名与 MIME 类型不一致。")
    return extension_format


async def _stream_to_private_temp_file(
    upload: UploadFile,
    *,
    upload_dir: Path,
    max_size_bytes: int,
    max_size_mb: int,
) -> tuple[Path, int, bytes]:
    await upload.seek(0)
    await to_thread.run_sync(_ensure_upload_directory, upload_dir)

    temp_path = upload_dir / f"{uuid4().hex}.upload"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = await to_thread.run_sync(
        os.open,
        temp_path,
        flags,
        0o600,
    )
    output = os.fdopen(file_descriptor, "wb")
    size_bytes = 0
    header = bytearray()

    try:
        while chunk := await upload.read(READ_CHUNK_BYTES):
            size_bytes += len(chunk)
            if size_bytes > max_size_bytes:
                raise _image_too_large(max_size_mb)
            if len(header) < HEADER_BYTES:
                header.extend(chunk[: HEADER_BYTES - len(header)])
            await to_thread.run_sync(output.write, chunk)

        await to_thread.run_sync(output.flush)
    except BaseException:
        with CancelScope(shield=True):
            try:
                await to_thread.run_sync(output.close)
            finally:
                await _delete_path(temp_path)
        raise
    else:
        with CancelScope(shield=True):
            await to_thread.run_sync(output.close)

    if size_bytes == 0:
        with CancelScope(shield=True):
            await _delete_path(temp_path)
        raise APIError(
            code="IMAGE_REQUIRED",
            message="上传的图片不能为空。",
            status_code=400,
        )

    return temp_path, size_bytes, bytes(header)


def _ensure_upload_directory(upload_dir: Path) -> None:
    upload_dir.mkdir(parents=True, exist_ok=True)


def _detect_magic_format(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
    return None


def _decode_and_inspect(
    path: Path,
    expected_format: str,
    max_image_pixels: int,
) -> tuple[str, int, int]:
    try:
        with Image.open(path) as image:
            image_format, width, height = _validate_open_image(
                image,
                expected_format,
                max_image_pixels,
            )
            image.verify()

        with Image.open(path) as image:
            image_format, width, height = _validate_open_image(
                image,
                expected_format,
                max_image_pixels,
            )
            image.load()
    except APIError:
        raise
    except Image.DecompressionBombError:
        raise _invalid_dimensions(max_image_pixels) from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise APIError(
            code="IMAGE_DECODE_FAILED",
            message="图片已损坏或无法解码。",
            status_code=422,
        ) from None

    return image_format, width, height


def _validate_open_image(
    image: Image.Image,
    expected_format: str,
    max_image_pixels: int,
) -> tuple[str, int, int]:
    image_format = (image.format or "").upper()
    width, height = image.size

    if width <= 0 or height <= 0 or width * height > max_image_pixels:
        raise _invalid_dimensions(max_image_pixels)
    if image_format != expected_format:
        raise _unsupported_image("图片声明格式与解码后的真实格式不一致。")
    return image_format, width, height


async def _delete_path(path: Path) -> None:
    await to_thread.run_sync(path.unlink, True)


def _unsupported_image(message: str) -> APIError:
    return APIError(
        code="UNSUPPORTED_IMAGE_TYPE",
        message=message,
        status_code=415,
    )


def _image_too_large(max_size_mb: int) -> APIError:
    return APIError(
        code="IMAGE_TOO_LARGE",
        message=f"单张图片不能超过 {max_size_mb} MB。",
        status_code=413,
    )


def _invalid_dimensions(max_image_pixels: int) -> APIError:
    return APIError(
        code="INVALID_IMAGE_DIMENSIONS",
        message=f"图片宽高无效或总像素超过 {max_image_pixels}。",
        status_code=422,
    )
