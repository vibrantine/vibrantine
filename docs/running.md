# Running Commissions

What you control and what you can see when you run a Commission tree,
and the short list of conventions an operator holds in their head. The
concepts behind the boundary are [commission-model.md](commission-model.md);
building Commissions is [docs/authoring.md](authoring.md).

The point of everything on this page is a specific kind of trust: you
can hand a tree real money and real tools, walk away, and know the
worst case was set by you. That holds no matter who decides inside the
run: a model coordinating children answers to the same controls you set
at the top.

## One Run, One Set of Controls

`run_commission` is the only way to start a run, and `dispatch` is the
only way to invoke work inside one. That single entry point is what
makes run-wide guarantees possible: the caller sets the controls once,
at the top, and every LLM call and every child invocation in the tree
passes through the same seam. Nothing in the tree can swap them out or
route around them.

The controls, all keyword arguments on `run_commission`:

- **A budget with a fuse.** `budget_usd` is one number doing two jobs:
  the root's allocated grant, sliced down the tree as parents delegate,
  and a run-wide spend fuse that halts the run at the caller's limit.
  Inside the run, the deciding model sees its remaining grant as a
  `[budget]` line in its context, so wind-down is informed, not blind.
- **Resource fuses.** An LLM-call backstop (default 1000, always
  armed), an opt-in time limit (`time_limit_seconds`), and the spend
  fuse above. A tripped fuse halts the run loudly and structurally: new
  invocations are refused at the dispatch seam, in-flight work finishes
  and counts, and the root returns a `run_halted` failure naming the
  fuse, with true total spend reported.
- **A tool-exposure ceiling.** `tool_ceiling` clamps every tool menu in
  the tree, immutably, no matter what custom code does below. It bounds
  what the whole run may ever touch.
- **A concurrency room.** One tree-wide limit (default 16) on LLM calls
  in flight, so a wide fan-out cannot stampede the provider.
- **The model catalog.** `models=[...]` defines each model profile once
  (wire id, prices, call settings) under a name for the role it plays;
  every Commission references an entry by name or takes the run
  default. The catalog vends the provider clients, unknown names fail
  fast, and registering nothing gets the system default.

Fuses bound what a run consumes, never how it is composed, and no node
can read the run's running totals to steer by them: calls report in,
control flows out.

## Observability

Three tiers, matched to three needs:

- **Watch:** stdlib logging, for a human following a run in a terminal.
- **React:** live callbacks as the run proceeds. `on_progress` streams
  Commission-level progress events, `on_llm_call` streams every
  provider call with its spend, and `on_dispatch` streams every
  boundary crossing.
- **Query:** persisted records, when a backend is wired (JSON files or
  SQLite ship in the box). Records carry inputs, results, and full LLM
  transcripts per node, linked by run id. With SQLite, two metadata
  tables land beside them: `calls` (every provider call and its cost)
  and `dispatches` (every invocation's lineage, timing, and status), so
  a run's shape is queryable after the fact.

The dispatch register behind that `dispatches` table is always on, even
with no backend wired: every sanctioned invocation leaves a metadata
row, tools and Commissions alike. The tree you ran is never a matter of
memory.

## What You Must Actually Hold

Most of the discipline in Vibrantine is enforced by the machine:
identity checks fire at class definition, inputs and outputs are
validated at the boundary, exceptions become failure envelopes, unknown
model names fail fast. You do not have to remember those rules;
breaking them is loud and immediate.

A few things are conventions you carry in your head instead. This is
the honest, complete list.

1. **The underscore vocabulary.** A leading underscore means internal,
   with named exceptions. `_run` is implement-only: you write it, you
   never call it (callers always go through `run_commission`,
   `run_commission_sync`, or `dispatch`, which is where the boundary
   guarantees live). `_succeed`, `_fail`, and `_emit` are supported
   author helpers despite the underscore; they stay protected until the
   authoring-surface freeze promotes them.

2. **What `None` means, knob by knob.** `None` always means "no limit"
   or "no opinion," never zero. `budget_usd=None` is no grant and no
   spend fuse. `max_llm_calls=None` disarms the call backstop (the
   default, 1000, is armed). `time_limit_seconds=None` is no deadline.
   `tool_ceiling=None` is no ceiling, while an empty list is a ceiling
   that exposes nothing. Unrestricted capabilities permit everything,
   while an empty allow-list permits nothing. `record=None` defers to each
   node's `persistence_mode`, then to the wired backend's default; a node's
   `persistence_mode=None` means "no opinion."

3. **Three words for tool restriction, three owners.** `toolbox` is
   what a Commission owns: part of its identity, set at construction.
   `capabilities` is what a branch is permitted: a grant that can
   narrow as it passes down the tree. `tool_ceiling` is what the whole
   run may ever expose: immutable, set once at `run_commission`. The
   menu a model actually sees is the intersection of all three.

4. **Money speaks three dialects.** `budget_exceeded` means one node's
   grant ran out; it surfaces from that node and the tree above it
   decides what to do. `run_halted` means a run-wide fuse tripped
   (spend, calls, or time); it surfaces at the root and names the fuse.
   And the bound is real but soft: in-flight work finishes and counts,
   so a halted run can overshoot by roughly one turn per level of
   depth, with true spend always reported. If a number must never be
   exceeded, enforce it above the library.

5. **Persistence has an order of precedence.** Nothing records without
   a backend; wiring one is the "I care about logs" signal and defaults
   to recording everything. A non-`None` application `record=` governs
   every node. When it is `None`, a node's explicit `persistence_mode`
   supplies that node's default, followed by the wired default.

Everything not on this list is either enforced by the machine or
written down where the machine checks it
([docs/authoring.md](authoring.md) is machine-verified in CI).

## Where to Go Next

- The concepts behind the boundary:
  [commission-model.md](commission-model.md).
- Build a Commission: [docs/authoring.md](authoring.md).
- Prove one works, without a key:
  [docs/commission-testing.md](commission-testing.md).
- Why the controls are shaped this way:
  [docs/design.md](design.md).
