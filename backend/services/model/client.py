"""Minimal asynchronous client for SiliconFlow Chat Completions."""

from dataclasses import dataclass
from typing import Any, Literal

from anyio import fail_after, get_cancelled_exc_class
import httpx

from backend.core.config import Settings


@dataclass(frozen=True, slots=True)
class ProviderCompletion:
    """Safe subset of a successful provider response."""

    content: str
    finish_reason: str | None
    trace_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """Sanitized provider failure with no exception traceback or request body."""

    kind: Literal["timeout", "request", "response"]
    status_code: int | None = None
    trace_id: str | None = None


class SiliconFlowClient:
    """Shared HTTP client for SiliconFlow's OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        timeout_seconds = float(settings.model_timeout_seconds)
        base_url = f"{str(settings.siliconflow_base_url).rstrip('/')}/"
        self._total_timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Accept": "application/json",
                "Authorization": (
                    "Bearer "
                    f"{settings.siliconflow_api_key.get_secret_value()}"
                ),
            },
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=min(10.0, timeout_seconds),
                pool=min(5.0, timeout_seconds),
            ),
            follow_redirects=False,
            transport=transport,
        )

    @property
    def is_closed(self) -> bool:
        """Expose lifecycle state for application shutdown verification."""
        return self._client.is_closed

    async def create_chat_completion(
        self,
        payload: dict[str, Any],
    ) -> ProviderCompletion | ProviderFailure:
        """POST one non-streaming chat completion and validate its envelope."""
        cancelled_error_class = get_cancelled_exc_class()
        response: httpx.Response | None = None
        timed_out = False
        request_failed = False
        request_cancelled = False
        cancellation_args: tuple[object, ...] = ()
        client: httpx.AsyncClient | None = self._client
        try:
            with fail_after(self._total_timeout_seconds):
                response = await client.post(
                    "chat/completions",
                    json=payload,
                )
        except cancelled_error_class as error:
            request_cancelled = True
            cancellation_args = tuple(error.args)
        except (TimeoutError, httpx.TimeoutException):
            timed_out = True
        except httpx.RequestError:
            request_failed = True

        if request_cancelled:
            # Re-raise outside HTTPX frames after removing request references.
            # Preserving args lets an enclosing AnyIO cancel scope recognize
            # and suppress only the cancellation that it initiated itself.
            payload.clear()
            response = None
            client = None
            self = None
            raise cancelled_error_class(*cancellation_args)

        if timed_out:
            return ProviderFailure(kind="timeout")
        if request_failed:
            return ProviderFailure(kind="request")
        if response is None:  # Defensive: every expected path is handled above.
            return ProviderFailure(kind="request")

        trace_id = response.headers.get("x-siliconcloud-trace-id")
        if not 200 <= response.status_code < 300:
            return ProviderFailure(
                kind="request",
                status_code=response.status_code,
                trace_id=trace_id,
            )

        invalid_json = False
        body: object = None
        try:
            body = response.json()
        except ValueError:
            invalid_json = True
        if invalid_json:
            return ProviderFailure(kind="response", trace_id=trace_id)

        if not isinstance(body, dict):
            return ProviderFailure(kind="response", trace_id=trace_id)
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return ProviderFailure(kind="response", trace_id=trace_id)
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ProviderFailure(kind="response", trace_id=trace_id)
        message = first_choice.get("message")
        if not isinstance(message, dict):
            return ProviderFailure(kind="response", trace_id=trace_id)
        content = message.get("content")
        finish_reason = first_choice.get("finish_reason")
        if not isinstance(content, str) or not content.strip():
            return ProviderFailure(kind="response", trace_id=trace_id)
        if finish_reason is not None and not isinstance(finish_reason, str):
            return ProviderFailure(kind="response", trace_id=trace_id)

        return ProviderCompletion(
            content=content,
            finish_reason=finish_reason,
            trace_id=trace_id,
        )

    async def aclose(self) -> None:
        """Release pooled HTTP connections during application shutdown."""
        await self._client.aclose()
