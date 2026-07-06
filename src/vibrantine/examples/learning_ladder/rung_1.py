"""Rung 1: one Commission.

The whole shape in one file. You craft the decisions no one can make for
you: the input type, the output type, a name, and a description written
for the LLM that might call this Commission. `create_commission`
manufactures everything else. What comes back from `invoke_sync` is the
result envelope every Commission returns: status, typed output, cost,
provenance.
"""

from pydantic import BaseModel, Field

from vibrantine import create_commission, invoke_sync


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


def main() -> None:
    result = invoke_sync(recipe_writer, RecipeInput(dish="shakshuka"))

    if result.status != "success" or result.output is None:
        print(f"failed: {result.error}")
        return
    print(result.output.recipe)
    print(f"\ncost: ${result.cost.estimated_usd:.6f}")


if __name__ == "__main__":
    main()
