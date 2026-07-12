"""Tests for create_commission, the deterministic authoring factory.

The factory manufactures a basic LLM-loop Commission from the crafted
decisions (name, description, input/output types, tools). Tests prove the
result is an ordinary Commission: it runs the default loop end to end
through run_commission, carries its identity, and shares nothing between two
factory calls. Construction itself is deterministic: no client is built
and nothing is spent until the Commission actually runs.
"""

import json
from typing import Any

from pydantic import BaseModel, Field

from vibrantine import CallContext, Commission, create_commission, run_commission
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model
from vibrantine.tools import ReadTool


class RecipeInput(BaseModel):
    """Input for the recipe example Commission used across these tests."""

    dish: str = Field(description="The dish to write a recipe for.")


class RecipeOutput(BaseModel):
    """Output for the recipe example Commission used across these tests."""

    recipe: str = Field(description="A complete recipe, ingredients then steps.")


def _commission(
    *,
    name: str = "recipe_writer",
    description: str = "Writes a recipe for a named dish.",
    toolbox: "tuple[Commission[Any, Any], ...]" = (),
    system_prompt: str | None = None,
) -> Commission[RecipeInput, RecipeOutput]:
    return create_commission(
        name=name,
        description=description,
        input=RecipeInput,
        output=RecipeOutput,
        toolbox=toolbox,
        system_prompt=system_prompt,
    )


async def test_factory_commission_runs_the_default_loop() -> None:
    fake = ScriptedLLM(
        [llm_response(tool_calls=[("c1", "conclude", {"recipe": "Simmer the tomatoes."})])]
    )
    commission = _commission()

    result = await run_commission(
        commission, RecipeInput(dish="shakshuka"), models=[scripted_model(fake)]
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert isinstance(result.output, RecipeOutput)
    assert result.output.recipe == "Simmer the tomatoes."
    # The default loop's accounting works unchanged: one turn at the
    # llm_response defaults (100 in / 50 out).
    assert result.cost.in_tokens == 100
    assert result.cost.out_tokens == 50


def test_factory_sets_identity() -> None:
    commission = _commission()

    assert isinstance(commission, Commission)
    assert commission.name == "recipe_writer"
    assert commission.description == "Writes a recipe for a named dish."
    assert commission.input_type is RecipeInput
    assert commission.output_type is RecipeOutput
    assert type(commission).__name__ == "recipe_writer"


def test_default_system_prompt_carries_description_and_field_docs() -> None:
    commission = _commission()

    prompt = commission.system_prompt
    assert prompt is not None
    assert "Writes a recipe for a named dish." in prompt
    assert "dish: The dish to write a recipe for." in prompt
    assert "conclude" in prompt


def test_system_prompt_override_replaces_the_default() -> None:
    commission = _commission(system_prompt="Only ever suggest soup.")

    assert commission.system_prompt == "Only ever suggest soup."


def test_user_message_is_the_input_as_json() -> None:
    commission = _commission()

    message = commission.build_user_message(RecipeInput(dish="soup"), CallContext())

    assert isinstance(message, str)
    assert json.loads(message) == {"dish": "soup"}


def test_tools_land_in_the_toolbox() -> None:
    read = ReadTool()
    commission = _commission(toolbox=(read,))

    assert commission.toolbox == (read,)


def test_default_toolbox_is_empty() -> None:
    assert _commission().toolbox == ()


def test_two_factory_calls_share_nothing() -> None:
    first = _commission()
    second = _commission(name="menu_writer", description="Writes a menu.")

    assert type(first) is not type(second)
    assert first.name == "recipe_writer"
    assert second.name == "menu_writer"
    # Identity stayed put on the first despite the second's construction.
    assert first.description == "Writes a recipe for a named dish."
