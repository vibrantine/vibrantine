"""Bind selected external MCP operations as ordinary typed Vibrantine Tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.tool_name_validation import validate_tool_name
from mcp.types import TextContent
from pydantic import BaseModel, ValidationError

from vibrantine.contract import (
    CallContext,
    CancelToken,
    Commission,
    CommissionResult,
    CostMetrics,
    ErrorKind,
    ErrorState,
    Provenance,
)

__all__ = ["MCPConnection", "MCPResult", "bind_mcp_tool", "open_mcp_http"]

_MAX_ARGUMENT_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 1024 * 1024
_MAX_ERROR_DETAIL_BYTES = 4096
_CANCEL_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class MCPResult:
    """Present the supported MCP result subset to an explicit output decoder."""

    structured_content: dict[str, Any] | None
    text_content: tuple[str, ...]
    is_error: bool


class _ConnectionClosed(Exception):
    pass


class _InvocationCancelled(Exception):
    pass


class _UnsupportedResultContent(Exception):
    pass


class MCPConnection:
    """Keep one initialized SDK session application-owned and shareable by bound Tools."""

    def __init__(self, session: ClientSession, *, source: str) -> None:
        self._session = session
        self._source = source
        self._active = True

    @property
    def source(self) -> str:
        """Identify the server without retaining URL credentials or query parameters."""
        return self._source

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResult:
        """Call one fixed-name operation for a bound Tool and normalize its result."""
        if not self._active:
            raise _ConnectionClosed

        response = await self._session.call_tool(name, arguments)
        text_content: list[str] = []
        for block in response.content:
            if not isinstance(block, TextContent):
                block_name = type(block).__name__
                raise _UnsupportedResultContent(f"unsupported MCP result content: {block_name}")
            text_content.append(block.text)

        return MCPResult(
            structured_content=response.structuredContent,
            text_content=tuple(text_content),
            is_error=response.isError,
        )

    def _close(self) -> None:
        self._active = False


@asynccontextmanager
async def open_mcp_http(url: str) -> AsyncGenerator[MCPConnection]:
    """Open one SDK v1 Streamable HTTP session without leaking its types downstream."""
    source = _safe_server_source(url)
    async with (
        streamable_http_client(url) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        connection = MCPConnection(session, source=source)
        try:
            yield connection
        finally:
            connection._close()  # pyright: ignore[reportPrivateUsage]


def bind_mcp_tool[InputT: BaseModel, OutputT: BaseModel](
    *,
    connection: MCPConnection,
    remote_name: str,
    name: str,
    description: str,
    input: type[InputT],
    output: type[OutputT],
    encode: Callable[[InputT], object] | None = None,
    decode: Callable[[MCPResult], object] | None = None,
    timeout_seconds: float = 30.0,
    deterministic: bool = False,
) -> Commission[InputT, OutputT]:
    """Bind one fixed remote operation behind a stable local typed Tool contract."""
    _require_tool_name(remote_name, field="remote_name")
    _require_tool_name(name, field="name")
    if not description.strip():
        raise ValueError("description must not be empty")
    _require_model_type(input, field="input")
    _require_model_type(output, field="output")
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and greater than zero")

    local_name = name
    local_description = description
    bound_input_type = input
    bound_output_type = output
    input_encoder = encode or _default_encode
    output_decoder = decode
    remote_tool_name = remote_name
    tool_timeout = timeout_seconds
    tool_deterministic = deterministic

    class _BoundMCPTool(Commission[InputT, OutputT]):
        name = local_name
        description = local_description
        input_type = bound_input_type
        output_type = bound_output_type
        deterministic = tool_deterministic

        async def _run(
            self,
            input: InputT,
            ctx: CallContext,
        ) -> CommissionResult[OutputT]:
            provenance = _provenance(connection.source, remote_tool_name)

            if ctx.cancel.is_cancelled:
                return _failure(
                    "cancelled",
                    "Cancelled before the MCP operation began.",
                    retryable=False,
                    provenance=provenance,
                )

            try:
                encoded: object = input_encoder(input)
            except Exception:
                return _failure(
                    "internal",
                    "The MCP input encoder failed.",
                    retryable=False,
                    provenance=provenance,
                )
            if not isinstance(encoded, dict):
                return _failure(
                    "internal",
                    "The MCP input encoder did not return an argument object.",
                    retryable=False,
                    provenance=provenance,
                )
            arguments = cast("dict[str, Any]", encoded)
            try:
                arguments_json = _canonical_json(arguments)
            except (TypeError, ValueError):
                return _failure(
                    "internal",
                    "The MCP input encoder returned data that is not valid JSON.",
                    retryable=False,
                    provenance=provenance,
                )
            if _utf8_size(arguments_json) > _MAX_ARGUMENT_BYTES:
                return _failure(
                    "validation",
                    f"MCP arguments exceed the {_MAX_ARGUMENT_BYTES}-byte request limit.",
                    retryable=False,
                    provenance=provenance,
                )

            try:
                result = await _call_with_controls(
                    connection,
                    remote_tool_name,
                    arguments,
                    cancel=ctx.cancel,
                    timeout_seconds=tool_timeout,
                )
            except _InvocationCancelled:
                return _failure(
                    "cancelled",
                    "Cancelled while the MCP operation was in progress.",
                    retryable=False,
                    provenance=provenance,
                )
            except TimeoutError:
                return _failure(
                    "timeout",
                    f"MCP operation exceeded {tool_timeout:g} seconds.",
                    retryable=True,
                    provenance=provenance,
                )
            except _ConnectionClosed:
                return _failure(
                    "internal",
                    "The MCP connection is closed.",
                    retryable=False,
                    provenance=provenance,
                )
            except _UnsupportedResultContent as exc:
                return _failure(
                    "internal",
                    _bounded_text(str(exc)),
                    retryable=False,
                    provenance=provenance,
                )
            except Exception:
                return _failure(
                    "internal",
                    "The MCP operation failed at the transport or protocol boundary.",
                    retryable=True,
                    provenance=provenance,
                )

            try:
                result_json = _canonical_json(
                    {
                        "structured_content": result.structured_content,
                        "text_content": result.text_content,
                    }
                )
            except (TypeError, ValueError):
                return _failure(
                    "internal",
                    "The MCP operation returned data that is not valid JSON.",
                    retryable=False,
                    provenance=provenance,
                )
            if _utf8_size(result_json) > _MAX_RESULT_BYTES:
                return _failure(
                    "output_too_large",
                    f"MCP result exceeds the {_MAX_RESULT_BYTES}-byte response limit.",
                    retryable=False,
                    provenance=provenance,
                )

            if result.is_error:
                detail = "\n".join(result.text_content).strip()
                if not detail:
                    detail = "The remote MCP operation returned an error."
                return _failure(
                    "internal",
                    _bounded_text(detail),
                    retryable=False,
                    provenance=provenance,
                )

            if output_decoder is None:
                if result.structured_content is None:
                    return _failure(
                        "internal",
                        "The MCP result has no structured content; bind an explicit decoder.",
                        retryable=False,
                        provenance=provenance,
                    )
                decoded: object = result.structured_content
            else:
                try:
                    decoded = output_decoder(result)
                except Exception:
                    return _failure(
                        "internal",
                        "The MCP output decoder failed.",
                        retryable=False,
                        provenance=provenance,
                    )

            try:
                validated = bound_output_type.model_validate(decoded)
            except ValidationError:
                return _failure(
                    "internal",
                    "The MCP result does not satisfy the bound output contract.",
                    retryable=False,
                    provenance=provenance,
                )

            return CommissionResult[OutputT](
                status="success",
                output=validated,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=0.0),
            )

    _BoundMCPTool.__name__ = name
    _BoundMCPTool.__qualname__ = name
    return _BoundMCPTool(max_input_tokens=None)


def _default_encode(input: BaseModel) -> dict[str, Any]:
    return input.model_dump(mode="json")


async def _call_with_controls(
    connection: MCPConnection,
    remote_name: str,
    arguments: dict[str, Any],
    *,
    cancel: CancelToken,
    timeout_seconds: float,
) -> MCPResult:
    call_task = asyncio.create_task(connection.call_tool(remote_name, arguments))
    cancel_task = asyncio.create_task(_wait_for_cancel(cancel))
    try:
        done, _ = await asyncio.wait(
            {call_task, cancel_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if call_task in done:
            return await call_task
        if cancel_task in done:
            raise _InvocationCancelled
        raise TimeoutError
    finally:
        await _stop_task(call_task)
        await _stop_task(cancel_task)


async def _wait_for_cancel(cancel: CancelToken) -> None:
    while not cancel.is_cancelled:
        await asyncio.sleep(_CANCEL_POLL_SECONDS)


async def _stop_task(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _require_tool_name(value: str, *, field: str) -> None:
    validation = validate_tool_name(value)
    if not validation.is_valid:
        detail = "; ".join(validation.warnings)
        raise ValueError(f"invalid {field} {value!r}: {detail}")


def _require_model_type(value: object, *, field: str) -> None:
    if not isinstance(value, type) or not issubclass(value, BaseModel):
        raise TypeError(f"{field} must be a Pydantic BaseModel type")


def _safe_server_source(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("MCP server URL must be absolute with an http(s) scheme")
    safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, "", ""))


def _provenance(server_source: str, remote_name: str) -> Provenance:
    return Provenance(
        source=f"mcp:{server_source}#{remote_name}",
        fetched_at=datetime.now(UTC),
        confidence="grounded",
    )


def _failure[OutputT](
    kind: ErrorKind,
    detail: str,
    *,
    retryable: bool,
    provenance: Provenance,
) -> CommissionResult[OutputT]:
    return cast(
        "CommissionResult[OutputT]",
        CommissionResult(
            status="failure",
            error=ErrorState(
                kind=kind,
                detail=_bounded_text(detail),
                retryable=retryable,
            ),
            provenance=provenance,
            cost=CostMetrics(estimated_usd=0.0),
        ),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _bounded_text(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= _MAX_ERROR_DETAIL_BYTES:
        return value
    suffix = b"..."
    prefix = encoded[: _MAX_ERROR_DETAIL_BYTES - len(suffix)].decode("utf-8", errors="ignore")
    return prefix + suffix.decode("ascii")
