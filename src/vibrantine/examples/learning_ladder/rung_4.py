"""Rung 4: the same tree, watched.

One new idea: run conditions. The tree is rung 3's, unchanged; what changes
is how it is run. A budget the loop enforces mid-run, and a recording
backend that keeps one record per node in the tree, switched on with
`record="always"`. Afterward the envelope shows dollars rolled up across
the tree while token counts stay per node, and the SQLite file the backend
wrote answers questions in plain SQL: no query API needed, the file is the
query surface.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, Field

from vibrantine import SqliteBackend, create_commission, invoke_sync


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
    tools=(recipe_writer,),
)


def main() -> None:
    db = Path("ladder_runs.db")
    backend = SqliteBackend(db)

    result = invoke_sync(
        meal_planner,
        DinnerInput(theme="cozy winter evening"),
        budget_usd=0.05,
        backend=backend,
        record="always",
    )

    if result.status != "success" or result.output is None:
        print(f"failed: {result.error}")
    else:
        print(f"tonight: {result.output.dish}\n")

    print(f"tree cost: ${result.cost.estimated_usd:.6f} (children included)")
    # Token counts are None when the run failed before any LLM turn
    # (missing API key, budget declined pre-flight).
    if result.cost.in_tokens is not None:
        print(
            f"planner's own turns: {result.cost.in_tokens} tokens in, "
            f"{result.cost.out_tokens} out (child tokens live on the child's record)\n"
        )

    print(f"every node left a record in {db}; ask it anything in SQL:")
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            # The db accumulates across runs, so scope to this run's tree:
            # the planner's own record plus its direct children (the whole
            # tree here is two levels).
            "SELECT commission_name, status, cost_usd FROM records "
            "WHERE run_id = ? OR parent_run_id = ? ORDER BY created_at",
            (result.run_id, result.run_id),
        ).fetchall()
    for name, status, cost_usd in rows:
        print(f"  {name:<15} {status:<8} ${cost_usd:.6f}")


if __name__ == "__main__":
    main()
