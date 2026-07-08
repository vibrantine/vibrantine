# Vibrantine: Design

## What this document is

Three documents cover Vibrantine, and each has one job. The
[`README.md`](../README.md) is the source of truth: what Vibrantine is, what
ships today, and why you would use it. [`authoring.md`](authoring.md) is the
builder's manual: how to write a Commission against the contract, with its
claims verified in CI. This document is the design record: why the library is
shaped the way it is, what that shape costs, and what is planned but not
built.

One admission test keeps it honest. Everything in here is a decision with a
reason, a cost, or a plan. A claim about what exists belongs to the README; a
claim about how to build belongs to authoring.md; anything that is none of
the three does not belong in this document.

## The core

Vibrantine is built toward one goal: **agentic behavior that is effective,
reliable, and maintainable.** Effective: real work that needs judgment, not
just retrieval or templating. Reliable: results you can depend on without
watching every step. Maintainable: change one part without fearing the rest.
Most agentic systems today manage the first and fail the other two, because
they are built from a part that keeps no promises.

That part is the raw LLM call, which breaks the rules the rest of your code
lives by. It returns prose where your program wants values, fails by
surprise, spends money invisibly, and gives no account of where its answer
came from. An agent assembled from such calls inherits those properties and
compounds them with every step it takes.

A Commission is an LLM call turned into a work order. The name carries the
design: a principal commissions bounded work under a firm contract, the form
of the task and the form of the deliverable agreed before any work begins.
Typed input in, typed output out, failure as a value you can handle, and a
receipt on every result saying what it cost and where it came from. The work
inside can be anything a work order can name: answer a question, review a
patch, make a decision, or act on the world (edit this report, turn left);
for an action, the deliverable can be as thin as a typed confirmation that
it was carried out. The contract never fixes the activity; it fixes the two
forms the caller touches, the task and the deliverable.

The whole model is two sentences:

> **A Commission is one typed function with an LLM somewhere inside: one
> input in, one result envelope out.**
>
> **The parent is the only path between children: no sibling channels, no
> shared state.**

The first sentence makes a single unit of AI work reliable. The second makes
reliable units composable, because a mistake in one child cannot reach
another except through a parent that saw it happen. And because every joint
is the same contract, the boundary is scale-invariant: a Commission
coordinating a hundred subcommissions presents the same surface as one
wrapping a single call, to a human caller and to an AI agent handed it as a
tool alike. This is abstraction, the property that makes large software
possible, extended to work that involves judgment: you can call it without
reading its body, and complexity grows inside boundaries, never between
them. Agentic behavior is not a primitive here; it is what emerges when
disciplined units nest.

Everything else in this document is a consequence of these two sentences or
a boundary drawn to protect them; when a design grows machinery that no
longer reduces to them, the machinery is wrong, not the core. Behind both
sits the one question every decision below answers: **how do you hand work
to something fallible and still rely on the result?**

## Decisions

Every settled decision, in one fixed shape: the decision, why, and what it
rules out. The entries are grouped by what they protect: the unit, the
joints between units, and the caller's controls.

### The unit

What one Commission is.

#### Three categories, no fourth

- **Decision.** Everything in the system is a **Commission** (an LLM call
  somewhere in its subtree), a **Tool** (identical contract, deterministic
  throughout), or plain **application code** (above the library, wearing no
  contract). A Tool is literally a Commission subclass; the distinction is
  authoring discipline, not a type.
- **Why.** Relying on a fallible worker means knowing which parts can be
  wrong in judgment-shaped ways. The LLM-anywhere rule answers that at every
  scale: a deterministic coordinator with LLM-bearing children is a
  Commission; a composite of ten deterministic tools is still a Tool. Tools
  wear the same contract because callers need the same discipline from them.
- **Rules out.**
  - A separate Tool ABC.
  - Any fourth "workflow," "graph," or "traffic controller" type; those
    decompose into a coordinator Commission or plain application code.

#### The interior is the author's choice, never a contract property

- **Decision.** Inside a Commission, either the author fixes the control
  flow in Python or an LLM chooses steps from a toolbox. Both wear the same
  contract, and the framework never inspects or branches on which one it is.
- **Why.** The contract boundary is the invariant; the interior is not. If
  callers or the framework could tell how a Commission works inside,
  implementation details would harden into obligations. Invisibility is
  what keeps the interior rewritable: swap a hand-coded pipeline for an LLM
  loop, or back, and no caller notices.
- **Rules out.**
  - Framework features keyed to interior style, and callers that depend on
    one.
  - (A working default rides along: keep control flow in Python, which is
    deterministic, cheap, and testable; hand it to the LLM only when the
    routing genuinely needs judgment.)

#### One base class, one escape hatch

- **Decision.** One comprehensive `Commission` base underlies every unit.
  Its default interior is the complete LLM loop, so a basic Commission
  supplies only identity, types, a prompt, and a toolbox. A custom
  Commission overrides `_run`, and that override is the only extension
  point.
- **Why.** The authoring surface is meant to carry hundreds of Commissions,
  including ones written by novices and by lesser-model agents. Every added
  subtype or hook multiplies what an author can get wrong; one default path
  plus one escape hatch keeps the guarantees automatic on the common path
  and explicit on the custom one.
- **Rules out.**
  - Subtype hierarchies, plugin hooks, per-feature mixins.
  - Any second way to change what a Commission does.

#### The model's interface is structured at both ends

- **Decision.** Tools reach the LLM through the provider's `tools=` API
  parameter, never through prompt injection. Output leaves the loop only
  through the framework-injected `conclude` tool, whose input schema is the
  Commission's declared output type. There is no free-form "done."
- **Why.** The unit's reliability rests on never parsing prose at a
  boundary, in either direction. Inbound, the API parameter is what models
  are trained on. Outbound, completion by structured tool call means the
  deliverable is validated against the declared promise before it can cross
  the boundary.
- **Rules out.**
  - Prompt-assembled tool menus.
  - Regex-parsing model output, or accepting a plausible-looking text
    answer as completion.

### The joints

How units meet.

#### The parent is the only path between children

- **Decision.** Children never communicate sideways or share mutable
  state; every result returns to the parent, which decides what happens
  next. Shared *reading* is fine and travels by handle (a path into a
  corpus or codebase, not a copied payload): reads look, writes carry, and
  anything that changes shared state funnels back through the parent, the
  single writer.
- **Why.** A mistake is contained the moment it is born, errors converge
  at the one point that has the context to decide, and the whole data flow
  of a tree is readable in one place, the parent's `_run`. This is the
  second core sentence as a decision: the maintainability load-bearer.
- **Rules out.**
  - Sibling channels, blackboards, shared reducers.
  - Mid-flight gossip between workers; coordination that cannot be read
    off the parent's code.

#### Errors are values

- **Decision.** No exception crosses the boundary. Failure returns as a
  structured error with a closed vocabulary of kinds, and partial results
  are first-class.
- **Why.** A parent can only plan around failures it receives as data. An
  exception tears through coordination; a value arrives exactly where the
  recovery decision lives. The vocabulary stays closed because each kind
  must represent a structurally distinct caller decision.
- **Rules out.**
  - try/except as cross-boundary control flow.
  - Growing error kinds that don't change what a caller would do.

#### State lives outside the library

- **Decision.** A Commission holds nothing between invocations, and the
  framework offers no state objects or artifact slots. Accumulation lives
  in a coordinator's local variables while it runs, or above the library
  with the caller, threaded back in through typed input.
- **Why.** Statelessness keeps a Commission evaluable as a function of its
  inputs, which is what makes it testable, reproducible, and swappable.
  Framework-held state would be a back-channel with a lifetime.
- **Rules out.**
  - Memory layers and artifact slots in the library.
  - Resume machinery in the library: run *records* exist for inspection,
    and assembling them into resumable state is the caller's job.

#### Cost and provenance are structural

- **Decision.** Every result carries a cost and a provenance, and a
  child's cost rolls into its parent's result on both interior paths, at
  every depth.
- **Why.** Delegation you cannot account for is not reliable, just
  unexamined. Structural rollup means the aggregate cost of any subtree is
  always knowable and tiering decisions are auditable; provenance says how
  much to trust a result without rerunning it.
- **Rules out.**
  - Ambient or global cost tracking.
  - Results without receipts.

### The caller's controls

What the invoker holds.

#### Budgets are allocated, not drawn down

- **Decision.** A budget is what the caller is willing to spend on this
  invocation. The Commission stays within it and reports actual cost on
  return; a parent gives each child a slice of its own remaining
  allocation. There is no mid-run drawdown account and no reservation
  protocol.
- **Why.** Allocation composes with one number and no coordination. A
  drawdown ledger would be shared mutable state between siblings, exactly
  what the joints forbid.
- **Rules out.**
  - Reservation/refund protocols.
  - Reclaiming a child's unused budget for its siblings (application-layer
    if a workload ever earns it).

#### Capabilities bound what a Commission may do; gating is the caller's policy

- **Decision.** The caller hands down an allow-list of tool names. The
  LLM's menu is the Commission's toolbox intersected with that list, and
  children inherit the caller's grant, so on the default path a grant
  only ever narrows. A custom coordinator builds its children's contexts
  itself; keeping grants narrowing there is authoring discipline, not a
  framework check. Whether a Commission acts on the world or merely
  drafts is decided by what the caller granted, not by any contract
  property.
- **Why.** Authority delegated to a fallible worker must be bounded by the
  delegator, and the same Commission must be safely reusable at different
  trust levels: grant the write tool and it acts, withhold it and the same
  unit can only draft. For irreversible actions, splitting the decision
  from the execution and putting a gate between them is a recommended
  pattern, but the gate is policy, and policy belongs to the caller.
- **Rules out.**
  - Commissions that grant themselves tools.
  - A framework rule that side effects live above the library.
  - Framework-decided confirmation gates.

#### Oversized output is a policy the caller picks

- **Decision.** Every Commission can declare an output budget and an
  overflow policy, enforced at dispatch: at the boundary, not inside the
  unit. Four policies: `reject` fails the result; `partial` keeps the
  output and flags it on the envelope (the default);
  `truncate_with_reference` chops the output via the Commission's own
  `truncate_output` hook and force-persists the full version, reachable
  by the run_id named on the envelope (degrading to `partial`, never
  silently, when there is no backend, no hook, or a failed store); `flag`
  keeps the output and emits only a progress event, an explicit opt-out
  for callers that watch progress.
- **Why.** An oversized child result poisons an LLM-loop parent's context,
  and the parent cannot defend itself after the fact. The budget lives in
  the contract because the victim is upstream of the offender.
- **Rules out.**
  - Silent truncation.
  - LLM-summarized fallbacks, which put non-determinism in the failure
    path.
  - Unrecorded overflow by default: the default policy marks the
    envelope, and the progress-only `flag` policy must be chosen
    deliberately.

#### Persistence records runs, never state

- **Decision.** Any invocation can persist a full record of its run
  (input, result, context snapshot, trace) to a backend the caller
  supplies at runtime. Children persist as independent records linked by
  run id.
- **Why.** "What did fan #7 actually return" must be answerable after the
  fact, or a hundred-unit tree is only debuggable while it is still in
  memory. Records are for inspection; the state decision already rules out
  records quietly becoming memory.
- **Rules out.**
  - A framework memory system growing out of the record store.
  - Backends wired at construction; the backend is a runtime concern, so
    it travels in the call context.

#### The public surface is minimized mercilessly

- **Decision.** Complexity is judged at the boundary, not in the interior.
  The surfaces a user's head must hold (`vibrantine.__all__`, the
  `Commission` constructor, `CallContext`) grow only under pressure from a
  real, named consumer, never from convenience, symmetry, or anticipation.
  Each is pinned by an exact lock test in `tests/test_public_api.py`, so
  growing one is a deliberate act: the lock is edited in the same commit,
  and the justification travels with it. When a fix or feature can be
  built as interior complexity or as new surface, the interior wins every
  time.
- **Why.** Every exported name, constructor kwarg, and context field is a
  permanent claim on the user's memory and a SemVer commitment; interiors
  are invisible and refactorable. LLM-driven development pulls hard toward
  plausible additions (a knob, a field, a helper export) that each read as
  simple in isolation and compound into an unholdable surface. Prose
  guidance gets rationalized past in the moment; the lock makes the pull
  visible and deliberate at the exact commit where it happens.
- **Rules out.**
  - Convenience exports ("it was already public in spirit").
  - Speculative kwargs and fields ahead of a demonstrated consumer.
  - Options as a substitute for a decision: adding a flag where the
    framework should pick one behavior.

## What the library refuses to do

The sections above say what a Commission is. This one says what Vibrantine
leaves out, deliberately and permanently, so you know which half of an
agentic system is yours to build.

One refusal underlies all of them: **the library never knows what sits
above it.** The caller might be a test script, a cron job, a web server, a
long-running personal agent, or another framework entirely. The contract
works the same for each and is designed to fit none of them specially,
because the moment the library knows there is a scheduler above it, the
contract starts bending toward that scheduler, and then it fits nothing
else. Refusing to know is what keeps both sides free.

The nevers:

- **Never starts itself.** Nothing runs until a caller invokes it, so
  every run is attributable to somebody's decision. Initiative, time, and
  event handling belong to the application.
- **Never remembers.** Nothing survives between invocations unless the
  caller saves it and threads it back in. (Already a decision: *state
  lives outside the library*.)
- **Never talks sideways.** No channel exists between children except the
  parent. (Already a decision: *the parent is the only path*.)
- **Never faces the user.** No conversation, no notifications, no
  rendering. The library returns values; showing them to a human is the
  application's job.
- **Never schedules.** No queues, no timers, no retry-later, no
  background work.
- **Never sets policy.** Which model tier a job deserves, which actions
  need a human's confirmation, whether a composition even makes sense:
  the framework guarantees the contract holds no matter what you wire
  together, and takes no view on whether the wiring is wise. Loop
  detection, topology validation, and confirmation gates are the
  caller's to add.

What the refusals buy: the contract cannot drift toward someone else's
application, an update can never surprise you by growing opinions about
scheduling or memory, and the library stays usable from any host, whether
a five-line script or another agent framework, because it demands nothing
of its surroundings.

## The trades

Every design buys its guarantees with real currency. This section names
the prices, because a design record that hides them cannot be trusted
about anything else. Each entry is a deliberate trade, not an oversight:
what you give, what you get, and when the giving hurts.

### Sibling isolation, for containment

- **You give.** No streaming between siblings and no pipeline
  parallelism: worker A's output reaches worker B only after A returns
  and the parent forwards it. Large intermediates sit in the parent's
  memory.
- **You get.** Containment and a readable data path: a mistake cannot
  travel anywhere the parent did not send it.
- **When it bites.** Long fan-outs with heavy payloads. The read-handle
  pattern relieves the heavy-payload case today; streaming stays
  consciously deferred until a real workload makes it felt.

### Shallow trees, for compounding control

- **You give.** Deep nesting is expensive. Each level slices budget,
  stacks latency, multiplies error rates, and loses signal in
  translation, so the instinct to solve a big job with many nested LLM
  levels runs into physics the contract does not soften.
- **You get.** Predictable cost and error behavior at scale, because
  breadth is cheap: siblings do not compound each other's errors, drift
  each other's goals, or stack each other's latency.
- **When it bites.** When an LLM mid-tree delegates deeper than the
  pattern was tested for. The working rules: go wide, not deep; let depth
  come from pattern choice, not mid-tree improvisation. And two
  reassurances: pipeline length is not tree depth (eight sequential
  stages under one coordinator is a shallow tree), and deterministic
  levels are nearly free. The caution is about stacked LLM judgment only.

### Coordination through structure, not emergence

- **You give.** Swarm, blackboard, market, and gossip patterns are not
  expressible inside a single tree; they need exactly the sibling
  channels the joints forbid.
- **You get.** The layering that makes such patterns buildable *well*.
  Above the library, coordination logic of any shape can be written out
  of units that keep promises, observable and debuggable, instead of
  emerging between agents that keep none. Inside the tree, round-based
  coordination through a parent covers most of what a fan of workers
  actually needs.
- **When it bites.** When the problem genuinely wants free-running
  peers. Build that as an application above the library; that placement
  is the core design working as intended, not a workaround.

### Contracts, for callability

- **You give.** Prototyping speed. Every boundary needs its two forms
  agreed before work begins, which is real friction next to a one-line
  prompt call, and you pay it on day one.
- **You get.** Everything else in this document: results you can rely
  on, parts you can swap, a tree you can debug after the fact.
- **When it bites.** Constantly, in small amounts. The design's answer
  is to keep the toll small rather than pretend it away: one base class,
  and a basic Commission that is mostly two typed models and a prompt.

## Not built yet

The design above is whole; the implementation is not. This section lists
the gap, with one discipline: every item names its trigger, the condition
under which it gets built. An item that cannot name one is a wish, and
wishes do not belong in the design record.

- **Coordinator templates.** Named, reusable coordinator classes
  (plan-fan-review, agent-loop, pipeline, route-dispatch,
  iterative-refine) with policy knobs as constructor arguments. Built
  when a second real coordinator repeats a shape; a template extracts
  what coordinators turn out to share, never speculation.
- **The envelope prompt layer.** An application-level prompt that flows
  unchanged through every Commission in a tree, plus per-call named
  sections a parent can add to without disturbing the others. Direction
  settled; section shape, ordering, and cache discipline still open.
  Built when the first application above the library needs to speak to a
  whole tree.
- **Honest local-model accounting.** Cost is USD-only today, so a free
  local worker rolls up as $0 while consuming real compute. Per-model
  budgets and token/time accounting are the settled direction. Built
  with the first genuinely tiered workload: frontier judgment above,
  local fan workers below.
- **Model ownership: catalog, profile, grant.** The settled ownership
  spine for model access: the application owns the inventory of model
  profiles (a catalog, living above the library where state belongs), a
  Commission owns its default model and capacity, the caller grants a run
  its permitted subset, and a crafted Commission picks by key from within
  the grant; it never freely discovers or invents model access. A
  builder-side static spend cap ("this worker may never exceed $0.01",
  the capacity half of budgeting, taking the minimum with the caller's
  grant) rides the same direction. Built with autonomous Commission
  crafting, or with the first application that must hand different
  callers different model menus.
- **Adapters.** Small wrappers that expose any Commission as a tool to
  external agent systems, MCP first. Built when the first external
  consumer wants one.
- **Multimodal input and output.** The message shapes already leave room
  (typed parts rather than bare strings). Built when the first
  image-bearing consumer fixes the real fields.
- **Abstract intermediate Commissions.** The definition-time identity
  check requires all four ClassVars on every subclass, so a shared
  template base class cannot defer identity to its children; shared
  plumbing is shared as plain functions instead, which has covered every
  real case so far. Any fix softens the fail-fast check, so the shape of
  the softening is not guessed in advance. Built when the first real
  Commission family hurts without it.
- **Sibling streaming and a tree-wide concurrency cap.** Both
  consciously deferred, per the trades. Built when a real workload hurts
  without them.
- **The authoring-surface freeze.** The protected helpers and the tool
  namespace stay provisional until enough real consumers have exercised
  them. The freeze, including promoting protected names to public ones,
  is the v1 gate.

## The Vibrantine Thesis

Everything above reduces to one thesis: **a bounded, contracted, isolated
unit is the right primitive for AI work, and everything else composes
above that unit without leaking back into it.**

If the thesis holds, the library stays small, stable, and durable, and
the interesting work happens above it, in applications that can finally
trust their parts.

The thesis is not proven; it is what building on the library finds out,
one consumer at a time. This document's job is to keep that test honest.
When a workload strains a rule recorded here, the strain is legible
against a written decision rather than argued from scratch, and the
presumption is always the same: the answer belongs above the library, not
inside it.
