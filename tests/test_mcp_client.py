"""Contract tests for binding selected external MCP operations as typed Tools."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import BaseModel, Field

import vibrantine.mcp.client as mcp_client
from vibrantine import CancelToken, create_commission, run_commission
from vibrantine.mcp.client import MCPConnection, MCPResult, bind_mcp_tool, open_mcp_http
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response, scripted_model


class QueryInput(BaseModel):
    query: str = Field(description="Question sent to the remote operation.")


class QueryOutput(BaseModel):
    answer: str = Field(description="Answer returned by the remote operation.")


class GuideInput(BaseModel):
    question: str = Field(description="Question the guide should answer.")


class GuideOutput(BaseModel):
    answer: str = Field(description="Final answer grounded in the remote result.")


class _FakeSession:
    def __init__(
        self,
        response: CallToolResult | None = None,
        *,
        delay_seconds: float = 0.0,
        failure: Exception | None = None,
    ) -> None:
        self.response = response or _structured_response("bound")
        self.delay_seconds = delay_seconds
        self.failure = failure
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.started = asyncio.Event()
        self.cancelled = False

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        self.calls.append((name, arguments or {}))
        self.started.set()
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.failure is not None:
            raise self.failure
        return self.response


class _MutableCancel:
    def __init__(self) -> None:
        self.cancelled = False

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled


def _structured_response(answer: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=answer)],
        structuredContent={"answer": answer},
    )


def _connection(session: _FakeSession, *, source: str = "https://mcp.test/mcp") -> MCPConnection:
    return MCPConnection(cast(ClientSession, session), source=source)


def _encode_non_object(_: QueryInput) -> str:
    return "not an object"


def _encode_non_json(_: QueryInput) -> dict[str, object]:
    return {"bad": object()}


def _decoder_failure(_: MCPResult) -> QueryOutput:
    raise ValueError("secret detail")


def _tool(
    session: _FakeSession,
    *,
    encode: Any = None,
    decode: Any = None,
    timeout_seconds: float = 30.0,
    deterministic: bool = False,
):
    return bind_mcp_tool(
        connection=_connection(session),
        remote_name="remote_query",
        name="local_query",
        description=(
            "Query the bound remote knowledge service. Use this for test questions. "
            "Returns one typed answer."
        ),
        input=QueryInput,
        output=QueryOutput,
        encode=encode,
        decode=decode,
        timeout_seconds=timeout_seconds,
        deterministic=deterministic,
    )


def test_binding_builds_an_ordinary_typed_tool() -> None:
    tool = _tool(_FakeSession(), deterministic=True)

    assert tool.name == "local_query"
    assert tool.input_type is QueryInput
    assert tool.output_type is QueryOutput
    assert tool.max_input_tokens is None
    assert tool.deterministic is True
    assert type(tool).__name__ == "local_query"


@pytest.mark.parametrize(
    ("overrides", "error", "message"),
    [
        ({"name": "bad name"}, ValueError, "invalid name"),
        ({"remote_name": "bad name"}, ValueError, "invalid remote_name"),
        ({"description": "  "}, ValueError, "description"),
        ({"input": cast(Any, str)}, TypeError, "input must be"),
        ({"output": cast(Any, str)}, TypeError, "output must be"),
        ({"timeout_seconds": 0.0}, ValueError, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, ValueError, "timeout_seconds"),
    ],
)
def test_invalid_bindings_fail_at_construction(
    overrides: dict[str, Any],
    error: type[Exception],
    message: str,
) -> None:
    kwargs: dict[str, Any] = {
        "connection": _connection(_FakeSession()),
        "remote_name": "remote_query",
        "name": "local_query",
        "description": "Return one answer.",
        "input": QueryInput,
        "output": QueryOutput,
    }
    kwargs.update(overrides)

    with pytest.raises(error, match=message):
        bind_mcp_tool(**kwargs)


async def test_default_binding_sends_model_json_and_validates_structured_output() -> None:
    session = _FakeSession(_structured_response("translated"))

    result = await run_commission(_tool(session), QueryInput(query="hello"))

    assert result.status == "success", result.error
    assert result.output == QueryOutput(answer="translated")
    assert session.calls == [("remote_query", {"query": "hello"})]
    assert result.provenance.source == "mcp:https://mcp.test/mcp#remote_query"
    assert result.cost.estimated_usd == 0.0


async def test_bound_tool_runs_inside_an_llm_commission_toolbox() -> None:
    session = _FakeSession(_structured_response("remote evidence"))
    tool = _tool(session)
    guide = create_commission(
        name="repository_guide",
        description="Answer one repository question using the bound knowledge Tool.",
        input=GuideInput,
        output=GuideOutput,
        toolbox=(tool,),
    )
    model = ScriptedLLM(
        [
            llm_response(
                tool_calls=[
                    (
                        "call-1",
                        "local_query",
                        {"query": "How is the client structured?"},
                    )
                ]
            ),
            llm_response(
                tool_calls=[
                    (
                        "call-2",
                        "conclude",
                        {"answer": "The client uses the remote evidence."},
                    )
                ]
            ),
        ]
    )

    result = await run_commission(
        guide,
        GuideInput(question="How is the client structured?"),
        models=[scripted_model(model)],
    )

    assert result.status == "success", result.error
    assert result.output == GuideOutput(answer="The client uses the remote evidence.")
    assert session.calls == [("remote_query", {"query": "How is the client structured?"})]


async def test_custom_encoder_and_text_decoder_handle_an_irregular_remote_contract() -> None:
    session = _FakeSession(CallToolResult(content=[TextContent(type="text", text="plain answer")]))

    def encode(input: QueryInput) -> dict[str, str]:
        return {"questionText": input.query}

    def decode(result: MCPResult) -> QueryOutput:
        return QueryOutput(answer=result.text_content[0])

    result = await run_commission(
        _tool(session, encode=encode, decode=decode),
        QueryInput(query="hello"),
    )

    assert result.status == "success", result.error
    assert result.output == QueryOutput(answer="plain answer")
    assert session.calls == [("remote_query", {"questionText": "hello"})]


@pytest.mark.parametrize(
    ("encode", "detail"),
    [
        (_encode_non_object, "did not return an argument object"),
        (_encode_non_json, "not valid JSON"),
    ],
)
async def test_invalid_encoder_results_fail_without_calling_mcp(
    encode: Any,
    detail: str,
) -> None:
    session = _FakeSession()

    result = await run_commission(
        _tool(session, encode=encode),
        QueryInput(query="hello"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert detail in result.error.detail
    assert session.calls == []


async def test_plain_text_without_a_decoder_fails_explicitly() -> None:
    session = _FakeSession(CallToolResult(content=[TextContent(type="text", text="answer")]))

    result = await run_commission(_tool(session), QueryInput(query="hello"))

    assert result.status == "failure"
    assert result.error is not None
    assert "no structured content" in result.error.detail


async def test_decoder_and_output_validation_failures_are_values() -> None:
    decoder_failure = await run_commission(
        _tool(
            _FakeSession(),
            decode=_decoder_failure,
        ),
        QueryInput(query="hello"),
    )
    invalid_output = await run_commission(
        _tool(_FakeSession(CallToolResult(content=[], structuredContent={"wrong": "shape"}))),
        QueryInput(query="hello"),
    )

    assert decoder_failure.status == "failure"
    assert decoder_failure.error is not None
    assert decoder_failure.error.detail == "The MCP output decoder failed."
    assert "secret detail" not in decoder_failure.error.detail
    assert invalid_output.status == "failure"
    assert invalid_output.error is not None
    assert "does not satisfy" in invalid_output.error.detail


async def test_remote_error_is_bounded_and_never_decoded() -> None:
    detail = "x" * 10_000
    session = _FakeSession(
        CallToolResult(
            content=[TextContent(type="text", text=detail)],
            isError=True,
        )
    )
    decoder_called = False

    def decode(_: MCPResult) -> QueryOutput:
        nonlocal decoder_called
        decoder_called = True
        return QueryOutput(answer="wrong")

    result = await run_commission(
        _tool(session, decode=decode),
        QueryInput(query="hello"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert len(result.error.detail.encode("utf-8")) <= 4096
    assert result.error.detail.endswith("...")
    assert decoder_called is False


async def test_unsupported_content_fails_without_discarding_it_silently() -> None:
    session = _FakeSession(
        CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data="aGVsbG8=",
                    mimeType="image/png",
                )
            ]
        )
    )

    result = await run_commission(_tool(session), QueryInput(query="hello"))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.detail == "unsupported MCP result content: ImageContent"


async def test_large_arguments_and_results_are_bounded() -> None:
    request_session = _FakeSession()
    request = await run_commission(
        _tool(request_session),
        QueryInput(query="x" * (1024 * 1024)),
    )
    response = await run_commission(
        _tool(_FakeSession(_structured_response("x" * (1024 * 1024)))),
        QueryInput(query="hello"),
    )

    assert request.status == "failure"
    assert request.error is not None
    assert request.error.kind == "validation"
    assert request_session.calls == []
    assert response.status == "failure"
    assert response.error is not None
    assert response.error.kind == "output_too_large"


async def test_cancelled_before_call_returns_without_contacting_mcp() -> None:
    session = _FakeSession()

    result = await run_commission(
        _tool(session),
        QueryInput(query="hello"),
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert session.calls == []


async def test_in_flight_cancellation_cancels_the_sdk_call() -> None:
    session = _FakeSession(delay_seconds=30.0)
    cancel = _MutableCancel()
    run = asyncio.create_task(
        run_commission(
            _tool(session),
            QueryInput(query="hello"),
            cancel=cast(CancelToken, cancel),
        )
    )
    await session.started.wait()

    cancel.cancelled = True
    result = await asyncio.wait_for(run, timeout=1.0)

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert session.cancelled is True


async def test_timeout_cancels_the_sdk_call() -> None:
    session = _FakeSession(delay_seconds=30.0)

    result = await run_commission(
        _tool(session, timeout_seconds=0.01),
        QueryInput(query="hello"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "timeout"
    assert result.error.retryable is True
    assert session.cancelled is True


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("credential=secret"),
        TypeError("credential=secret"),
    ],
)
async def test_transport_failure_and_closed_connection_are_values(
    failure: Exception,
) -> None:
    transport = await run_commission(
        _tool(_FakeSession(failure=failure)), QueryInput(query="hello")
    )
    session = _FakeSession()
    connection = _connection(session)
    closed_tool = bind_mcp_tool(
        connection=connection,
        remote_name="remote_query",
        name="local_query",
        description="Return one answer.",
        input=QueryInput,
        output=QueryOutput,
    )
    connection._close()  # pyright: ignore[reportPrivateUsage]
    closed = await run_commission(closed_tool, QueryInput(query="hello"))

    assert transport.status == "failure"
    assert transport.error is not None
    assert "credential=secret" not in transport.error.detail
    assert transport.error.retryable is True
    assert closed.status == "failure"
    assert closed.error is not None
    assert closed.error.detail == "The MCP connection is closed."


async def test_one_connection_supports_concurrent_bound_tools() -> None:
    session = _FakeSession()
    connection = _connection(session)
    first = bind_mcp_tool(
        connection=connection,
        remote_name="remote_first",
        name="local_first",
        description="Return the first answer.",
        input=QueryInput,
        output=QueryOutput,
    )
    second = bind_mcp_tool(
        connection=connection,
        remote_name="remote_second",
        name="local_second",
        description="Return the second answer.",
        input=QueryInput,
        output=QueryOutput,
    )

    first_result, second_result = await asyncio.gather(
        run_commission(first, QueryInput(query="one")),
        run_commission(second, QueryInput(query="two")),
    )

    assert first_result.status == "success"
    assert second_result.status == "success"
    assert sorted(session.calls) == [
        ("remote_first", {"query": "one"}),
        ("remote_second", {"query": "two"}),
    ]


async def test_http_context_initializes_once_and_hides_url_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    initialized = 0
    exited = 0

    @asynccontextmanager
    async def fake_transport(_: str):
        yield object(), object(), lambda: None

    class FakeClientSession:
        def __init__(self, *_: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            nonlocal exited
            exited += 1

        async def initialize(self) -> None:
            nonlocal initialized
            initialized += 1

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, Any] | None = None,
        ) -> CallToolResult:
            return await session.call_tool(name, arguments)

    monkeypatch.setattr(mcp_client, "streamable_http_client", fake_transport)
    monkeypatch.setattr(mcp_client, "ClientSession", FakeClientSession)

    async with open_mcp_http("https://user:password@mcp.test/mcp?token=secret") as connection:
        tool = bind_mcp_tool(
            connection=connection,
            remote_name="remote_query",
            name="local_query",
            description="Return one answer.",
            input=QueryInput,
            output=QueryOutput,
        )
        result = await run_commission(tool, QueryInput(query="hello"))

    assert result.status == "success"
    assert result.provenance.source == "mcp:https://mcp.test/mcp#remote_query"
    assert initialized == 1
    assert exited == 1

    after_close = await run_commission(tool, QueryInput(query="again"))
    assert after_close.status == "failure"
    assert after_close.error is not None
    assert after_close.error.detail == "The MCP connection is closed."


def test_client_module_surface_is_exact() -> None:
    assert set(mcp_client.__all__) == {
        "MCPConnection",
        "MCPResult",
        "bind_mcp_tool",
        "open_mcp_http",
    }
