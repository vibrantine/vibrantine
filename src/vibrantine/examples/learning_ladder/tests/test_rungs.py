"""Smoke tests for the learning ladder.

The rungs are teaching scripts; these tests keep them from rotting. They
prove each rung imports without side effects (construction is deterministic,
no network, no files) and that the one new idea each rung claims is actually
present in what it builds: rung 2's tool grant, rung 3's nested child,
rung 4's identical tree. The live `main()` paths are exercised by running
the rungs themselves, not here.
"""

from vibrantine import Commission
from vibrantine.examples.learning_ladder import rung_1, rung_2, rung_3, rung_4
from vibrantine.tools import ReadTool


def test_rung_1_builds_one_commission() -> None:
    assert isinstance(rung_1.recipe_writer, Commission)
    assert rung_1.recipe_writer.name == "recipe_writer"
    assert rung_1.recipe_writer.toolbox == ()


def test_rung_2_grants_exactly_the_read_tool() -> None:
    toolbox = rung_2.file_answerer.toolbox
    assert len(toolbox) == 1
    assert isinstance(toolbox[0], ReadTool)


def test_rung_3_nests_the_recipe_writer_as_a_child() -> None:
    toolbox = rung_3.meal_planner.toolbox
    assert len(toolbox) == 1
    child = toolbox[0]
    assert isinstance(child, Commission)
    assert child.name == "recipe_writer"


def test_rung_4_is_rung_3_tree_unchanged() -> None:
    # The rung's claim: only the run conditions change, never the tree.
    assert rung_4.meal_planner.name == rung_3.meal_planner.name
    assert rung_4.meal_planner.description == rung_3.meal_planner.description
    assert rung_4.recipe_writer.name == rung_3.recipe_writer.name
    assert type(rung_4.meal_planner).system_prompt == type(rung_3.meal_planner).system_prompt
