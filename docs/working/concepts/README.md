# `concepts/`: concept drafts for later docs

⚠️ **These drafts are working material, not current product documentation.**

This folder holds deeper conceptual drafts that the live docs deliberately do
not carry yet. They are source material: when a draft is true and polished it
promotes into the live docs (the root README, `design.md`, or `authoring.md`)
or retires. This folder shrinks; it never becomes a layer of its own.

## Rules

- **Voice: write for a novice AI coder.** Plain language, minimum jargon and
  assumed prerequisites; use a technical term only when it makes things
  *simpler*, not just more expert-sounding. Translate the design record's
  register *down*, don't copy it. Define the one coined term (*Commission*)
  in plain words on first use.
- **One-way fence.** These concept drafts may link *out* to real docs. Live
  docs should link here only when explicitly labeling the target as a working
  draft.
- **Not machine-checked.** The live contract claims are machine-checked on
  `authoring.md`; nothing here is.
- **Promote, don't accumulate.** When a draft is true and polished it moves
  into the live docs or retires.

## Contents

- [`commission-fundamentals.md`](commission-fundamentals.md): standalone
  conceptual front door: a Commission separates **identity, capacity,
  permission, task, and result**, five surfaces each with a distinct owner
  (author / builder / caller / caller / framework). Shows the dials on each
  surface and the two inviolable promises (the declared boundary + the result
  envelope). Carries the capacity-vs-permission spending split and the 🔭
  model-ownership shape (catalog / profile / grant). Map-only; the
  five-surface map is the part most likely to promote.
- [`boundary-types.md`](boundary-types.md): how-to companion to
  `commission-fundamentals.md`: how to write a Commission's **input** and
  **output** types, and why they matter. Built on one thesis: the two types
  turn an AI instruction into a typed order of work (input = the form the
  caller fills, output = the promised deliverable) instead of a chat. Walks
  the author through making each type (substance vs steering, preconditions
  in the type, the instruction written once in `build_user_message`; smallest
  return shape, `Claim[T]` when a result must be traceable), plus the design
  rules and the 🔭 fixed-return-type gotcha.
- [`composing-commissions.md`](composing-commissions.md): conceptual how-to
  for the runtime angle: how small Commissions become larger behavior.
  Explains parent-as-hub, Python coordinators vs AI-loop Commissions, toolbox
  as capacity vs capabilities as permission, child result handling,
  cost/provenance rollup, state staying above the tree, and the first three
  composition shapes (pipeline, fan-out/gather, loop-until-done).
