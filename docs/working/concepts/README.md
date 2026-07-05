# `concepts/` — concept drafts for later docs

⚠️ **These drafts are working material, not current product documentation.**

This folder holds deeper conceptual drafts that the root
[`README.md`](../../../README.md) deliberately does not carry. They are source
material for a possible future tutorial/reference layer, not live guidance.

## Possible Target Topology

A tight root README + a small **subordinate, derived** layer:

```
README.md        <- the public front door: tight, scannable, current.
docs/
  tutorial.md    <- future subordinate deep author-a-commission walk-through.
  reference.md   <- future subordinate field-by-field contract.
```

**Subordinate = true to the README, never co-equal.** It elaborates; it
never makes a canonical claim the README doesn't. If the README and a
subordinate doc disagree, the README wins and the subordinate is wrong.

## What each draft owns (the rubric)

The six reader jobs (Orient -> Convince -> Prove -> Model -> Enable ->
Sustain), mapped to the target docs.

| Reader job | Lives in |
|---|---|
| **Orient** — what is this, is it for me (one line) | README |
| **Convince** — why this, not LangChain / roll-my-own | README |
| **Prove** — a runnable win, fast | README (quickstart) |
| **Model** — typed in→out, nest, state outside | README states it; future tutorial shows it |
| **Enable** — author a commission, then compose | README: the fast taste; future tutorial: the depth |
| **Sustain** — test, debug, what's safe to depend on | README: pointers; future tutorial/reference: the depth |

## Rules

- **Voice: write for a novice AI coder.** Plain language, minimum jargon and
  assumed prerequisites; use a technical term only when it makes things
  *simpler*, not just more expert-sounding. Translate the SSOT's register
  *down* — don't copy it. Define the one coined term (*commission*) in plain
  words on first use.
- **One-way fence.** These concept drafts may link *out* to real docs. Live
  docs should link here only when explicitly labeling the target as a working
  draft.
- **Not machine-checked (yet).** At production, the contract section's
  machine-check (today on `authoring.md`) moves onto
  the README.
- **Promote, don't accumulate.** When a draft is true and polished it moves
  into the live docs, folds into the root README, or retires.

## Contents

- [`commission-fundamentals.md`](commission-fundamentals.md) — standalone
  conceptual front door: a commission separates **identity, capacity,
  permission, task, and result**, five surfaces each with a distinct owner
  (author / builder / caller / caller / framework). Shows the dials on each
  surface and the two inviolable promises (the declared boundary + the result
  envelope). Carries the capacity-vs-permission spending split and the 🔭
  model-ownership shape (catalog / profile / grant). Map-only; likely feeds a
  future tutorial/reference split once it settles.
- [`boundary-types.md`](boundary-types.md) — how-to companion to
  `commission-fundamentals.md`: how to write a commission's **input** and
  **output** types, and why they matter. Built on one thesis: the two types turn
  an AI instruction into a typed order of work (input = the form the caller
  fills, output = the promised deliverable) instead of a chat. Walks the author
  through making each type (substance vs steering, preconditions in the type,
  the instruction written once in `build_user_message`; smallest return shape,
  `Claim[T]` when a result must be traceable), plus the design rules and the 🔭
  fixed-return-type gotcha.
- [`composing-commissions.md`](composing-commissions.md) — conceptual how-to
  for the missing runtime angle: how small commissions become larger behavior.
  Explains parent-as-hub, Python coordinators vs AI-loop commissions, toolbox as
  capacity vs capabilities as permission, child result handling, cost/provenance
  rollup, state staying above the tree, and the first three composition shapes
  (pipeline, fan-out/gather, loop-until-done). Likely feeds a future tutorial's
  worked coordinator section.
