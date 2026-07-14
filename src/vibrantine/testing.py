"""Supported test doubles: script the LLM, keep every other part of the run real.

A run's models are defined in its catalog (`run_commission(models=[...])`), and the
catalog vends the provider clients. That makes testing a Commission cheap and
deterministic: register a `scripted_model(...)` entry whose "provider" is a
queue of responses you wrote, and the full machinery (dispatch wrapping, the
Gatekeeper's fuses and room, tool execution, conclude validation, cost and
budget math) runs for real against it. No network, no API key, no spend. The
model's intelligence is never what a contract test proves; the Commission's
behavior around the model's responses is.

This module is supported public surface, the testing half of the run
catalog's client-vending seam:

- `scripted_model(scripted, ...)`: a catalog entry carrying a scripted fake
  as its provider. Register it in `run_commission(models=[...])`; a single entry is
  the run default, so the Commission under test needs no `model=` at all.
- `ScriptedLLM(responses)`: the fake provider. Pops one response per LLM
  call, in order, and records every request it received in `calls`.
- `llm_response(...)`: builds one scripted reply, either tool calls or plain
  text, with token counts so pricing runs for real.
- `AlwaysCancelled`: a `CancelToken` that is already cancelled, for testing
  cancellation paths.
- `FIXTURE_MODEL`: a `Model` with pinned pricing, for tests that assert on
  cost or budget math. Deliberately not a real catalog slug, so retiring or
  repricing a real model never silently rewrites a test's expected dollars.
  `scripted_model` defaults to its id and rates.

The one test worth writing first:

    from vibrantine import run_commission
    from vibrantine.testing import ScriptedLLM, llm_response, scripted_model

    async def test_concludes() -> None:
        scripted = ScriptedLLM([
            llm_response(tool_calls=[("t1", "conclude", {"answer": "42"})]),
        ])
        result = await run_commission(
            MyCommission(),
            MyInput(question="?"),
            models=[scripted_model(scripted)],
        )
        assert result.status == "success"
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from vibrantine.models import Model

# The supported testing surface, locked in test_public_api.py like the
# top level's __all__: this module is public by declaration, so its
# exports grow with the same deliberateness.
__all__ = [
    "FIXTURE_MODEL",
    "AlwaysCancelled",
    "ScriptedLLM",
    "llm_response",
    "scripted_model",
]

# A priced Model for unit tests that assert on cost or budget arithmetic.
# Deliberately not a real catalog slug: a run catalog containing it never
# collides with a real model, so retiring, repricing, or renaming a real
# model never silently changes a test's expected numbers. The rates
# ($0.50/M in, $3.00/M out) are the fixture pricing these tests were
# written against, so existing cost arithmetic is unchanged.
FIXTURE_MODEL = Model(
    id="fixture/priced-model",
    context_window=1_050_000,
    input_usd_per_million=0.50,
    output_usd_per_million=3.00,
)


class AlwaysCancelled:
    """A CancelToken whose `is_cancelled` is always True.

    Pass as `run_commission(..., cancel=AlwaysCancelled())` to prove a Commission
    checks for cancellation before doing the work.
    """

    @property
    def is_cancelled(self) -> bool:
        return True


def llm_response(
    *,
    tool_calls: list[tuple[str, str, dict[str, Any]]] | None = None,
    content: str | None = None,
    in_tokens: int = 100,
    out_tokens: int = 50,
) -> SimpleNamespace:
    """Build one scripted LLM reply for a `ScriptedLLM` queue.

    `tool_calls` is a list of (id, tool_name, arguments) tuples; use the name
    "conclude" to end an LLM loop with a typed result. `content` is plain
    assistant text (a reply with no tool call, or the body a direct-call
    Commission reads). One builder serves both: the reply carries whichever
    of the two you set (both None models an empty reply). Token counts feed
    the real cost and budget math; override them when a test asserts on spend.
    """
    tcs = None
    if tool_calls is not None:
        tcs = [
            SimpleNamespace(
                id=tc_id,
                type="function",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for tc_id, name, args in tool_calls
        ]
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=in_tokens, completion_tokens=out_tokens),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tcs))],
    )


class _ScriptedCompletions:
    """The `chat.completions` endpoint of a ScriptedLLM. Internal."""

    def __init__(self, responses: list[SimpleNamespace], calls: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._calls = calls

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self._calls.append(kwargs)
        if not self._responses:
            raise AssertionError(
                f"ScriptedLLM ran out of scripted responses on call "
                f"{len(self._calls)}; the code under test made more LLM "
                f"calls than the script anticipated."
            )
        return self._responses.pop(0)


class ScriptedLLM:
    """An LLM whose turns follow a script: the fake provider for tests.

    Construct with the responses the "model" should give, in order (build
    each with `llm_response`). Each LLM call the Commission makes pops the
    next response off the queue; running past the end fails the test loudly
    rather than hanging or looping. `calls` records the keyword arguments of
    every request received (model, messages, tools), so a test can assert on
    exactly what the Commission sent.

    Register it with the run through `scripted_model`: the run's client
    vending returns it in place of a real provider client, as only the
    `chat.completions.create` surface the framework touches is provided.
    """

    def __init__(self, responses: Sequence[SimpleNamespace]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=_ScriptedCompletions(list(responses), self.calls))


@dataclass(frozen=True)
class _ScriptedModel(Model):
    """A catalog entry carrying its scripted provider. Internal.

    The run's client vending checks for `scripted_client` and returns it
    instead of building a real client, so the fake rides the same door as a
    real provider without the public `Model` type growing a field.
    """

    scripted_client: ScriptedLLM | None = field(default=None, compare=False)


def scripted_model(
    scripted: ScriptedLLM,
    *,
    id: str = FIXTURE_MODEL.id,
    name: str = "",
    params: dict[str, Any] | None = None,
    context_window: int | None = FIXTURE_MODEL.context_window,
    input_usd_per_million: float | None = FIXTURE_MODEL.input_usd_per_million,
    output_usd_per_million: float | None = FIXTURE_MODEL.output_usd_per_million,
) -> Model:
    """A run catalog entry whose provider is a scripted fake: the test seam.

    Register the returned entry in `run_commission(models=[...])`; every LLM call a
    Commission naming it makes (a single entry is the run default) is served
    by `scripted`, while dispatch, the Gatekeeper, cost, and budget math run
    for real. Defaults reuse `FIXTURE_MODEL`'s id and pinned pricing so cost
    assertions keep their dollars; pass a distinct `id` per fake to script a
    multi-model tree (name each seat's model, register every fake in one
    `models=[...]`, and set `default_model=` when no entry is the system
    default). `name=` and `params=` pass through like any profile, so a
    test can script two roles sharing one wire id or assert that a
    profile's params reached the provider (they land in `scripted.calls`).
    """
    return _ScriptedModel(
        id=id,
        name=name,
        params=params if params is not None else {},
        context_window=context_window,
        input_usd_per_million=input_usd_per_million,
        output_usd_per_million=output_usd_per_million,
        scripted_client=scripted,
    )
