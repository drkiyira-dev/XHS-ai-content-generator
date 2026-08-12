"""Create a small metadata-free image for owner-scoped history previews."""

from dataclasses import dataclass, field
from io import BytesIO

from anyio import to_thread
from PIL import Image, UnidentifiedImageError

from backend.services.image.preprocessing import ProcessedImage


PREVIEW_MAX_EDGE = 480
PREVIEW_MAX_BYTES = 256 * 1024
PREVIEW_MIN_EDGE = 128
PREVIEW_MEDIA_TYPE = "image/webp"
_WEBP_QUALITY_STEPS = (78, 68, 58, 48, 38)


class HistoryPreviewError(Exception):
    """A history preview could not be created without exposing image details."""

    def __init__(self) -> None:
        super().__init__("history image preview could not be created")


@dataclass(frozen=True, slots=True)
class HistoryImagePreview:
    """Bounded image bytes safe to hand to the persistence boundary."""

    content: bytes = field(repr=False)
    media_type: str
    width: int
    height: int


async def create_history_image_preview(
    image: ProcessedImage,
) -> HistoryImagePreview:
    """Downsample an already-normalized model image without blocking the loop."""
    try:
        return await to_thread.run_sync(
            _create_history_image_preview_sync,
            image,
            # The request finally block deletes ``image.path``. Wait for this
            # bounded conversion instead of abandoning a worker that could
            # still be reading the file during cleanup.
            abandon_on_cancel=False,
        )
    except HistoryPreviewError:
        raise
    except Exception:
        raise HistoryPreviewError() from None


def _create_history_image_preview_sync(
    image: ProcessedImage,
) -> HistoryImagePreview:
    working: Image.Image | None = None
    try:
        with Image.open(image.path) as source:
            source.load()
            if (
                source.format != image.image_format
                or source.size != (image.width, image.height)
                or source.width <= 0
                or source.height <= 0
            ):
                raise HistoryPreviewError()
            working = source.convert("RGB")
        working.info.clear()
        working.thumbnail(
            (PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE),
            resample=Image.Resampling.LANCZOS,
            reducing_gap=3.0,
        )

        while True:
            for quality in _WEBP_QUALITY_STEPS:
                content = _encode_webp(working, quality=quality)
                if 0 < len(content) <= PREVIEW_MAX_BYTES:
                    return HistoryImagePreview(
                        content=content,
                        media_type=PREVIEW_MEDIA_TYPE,
                        width=working.width,
                        height=working.height,
                    )

            largest_edge = max(working.size)
            if largest_edge <= PREVIEW_MIN_EDGE:
                raise HistoryPreviewError()
            next_edge = max(PREVIEW_MIN_EDGE, int(largest_edge * 0.8))
            working.thumbnail(
                (next_edge, next_edge),
                resample=Image.Resampling.LANCZOS,
                reducing_gap=3.0,
            )
    except (HistoryPreviewError, UnidentifiedImageError):
        raise HistoryPreviewError() from None
    except Exception:
        raise HistoryPreviewError() from None
    finally:
        if working is not None:
            working.close()


def _encode_webp(image: Image.Image, *, quality: int) -> bytes:
    output = BytesIO()
    image.save(
        output,
        format="WEBP",
        quality=quality,
        method=4,
        exact=True,
    )
    return output.getvalue()


__all__ = [
    "HistoryImagePreview",
    "HistoryPreviewError",
    "PREVIEW_MAX_BYTES",
    "PREVIEW_MAX_EDGE",
    "PREVIEW_MEDIA_TYPE",
    "create_history_image_preview",
]
