"""Functional contract tests for exposing explicit Commissions as MCP tools."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, Field

from vibrantine import (
    CallContext,
    CancelToken,
    Commission,
    CommissionResult,
    CommissionStatus,
    CostMetrics,
    ErrorState,
    Provenance,
)
from vibrantine.mcp.server import create_commission_mcp_server

_FETCHED_AT = datetime(2026, 7, 23, tzinfo=UTC)

_BLOCK_MCP_IMPORT = """
import importlib.abc
import sys

class BlockMCP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "mcp" or fullname.startswith("mcp."):
            raise ModuleNotFoundError("MCP extra deliberately unavailable")
        return None

sys.meta_path.insert(0, BlockMCP())
import vibrantine
"""


class _FileReference(BaseModel):
    path: str = Field(description="Repository-relative path supplied to the probe.")


class _ProbeInput(BaseModel):
    subject: str = Field(description="Subject echoed by the probe.")
    files: list[_FileReference] = Field(
        default_factory=list,
        description="Files relevant to the probe.",
    )


class _ProbeOutput(BaseModel):
    answer: str = Field(description="Answer returned by the probe.")


class _ProbeCommission(Commission[_ProbeInput, _ProbeOutput]):
    name: ClassVar[str] = "probe_commission"
    description: ClassVar[str] = (
        "Probe MCP translation. Use this in adapter tests. Returns one typed answer."
    )
    input_type: ClassVar[type] = _ProbeInput
    output_type: ClassVar[type] = _ProbeOutput

    async def _run(
        self,
        input: _ProbeInput,
        ctx: CallContext,
    ) -> CommissionResult[_ProbeOutput]:
        raise AssertionError("the MCP adapter must use its supplied invocation function")


class _SecondProbeCommission(_ProbeCommission):
    name: ClassVar[str] = "second_probe"


class _InvalidNameCommission(_ProbeCommission):
    name: ClassVar[str] = "invalid tool name"


def _result(
    status: CommissionStatus = "success",
    *,
    answer: str = "translated",
) -> CommissionResult[_ProbeOutput]:
    error = None
    if status != "success":
        error = ErrorState(kind="validation", detail="incomplete", retryable=False)
    output = _ProbeOutput(answer=answer) if status != "failure" else None
    return CommissionResult[_ProbeOutput](
        status=status,
        output=output,
        error=error,
        provenance=Provenance(
            source="test:mcp",
            fetched_at=_FETCHED_AT,
            confidence="verified",
        ),
        cost=CostMetrics(estimated_usd=0.01, in_tokens=10, out_tokens=5),
        run_id="run-123",
        parent_run_id=None,
    )


async def _success_invoke(
    commission: Commission[Any, Any],
    input: BaseModel,
    *,
    cancel: CancelToken,
) -> CommissionResult[Any]:
    return _result()


def test_core_import_does_not_require_mcp_extra() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _BLOCK_MCP_IMPORT],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


async def test_tools_list_exposes_exact_registered_contracts() -> None:
    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(), _SecondProbeCommission()),
        invoke=_success_invoke,
    )

    assert isinstance(server, FastMCP)
    tools = await server.list_tools()

    assert [tool.name for tool in tools] == ["probe_commission", "second_probe"]
    assert tools[0].description == _ProbeCommission.description
    assert tools[0].inputSchema == _ProbeInput.model_json_schema()
    assert tools[0].outputSchema == CommissionResult[_ProbeOutput].model_json_schema()


async def test_official_in_memory_client_discovers_and_calls_commission() -> None:
    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=_success_invoke,
    )

    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
        response = await session.call_tool(
            "probe_commission",
            {"subject": "through the protocol"},
        )

    assert [tool.name for tool in listed.tools] == ["probe_commission"]
    assert response.isError is False
    assert response.structuredContent == _result().model_dump(mode="json")


@pytest.mark.parametrize(
    ("commissions", "message"),
    [
        ((), "at least one Commission"),
        ((_ProbeCommission(), _ProbeCommission()), "duplicate MCP tool name"),
        ((_InvalidNameCommission(),), "invalid MCP tool name"),
    ],
)
def test_invalid_registration_fails_before_server_start(
    commissions: tuple[Commission[Any, Any], ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        create_commission_mcp_server(commissions=commissions, invoke=_success_invoke)


def test_registration_requires_commissions_and_a_callable_runner() -> None:
    with pytest.raises(TypeError, match="every registered object"):
        create_commission_mcp_server(
            commissions=cast(Any, (object(),)),
            invoke=_success_invoke,
        )
    with pytest.raises(TypeError, match="invoke must be callable"):
        create_commission_mcp_server(
            commissions=(_ProbeCommission(),),
            invoke=cast(Any, "not callable"),
        )


async def test_valid_call_constructs_exact_input_and_invokes_once() -> None:
    calls: list[tuple[Commission[Any, Any], BaseModel, CancelToken]] = []

    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        calls.append((commission, input, cancel))
        return _result()

    commission = _ProbeCommission()
    server = create_commission_mcp_server(commissions=(commission,), invoke=invoke)

    response = await server.call_tool(
        "probe_commission",
        {"subject": "boundary", "files": [{"path": "README.md"}]},
    )

    assert isinstance(response, CallToolResult)
    assert len(calls) == 1
    assert calls[0][0] is commission
    assert calls[0][1] == _ProbeInput(
        subject="boundary",
        files=[_FileReference(path="README.md")],
    )
    assert calls[0][2].is_cancelled is False
    assert response.isError is False
    assert response.structuredContent == _result().model_dump(mode="json")
    assert isinstance(response.content[0], TextContent)
    assert json.loads(response.content[0].text) == response.structuredContent


async def test_invalid_nested_arguments_start_no_invocation() -> None:
    calls = 0

    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        nonlocal calls
        calls += 1
        return _result()

    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=invoke,
    )

    response = await server.call_tool(
        "probe_commission",
        {"subject": "boundary", "files": [{"path": 42}]},
    )

    assert isinstance(response, CallToolResult)
    assert calls == 0
    assert response.isError is True
    assert response.structuredContent is not None
    detail = response.structuredContent["adapter_error"]["detail"]
    assert "files.0.path" in detail
    assert "string" in detail


@pytest.mark.parametrize(
    ("status", "is_error"),
    [("success", False), ("partial", False), ("failure", True)],
)
async def test_commission_status_maps_without_losing_the_envelope(
    status: CommissionStatus,
    is_error: bool,
) -> None:
    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        return _result(status)

    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=invoke,
    )

    response = await server.call_tool("probe_commission", {"subject": "boundary"})

    assert isinstance(response, CallToolResult)
    assert response.isError is is_error
    assert response.structuredContent == _result(status).model_dump(mode="json")


async def test_unknown_tool_is_an_adapter_error() -> None:
    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=_success_invoke,
    )

    response = await server.call_tool("not_registered", {})

    assert isinstance(response, CallToolResult)
    assert response.isError is True
    assert response.structuredContent is not None
    assert response.structuredContent["adapter_error"]["kind"] == "unknown_tool"


@pytest.mark.parametrize("mode", ["raises", "wrong_return"])
async def test_broken_invocation_function_becomes_an_adapter_error(mode: str) -> None:
    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        if mode == "raises":
            raise RuntimeError("secret detail")
        return cast(Any, object())

    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=invoke,
    )

    response = await server.call_tool("probe_commission", {"subject": "boundary"})

    assert isinstance(response, CallToolResult)
    assert response.isError is True
    assert response.structuredContent is not None
    detail = response.structuredContent["adapter_error"]["detail"]
    assert "secret detail" not in detail
    assert response.structuredContent["adapter_error"]["kind"] == "internal"
