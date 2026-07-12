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
- **Provider boundary.** An external custom `_run` that needs LLM work
  dispatches an LLM-bearing child Commission. Direct provider calls are
  library-internal machinery because they must pass through the private run
  Gatekeeper; a raw call would bypass the run's fuses, room, cancellation,
  and call log.
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

#### One result envelope, one class

- **Decision.** `CommissionResult` stays a single class across all three
  statuses. The population invariants (output on success and partial,
  error on failure and partial) are runtime facts and documentation, not
  a tagged union a type checker narrows.
- **Why.** The envelope is the most-taught name in the library; splitting
  it into success/partial/failure types would triple the first vocabulary
  every consumer learns, for a guarantee the runtime already provides.
  The named price, accepted with eyes open: every call site pays a
  two-condition check (`status == "success" and output is not None`),
  and nothing in the types forces the partial branch to be handled.
- **Rules out.**
  - A discriminated `SuccessResult | PartialResult | FailureResult` union.
  - Accessors that raise (`unwrap()`), which would put exceptions back on
    the caller's path.

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

#### One menu; a reusable Commission is shared by object

- **Decision.** There is one toolbox menu, and tools and Commissions sit
  on it together; no parallel "Commission menu" exists. A general-purpose
  Commission that many nodes want (a coding Commission, say) is
  constructed once and the same object is placed in every toolbox that
  wants it, treated as frozen once shared. Name-based linking (a node
  carrying a string resolved against some central Commission registry)
  is deliberately absent; it becomes worth building only where a
  reference must cross a data boundary, which is the crafter's problem,
  not the library's. (Ruled 2026-07-12.)
- **Why.** A second menu would rebuild the tool/Commission type split
  the categories erased. Object sharing is already safe and lossless
  because Commissions hold no state between invocations; a name is
  strictly poorer (it needs a registry, a resolution failure mode, and a
  new frozen surface) and buys nothing until the reference genuinely
  leaves the process. Models are by-name because deployment config
  crosses that boundary; in-process composition does not.
- **Rules out.**
  - A central Commission registry or "universal menu" object in the
    library.
  - `Commission(tool="name")`-style resolution of children.
  - Mutating a shared instance's configuration after handing it out.

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
  protocol. The Gatekeeper's spend fuse reads a run-wide total solely to
  halt the run at the caller's limit; it never reallocates, refunds, or
  informs a node's decisions, so allocation remains the only budgeting
  mechanism.
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
  framework check. One mechanical bound stands above that discipline:
  the run's tool-exposure ceiling (see the Gatekeeper decision) clamps
  every menu in the tree, so even a grant widened by custom code cannot
  offer a tool outside it. Whether a Commission acts on the world or merely
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

#### One run, one Gatekeeper at the provider seam

- **Decision.** Every run gets one internal control object, created by
  `run_one`, carried to every node by reference, and consulted by every
  governed LLM call: the Gatekeeper, standing at the provider door as
  `dispatch`'s mirror. It holds only what is identical for every node:
  three resource fuses (LLM-call count, default on; time limit, opt-in;
  spend, armed by the budget), a tree-wide concurrency room that counts
  calls in flight rather than coordinators, an immutable tool-exposure
  ceiling, the provider-call log (always on in memory, landing beside the
  run records as a queryable table when a backend is wired), the dispatch
  register (the next decision), and the model catalog. `run_one` is the only entry into a run and `dispatch` the only
  path around inside one; each refuses the other's job, and `dispatch`
  refuses a context carrying a different run object, so the Gatekeeper can
  never be swapped mid-tree. It has no public name: the caller configures
  it entirely through `run_one` keyword arguments.
- **Why.** The library mediates unit-to-unit calls, but a Commission's LLM
  call went straight to the provider, so every run-wide guarantee had
  nowhere to live except authoring discipline. What keeps the object from
  being the shared state the joints forbid is one invariant: calls report
  in, control flows out, and data never flows back into a node's decision;
  a node reads its own granted slice, never the run's running totals.
  Fuses bound resources the run consumes, never how it is composed, and
  they stop the bleeding loudly (a `run_halted` failure naming the fuse
  and the numbers, all provider-reported spend included) rather than
  steering. A dollar-accounted call (a node grant or the run's spend
  fuse, including a grant-stripped subtree under a budgeted run) whose
  provider omits usage fails instead of being treated as free. The
  spend fuse is honest, not absolute: it refuses new calls at the limit
  and lets in-flight calls finish, so overshoot is bounded in calls, not
  dollars. The `run_halted` rewrite is causal and claims failed roots
  only (ratified 2026-07-12): causality is a stamp the framework sets at
  the point of translation (a refused provider call, a breaker-caused
  checkpoint exit), riding the error object up the tree, never inferred
  from the failure's kind or text. A stamped root failure (or a root
  `budget_exceeded` when the spend fuse and the root grant are one number
  read two ways) is rewritten before the record is persisted, so the
  stored record and the returned envelope tell one story; a root that
  still concluded despite a trip keeps its result, because winding down
  and concluding with what it has is the designed response to a trip, not
  a failure to override; and an unrelated failure that merely happened
  during a trip (including a coordinator's own scoped-token cancellation,
  or a failure an author manufactured rather than propagated) keeps its
  own error rather than being masked by the fuse story (the trip stays
  visible in the call log either way). And it
  is an in-process guardrail, not a sandbox: custom Python
  can step around it, a deliberate escape the library does not claim to
  close.
- **Rules out.**
  - A public Gatekeeper type, and any grant stored in the shared object.
  - Structural fuses (depth, invocation count), which bound composition
    rather than resources.
  - Nested `run_one` and hand-built-context entry: both doors refuse.
  - A second observability system: the provider log joins the record
    store, never replaces it.
  - Sandbox claims.

#### Every sanctioned call is logged at the one seam

- **Decision.** The run keeps a dispatch register: one metadata-only row
  per sanctioned invocation, tools and Commissions alike, settled when
  the call returns or is refused. A row carries the tree's edges
  (`run_id` / `parent_run_id`), the Commission's name, its deterministic
  flag, timing, and a status speaking the envelope vocabulary plus
  `"refused"` for an invocation a halted run never started; never
  payloads, never dollars (verbatim input and output belong to the
  records, spend to the call log, all joined by run id). The register
  lives in `dispatch`, always on in memory, landing beside the run
  records as a queryable `dispatches` table when a backend is wired and
  streaming live through `run_one(on_dispatch=)`. The seam enforces as
  well as observes: after a fuse trips, `dispatch` refuses new
  invocations, so stop means stop for children, not just for provider
  calls. (Ruled 2026-07-12, superseding a per-tool "door" drafted the
  same day: a door on every tool is many doors to keep honest, and
  `dispatch` is the one they all already walk through.)
- **Why.** The Gatekeeper made the provider boundary accountable with
  one choke point, but everything else a run did left traces only in
  mode-gated records: a tool call, or a pure-Python coordinator's
  children, could run with no always-on account of what ran under whom,
  when. The forensic question comes from the document-management bundle
  (prompt-injection forensics is its core threat model): the tree's
  shape must be reconstructable after the fact without having opted into
  full records. Metadata-only is what keeps the register from becoming a
  second record store.
- **Rules out.**
  - Payload or spend capture in the register.
  - Per-tool logging doors, or any second logging seam.
  - Dispatching new work after a halt.
  - A node reading the register mid-run to steer (the Gatekeeper's
    invariant again: calls report in, data never flows back into a
    node's decision).

#### The run's models are defined once; the catalog vends the clients

- **Decision.** The caller registers the run's models once at `run_one`
  (registering nothing gets the system default), and every Commission
  names an entry or takes the run default. An entry is a *profile*: one
  model configuration done right in one place (wire id, endpoint,
  prices, provider call settings) and named for the role it plays in the
  run, so the same underlying model may sit in the catalog twice under
  two roles, and the system default is itself a profile (ruled
  2026-07-12). The catalog builds and holds the clients; a Commission
  never constructs one, and a name not in the catalog fails fast. Model
  *choice* stays distributed: each node carries which entry it uses,
  never a copy of the catalog.
- **Why.** People run one to three models per use case; the catalog is
  where they are defined right, once, and then linked to. And because the
  catalog vends the clients, the framework owns provider access by
  construction, which is what makes the Gatekeeper's seam structural
  rather than advisory. The named price, accepted in the dev phase:
  `Commission(client=...)` is removed (it was the raw-client escape
  sitting in the framework's own front door), `model=` narrows to a pure
  name, and the testing seam moves to the catalog.
- **Rules out.**
  - Per-Commission clients on the governed path.
  - Silent fallback for unknown model ids inside a run.
  - Per-branch model menus, until a real consumer needs them.

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

#### None keeps a meaning per knob; the sentinel absorbs "unset"

- **Decision.** Constructor knobs distinguish "caller said nothing" (the
  unset sentinel, falling to the class default) from an explicit `None`,
  which keeps a real, knob-specific meaning: gate off
  (`max_input_tokens`), no cap (`max_output_tokens`), no opinion
  (`persistence_mode`), unrestricted (`CapabilitySet.tools`), no ceiling
  (`budget_usd`).
- **Why.** Each meaning is locally load-bearing: a tool must be able to
  say "no gate" as a different thing from "auto-size the gate"; a node
  must be able to defer recording without saying "off". A uniform
  None-means-off convention would buy surface consistency by breaking
  knob semantics. The cost is a per-knob lookup table in the user's head;
  the consolidated "meanings of None" table in authoring.md Part III is
  the designated re-entry point.
- **Rules out.**
  - Collapsing the meanings for consistency's sake.
  - A new knob adding another meaning of `None` without a row in that
    table and a reason recorded here.

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

### The escape hatch, for obligations

- **You give.** A custom `_run` takes over duties the default loop
  performs automatically: checking cancellation, dispatching children,
  summing their costs, carrying provenance on every return, depositing
  traces. These are checklist discipline, not rails, and the costliest
  one (cost rollup) is the easiest to slip on.
- **You get.** Full ownership of control flow, with the framework never
  inspecting the interior. The one-escape-hatch decision only works if
  the hatch is genuinely free; the price of that freedom is carrying the
  obligations yourself.
- **When it bites.** Per custom coordinator, at authoring time; a slip
  corrupts the subtree's receipts until noticed. The mitigations are
  `_succeed` / `_fail` for envelope assembly, the cost-rollup test
  recipe in commission-testing.md, and a runtime observation at the
  seam: `dispatch` logs a warning when a returned envelope's cost falls
  short of the provider spend the run witnessed in that call's subtree
  (observation only; nothing branches, and over-reporting stays legal,
  since an author may add costs the provider door never saw).

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
- **Per-model capacity caps.** A builder-side static spend cap riding
  with a Commission ("this worker may never exceed $0.01", the capacity
  half of budgeting), taking the minimum with the caller's grant. Built
  with the first genuinely tiered workload.
- **Adapters.** Small wrappers that expose any Commission as a tool to
  external agent systems, MCP first. Built when the first external
  consumer wants one.
- **Multimodal output, and further input modalities.** Image and audio
  *input* are built: typed parts (`TextPart` / `ImagePart` / `AudioPart`),
  verified live against the default model (2026-07). Video and document
  input wait for a settled provider shape and a real consumer (PDF is
  ruled to the doc-management bundle via text extraction first);
  multimodal *output* has no design yet.
- **Abstract intermediate Commissions.** The definition-time identity
  check requires all four ClassVars on every subclass, so a shared
  template base class cannot defer identity to its children; shared
  plumbing is shared as plain functions instead, which has covered every
  real case so far. Any fix softens the fail-fast check, so the shape of
  the softening is not guessed in advance. Built when the first real
  Commission family hurts without it.
- **Sibling streaming.** Consciously deferred, per the trades. Built
  when a real workload hurts without it. (The tree-wide concurrency cap
  that used to share this entry is settled into the Gatekeeper.)
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
