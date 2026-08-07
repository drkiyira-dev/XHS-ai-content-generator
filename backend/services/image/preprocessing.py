"""Normalize validated images before sending them to a vision model."""

from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4

from anyio import CancelScope, to_thread
from PIL import Image, ImageOps

from backend.core.config import Settings
from backend.services.image.validation import ValidatedImage


OUTPUT_SUFFIX = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}
OUTPUT_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """Model-ready image metadata and its private temporary path."""

    path: Path
    image_format: str
    mime_type: str
    width: int
    height: int
    exif_transposed: bool
    alpha_composited: bool
    resized: bool


async def preprocess_validated_image(
    image: ValidatedImage,
    settings: Settings,
) -> ProcessedImage:
    """Correct orientation, flatten alpha, resize, and strip metadata."""
    output_path = settings.resolved_upload_dir / (
        f"{uuid4().hex}{OUTPUT_SUFFIX[image.image_format]}"
    )
    try:
        return await to_thread.run_sync(
            _preprocess_sync,
            image,
            output_path,
            settings.model_max_image_edge,
        )
    except BaseException:
        with CancelScope(shield=True):
            await to_thread.run_sync(output_path.unlink, True)
        raise


async def delete_processed_image(image: ProcessedImage) -> None:
    """Remove a model-ready temporary image, including during cancellation."""
    with CancelScope(shield=True):
        await to_thread.run_sync(image.path.unlink, True)


def _preprocess_sync(
    validated: ValidatedImage,
    output_path: Path,
    max_edge: int,
) -> ProcessedImage:
    working: Image.Image | None = None

    try:
        with Image.open(validated.path) as source:
            orientation = source.getexif().get(274, 1)
            exif_transposed = orientation not in (None, 1)
            transposed = ImageOps.exif_transpose(source)

        try:
            oriented_size = transposed.size
            alpha_composited = _has_alpha(transposed)
            needs_trns_conversion = (
                "A" not in transposed.getbands()
                and "transparency" in transposed.info
            )

            if needs_trns_conversion:
                rgba = transposed.convert("RGBA")
                try:
                    _resize_in_place(rgba, max_edge)
                    resized = rgba.size != oriented_size
                    working = _flatten_rgba_onto_white(rgba)
                finally:
                    rgba.close()
            else:
                _resize_in_place(transposed, max_edge)
                resized = transposed.size != oriented_size
                working = _to_rgb(transposed, alpha_composited)
            working.info.clear()
        finally:
            transposed.close()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        _save_private_image(working, output_path, validated.image_format)

        return ProcessedImage(
            path=output_path,
            image_format=validated.image_format,
            mime_type=OUTPUT_MIME[validated.image_format],
            width=working.width,
            height=working.height,
            exif_transposed=exif_transposed,
            alpha_composited=alpha_composited,
            resized=resized,
        )
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        if working is not None:
            working.close()


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or "transparency" in image.info


def _to_rgb(image: Image.Image, has_alpha: bool) -> Image.Image:
    if not has_alpha:
        return image.convert("RGB")

    rgba = image.convert("RGBA")
    try:
        return _flatten_rgba_onto_white(rgba)
    finally:
        rgba.close()


def _flatten_rgba_onto_white(rgba: Image.Image) -> Image.Image:
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    try:
        composite = Image.alpha_composite(background, rgba)
        try:
            return composite.convert("RGB")
        finally:
            composite.close()
    finally:
        background.close()


def _resize_in_place(image: Image.Image, max_edge: int) -> None:
    image.thumbnail(
        (max_edge, max_edge),
        resample=Image.Resampling.LANCZOS,
        reducing_gap=3.0,
    )


def _save_private_image(
    image: Image.Image,
    path: Path,
    image_format: str,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = os.open(path, flags, 0o600)

    try:
        try:
            output = os.fdopen(file_descriptor, "wb")
        except BaseException:
            os.close(file_descriptor)
            raise

        with output:
            if image_format == "JPEG":
                image.save(
                    output,
                    format="JPEG",
                    quality=92,
                    subsampling=0,
                    optimize=True,
                    progressive=True,
                )
            elif image_format == "PNG":
                image.save(output, format="PNG", compress_level=6)
            elif image_format == "WEBP":
                image.save(output, format="WEBP", quality=92, method=4)
            else:  # Defensive: validation currently permits only these formats.
                raise ValueError(f"Unsupported normalized format: {image_format}")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
