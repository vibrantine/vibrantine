"""Expose an explicit application-owned Commission set through standard MCP tools."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, cast

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


class _RequestCancelToken:
    """Give every invocation its own token before protocol cancellation is connected."""

    @property
    def is_cancelled(self) -> bool:
        return False


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
            commission_input = registration.input_type.model_validate(arguments)
        except ValidationError as exc:
            return _adapter_error(name, "validation", _validation_detail(exc))

        cancel = _RequestCancelToken()
        try:
            result = await self._invoke(
                registration.commission,
                commission_input,
                cancel=cancel,
            )
        except Exception as exc:
            return _adapter_error(
                name,
                "internal",
                f"The invocation function raised {type(exc).__name__}.",
            )

        if not isinstance(result, CommissionResult):
            return _adapter_error(
                name,
                "internal",
                "The invocation function did not return a CommissionResult.",
            )

        try:
            validated = registration.result_type.model_validate(result.model_dump(mode="json"))
        except ValidationError:
            return _adapter_error(
                name,
                "internal",
                "The invocation function returned an invalid CommissionResult envelope.",
            )

        structured = validated.model_dump(mode="json")
        text = _canonical_json(structured)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
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
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "arguments"
        details.append(f"{location}: {error['msg']}")
    return "Invalid arguments: " + "; ".join(details)


def _adapter_error(tool_name: str, kind: str, detail: str) -> CallToolResult:
    structured = {
        "adapter_error": {
            "kind": kind,
            "tool": tool_name,
            "detail": detail,
        }
    }
    return CallToolResult(
        content=[TextContent(type="text", text=_canonical_json(structured))],
        structuredContent=structured,
        isError=True,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
