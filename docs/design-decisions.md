# Vibrantine: Design Decisions

The ruling record. Every entry is a settled decision in one fixed
shape: the decision, why, and what it rules out, dated where the
ruling has a date. The argument these rulings serve is
[design.md](design.md); this file records outcomes and never
re-argues them.

Two disciplines keep it useful:

- Everything here is a ruling with a reason, or (under Not built yet)
  a plan with a named trigger. Anything else does not belong.
- If work you are about to do collides with an entry, stop and flag
  the collision. Rulings change by explicit re-rule, never by drift.

## Decisions

Grouped by what they protect: the unit, the joints, the caller's
controls.

### The unit

What one Commission is. These entries fix the anatomy of the
primitive: what counts as one, how its interior may be built, how the
model inside speaks and concludes, and the one envelope everything
returns in. They serve the first core sentence.

#### Three categories, no fourth

- **Decision.** Everything is a **Commission** (an LLM call somewhere
  in its subtree), a **Tool** (identical contract, deterministic
  throughout), or plain **application code** (above the library, no
  contract). A Tool is literally a Commission subclass; the
  distinction is authoring discipline, not a type.
- **Why.** Relying on a fallible worker means knowing which parts can
  be wrong in judgment-shaped ways; the LLM-anywhere rule answers that
  at every scale. Tools wear the same contract because callers need
  the same discipline from them.
- **Rules out.** A separate Tool ABC; any fourth "workflow" or
  "traffic controller" type (those decompose into a coordinator
  Commission or application code).

#### The interior is the author's choice, never a contract property

- **Decision.** Inside a Commission, either the author fixes the
  control flow in Python or an LLM chooses steps from a toolbox. Both
  wear the same contract, and the framework never inspects or branches
  on which one it is.
- **Why.** The boundary is the invariant; the interior is not. If
  callers could tell how a Commission works inside, implementation
  details would harden into obligations. Invisibility keeps the
  interior rewritable: swap a hand-coded pipeline for an LLM loop, or
  back, and no caller notices.
- **Rules out.** Framework features keyed to interior style, and
  callers that depend on one. (A working default rides along: keep
  control flow in Python; hand it to the LLM only when the routing
  genuinely needs judgment.)

#### One base class, one escape hatch

- **Decision.** One comprehensive `Commission` base underlies every
  unit. Its default interior is the complete LLM loop, so a basic
  Commission supplies only identity, types, a prompt, and a toolbox. A
  custom Commission overrides `_run`, the only extension point.
- **Why.** The authoring surface must carry hundreds of Commissions,
  including ones written by novices and lesser-model agents. Every
  added subtype or hook multiplies what an author can get wrong; one
  default path plus one escape hatch keeps the guarantees automatic on
  the common path and explicit on the custom one.
- **Provider boundary.** A custom `_run` that needs LLM work
  dispatches an LLM-bearing child. Direct provider calls are
  library-internal because they must pass the run Gatekeeper; a raw
  call would bypass the run's fuses, room, cancellation, and call log.
- **Rules out.** Subtype hierarchies, plugin hooks, per-feature
  mixins; any second way to change what a Commission does.

#### The model's interface is structured at both ends

- **Decision.** Tools reach the LLM through the provider's `tools=`
  API parameter, never prompt injection. Output leaves the loop only
  through the framework-injected `conclude` tool, whose input schema
  is the declared output type. There is no free-form "done."
- **Why.** Reliability rests on never parsing prose at a boundary, in
  either direction. Inbound, the API parameter is what models are
  trained on; outbound, completion by structured tool call validates
  the deliverable against the declared promise before it crosses.
- **Rules out.** Prompt-assembled tool menus; regex-parsing model
  output, or accepting a plausible-looking text answer as completion.

#### One result envelope, one class

- **Decision.** `CommissionResult` stays a single class across all
  three statuses. The population invariants (output on success and
  partial, error on failure and partial) are runtime facts and
  documentation, not a tagged union a type checker narrows.
- **Why.** The envelope is the most-taught name in the library;
  splitting it would triple the first vocabulary every consumer
  learns, for a guarantee the runtime already provides. The named
  price, accepted with eyes open: every call site pays a two-condition
  check (`status == "success" and output is not None`), and nothing in
  the types forces the partial branch to be handled.
- **Rules out.** A discriminated `SuccessResult | PartialResult |
  FailureResult` union; accessors that raise (`unwrap()`), which would
  put exceptions back on the caller's path.

### The joints

How units meet. These entries keep the second core sentence true under
composition: every path between units is visible, every crossing
leaves a receipt, and nothing accumulates in the dark.

#### The parent is the only path between children

- **Decision.** Children never communicate sideways or share mutable
  state; every result returns to the parent, which decides what
  happens next. Shared *reading* is fine and travels by handle (a path
  into a corpus, not a copied payload): reads look, writes carry, and
  anything that changes shared state funnels back through the parent,
  the single writer.
- **Why.** A mistake is contained the moment it is born, errors
  converge at the one point with the context to decide, and a tree's
  whole data flow is readable in one place, the parent's `_run`. The
  second core sentence as a decision: the maintainability load-bearer.
- **Rules out.** Sibling channels, blackboards, shared reducers;
  coordination that cannot be read off the parent's code.

#### One menu; a reusable Commission is shared by object

- **Decision.** Tools and Commissions sit together on the one toolbox
  menu; no parallel "Commission menu" exists. A Commission many nodes
  want is constructed once and the same object placed in every toolbox,
  treated as frozen once shared. Name-based linking is deliberately
  absent until a reference must cross a data boundary, which is the
  crafter's problem, not the library's. (Ruled 2026-07-12.)
- **Why.** A second menu would rebuild the type split the categories
  erased. Object sharing is safe because Commissions hold no state
  between invocations; a name needs a registry, a resolution failure
  mode, and a new frozen surface, and buys nothing in-process. Models
  are by-name because deployment config crosses a data boundary;
  in-process composition does not.
- **Rules out.** A central Commission registry or "universal menu";
  `Commission(tool="name")`-style resolution; mutating a shared
  instance after handing it out.

#### Errors are values

- **Decision.** No exception crosses the boundary. Failure returns as
  a structured error with a closed vocabulary of kinds, and partial
  results are first-class.
- **Why.** A parent can only plan around failures it receives as data:
  an exception tears through coordination, a value arrives exactly
  where the recovery decision lives. The vocabulary stays closed
  because each kind must represent a structurally distinct caller
  decision.
- **Rules out.** try/except as cross-boundary control flow; error
  kinds that don't change what a caller would do.

#### State lives outside the library

- **Decision.** A Commission holds nothing between invocations, and
  the framework offers no state objects or artifact slots.
  Accumulation lives in a coordinator's local variables while it runs,
  or above the library with the caller, threaded back in through typed
  input.
- **Why.** Statelessness keeps a Commission evaluable as a function of
  its inputs: testable, reproducible, swappable. Framework-held state
  would be a back-channel with a lifetime.
- **Rules out.** Memory layers and artifact slots; resume machinery
  (run *records* exist for inspection, and assembling them into
  resumable state is the caller's job).

#### Cost and provenance are structural

- **Decision.** Every result carries a cost and a provenance, and a
  child's cost rolls into its parent's result on both interior paths,
  at every depth.
- **Why.** Delegation you cannot account for is not reliable, just
  unexamined. Structural rollup makes any subtree's cost knowable and
  tiering auditable; provenance says how much to trust a result
  without rerunning it.
- **Rules out.** Ambient or global cost tracking; results without
  receipts.

### The caller's controls

What the invoker holds. One entry point sets everything a run must
obey; these entries keep every control in the caller's hand, out of
the tree's, and honest at the one seam all work passes through.

#### Budgets are allocated, not drawn down

- **Decision.** A budget is what the caller is willing to spend on
  this invocation. The Commission stays within it and reports actual
  cost on return; a parent gives each child a slice of its own
  remaining allocation. No mid-run drawdown account, no reservation
  protocol. The Gatekeeper's spend fuse reads the run-wide total
  solely to halt at the caller's limit; it never reallocates, refunds,
  or informs a node's decisions.
- **Why.** Allocation composes with one number and no coordination. A
  drawdown ledger would be shared mutable state between siblings,
  exactly what the joints forbid.
- **Rules out.** Reservation/refund protocols; reclaiming a child's
  unused budget for its siblings (application-layer if a workload ever
  earns it).

#### Capabilities bound what a Commission may do; gating is the caller's policy

- **Decision.** The caller hands down an allow-list of tool names. The
  LLM's menu is the toolbox intersected with that grant, and children
  inherit it, so on the default path a grant only narrows. A custom
  coordinator builds its children's contexts itself; keeping grants
  narrowing there is authoring discipline, with one mechanical bound
  above it: the run's tool-exposure ceiling (see the Gatekeeper)
  clamps every menu in the tree. Whether a Commission acts on the
  world or merely drafts is decided by what the caller granted, never
  by a contract property.
- **Why.** Authority delegated to a fallible worker must be bounded by
  the delegator, and the same unit must be reusable at different trust
  levels: grant the write tool and it acts, withhold it and it can
  only draft. For irreversible actions, a gate between decision and
  execution is a recommended pattern, but a gate is policy, and policy
  belongs to the caller.
- **Rules out.** Commissions that grant themselves tools; a framework
  rule that side effects live above the library; framework-decided
  confirmation gates.

#### One run, one Gatekeeper at the provider seam

- **Decision.** Every run gets one internal control object, the
  Gatekeeper: created by `run_commission`, carried to every node by
  reference, consulted by every governed LLM call at the provider door
  as `dispatch`'s mirror. It holds only what is identical for every
  node: three resource fuses (LLM-call count, default on; time limit,
  opt-in; spend, armed by the budget), a tree-wide concurrency room
  counting calls in flight, an immutable tool-exposure ceiling, the
  provider-call log (always on in memory, a queryable table when a
  backend is wired), the dispatch register (next decision), and the
  model catalog. `run_commission` is the only entry into a run and
  `dispatch` the only path around inside one; each refuses the other's
  job, and `dispatch` refuses a context carrying a different run
  object. The Gatekeeper has no public name: the caller configures it
  entirely through `run_commission` keywords.
- **Why.** A Commission's LLM call went straight to the provider, so
  run-wide guarantees had nowhere to live except authoring discipline.
  One invariant keeps the object from being the shared state the
  joints forbid: calls report in, control flows out; a node reads its
  own granted slice, never the run's totals. Fuses bound resources,
  never composition, and halt loudly: a `run_halted` failure naming
  the fuse and the numbers, all provider-reported spend included. A
  dollar-accounted call whose provider omits usage fails rather than
  counting as free. The spend fuse refuses new calls at the limit and
  lets in-flight calls finish: overshoot is bounded in calls, not
  dollars. The `run_halted` rewrite is causal and claims failed roots
  only (ratified 2026-07-12): causality is a stamp set where the
  framework translates a trip into an error (a refused provider call,
  a breaker checkpoint exit), riding the error upward, never inferred
  from a failure's kind or text. A stamped root failure (or a root
  `budget_exceeded` where fuse and root grant are one number read two
  ways) is rewritten before persistence, so record and envelope tell
  one story; a root that concluded despite a trip keeps its result,
  winding down being the designed response; an unrelated failure
  during a trip (a coordinator's own scoped cancellation, a
  manufactured error) keeps its own error, the trip visible in the
  call log. In-process guardrail, not a sandbox: custom Python can
  step around it, a deliberate escape the library does not claim to
  close.
- **Rules out.** A public Gatekeeper type, or any grant stored in the
  shared object; structural fuses (depth, invocation count), which
  bound composition rather than resources; nested `run_commission` and
  hand-built-context entry (both doors refuse); a second observability
  system (the provider log joins the record store, never replaces it);
  sandbox claims.

#### Every sanctioned call is logged at the one seam

- **Decision.** The run keeps a dispatch register: one metadata-only
  row per sanctioned invocation, tools and Commissions alike, settled
  when the call returns or is refused. A row carries the tree's edges
  (`run_id` / `parent_run_id`), the Commission's name, its
  deterministic flag, timing, and a status: the envelope vocabulary
  plus `"refused"`. Never payloads, never dollars: verbatim input and
  output belong to the records, spend to the call log, joined by run
  id. The register lives in `dispatch`, always on in memory, a
  queryable `dispatches` table when a backend is wired, streaming live
  through `run_commission(on_dispatch=)`. The seam enforces as well as
  observes: after a fuse trips, `dispatch` refuses new invocations, so
  stop means stop for children, not just provider calls. (Ruled
  2026-07-12, superseding a per-tool "door" drafted the same day: a
  door on every tool is many doors to keep honest, and `dispatch` is
  the one they all already walk through.)
- **Why.** The Gatekeeper made the provider boundary accountable, but
  a tool call or a pure-Python coordinator's children could run with
  no always-on account of what ran under whom, when. The forensic need
  comes from the document-management bundle, whose core threat model
  is prompt-injection forensics: a tree's shape must be
  reconstructable without having opted into full records.
  Metadata-only keeps the register from becoming a second record
  store.
- **Rules out.** Payload or spend capture in the register; per-tool
  logging doors, or any second logging seam; dispatching new work
  after a halt; a node reading the register mid-run to steer (the
  Gatekeeper's invariant again).

#### The run's models are defined once; the catalog vends the clients

- **Decision.** The caller registers the run's models once at
  `run_commission` (registering nothing gets the system default);
  every Commission names an entry or takes the run default. An entry
  is a *profile*: one model configuration done right in one place
  (wire id, endpoint, prices, call settings), named for its role, so
  the same model may sit in the catalog twice under two roles, and the
  system default is itself a profile (ruled 2026-07-12). The catalog
  builds and holds the clients; a Commission never constructs one, and
  an unknown name fails fast. Model *choice* stays distributed: each
  node carries which entry it uses, never a copy of the catalog.
- **Why.** People run one to three models per use case; the catalog is
  where each is defined right, once, then linked to. Because the
  catalog vends the clients, the framework owns provider access by
  construction, which makes the Gatekeeper's seam structural rather
  than advisory. Named price, accepted in the dev phase:
  `Commission(client=...)` removed (the raw-client escape in the
  framework's own front door), `model=` narrowed to a pure name, the
  testing seam moved to the catalog.
- **Rules out.** Per-Commission clients on the governed path; silent
  fallback for unknown model ids; per-branch model menus, until a real
  consumer needs them.

#### Oversized output is a policy the caller picks

- **Decision.** Every Commission can declare an output budget and an
  overflow policy, enforced at dispatch: the boundary, not the
  interior. The cap is off by default (`max_output_tokens=None`): no
  framework number fits every output type, so arming the defense is
  the author's move, made knowing the deliverable's shape. Four
  policies: `reject` fails the result; `partial` keeps
  the output and flags it on the envelope (the default);
  `truncate_with_reference` chops via the Commission's own
  `truncate_output` hook and force-persists the full version,
  reachable by the run_id named on the envelope (degrading to
  `partial`, never silently, when there is no backend, no hook, or a
  failed store); `flag` keeps the output and emits only a progress
  event, an explicit opt-out for callers that watch progress.
- **Why.** An oversized child result poisons an LLM-loop parent's
  context, and the parent cannot defend itself after the fact. The
  budget lives in the contract because the victim is upstream of the
  offender.
- **Rules out.** Silent truncation; LLM-summarized fallbacks
  (non-determinism in the failure path); unrecorded overflow by
  default.

#### Persistence records runs, never state

- **Decision.** Any invocation can persist a full record of its run
  (input, result, context snapshot, trace) to a backend the caller
  supplies at runtime. Children persist as independent records linked
  by run id.
- **Why.** "What did fan #7 actually return" must be answerable after
  the fact, or a hundred-unit tree is only debuggable while still in
  memory. Records are for inspection; the state decision already rules
  out records quietly becoming memory.
- **Rules out.** A framework memory system growing out of the record
  store; backends wired at construction (the backend is a runtime
  concern, so it travels in the call context).

#### None keeps a meaning per knob; the sentinel absorbs "unset"

- **Decision.** Constructor knobs distinguish "caller said nothing"
  (the unset sentinel, falling to the class default) from an explicit
  `None`, which keeps a real, knob-specific meaning: gate off
  (`max_input_tokens`), no cap (`max_output_tokens`), no opinion
  (`persistence_mode`), unrestricted (`CapabilitySet.tools`), no
  ceiling (`budget_usd`).
- **Why.** Each meaning is locally load-bearing: "no gate" is not
  "auto-size the gate," and deferring to the caller is not "off." A
  uniform None-means-off convention would buy surface consistency by
  breaking knob semantics. The cost, a per-knob lookup table in the
  user's head, is paid down by the consolidated table in authoring.md
  Part III, the designated re-entry point.
- **Rules out.** Collapsing the meanings for consistency's sake; a new
  knob adding another meaning of `None` without a row in that table
  and a reason recorded here.

#### The public surface is minimized mercilessly

- **Decision.** Complexity is judged at the boundary, not the
  interior. The surfaces a user's head must hold (`vibrantine.__all__`,
  the `Commission` constructor, `run_commission`'s keywords,
  `CallContext`) grow only under pressure from a real, named consumer,
  never from convenience, symmetry, or anticipation. Each is pinned by
  an exact lock test in `tests/test_public_api.py`, so growing one is
  a deliberate act: the lock is edited in the same commit, and the
  justification travels with it. When a fix can be interior complexity
  or new surface, the interior wins every time.
- **Why.** Every exported name, kwarg, and context field is a
  permanent claim on the user's memory and a SemVer commitment;
  interiors are invisible and refactorable. LLM-driven development
  pulls hard toward plausible additions that read simple in isolation
  and compound into an unholdable surface; prose guidance gets
  rationalized past, and the lock makes the pull visible at the exact
  commit where it happens.
- **Rules out.** Convenience exports ("public in spirit"); speculative
  kwargs and fields ahead of a demonstrated consumer; options as a
  substitute for a decision.

#### External-agent exposure is an outward adapter

- **Decision.** A small optional adapter under `vibrantine.mcp.server`
  may expose an application-supplied set of Commission objects as
  ordinary MCP tools. The first named consumer is a repository-local
  stdio server for Codex and Claude Code. Each valid tool call is one
  independent `run_commission` root invocation through an
  application-supplied runner, and its complete `CommissionResult`
  envelope crosses the protocol boundary. The application chooses the
  exposed objects and all run policy; the adapter translates names,
  schemas, arguments, cancellation, and results. The implementation
  plan is
  [`working/commission-as-local-mcp-spec.md`](working/commission-as-local-mcp-spec.md).
  Initial development uses the currently released SDK v1 line through
  the temporary range `mcp>=1.28,<2`. Stable SDK v2 is an explicit
  replacement trigger, not an optional upgrade: the adapter must move
  in place to `mcp>=2,<3`, remove its v1 dependency and v1-specific
  tests, and pass the complete compatibility plan before release. No
  dual-version path is permitted.
  (Ruled 2026-07-23.)
- **Why.** The Commission boundary already has the identity, selection
  prose, typed input, typed result, error, cost, provenance, and governed
  entry point an external tool surface needs. Codex and Claude Code are
  the real consumers that trigger the previously deferred adapter, while
  a repository-local application supplies the composition root the
  library deliberately refuses to own.
- **Rules out.** MCP concepts in `Commission`, `CallContext`, the
  Gatekeeper, or the top-level public surface; automatic Commission
  discovery or a generic call-by-name registry; implicit host context;
  shared state between MCP calls; adapter-owned routing, run policy, or
  tool-menu policy; building the inward MCP client at the same time.

## Not built yet

The design is whole; the implementation is not. One discipline: every
item names its trigger, the condition under which it gets built. An
item that cannot name one is a wish, and wishes do not belong in the
ruling record.

- **Coordinator templates.** Named, reusable coordinator classes
  (plan-fan-review, agent-loop, pipeline, route-dispatch,
  iterative-refine) with policy knobs as constructor arguments.
  Trigger: a second real coordinator repeats a shape; a template
  extracts what coordinators turn out to share, never speculation.
- **The envelope prompt layer.** An application-level prompt flowing
  unchanged through every Commission in a tree, plus per-call named
  sections a parent can add to without disturbing the others.
  Direction settled; section shape, ordering, and cache discipline
  open. Trigger: the first application that needs to speak to a whole
  tree.
- **Honest local-model accounting.** Cost is USD-only today, so a free
  local worker rolls up as $0 while consuming real compute; per-model
  budgets and token/time accounting are the settled direction.
  Trigger: the first genuinely tiered workload, frontier judgment
  above local fan workers.
- **Per-model capacity caps.** A builder-side static spend cap riding
  with a Commission ("this worker may never exceed $0.01", the
  capacity half of budgeting), taking the minimum with the caller's
  grant. Trigger: the same tiered workload.
- **Inward MCP adapter.** Wrapping selected operations from external MCP
  servers as explicitly placed Vibrantine Tools remains parked. Trigger:
  the outward adapter has shipped on stable MCP v2 and a real Commission
  needs one external MCP operation; the parked working specification is
  then reviewed against what the outward implementation taught us.
- **Multimodal output, and further input modalities.** Image and audio
  *input* are built: typed parts (`TextPart` / `ImagePart` /
  `AudioPart`), verified live against the default model (2026-07).
  Video and document input wait for a settled provider shape and a
  real consumer (PDF is ruled to the doc-management bundle via text
  extraction first); multimodal *output* has no design yet.
- **Abstract intermediate Commissions.** The definition-time identity
  check requires all four ClassVars on every subclass, so a shared
  template base cannot defer identity to its children; shared plumbing
  is plain functions instead, which has covered every real case so
  far. Any fix softens the fail-fast check, so its shape is not
  guessed in advance. Trigger: the first real Commission family that
  hurts without it.
- **Sibling streaming.** Consciously deferred, per the trades in
  [design.md](design.md). Trigger: a real workload that hurts without
  it. (The tree-wide concurrency cap that once shared this entry is
  settled into the Gatekeeper.)
- **The authoring-surface freeze.** The protected helpers and the tool
  namespace stay provisional until enough real consumers have
  exercised them. The freeze, including promoting protected names to
  public ones, is the v1 gate.
