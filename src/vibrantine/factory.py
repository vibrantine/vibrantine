"""create_commission: a basic LLM-loop Commission from the four real decisions.

Authoring a Commission splits into two kinds of work. Crafting: choosing the
input and output types, the name, the description, the tools. Manufacturing:
client construction, model resolution, prompt scaffolding, and the
provenance/cost plumbing, identical every time. This factory absorbs the
manufacturing so authoring shrinks to the crafting.

The factory is deliberately deterministic: no LLM is involved in building
the Commission, nothing is fetched, nothing is spent. What comes back is an
ordinary basic Commission riding the default `_run` loop; it composes,
records, budgets, and nests exactly like a hand-written subclass, and when
a Commission outgrows the factory the exit is subclassing `Commission`,
where nothing learned here changes.
"""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from vibrantine.contract import (
    DEFAULT_MAX_ITERATIONS,
    CallContext,
    Commission,
    ContentPart,
)
from vibrantine.models import Model

if TYPE_CHECKING:
    from openai import AsyncOpenAI


def _default_system_prompt(description: str, input_type: type[BaseModel]) -> str:
    """Assemble the default Commission-layer prompt from the crafted parts.

    The description is the author's statement of purpose; the input schema's
    field descriptions explain the JSON the user message carries. The closing
    instruction is the default loop's completion protocol (the synthetic
    `conclude` tool, whose schema is the output type).
    """
    lines = [
        description,
        "",
        "The user message carries the input as JSON with these fields:",
    ]
    for field_name, field in input_type.model_fields.items():
        detail = field.description or "(no description provided)"
        lines.append(f"- {field_name}: {detail}")
    lines.extend(
        [
            "",
            "When you have the result, call `conclude` with its required "
            "fields. Do not produce free-form text outside of tool calls.",
        ]
    )
    return "\n".join(lines)


def create_commission[InputT: BaseModel, OutputT: BaseModel](
    *,
    name: str,
    description: str,
    input: type[InputT],
    output: type[OutputT],
    toolbox: "tuple[Commission[Any, Any], ...]" = (),
    system_prompt: str | None = None,
    model: str | Model | None = None,
    client: "AsyncOpenAI | None" = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> Commission[InputT, OutputT]:
    """Build a basic LLM-loop Commission from its irreducible decisions.

    Required arguments are the crafted parts: `name` and `description` are
    the Commission's identity (the description is written for the LLM that
    decides whether to call it), and `input`/`output` are the typed contract
    (Pydantic models whose every field carries a `description=`). `toolbox`
    is the sub-Commissions the loop may dispatch; empty means pure judgment
    over the input.

    Everything else is manufactured: the system prompt defaults to the
    description plus the input schema's field descriptions (pass
    `system_prompt=` to replace it), the opening user message is the input
    serialized as JSON, and `model` resolves through the standard catalog
    (None means the system default). `client=` is the usual testing seam.

    Returns an ordinary `Commission[InputT, OutputT]`; run it through
    `run_one` / `invoke_sync` / `dispatch` like any other.
    """
    commission_name = name
    commission_description = description
    prompt = (
        system_prompt if system_prompt is not None else _default_system_prompt(description, input)
    )

    class _FromFactory(Commission[InputT, OutputT]):
        name = commission_name
        description = commission_description
        input_type = input
        output_type = output
        system_prompt = prompt

        def build_user_message(self, input: InputT, ctx: CallContext) -> "str | list[ContentPart]":
            return input.model_dump_json(indent=2)

    # The class name surfaces in reprs and debuggers; the placeholder
    # `_FromFactory` would make every crafted Commission look identical.
    _FromFactory.__name__ = name
    _FromFactory.__qualname__ = name

    return _FromFactory(
        model=model,
        client=client,
        toolbox=toolbox,
        max_iterations=max_iterations,
    )
