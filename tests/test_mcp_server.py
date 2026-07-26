"""Functional contract tests for exposing explicit Commissions as MCP tools."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any, ClassVar, cast

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import (
    CallToolResult,
    CancelledNotification,
    CancelledNotificationParams,
    ClientNotification,
    TextContent,
)
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
    run_commission,
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

_STDIO_SONNET_SERVER = """
from vibrantine import CancelToken, Commission, CommissionResult, run_commission
from vibrantine.examples.compose_sonnet import ComposeSonnetCommission
from vibrantine.mcp.server import create_commission_mcp_server
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model

lines = [f"Line {number}" for number in range(1, 15)]
fake = ScriptedLLM([
    llm_response(tool_calls=[("c1", "conclude", {"title": "Stdio", "lines": lines})])
])
model = scripted_model(fake)

async def invoke(commission, input, *, cancel):
    return await run_commission(commission, input, models=[model], cancel=cancel)

server = create_commission_mcp_server(
    commissions=(ComposeSonnetCommission(),),
    invoke=invoke,
)
server.run(transport="stdio")
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
    name: ClassVar[str] = "invalid_name"


# The authoring boundary rejects this spelling at class definition. Mutate
# afterward to keep the MCP adapter's own defense-in-depth test reachable.
_InvalidNameCommission.name = "invalid tool name"


class _ConcurrentProbeCommission(_ProbeCommission):
    name: ClassVar[str] = "concurrent_probe"

    def __init__(self, gatekeepers: list[int], release: anyio.Event) -> None:
        super().__init__()
        self._gatekeepers = gatekeepers
        self._release = release

    async def _run(
        self,
        input: _ProbeInput,
        ctx: CallContext,
    ) -> CommissionResult[_ProbeOutput]:
        gatekeeper = ctx._gatekeeper  # pyright: ignore[reportPrivateUsage]
        assert gatekeeper is not None
        self._gatekeepers.append(id(gatekeeper))
        if len(self._gatekeepers) == 2:
            self._release.set()
        await self._release.wait()
        return _result().model_copy(update={"run_id": None})


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


async def test_stdio_client_discovers_validates_invokes_and_shuts_down_cleanly() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-c", _STDIO_SONNET_SERVER],
        cwd=Path.cwd(),
    )
    with TemporaryFile(mode="w+", encoding="utf-8") as diagnostics:
        with anyio.fail_after(10):
            async with stdio_client(parameters, errlog=diagnostics) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    invalid = await session.call_tool("compose_vibrantine_sonnet", {})
                    response = await session.call_tool(
                        "compose_vibrantine_sonnet",
                        {"subject": "protocol boundaries"},
                    )

    assert [tool.name for tool in listed.tools] == ["compose_vibrantine_sonnet"]
    assert invalid.isError is True
    assert invalid.structuredContent is not None
    assert invalid.structuredContent["adapter_error"]["kind"] == "validation"
    assert response.isError is False
    assert response.structuredContent is not None
    assert response.structuredContent["status"] == "success"
    assert len(response.structuredContent["output"]["lines"]) == 14


async def test_repository_launcher_exposes_only_the_sonnet_commission() -> None:
    from vibrantine.examples.sonnet_mcp_server import server

    tools = await server.list_tools()

    assert [tool.name for tool in tools] == ["compose_vibrantine_sonnet"]


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


async def test_oversized_arguments_are_rejected_before_validation_or_invocation() -> None:
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
        {"subject": "x" * (1024 * 1024)},
    )

    assert isinstance(response, CallToolResult)
    assert calls == 0
    assert response.isError is True
    assert response.structuredContent is not None
    assert "1048576-byte MCP request limit" in response.structuredContent["adapter_error"]["detail"]


async def test_validation_detail_has_a_fixed_issue_bound() -> None:
    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=_success_invoke,
    )

    response = await server.call_tool(
        "probe_commission",
        {"subject": "boundary", "files": [{"path": number} for number in range(100)]},
    )

    assert isinstance(response, CallToolResult)
    assert response.structuredContent is not None
    detail = response.structuredContent["adapter_error"]["detail"]
    assert "files.7.path" in detail
    assert "files.8.path" not in detail
    assert "and 92 more validation errors" in detail
    assert len(detail.encode("utf-8")) <= 4096


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


async def test_large_compatibility_text_becomes_a_valid_json_notice() -> None:
    result = _result(answer="x" * 70_000)

    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        return result

    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=invoke,
    )

    response = await server.call_tool("probe_commission", {"subject": "boundary"})

    assert isinstance(response, CallToolResult)
    assert response.isError is False
    assert response.structuredContent == result.model_dump(mode="json")
    assert isinstance(response.content[0], TextContent)
    notice = json.loads(response.content[0].text)
    assert notice == {
        "notice": "Complete CommissionResult is available only in structuredContent.",
        "run_id": "run-123",
    }


async def test_mcp_revalidation_rejects_bypass_built_malformed_envelope() -> None:
    malformed = CommissionResult[_ProbeOutput].model_construct(
        status="success",
        output=None,
        error=None,
        provenance=Provenance(
            source="test:mcp",
            fetched_at=_FETCHED_AT,
            confidence="verified",
        ),
        cost=CostMetrics(estimated_usd=0.0),
    )

    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        return malformed

    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=invoke,
    )
    response = await server.call_tool("probe_commission", {"subject": "boundary"})

    assert isinstance(response, CallToolResult)
    assert response.isError is True
    assert response.structuredContent is not None
    assert response.structuredContent["adapter_error"]["kind"] == "internal"
    assert (
        "invalid CommissionResult envelope" in response.structuredContent["adapter_error"]["detail"]
    )


async def test_oversized_result_envelope_returns_bounded_error_with_run_id() -> None:
    result = _result(answer="x" * 1_100_000)

    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        return result

    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=invoke,
    )

    response = await server.call_tool("probe_commission", {"subject": "boundary"})

    assert isinstance(response, CallToolResult)
    assert response.isError is True
    assert response.structuredContent is not None
    error = response.structuredContent["adapter_error"]
    assert error["kind"] == "output_too_large"
    assert "run-123" in error["detail"]
    assert isinstance(response.content[0], TextContent)
    assert len(response.content[0].text.encode("utf-8")) <= 4096


async def test_mcp_cancellation_reaches_the_request_token() -> None:
    started = anyio.Event()
    call_done = anyio.Event()
    observed_during_unwind: list[bool] = []
    captured_tokens: list[CancelToken] = []
    call_errors: list[McpError] = []

    async def invoke(
        commission: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        captured_tokens.append(cancel)
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            observed_during_unwind.append(cancel.is_cancelled)
        raise AssertionError("sleep_forever returned")

    server = create_commission_mcp_server(
        commissions=(_ProbeCommission(),),
        invoke=invoke,
    )

    async with create_connected_server_and_client_session(server) as session:
        request_id = session._request_id  # pyright: ignore[reportPrivateUsage]

        async def call() -> None:
            try:
                await session.call_tool("probe_commission", {"subject": "cancel me"})
            except McpError as exc:
                call_errors.append(exc)
            finally:
                call_done.set()

        with anyio.fail_after(2):
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(call)
                await started.wait()
                await session.send_notification(
                    ClientNotification(
                        CancelledNotification(
                            params=CancelledNotificationParams(
                                requestId=request_id,
                                reason="test cancellation",
                            )
                        )
                    )
                )
                await call_done.wait()

    assert observed_during_unwind == [True]
    assert len(captured_tokens) == 1
    assert captured_tokens[0].is_cancelled is True
    assert len(call_errors) == 1
    assert "cancelled" in str(call_errors[0]).lower()


async def test_simultaneous_calls_are_independent_root_runs() -> None:
    gatekeepers: list[int] = []
    release = anyio.Event()
    commission = _ConcurrentProbeCommission(gatekeepers, release)
    responses: list[CallToolResult] = []

    async def invoke(
        selected: Commission[Any, Any],
        input: BaseModel,
        *,
        cancel: CancelToken,
    ) -> CommissionResult[Any]:
        return await run_commission(selected, input, cancel=cancel)

    server = create_commission_mcp_server(commissions=(commission,), invoke=invoke)

    async def call(subject: str) -> None:
        response = await server.call_tool("concurrent_probe", {"subject": subject})
        assert isinstance(response, CallToolResult)
        responses.append(response)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(call, "first")
            task_group.start_soon(call, "second")

    assert len(responses) == 2
    run_ids: set[str] = set()
    for response in responses:
        assert response.structuredContent is not None
        run_id = response.structuredContent["run_id"]
        assert isinstance(run_id, str)
        run_ids.add(run_id)
        assert response.structuredContent["parent_run_id"] is None
    assert len(run_ids) == 2
    assert len(set(gatekeepers)) == 2


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
async def test_broken_invocation_function_becomes_an_adapter_error(
    mode: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "probe_commission" in captured.err
    if mode == "raises":
        assert "RuntimeError" in captured.err
