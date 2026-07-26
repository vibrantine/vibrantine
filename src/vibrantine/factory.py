"""create_commission: a basic LLM-loop Commission from the four real decisions.

Authoring a Commission splits into two kinds of work. Crafting: choosing the
input and output types, the name, the description, the tools. Manufacturing:
prompt scaffolding, catalog wiring, and the provenance/cost plumbing,
identical every time. This factory absorbs the manufacturing so authoring
shrinks to the crafting.

The factory is deliberately deterministic: no LLM is involved in building
the Commission, nothing is fetched, nothing is spent. What comes back is an
ordinary basic Commission riding the default `_run` loop; it composes,
records, budgets, and nests exactly like a hand-written subclass, and when
a Commission outgrows the factory the exit is subclassing `Commission`,
where nothing learned here changes.
"""

from typing import Any, get_args

from pydantic import BaseModel

from vibrantine.contract import (
    DEFAULT_MAX_ITERATIONS,
    CallContext,
    Commission,
    ContentPart,
)


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
        assert field.description is not None
        lines.append(f"- {field_name}: {field.description}")
    lines.extend(
        [
            "",
            "When you have the result, call `conclude` with its required "
            "fields. Do not produce free-form text outside of tool calls.",
        ]
    )
    return "\n".join(lines)


def _nested_model_types(annotation: Any) -> tuple[type[BaseModel], ...]:
    """Find Pydantic models nested inside a field annotation."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    nested: list[type[BaseModel]] = []
    for argument in get_args(annotation):
        nested.extend(_nested_model_types(argument))
    return tuple(nested)


def _missing_field_descriptions(
    model_type: type[BaseModel],
    *,
    surface: str,
) -> list[str]:
    """Name under-described fields before they become provider schemas."""
    missing: list[str] = []
    visited: set[type[BaseModel]] = set()

    def visit(current: type[BaseModel], path: str) -> None:
        if current in visited:
            return
        visited.add(current)
        for field_name, field in current.model_fields.items():
            field_path = f"{path}.{field_name}"
            if field.description is None or not field.description.strip():
                missing.append(field_path)
            for nested in _nested_model_types(field.annotation):
                visit(nested, field_path)

    visit(model_type, surface)
    return missing


def _validate_contract_models(
    input_type: type[BaseModel],
    output_type: type[BaseModel],
) -> None:
    """Reject schemas whose fields cannot explain themselves to an LLM."""
    missing = [
        *_missing_field_descriptions(input_type, surface="input"),
        *_missing_field_descriptions(output_type, surface="output"),
    ]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(
            "create_commission requires a non-empty Field(description=...) "
            f"on every input and output field; missing: {joined}."
        )


def create_commission[InputT: BaseModel, OutputT: BaseModel](
    *,
    name: str,
    description: str,
    input: type[InputT],
    output: type[OutputT],
    toolbox: "tuple[Commission[Any, Any], ...]" = (),
    system_prompt: str | None = None,
    model: str | None = None,
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
    serialized as JSON, and `model` is a name resolved against the run's
    catalog when the loop runs (None means the run default). Tests script
    the model through the catalog: `testing.scripted_model`.

    Returns an ordinary `Commission[InputT, OutputT]`; run it through
    `run_commission` / `run_commission_sync` / `dispatch` like any other.
    """
    _validate_contract_models(input, output)
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
        toolbox=toolbox,
        max_iterations=max_iterations,
    )
