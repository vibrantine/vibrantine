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
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionContentPartParam,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)
from pydantic import BaseModel, ValidationError

from vibrantine._gatekeeper import RunCancel, RunHaltedError, UnmeterableCallError
from vibrantine.contract import (
    AudioPart,
    CallContext,
    Commission,
    CommissionResult,
    ContentPart,
    ErrorKind,
    ErrorState,
    ImagePart,
    TextPart,
    estimate_tokens,
)
from vibrantine.dispatch import (
    deposit_llm_trace,
    dispatch,
    halt_checkpoint_error,
    stop_signal_error,
)
from vibrantine.models import UnknownModelError

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
    model: str | None,
    commission_name: str,
    system_prompt: str,
    user_message: str | list[ContentPart],
    toolbox: Sequence[Commission[Any, Any]],
    output_type: type[OutputT],
    ctx: CallContext,
    max_iterations: int,
) -> LoopOutcome[OutputT]:
    """Run the LLM call-dispatch-feed cycle until conclude or a stop condition.

    Stop conditions: conclude tool called, budget exceeded (checked after
    each turn, and pre-flight before each turn when the next call's input
    floor alone would break the grant), max_iterations hit, cancellation,
    no tool call returned by the LLM (treated as a failure; the loop
    disallows free-form completion).

    Budget flows down as allocation: each dispatched child receives the
    grant minus everything already spent (own turns plus prior children),
    so ceilings only shrink down the tree and a delegating Commission
    cannot spend a multiple of its grant through same-turn children.

    Budget is also visible mid-run: when a budget is set, a one-line
    `[budget]` status follows each turn's tool results, so the LLM can
    wind down (stop delegating, conclude with what it has) instead of
    running blind into the hard stop.

    Tool errors are fed back to the LLM as tool results, not raised as
    Commission failures; the LLM gets to decide whether to retry or
    conclude with an apologetic answer.
    """
    # Every governed LLM call passes the run's provider door: the fuses, the
    # room, the client vending, and the call log live there. dispatch refuses
    # a context without the run object, so a loop can only run inside a run.
    gatekeeper = ctx._gatekeeper  # pyright: ignore[reportPrivateUsage]
    assert gatekeeper is not None, "run_llm_loop requires a run; dispatch refuses outside one"
    # `model` is a pure name (None = the run default), resolved against the
    # run's catalog; an unknown name fails structurally before anything is
    # sent or spent.
    try:
        entry = gatekeeper.resolve_model(model)
    except UnknownModelError as exc:
        return _loop_error("validation", str(exc), retryable=False, in_tokens=0, out_tokens=0)
    # The effective menu: toolbox ∩ branch grant ∩ run ceiling. The branch
    # grant (ctx.capabilities) is the caller's distributed allow-list; the
    # ceiling is the run's immutable tree-wide bound, held on the shared
    # object so a grant widened by custom code stays clamped. None means
    # unrestricted for both. A forbidden tool is simply absent from the
    # menu, so any call to it falls through the unknown-tool branch below;
    # no separate gate. `conclude` is framework-injected and never gated.
    # See docs/design.md.
    allowed = ctx.capabilities.tools
    ceiling = gatekeeper.tool_ceiling
    permitted = [
        c
        for c in toolbox
        if (allowed is None or c.name in allowed) and (ceiling is None or c.name in ceiling)
    ]
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

    # The transcript escapes via the trace mailbox on every exit path
    # (invalid opening message, conclude, budget stop, iteration cap,
    # cancellation, a raise), so failure runs, the ones worth autopsying,
    # are recorded too.
    try:
        # Translate before anything is sent or spent: an invalid opening
        # message is an authoring error, reported structurally rather than
        # raised.
        try:
            messages.append({"role": "user", "content": _to_provider_content(user_message)})
        except _InvalidOpeningMessage as exc:
            return _loop_error("validation", str(exc), retryable=False, in_tokens=0, out_tokens=0)
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
                # Attributed at the source: a checkpoint that fires because
                # the breaker tripped (not the caller's token) is stamped
                # breaker-born for the root rewrite; a caller cancellation
                # keeps its plain, unstamped story.
                breaker_only = isinstance(ctx.cancel, RunCancel) and not ctx.cancel.caller_cancelled
                return LoopOutcome(
                    output=None,
                    error=(
                        halt_checkpoint_error("between loop iterations")
                        if breaker_only
                        else ErrorState(
                            kind="cancelled",
                            detail="Cancelled between loop iterations.",
                            retryable=False,
                        )
                    ),
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )

            # Pre-turn gate: decline the next LLM call when the floor of its
            # input cost alone already breaks the grant, instead of spending
            # past the budget and failing on the post-turn check below. The
            # failure case is a big accumulated transcript on an expensively
            # priced model, where one more turn overshoots by whole dollars,
            # not pennies. Floor means underestimate: the gate never kills a
            # turn the post-turn check might have allowed.
            if ctx.budget_usd is not None:
                spent = entry.cost_usd(in_tokens, out_tokens) + children_cost
                input_floor = entry.cost_usd(_estimate_transcript_tokens(messages), 0)
                if spent + input_floor > ctx.budget_usd:
                    return _loop_error(
                        "budget_exceeded",
                        f"Declined the next LLM turn pre-flight: spend so far "
                        f"${spent:.6f} plus the next turn's estimated input "
                        f"floor of ${input_floor:.6f} exceeds the "
                        f"${ctx.budget_usd:.6f} grant.",
                        retryable=False,
                        in_tokens=in_tokens,
                        out_tokens=out_tokens,
                        children_cost=children_cost,
                    )

            try:
                # The chair is held around the provider call only and
                # released at the `with` exit, before this turn's tool calls
                # dispatch children: count leaf work, not coordinators.
                async with gatekeeper.provider_call(
                    entry=entry,
                    commission_name=commission_name,
                    grant_usd=ctx.budget_usd,
                ) as ticket:
                    # The params spread defeats the SDK's overload matching
                    # (it can no longer prove stream is absent), so pin the
                    # non-streaming type the door only ever speaks.
                    response = cast(
                        "ChatCompletion",
                        await ticket.client.chat.completions.create(
                            model=entry.id,
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            **entry.params,
                        ),
                    )
                    if response.usage is not None:
                        ticket.record_usage(
                            response.usage.prompt_tokens,
                            response.usage.completion_tokens,
                        )
            except RunHaltedError as exc:
                # The run's stop signal refused the call. Report an ordinary
                # cancellation: mid-tree nodes ride the cancel path, and only
                # the root speaks run_halted (dispatch rewrites it there,
                # claiming the breaker-born stamp this builder sets).
                return LoopOutcome(
                    output=None,
                    error=stop_signal_error(exc),
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )
            except UnmeterableCallError as exc:
                return _loop_error(
                    "internal",
                    str(exc),
                    retryable=False,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
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
            except openai.APIStatusError as exc:
                # A non-429 4xx is deterministic (bad request, context
                # overflow): the same input fails the same way, so don't
                # tell the caller a retry might succeed. 5xx may clear.
                return _loop_error(
                    "internal",
                    f"LLM provider error: {exc}",
                    retryable=exc.status_code >= 500,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )
            except openai.APIError as exc:
                # Connection or timeout trouble; retrying may succeed.
                return _loop_error(
                    "internal",
                    f"LLM provider error: {exc}",
                    retryable=True,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    children_cost=children_cost,
                )

            # A response without usage reaches here only when the door let it
            # pass (no dollar accounting armed); the door already warned that
            # this turn's cost and token counts go unreported.
            usage = response.usage
            if usage is not None:
                in_tokens += usage.prompt_tokens
                out_tokens += usage.completion_tokens
                logger.info(
                    "LLM turn model=%s in=%d out=%d",
                    entry.id,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                )

            # Own token cost plus everything dispatched children spent, so a
            # recursive or sub-Commission-bearing loop enforces the budget against
            # its whole subtree, not just its own turns. May still overshoot by
            # one turn: the pre-turn gate above declines only calls whose input
            # floor alone breaks the grant, and output tokens are unguessable.
            own_cost = entry.cost_usd(in_tokens, out_tokens)
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
                # `or ""`: an empty reply has content=None, and an assistant
                # message without tool_calls must carry string content or
                # spec-compliant providers reject the whole transcript.
                messages.append({"role": "assistant", "content": msg.content or ""})
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

            # Mid-run cost visibility: the same ledger the hard stop above
            # checks, shown to the LLM after the turn's tool results so it
            # can wind down before the stop fires. Emitted only under a
            # budget; an unbudgeted loop's transcript is unchanged.
            if ctx.budget_usd is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": _budget_status(own_cost + children_cost, ctx.budget_usd),
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


class _InvalidOpeningMessage(ValueError):
    """An opening message the loop must not send; surfaced as `validation`.

    Module-private so the loop's narrow except never relabels an incidental
    ValueError from a future translator edit as an authoring error.
    """


def _to_provider_content(
    message: str | list[ContentPart],
) -> str | list[ChatCompletionContentPartParam]:
    """Translate the opening message to OpenAI content format.

    A bare str passes through unchanged (the common single-text case). A
    parts list maps to the provider's typed content-part dicts, one explicit
    branch per modality. A message the loop must not send (a part with no
    branch here, or an empty parts list) raises `_InvalidOpeningMessage`,
    which the loop surfaces as a validation failure: a modality this loop
    cannot translate must never be silently sent as something else.
    """
    if isinstance(message, str):
        return message
    if not message:
        raise _InvalidOpeningMessage(
            "build_user_message returned an empty parts list; providers "
            "reject an empty content array. Return a bare string or at "
            "least one content part."
        )
    parts: list[ChatCompletionContentPartParam] = []
    # The list comes straight from author code, unvalidated at runtime, so
    # every element is checked rather than trusted to match the annotation.
    for part in cast("list[object]", message):
        if isinstance(part, TextPart):
            parts.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            parts.append({"type": "image_url", "image_url": {"url": part.image_url}})
        elif isinstance(part, AudioPart):
            parts.append(
                {
                    "type": "input_audio",
                    "input_audio": {"data": part.data, "format": part.format},
                }
            )
        else:
            raise _InvalidOpeningMessage(
                f"build_user_message returned a content part the default "
                f"loop cannot translate: {type(part).__name__}. Supported "
                f"parts: TextPart, ImagePart, AudioPart."
            )
    return parts


def _estimate_transcript_tokens(messages: Sequence[ChatCompletionMessageParam]) -> int:
    """Floor estimate of the input tokens the next LLM turn will send.

    Counts message text and tool-call names/arguments through the contract's
    `estimate_tokens` heuristic. Image parts and the tool schemas are left
    out: their token costs are not proportional to their text length, and
    the pre-turn gate needs a floor, an estimate that only *under*counts, so
    it never declines a turn the post-turn check might have allowed.
    """
    total = 0
    for message in cast("Sequence[dict[str, Any]]", messages):
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # Parts lists are built by _to_provider_content above, so every
            # entry is a typed content-part dict.
            for part in cast("list[dict[str, Any]]", content):
                if part.get("type") == "text":
                    total += estimate_tokens(str(part.get("text", "")))
        for tool_call in message.get("tool_calls") or ():
            function = tool_call.get("function", {})
            total += estimate_tokens(
                str(function.get("name", "")) + str(function.get("arguments", ""))
            )
    return total


def _budget_status(spent_usd: float, grant_usd: float) -> str:
    """The one-line spend report a budgeted loop shows its LLM each turn.

    Reports the same spent-vs-grant numbers the loop's hard stop checks, so
    the figure the LLM plans around and the figure that can end the run never
    disagree. Remaining is clamped at zero: children dispatched since the
    last check can overspend the grant, and a negative allowance reads as
    nonsense to the model (the next turn's hard stop handles the overrun).
    """
    remaining = max(grant_usd - spent_usd, 0.0)
    return f"[budget] spent ${spent_usd:.4f} of ${grant_usd:.4f} grant; ${remaining:.4f} remaining."


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
