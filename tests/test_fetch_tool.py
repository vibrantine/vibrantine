"""Tests for the Fetch tool."""

from datetime import UTC, datetime, timedelta

import httpx

from vibrantine.contract import CallContext
from vibrantine.dispatch import dispatch
from vibrantine.tools.fetch import FetchInput, FetchTool


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


def _handler_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        text="hello world",
        headers={"content-type": "text/plain"},
    )


def _handler_404(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, text="not found")


def _handler_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("simulated timeout", request=request)


async def test_fetch_success_populates_output_and_provenance() -> None:
    transport = httpx.MockTransport(_handler_ok)
    tool = FetchTool(transport=transport)
    before = datetime.now(UTC)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/hello"),
        CallContext(),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.content == "hello world"
    assert result.output.status_code == 200
    assert result.output.content_type == "text/plain"
    assert result.provenance.source == "https://example.test/hello"
    assert result.provenance.confidence == "grounded"
    skew = timedelta(seconds=5)
    after = datetime.now(UTC)
    assert before - skew <= result.provenance.fetched_at <= after + skew
    assert result.cost.estimated_usd == 0.0


async def test_fetch_404_returns_validation_failure() -> None:
    # The URL as given yields no document; retrying it unchanged can't help.
    # The filesystem analog is ReadTool's missing file, also "validation".
    transport = httpx.MockTransport(_handler_404)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/missing"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False
    assert result.cost.estimated_usd == 0.0


def _handler_429(request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, text="slow down")


async def test_fetch_429_returns_rate_limit_failure() -> None:
    transport = httpx.MockTransport(_handler_429)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/busy"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.error.retryable is True


def _handler_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="server error")


async def test_fetch_500_returns_internal_retryable_failure() -> None:
    transport = httpx.MockTransport(_handler_500)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/broken"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert result.error.retryable is True


async def test_fetch_rejects_url_without_scheme_before_any_request() -> None:
    # The description promises an absolute http(s) URL; a caller mistake is
    # validation (non-retryable), not a transport error. The mock transport
    # would fail loudly if a request were issued.
    transport = httpx.MockTransport(_handler_ok)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="example.test/no-scheme"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False
    assert "http(s) scheme" in result.error.detail


async def test_fetch_rejects_non_http_scheme() -> None:
    transport = httpx.MockTransport(_handler_ok)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="ftp://example.test/file"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False


async def test_fetch_timeout_returns_timeout_failure() -> None:
    transport = httpx.MockTransport(_handler_timeout)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/slow", timeout_seconds=0.1),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "timeout"
    assert result.error.retryable is True


async def test_fetch_cancelled_before_request_returns_cancelled() -> None:
    transport = httpx.MockTransport(_handler_ok)
    tool = FetchTool(transport=transport)
    ctx = CallContext(cancel=_AlwaysCancelled())

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/anything"),
        ctx,
    )

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert result.error.retryable is False


def _handler_big(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        text="x" * 200,
        headers={"content-type": "text/plain"},
    )


async def test_fetch_bounds_body_and_signals_truncation() -> None:
    transport = httpx.MockTransport(_handler_big)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/big", max_chars=50),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert len(result.output.content) == 50
    assert result.output.truncated is True
    assert result.output.total_chars == 200


async def test_fetch_offset_paginates_the_body() -> None:
    transport = httpx.MockTransport(_handler_big)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/big", offset=180, max_chars=50),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert len(result.output.content) == 20  # only 20 chars remain past offset 180
    assert result.output.truncated is False
    assert result.output.total_chars == 200


def _handler_redirect(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/old":
        return httpx.Response(301, headers={"location": "https://example.test/new"})
    return httpx.Response(200, text="moved here", headers={"content-type": "text/plain"})


async def test_fetch_follows_redirects_to_the_final_document() -> None:
    transport = httpx.MockTransport(_handler_redirect)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/old"),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.content == "moved here"
    assert result.output.status_code == 200


def _handler_bare_redirect(request: httpx.Request) -> httpx.Response:
    # A 3xx with no Location header: httpx cannot follow it, so it is the
    # final response and must fail (only 2xx populates output).
    return httpx.Response(304)


async def test_fetch_non_2xx_final_response_is_a_failure() -> None:
    transport = httpx.MockTransport(_handler_bare_redirect)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/cached"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    # An unresolved 3xx means the URL as given yields no document; like a
    # 4xx, retrying it unchanged can't help.
    assert result.error.kind == "validation"
    assert result.error.retryable is False
    assert "304" in result.error.detail


async def test_fetch_small_body_not_truncated() -> None:
    transport = httpx.MockTransport(_handler_ok)  # returns "hello world" (11 chars)
    tool = FetchTool(transport=transport)

    result = await dispatch(
        tool,
        FetchInput(url="https://example.test/hello"),
        CallContext(),
    )

    assert result.output is not None
    assert result.output.content == "hello world"
    assert result.output.truncated is False
    assert result.output.total_chars == 11
