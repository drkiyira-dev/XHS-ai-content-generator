"""Internal types shared by model-backed generation services."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.services.image import ProcessedImage


GENERATED_TITLE_MAX_LENGTH = 20


class GeneratedCopy(BaseModel):
    """Validated copy returned by a vision-language model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image_summary: str = Field(min_length=1)
    title: str = Field(
        min_length=1,
        max_length=GENERATED_TITLE_MAX_LENGTH,
    )
    body: str = Field(min_length=1)
    tags: tuple[str, ...] = Field(min_length=3, max_length=5)

    @field_validator("image_summary", "title", "body", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        """Reject non-text or whitespace-only model fields."""
        if not isinstance(value, str):
            raise ValueError("model field must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("model field must not be empty")
        return normalized

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> object:
        """Normalize tag prefixes while preserving the 3-5 item contract."""
        if not isinstance(value, (list, tuple)):
            raise ValueError("tags must be an array")

        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("each tag must be a string")
            tag = item.strip()
            if not tag:
                raise ValueError("tags must not be empty")
            if not tag.startswith("#"):
                tag = f"#{tag}"
            if tag == "#":
                raise ValueError("tags must contain text")
            normalized.append(tag)
        return tuple(normalized)


class GenerationModelService(Protocol):
    """Interface used by the HTTP route and test doubles."""

    async def generate(
        self,
        image: ProcessedImage,
        *,
        product_name: str | None,
        target_audience: str | None,
        tone: str | None,
    ) -> GeneratedCopy:
        """Analyze one image and generate structured Xiaohongshu copy."""
