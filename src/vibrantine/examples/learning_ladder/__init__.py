"""The learning ladder: four runnable rungs, each the previous plus one idea.

Run them in order; each is one small file, and the difference between
neighboring rungs is exactly one concept:

    uv run --env-file .env python -m vibrantine.examples.learning_ladder.rung_1
    uv run --env-file .env python -m vibrantine.examples.learning_ladder.rung_2
    uv run --env-file .env python -m vibrantine.examples.learning_ladder.rung_3
    uv run --env-file .env python -m vibrantine.examples.learning_ladder.rung_4

- rung_1: one Commission from `create_commission`. Typed input in, typed
  result envelope out.
- rung_2: the same shape handed a tool. The loop shows the model a menu
  built from what you grant, and dispatches what it picks.
- rung_3: a Commission inside another Commission's toolbox. Children are
  the same shape as parents; composition is just the toolbox again.
- rung_4: the same tree run with a budget and a recording backend. Dollars
  roll up the tree, token counts sit on each node, and the records answer
  plain SQL.

Every rung needs `OPENROUTER_API_KEY` in the environment (the repo README
covers setup). Each run costs a fraction of a cent.
"""
