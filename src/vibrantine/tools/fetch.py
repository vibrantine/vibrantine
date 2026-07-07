"""Fetch tool: issue an HTTP GET and return the response body.

The HTTP primitive of the std-lib tools layer. The deliverable is the
document, so a non-2xx final response is a structured failure rather
than a smaller success (unlike ShellTool, whose deliverable *is* the
exchange report); the error kind carries the caller's retry signal.
"""

from typing import ClassVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult, ErrorKind
from vibrantine.tools._helpers import ZERO_COST, failure, provenance

DEFAULT_TIMEOUT_SECONDS: float = 30.0
DEFAULT_MAX_CHARS: int = 50_000


class FetchInput(BaseModel):
    """Inputs for one HTTP GET."""

    url: str = Field(description="Absolute URL to fetch.")
    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional request headers.",
    )
    timeout_seconds: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        description="Per-request timeout in seconds.",
        gt=0.0,
    )
    offset: int = Field(
        default=0,
        description="Character offset into the decoded body to start returning from.",
        ge=0,
    )
    max_chars: int = Field(
        default=DEFAULT_MAX_CHARS,
        description="Maximum characters of the body to return, starting at offset.",
        gt=0,
    )


class FetchOutput(BaseModel):
    """Response payload from a successful fetch."""

    content: str = Field(description="Response body slice (decoded as text), from offset.")
    status_code: int = Field(description="HTTP status code returned by the server.")
    content_type: str | None = Field(
        default=None,
        description="Value of the response Content-Type header, if present.",
    )
    truncated: bool = Field(
        description="True if more body remains beyond the returned slice.",
    )
    total_chars: int = Field(
        description="Total length of the decoded body, in characters.",
    )


class FetchTool(Commission[FetchInput, FetchOutput]):
    """Issue an HTTP GET and return the response body, status, and content type."""

    name: ClassVar[str] = "fetch"
    description: ClassVar[str] = (
        "Issues an HTTP GET request to a URL and returns the response.\n"
        "\n"
        "Usage:\n"
        "- `url` must be absolute with a scheme (https:// or http://).\n"
        "- `headers` is an optional mapping (User-Agent, Authorization, etc.).\n"
        "- `timeout_seconds` defaults to 30; raise it for slow endpoints.\n"
        "- Redirects are followed automatically. Non-2xx final responses\n"
        "  and transport failures return ErrorState; only 2xx responses\n"
        "  populate `output`. HTTP 429 fails as rate_limit (retry later),\n"
        "  5xx as internal (retrying may succeed), any other non-2xx as\n"
        "  validation (the URL as given yields no document; do not retry\n"
        "  it unchanged).\n"
        "- The body is returned from `offset` (default 0) up to `max_chars`\n"
        "  (default 50000) characters. If `truncated` is true, more remains;\n"
        "  re-fetch with a higher `offset` to page through a large response.\n"
        "\n"
        "Returns `content` (the decoded body slice), `status_code`,\n"
        "`content_type` (Content-Type header if present), `truncated`, and\n"
        "`total_chars` (full body length)."
    )
    input_type: ClassVar[type] = FetchInput
    output_type: ClassVar[type] = FetchOutput

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(max_input_tokens=None)
        self._transport = transport

    async def invoke(
        self,
        input: FetchInput,
        ctx: CallContext,
    ) -> CommissionResult[FetchOutput]:
        prov = provenance(input.url)

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before request was issued.",
                retryable=False,
                provenance=prov,
            )

        # The description promises an absolute http(s) URL; checking it here
        # keeps a caller mistake classified as validation (non-retryable),
        # like every sibling tool, instead of surfacing as a transport error.
        parsed = urlparse(input.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return failure(
                "validation",
                f"url must be absolute with an http(s) scheme; got {input.url!r}.",
                retryable=False,
                provenance=prov,
            )

        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=input.timeout_seconds,
                # httpx does not follow redirects by default; without this a
                # 301 would sail through as "success" with a useless body.
                follow_redirects=True,
            ) as client:
                response = await client.get(input.url, headers=input.headers)
        except httpx.TimeoutException:
            return failure(
                "timeout",
                f"Request to {input.url} exceeded {input.timeout_seconds}s.",
                retryable=True,
                provenance=prov,
            )
        except httpx.HTTPError as exc:
            return failure(
                "internal",
                f"Transport error fetching {input.url}: {exc}",
                retryable=True,
                provenance=prov,
            )

        # Anything non-2xx fails; the description promises "only 2xx
        # responses populate output". Redirects are followed above, so a
        # 3xx here means redirection didn't resolve to a final document.
        # The kind is the caller's retry signal: 429 is the vocabulary's
        # own rate_limit; 5xx is the server malfunctioning, so retrying may
        # succeed; any other non-2xx means the URL as given yields no
        # document (ReadTool's missing file is the filesystem analog).
        if not response.is_success:
            status = response.status_code
            kind: ErrorKind
            if status == 429:
                kind, retryable = "rate_limit", True
            elif status >= 500:
                kind, retryable = "internal", True
            else:
                kind, retryable = "validation", False
            return failure(
                kind,
                f"HTTP {status} from {input.url}.",
                retryable=retryable,
                provenance=prov,
            )

        body = response.text
        total_chars = len(body)
        sliced = body[input.offset : input.offset + input.max_chars]
        truncated = (input.offset + len(sliced)) < total_chars

        return CommissionResult[FetchOutput](
            status="success",
            output=FetchOutput(
                content=sliced,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                truncated=truncated,
                total_chars=total_chars,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )
