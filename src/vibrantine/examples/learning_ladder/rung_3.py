"""Rung 3: a Commission inside another Commission's toolbox.

One new idea: children are the same shape as parents. `recipe_writer` is
rung 1's Commission, unchanged; dropping it into `meal_planner`'s toolbox
is all it takes for the planner's LLM to call it the way rung 2's model
called `read`. There is no separate plugin system or agent protocol,
composition is just the toolbox again. Whatever the child spends rolls up
into the parent's cost automatically.
"""

from pydantic import BaseModel, Field

from vibrantine import create_commission, run_commission_sync


class RecipeInput(BaseModel):
    """One dish to write a recipe for."""

    dish: str = Field(description="The dish to write a recipe for.")


class RecipeOutput(BaseModel):
    """The finished recipe."""

    recipe: str = Field(description="A complete recipe, ingredients then steps.")


recipe_writer = create_commission(
    name="recipe_writer",
    description="Writes a recipe for a named dish.",
    input=RecipeInput,
    output=RecipeOutput,
)


class DinnerInput(BaseModel):
    """A theme to plan one dinner around."""

    theme: str = Field(description="A mood or occasion for tonight's dinner.")


class DinnerOutput(BaseModel):
    """The planned dinner."""

    dish: str = Field(description="The single dish chosen to match the theme.")
    recipe: str = Field(description="The recipe for that dish, from the recipe_writer child.")


meal_planner = create_commission(
    name="meal_planner",
    description=(
        "Plans one dinner: chooses a single dish to match the given theme, "
        "then calls the `recipe_writer` tool to get its recipe. Never writes "
        "the recipe itself."
    ),
    input=DinnerInput,
    output=DinnerOutput,
    toolbox=(recipe_writer,),
)


def main() -> None:
    result = run_commission_sync(meal_planner, DinnerInput(theme="cozy winter evening"))

    if result.status != "success" or result.output is None:
        print(f"failed: {result.error}")
        return
    print(f"tonight: {result.output.dish}\n")
    print(result.output.recipe)
    print(f"\ncost: ${result.cost.estimated_usd:.6f} (the child's spend is included)")


if __name__ == "__main__":
    main()
