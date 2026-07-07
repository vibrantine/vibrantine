"""LLM-tool wrapper + LLM dispatch loop.

Three pieces, kept in one module while there's only one caller:

- `as_llm_tool(commission)` turns any Commission or Tool into the OpenAI
  function-calling descriptor `{"type": "function", "function": {...}}`.
  Used to fill the `tools=` parameter on a chat.completions call.

- `make_conclude_tool(output_type)` builds the framework-injected
  `conclude` tool. Its schema mirrors the consuming Commission's
  declared `output_type`; the LLM signals completion by calling it with
  args that validate as that type. No free-form "the LLM said done."

- `run_llm_loop(...)` runs the call-dispatch-feed cycle until the
  LLM calls conclude, the budget is exhausted, max_iterations is hit,
  or the call is cancelled. Returns a typed `LoopOutcome` the caller
  packages into its own `CommissionResult`.

Provenance and the final cost USD belong to the consuming Commission,
not the loop; the loop returns token counts and lets the Commission
do the model-specific pricing lookup.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionContentPartParam,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)
from pydantic import BaseModel, ValidationError

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    ContentPart,
    ErrorKind,
    ErrorState,
    TextPart,
)
from vibrantine.dispatch import deposit_llm_trace, dispatch

# One line per LLM round-trip at INFO (the httpx convention for "a network
# call happened"); loop pathologies worth a human's attention at WARNING;
# self-correcting chatter at DEBUG. Emit only; the application sets the volume.
logger = logging.getLogger(__name__)

CONCLUDE_TOOL_NAME = "conclude"


def as_llm_tool(commission: Commission[Any, Any]) -> ChatCompletionToolParam:
    """Render a Commission or Tool as an OpenAI tool-call descriptor."""
    return {
        "type": "function",
        "function": {
            "name": commission.name,
            "description": commission.description,
            "parameters": commission.input_type.model_json_schema(),
        },
    }


def make_conclude_tool(output_type: type[BaseModel]) -> ChatCompletionToolParam:
    """Build the framework-injected conclude tool for an LLM-loop Commission.

    Its parameter schema equals the Commission's declared output_type.
    Calling it with valid args is the only way for the LLM to exit the
    loop with a structured success.
    """
    return {
        "type": "function",
        "function": {
            "name": CONCLUDE_TOOL_NAME,
            "description": (
                "Finalize and return your typed result. Call this exactly "
                "once when you have a complete answer. The arguments you "
                "pass become the Commission's output."
            ),
            "parameters": output_type.model_json_schema(),
        },
    }


@dataclass(frozen=True)
class LoopOutcome[OutputT]:
    """What `run_llm_loop` returns to its caller.

    On success: `output` populated, `error` is None.
    On failure: `error` populated, `output` is None.
    Token counts are always populated so the caller can compute cost.
    `children_cost` is the summed cost of every sub-Commission this loop
    dispatched, so the caller can roll it into its own CommissionResult.cost.
    """

    output: OutputT | None
    error: ErrorState | None
    in_tokens: int
    out_tokens: int
    children_cost: float = 0.0


async def run_llm_loop[OutputT: BaseModel](
    *,
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_message: str | list[ContentPart],
    toolbox: Sequence[Commission[Any, Any]],
    output_type: type[OutputT],
    ctx: CallContext,
    max_iterations: int,
    prices_per_million: tuple[float, float],
) -> LoopOutcome[OutputT]:
    """Run the LLM call-dispatch-feed cycle until conclude or a stop condition.

    Stop conditions: conclude tool called, budget exceeded, max_iterations
    hit, cancellation, no tool call returned by the LLM (treated as a
    failure; the loop disallows free-form completion).

    Budget flows down as allocation: each dispatched child receives the
    grant minus everything already spent (own turns plus prior children),
    so ceilings only shrink down the tree and a delegating Commission
    cannot spend a multiple of its grant through same-turn children.

    Tool errors are fed back to the LLM as tool results, not raised as
    Commission failures; the LLM gets to decide whether to retry or
    conclude with an apologetic answer.
    """
    in_price, out_price = prices_per_million
    # Capability ceiling: the LLM is only offered the intersection of this
    # Commission's toolbox and ctx.capabilities. None = unrestricted. A
    # forbidden tool is simply absent from the menu, so any call to it falls
    # through the unknown-tool branch below; no separate gate. `conclude` is
    # framework-injected and never gated. See docs/design.md.
    allowed = ctx.capabilities.tools
    permitted = [c for c in toolbox if allowed is None or c.name in allowed]
    tools: list[ChatCompletionToolParam] = [
        *(as_llm_tool(c) for c in permitted),
        make_conclude_tool(output_type),
    ]
    by_name: dict[str, Commission[Any, Any]] = {c.name: c for c in permitted}

    # A Commission with no system prompt sends no system message at all;
    # some providers reject empty system content, and an empty message
    # carries nothing worth a cache slot.
    messages: list[ChatCompletionMessageParam] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": _to_provider_content(user_message)})

    # The transcript escapes via the trace mailbox on every exit path
    # (conclude, budget stop, iteration cap, cancellation, a raise), so
    # failure runs, the ones worth autopsying, are recorded too.
    try:
        in_tokens = 0
        out_tokens = 0
        # Summed cost of every sub-Commission dispatched below. Folded into the
        # budget check and returned so the caller's CommissionResult.cost includes
        # the subtree: the structural cost rollup the contract promises.
        children_cost = 0.0
        # One free-text reply (no tool call) gets a corrective nudge instead of
        # failing the whole run; smaller models drift into prose often enough
        # that forfeiting the entire spend on the first slip is a bad trade. The
        # second slip fails as before.
        nudged_for_missing_tool_call = False
        # Failed conclude attempts change what the iteration-cap error must say:
        # "never called conclude" points at prompting, "called it N times but the
        # args never validated" points at the output type's shape. Conflating the
        # two sent a live debugging session in the wrong direction.
        conclude_failures = 0
        last_conclude_error = ""

        for _ in range(max_iterations):
            if ctx.cancel.is_cancelled:
                return LoopOutcome(
                    output=None,
                    error=ErrorState(
                        kind="cancelled",
                        detail="Cancelled between loop iterations.",
                        retryable=False,
                    ),
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )

            try:
                response: ChatCompletion = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
            except openai.RateLimitError as exc:
                return _loop_error(
                    "rate_limit",
                    f"Rate limit from LLM provider: {exc}",
                    retryable=True,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )
            except openai.APIError as exc:
                return _loop_error(
                    "internal",
                    f"LLM provider error: {exc}",
                    retryable=True,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )

            usage = response.usage
            if usage is not None:
                in_tokens += usage.prompt_tokens
                out_tokens += usage.completion_tokens
                logger.info(
                    "LLM turn model=%s in=%d out=%d",
                    model,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                )

            # Own token cost plus everything dispatched children spent, so a
            # recursive or sub-Commission-bearing loop enforces the budget against
            # its whole subtree, not just its own turns (may overshoot by one turn).
            own_cost = (in_tokens * in_price + out_tokens * out_price) / 1_000_000
            cost_so_far = own_cost + children_cost
            if ctx.budget_usd is not None and cost_so_far > ctx.budget_usd:
                return _loop_error(
                    "budget_exceeded",
                    f"Cost ${cost_so_far:.6f} exceeded budget "
                    f"${ctx.budget_usd:.6f} after LLM turn.",
                    retryable=False,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )

            if not response.choices:
                return _loop_error(
                    "internal",
                    "LLM provider returned no choices.",
                    retryable=True,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )

            msg = response.choices[0].message
            # We only send function-type tools, so all returned calls must be
            # function calls. Cast the union from the SDK at this boundary so
            # the rest of the loop can access `tc.function.{name,arguments}` cleanly.
            tool_calls = cast(
                list[ChatCompletionMessageFunctionToolCall],
                msg.tool_calls or [],
            )
            if not tool_calls:
                if nudged_for_missing_tool_call:
                    return _loop_error(
                        "internal",
                        "LLM returned no tool call twice; the loop requires conclude.",
                        retryable=True,
                        in_tokens=in_tokens,
                        out_tokens=out_tokens,
                        children_cost=children_cost,
                    )
                nudged_for_missing_tool_call = True
                logger.debug("LLM replied free-text with no tool call; nudging once")
                messages.append({"role": "assistant", "content": msg.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Respond only with tool calls. To finish, call the "
                            f"`{CONCLUDE_TOOL_NAME}` tool with your typed result."
                        ),
                    }
                )
                continue

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments

                if name == CONCLUDE_TOOL_NAME:
                    try:
                        output = output_type.model_validate_json(raw_args)
                    except (json.JSONDecodeError, ValidationError) as exc:
                        conclude_failures += 1
                        last_conclude_error = str(exc)
                        logger.warning(
                            "conclude args failed to validate as %s (attempt %d): %s",
                            output_type.__name__,
                            conclude_failures,
                            exc,
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps(
                                    {
                                        "error": {
                                            "kind": "validation",
                                            "detail": (
                                                f"conclude arguments failed to "
                                                f"validate as {output_type.__name__}: "
                                                f"{exc}"
                                            ),
                                        }
                                    }
                                ),
                            }
                        )
                        continue
                    return LoopOutcome(
                        output=output,
                        error=None,
                        in_tokens=in_tokens,
                        out_tokens=out_tokens,
                        children_cost=children_cost,
                    )

                tool = by_name.get(name)
                if tool is None:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(
                                {"error": {"kind": "validation", "detail": f"Unknown tool: {name}"}}
                            ),
                        }
                    )
                    continue

                try:
                    tool_input = tool.input_type.model_validate_json(raw_args)
                except (json.JSONDecodeError, ValidationError) as exc:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(
                                {"error": {"kind": "validation", "detail": f"Invalid input: {exc}"}}
                            ),
                        }
                    )
                    continue

                # Allocation, not inheritance: a child's ceiling is what is
                # left of this call's grant, not a full copy of it. Passing
                # ctx unchanged would hand every child dispatched between
                # budget checks the whole grant, letting a delegating tree
                # spend a multiple of it. Recomputed per dispatch so
                # sequential children see each other's spend; clamped at 0.0
                # so an exhausted grant starves the child rather than going
                # negative. No budget means nothing to allocate.
                child_ctx: CallContext = ctx
                if ctx.budget_usd is not None:
                    remaining = max(ctx.budget_usd - own_cost - children_cost, 0.0)
                    child_ctx = replace(ctx, budget_usd=remaining)
                result: CommissionResult[Any] = await dispatch(tool, tool_input, child_ctx)
                children_cost += result.cost.estimated_usd
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _render_tool_result(result),
                    }
                )

        if conclude_failures:
            detail = (
                f"Exceeded iteration cap of {max_iterations}: conclude was called "
                f"{conclude_failures} time(s) but its arguments never validated as "
                f"{output_type.__name__}. Last validation error: "
                f"{last_conclude_error[:400]}"
            )
        else:
            detail = f"Exceeded iteration cap of {max_iterations} without calling conclude."
        return _loop_error(
            "internal",
            detail,
            retryable=True,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            children_cost=children_cost,
        )
    finally:
        deposit_llm_trace(cast("list[dict[str, Any]]", messages))


def _to_provider_content(
    message: str | list[ContentPart],
) -> str | list[ChatCompletionContentPartParam]:
    """Translate the opening message to OpenAI content format.

    A bare str passes through unchanged (the common single-text case). A
    parts list maps to the provider's typed content-part dicts.
    """
    if isinstance(message, str):
        return message
    parts: list[ChatCompletionContentPartParam] = []
    for part in message:
        if isinstance(part, TextPart):
            parts.append({"type": "text", "text": part.text})
        else:
            parts.append({"type": "image_url", "image_url": {"url": part.image_url}})
    return parts


def _render_tool_result(result: CommissionResult[Any]) -> str:
    """Render a CommissionResult for the LLM.

    Success: the output alone. Partial: the output *and* the error in one
    object; partial output is usable by contract (it's what overflow_policy
    "partial" exists to preserve), so rendering only the error would throw
    away the child's work. Failure: the error alone.
    """
    if result.status == "success" and result.output is not None:
        if isinstance(result.output, BaseModel):
            return result.output.model_dump_json()
        return json.dumps(result.output)
    if result.status == "partial" and result.output is not None:
        error = (
            {"kind": result.error.kind, "detail": result.error.detail}
            if result.error is not None
            else None
        )
        return json.dumps({"partial_output": _jsonable(result.output), "error": error})
    if result.error is not None:
        return json.dumps({"error": {"kind": result.error.kind, "detail": result.error.detail}})
    return json.dumps({"error": {"kind": "internal", "detail": "empty result"}})


def _jsonable(output: Any) -> Any:
    """A JSON-serializable view of a typed output."""
    if isinstance(output, BaseModel):
        return output.model_dump(mode="json")
    return output


def _loop_error[OutputT](
    kind: ErrorKind,
    detail: str,
    *,
    retryable: bool,
    in_tokens: int,
    out_tokens: int,
    children_cost: float = 0.0,
) -> LoopOutcome[OutputT]:
    return LoopOutcome(
        output=None,
        error=ErrorState(kind=kind, detail=detail, retryable=retryable),
        in_tokens=in_tokens,
        out_tokens=out_tokens,
        children_cost=children_cost,
    )
