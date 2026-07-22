"""Expose an explicit application-owned Commission set through standard MCP tools."""

from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, cast

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.shared.tool_name_validation import validate_tool_name
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, ValidationError

from vibrantine.contract import Commission, CommissionResult

__all__ = ["create_commission_mcp_server"]

_SERVER_NAME = "vibrantine_commissions"
_SERVER_INSTRUCTIONS = (
    "Each tool is an independent Vibrantine Commission invocation. Calls do not "
    "share run or conversation state. Results are complete CommissionResult "
    "envelopes; inspect status, output, error, cost, and provenance. Correct "
    "invalid arguments and retry only when safe."
)
_MAX_ARGUMENT_BYTES = 1024 * 1024
_MAX_ENVELOPE_BYTES = 1024 * 1024
_MAX_COMPATIBILITY_TEXT_BYTES = 64 * 1024
_MAX_ERROR_DETAIL_BYTES = 4096
_MAX_VALIDATION_ISSUES = 8


class _RequestCancelToken:
    """Reflect cancellation while inside the SDK scope and retain it afterward."""

    def __init__(self) -> None:
        self._cancelled = False

    @property
    def is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        try:
            return anyio.current_effective_deadline() == float("-inf")
        except RuntimeError:
            return False

    def cancel(self) -> None:
        self._cancelled = True


@dataclass(frozen=True)
class _Registration:
    commission: Commission[Any, Any]
    input_type: type[BaseModel]
    result_type: type[CommissionResult[Any]]
    tool: MCPTool


class _CommissionMCPServer(FastMCP[None]):
    """Keep MCP translation private while retaining the official SDK server lifecycle."""

    def __init__(
        self,
        registrations: tuple[_Registration, ...],
        invoke: Callable[..., Awaitable[CommissionResult[Any]]],
    ) -> None:
        self._registrations = {item.commission.name: item for item in registrations}
        self._invoke = cast(Callable[..., Awaitable[object]], invoke)
        super().__init__(
            name=_SERVER_NAME,
            instructions=_SERVER_INSTRUCTIONS,
            warn_on_duplicate_tools=False,
        )

    async def list_tools(self) -> list[MCPTool]:
        return [registration.tool for registration in self._registrations.values()]

    async def call_tool(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        registration = self._registrations.get(name)
        if registration is None:
            return _adapter_error(name, "unknown_tool", f"No Commission is registered as {name!r}.")

        try:
            arguments_text = _canonical_json(arguments)
        except (TypeError, ValueError):
            return _adapter_error(name, "validation", "Arguments are not valid JSON data.")
        if _utf8_size(arguments_text) > _MAX_ARGUMENT_BYTES:
            return _adapter_error(
                name,
                "validation",
                f"Arguments exceed the {_MAX_ARGUMENT_BYTES}-byte MCP request limit.",
            )

        try:
            commission_input = registration.input_type.model_validate(arguments)
        except ValidationError as exc:
            return _adapter_error(name, "validation", _validation_detail(exc))

        cancel = _RequestCancelToken()
        cancelled_exception = anyio.get_cancelled_exc_class()
        try:
            result = await self._invoke(
                registration.commission,
                commission_input,
                cancel=cancel,
            )
        except cancelled_exception:
            cancel.cancel()
            raise
        except Exception as exc:
            _diagnose(name, f"invocation raised {type(exc).__name__}")
            return _adapter_error(
                name,
                "internal",
                "The invocation function failed.",
            )

        if not isinstance(result, CommissionResult):
            _diagnose(name, f"invocation returned {type(result).__name__}")
            return _adapter_error(
                name,
                "internal",
                "The invocation function did not return a CommissionResult.",
            )

        try:
            validated = registration.result_type.model_validate(result.model_dump(mode="json"))
        except ValidationError:
            _diagnose(name, "invocation returned an invalid CommissionResult")
            return _adapter_error(
                name,
                "internal",
                "The invocation function returned an invalid CommissionResult envelope.",
            )

        try:
            structured = validated.model_dump(mode="json")
            text = _canonical_json(structured)
        except (TypeError, ValueError):
            _diagnose(name, "CommissionResult could not be encoded as JSON")
            return _adapter_error(
                name,
                "internal",
                "The CommissionResult envelope could not be encoded safely.",
            )

        run_id = validated.run_id or "unknown"
        if _utf8_size(text) > _MAX_ENVELOPE_BYTES:
            return _adapter_error(
                name,
                "output_too_large",
                (
                    f"CommissionResult for run {run_id!r} exceeds the "
                    f"{_MAX_ENVELOPE_BYTES}-byte MCP envelope limit."
                ),
            )

        compatibility_text = text
        if _utf8_size(text) > _MAX_COMPATIBILITY_TEXT_BYTES:
            compatibility_text = _canonical_json(
                {
                    "notice": "Complete CommissionResult is available only in structuredContent.",
                    "run_id": run_id,
                }
            )
        return CallToolResult(
            content=[TextContent(type="text", text=compatibility_text)],
            structuredContent=structured,
            isError=validated.status == "failure",
        )


def create_commission_mcp_server(
    *,
    commissions: Iterable[Commission[Any, Any]],
    invoke: Callable[..., Awaitable[CommissionResult[Any]]],
) -> FastMCP[Any]:
    """Translate an explicit Commission set without taking ownership of run policy."""
    if not callable(invoke):
        raise TypeError("invoke must be callable")

    supplied = tuple(commissions)
    if not supplied:
        raise ValueError("at least one Commission must be supplied")

    registrations: list[_Registration] = []
    names: set[str] = set()
    for commission in supplied:
        candidate: object = commission
        registration = _build_registration(candidate, names)
        registrations.append(registration)
        names.add(commission.name)

    return _CommissionMCPServer(tuple(registrations), invoke)


def _build_registration(
    commission: object,
    existing_names: set[str],
) -> _Registration:
    if not isinstance(commission, Commission):
        raise TypeError("every registered object must be a Commission")

    name = commission.name
    name_validation = validate_tool_name(name)
    if not name_validation.is_valid:
        detail = "; ".join(name_validation.warnings)
        raise ValueError(f"invalid MCP tool name {name!r}: {detail}")
    if name in existing_names:
        raise ValueError(f"duplicate MCP tool name: {name!r}")

    input_type = _require_model_type(commission.input_type, name)
    output_type = _require_model_type(commission.output_type, name)

    result_type = cast(type[CommissionResult[Any]], CommissionResult[output_type])
    input_schema = input_type.model_json_schema()
    result_schema = result_type.model_json_schema()
    try:
        _canonical_json(input_schema)
        _canonical_json(result_schema)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"Commission {name!r} has a non-serializable schema") from exc

    return _Registration(
        commission=cast(Commission[Any, Any], commission),
        input_type=input_type,
        result_type=result_type,
        tool=MCPTool(
            name=name,
            description=commission.description,
            inputSchema=input_schema,
            outputSchema=result_schema,
        ),
    )


def _require_model_type(value: object, commission_name: str) -> type[BaseModel]:
    if not isinstance(value, type) or not issubclass(value, BaseModel):
        raise TypeError(
            f"Commission {commission_name!r} must declare Pydantic input and output types"
        )
    return value


def _validation_detail(exc: ValidationError) -> str:
    details: list[str] = []
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    for error in errors[:_MAX_VALIDATION_ISSUES]:
        location = ".".join(str(part) for part in error["loc"]) or "arguments"
        details.append(f"{location}: {error['msg']}")
    if len(errors) > _MAX_VALIDATION_ISSUES:
        details.append(f"and {len(errors) - _MAX_VALIDATION_ISSUES} more validation errors")
    return _bounded_text("Invalid arguments: " + "; ".join(details))


def _adapter_error(tool_name: str, kind: str, detail: str) -> CallToolResult:
    structured = {
        "adapter_error": {
            "kind": kind,
            "tool": tool_name,
            "detail": _bounded_text(detail),
        }
    }
    return CallToolResult(
        content=[TextContent(type="text", text=_canonical_json(structured))],
        structuredContent=structured,
        isError=True,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _bounded_text(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= _MAX_ERROR_DETAIL_BYTES:
        return value
    suffix = b"..."
    prefix = encoded[: _MAX_ERROR_DETAIL_BYTES - len(suffix)].decode("utf-8", errors="ignore")
    return prefix + suffix.decode("ascii")


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _diagnose(tool_name: str, detail: str) -> None:
    message = _bounded_text(f"vibrantine MCP adapter [{tool_name}]: {detail}")
    print(message, file=sys.stderr, flush=True)
