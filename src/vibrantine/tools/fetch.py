"""Fetch tool: issue an HTTP GET and return the response body.

The HTTP primitive of the std-lib tools layer. The deliverable is the
document, so a non-2xx final response is a structured failure rather
than a smaller success (unlike ShellTool, whose deliverable *is* the
exchange report); the error kind carries the caller's retry signal.
"""

import codecs
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
    deterministic: ClassVar[bool] = True

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(max_input_tokens=None)
        self._transport = transport

    async def _run(
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
        try:
            parsed = urlparse(input.url)
        except ValueError as exc:
            # urlparse itself raises on some malformed URLs (e.g. an
            # unclosed IPv6 bracket host); same caller mistake, same kind.
            return failure(
                "validation",
                f"url could not be parsed: {input.url!r} ({exc}).",
                retryable=False,
                provenance=prov,
            )
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return failure(
                "validation",
                f"url must be absolute with an http(s) scheme; got {input.url!r}.",
                retryable=False,
                provenance=prov,
            )

        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=input.timeout_seconds,
                    # httpx does not follow redirects by default; without this a
                    # 301 would sail through as "success" with a useless body.
                    follow_redirects=True,
                ) as client,
                client.stream("GET", input.url, headers=input.headers) as response,
            ):
                # Anything non-2xx fails; redirects are followed above, so a
                # 3xx here did not resolve to a final document.
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

                content, total_chars = await _bounded_body_slice(
                    response,
                    offset=input.offset,
                    max_chars=input.max_chars,
                )
                status_code = response.status_code
                content_type = response.headers.get("content-type")
        except httpx.TimeoutException:
            return failure(
                "timeout",
                f"Request to {input.url} exceeded {input.timeout_seconds}s.",
                retryable=True,
                provenance=prov,
            )
        except httpx.TooManyRedirects as exc:
            # A redirect loop is deterministic: retrying the same URL
            # unchanged can never succeed, unlike a transport blip.
            return failure(
                "internal",
                f"Redirect loop fetching {input.url}: {exc}",
                retryable=False,
                provenance=prov,
            )
        except httpx.HTTPError as exc:
            return failure(
                "internal",
                f"Transport error fetching {input.url}: {exc}",
                retryable=True,
                provenance=prov,
            )

        truncated = (input.offset + len(content)) < total_chars

        return CommissionResult[FetchOutput](
            status="success",
            output=FetchOutput(
                content=content,
                status_code=status_code,
                content_type=content_type,
                truncated=truncated,
                total_chars=total_chars,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )


async def _bounded_body_slice(
    response: httpx.Response,
    *,
    offset: int,
    max_chars: int,
) -> tuple[str, int]:
    """Drain and count a decoded response while retaining one bounded slice."""
    decoder_type = codecs.getincrementaldecoder(response.encoding or "utf-8")
    decoder = decoder_type(errors="replace")
    kept: list[str] = []
    total = 0

    async for chunk in response.aiter_bytes(chunk_size=8_192):
        text = decoder.decode(chunk)
        _append_overlap(kept, text, total=total, offset=offset, max_chars=max_chars)
        total += len(text)

    tail = decoder.decode(b"", final=True)
    _append_overlap(kept, tail, total=total, offset=offset, max_chars=max_chars)
    total += len(tail)
    return "".join(kept), total


def _append_overlap(
    kept: list[str],
    text: str,
    *,
    total: int,
    offset: int,
    max_chars: int,
) -> None:
    """Append only the part of `text` intersecting the requested body slice."""
    start = max(0, offset - total)
    stop = min(len(text), offset + max_chars - total)
    if start < stop:
        kept.append(text[start:stop])
