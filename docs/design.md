# Vibrantine: Design

## The purpose

Vibrantine compresses complex behavior into something an AI can use
with a minimal footprint. A Commission wraps a bounded act of work,
however deep its interior runs, behind the surface of one typed tool:
a name, a description, an input schema, and a structured result.
Whatever happens inside, only that surface reaches the caller.

The caller is meant to be a model. A human can start a run, and every
promise below holds for a human reader too, but the design is aimed at
an LLM holding Commissions in its toolbox: minimize the context each
delegation costs, maximize the quality of each decision made with it.
Choosing among typed work orders and inspecting typed receipts is an
easier decision problem than steering prose through a pipeline, so
even a modest model coordinates well when its world is made of
Commissions.

The value compounds with scale: a coordinator's context grows with the
children it consults, not the work performed beneath them, so a tree
can grow deep and wide while the top-level caller holds only the
envelopes it decided from.

(Re-ruled 2026-07-14: across revisions the docs had drifted toward
trust as the headline. Trust is the enabler; compression is the point.)

## What this document is

Four documents cover Vibrantine, each with one job. The
[`README.md`](../README.md) is the source of truth: what ships and why
you would use it. [`authoring.md`](authoring.md) is the builder's
manual, its claims verified in CI.
[`design-decisions.md`](design-decisions.md) is the ruling record:
every settled decision, its reason, and what it rules out. This
document is the design argument: what the library is for, the model
that delivers it, why the boundary can be trusted, what is refused,
and what everything costs.

One admission test keeps it honest: everything here argues the design.
What exists belongs to the README, how to build belongs to
authoring.md, and a settled ruling belongs to the record; this
document may cite a ruling by name, but never restates one.

## The model

The obstacle is the raw LLM call, which breaks the rules the rest of
your code lives by: it returns prose where your program wants values,
fails by surprise, spends money invisibly, and gives no account of
where its answer came from. An agent assembled from such calls
inherits those properties and compounds them with every step. Nothing
built from that part can be compressed behind a surface, because
nothing about it can be relied on without watching.

A Commission is an LLM call turned into a work order, as the name
says: a principal commissions bounded work under a firm contract, the
form of the task and the form of the deliverable agreed before work
begins. Typed input in, typed output out, failure as a value you can
handle, a receipt on every result saying what it cost and where it
came from. The work inside can be anything a work order can name:
answer a question, review a patch, act on the world (for an action,
the deliverable can be as thin as a typed confirmation). The contract
never fixes the activity; it fixes the two forms the caller touches.

The whole model is two sentences:

> **A Commission is one typed function with an LLM somewhere inside: one
> input in, one result envelope out.**
>
> **The parent is the only path between children: no sibling channels, no
> shared state.**

The first sentence makes a single unit of AI work reliable. The second
makes reliable units composable: a mistake in one child cannot reach
another except through a parent that saw it happen. And because every
joint is the same contract, the boundary is scale-invariant: a
Commission coordinating a hundred subcommissions presents the same
surface as one wrapping a single call, to the model that holds it as a
tool and to the human who wired it there alike. This is abstraction,
the property that makes large software possible, extended to work that
involves judgment: you can call it without reading its body, and
complexity grows inside boundaries, never between them. Agentic
behavior is not a primitive here; it is what emerges when disciplined
units nest.

## The enabler

A caller that never reads the interior is trusting the boundary
completely. That trust is earned rather than asserted, because the
fixed boundary makes every level of the system evaluable:

- **The unit.** A Commission is a function of its typed input, so its
  capability can be scored: run the battery, read the number. This is
  what the AI world calls evals, and the contract is what makes them
  possible.
- **The tree.** A subtree presents the same contract as a leaf, so a
  whole composition is scoreable as one unit, by the same means.
- **The run.** Every run leaves receipts: cost and provenance on every
  envelope, a call log, a dispatch register, full records when a
  backend is wired. What a system actually did is a query, not a
  memory.

Evaluable means improvable. Because the boundary never moves, an
interior can be reworked and rescored without the caller renegotiating
anything: capability climbs one measured part at a time, and the
levels above notice nothing but better results. Capable and trusted
are what that loop produces; the framework provides the precondition,
evaluable by construction.

Every ruling in [design-decisions.md](design-decisions.md) is a
consequence of the two core sentences or a boundary drawn to protect
them; machinery that no longer reduces to them is wrong, not the core.
Behind every entry sits the one question: **how do you hand work to
something fallible and still rely on the result?**

## The decisions

Every settled ruling lives in
[`design-decisions.md`](design-decisions.md), in one fixed shape (the
decision, why, what it rules out), grouped by what it protects: the
unit, the joints between units, and the caller's controls. The record
also carries the not-built list, where every planned item names the
trigger that earns its build.

Consult the record before changing anything at a boundary: growing a
public surface, adding an error kind, touching how units meet, or
building anything on the not-built list. A collision with a ruling is
a stop-and-flag moment; rulings change by explicit re-rule, never by
drift.

## What the library refuses to do

The ruling record says what a Commission is. This section says what
Vibrantine leaves out, deliberately and permanently, so you know which
half of an agentic system is yours to build.

One refusal underlies the rest: **the library never knows what sits
above it.** The caller might be a test script, a cron job, a web
server, a long-running agent, or another framework entirely. The
contract fits each the same by fitting none specially, because a
library that knows there is a scheduler above it starts bending toward
that scheduler, and then fits nothing else.

The nevers:

- **Never starts itself.** Nothing runs until a caller invokes it, so
  every run is attributable to somebody's decision. Initiative, time,
  and events belong to the application.
- **Never remembers.** Nothing survives between invocations unless the
  caller saves it and threads it back in (*state lives outside the
  library*).
- **Never talks sideways.** No channel between children except the
  parent (*the parent is the only path*).
- **Never faces the user.** No conversation, notifications, or
  rendering. The library returns values; showing them to a human is
  the application's job.
- **Never schedules.** No queues, timers, retry-later, or background
  work.
- **Never sets policy.** Which model tier a job deserves, which
  actions need a human's confirmation, whether a composition is wise:
  the framework guarantees the contract holds however you wire it, and
  takes no view on the wiring. Loop detection, topology validation,
  and confirmation gates are the caller's to add.

What the refusals buy: the contract cannot drift toward someone else's
application, an update can never surprise you by growing opinions
about scheduling or memory, and the library stays usable from any host
because it demands nothing of its surroundings.

## The trades

Every design buys its guarantees with real currency, and a record that
hides the prices cannot be trusted about anything else. Each entry:
what you give, what you get, when the giving hurts.

### Sibling isolation, for containment

- **You give.** No streaming between siblings and no pipeline
  parallelism: worker A's output reaches worker B only after A returns
  and the parent forwards it. Large intermediates sit in the parent's
  memory.
- **You get.** Containment and a readable data path: a mistake cannot
  travel anywhere the parent did not send it.
- **When it bites.** Long fan-outs with heavy payloads. The
  read-handle pattern relieves the heavy-payload case today; streaming
  stays consciously deferred until a real workload makes it felt.

### Shallow trees, for compounding control

- **You give.** Deep nesting is expensive: each level slices budget,
  stacks latency, multiplies error rates, and loses signal in
  translation. The instinct to solve a big job with many nested LLM
  levels runs into physics the contract does not soften.
- **You get.** Predictable cost and error behavior at scale, because
  breadth is cheap: siblings do not compound each other's errors,
  drift each other's goals, or stack each other's latency.
- **When it bites.** When an LLM mid-tree delegates deeper than the
  pattern was tested for. Working rules: go wide, not deep; let depth
  come from pattern choice, not mid-tree improvisation. Two
  reassurances: pipeline length is not tree depth (eight sequential
  stages under one coordinator is a shallow tree), and deterministic
  levels are nearly free; the caution is about stacked LLM judgment
  only.

### Coordination through structure, not emergence

- **You give.** Swarm, blackboard, market, and gossip patterns are not
  expressible inside a single tree; they need exactly the sibling
  channels the joints forbid.
- **You get.** The layering that makes such patterns buildable *well*:
  above the library, coordination of any shape can be written out of
  units that keep promises, observable and debuggable, instead of
  emerging between agents that keep none. Inside the tree, round-based
  coordination through a parent covers most of what a fan of workers
  needs.
- **When it bites.** When the problem genuinely wants free-running
  peers. Build that as an application above the library; that
  placement is the design working as intended, not a workaround.

### Contracts, for callability

- **You give.** Prototyping speed. Every boundary needs its two forms
  agreed before work begins: real friction next to a one-line prompt
  call, paid on day one.
- **You get.** Everything else in this document: results you can rely
  on, parts you can swap, a tree you can debug after the fact.
- **When it bites.** Constantly, in small amounts. The design's answer
  is to keep the toll small rather than pretend it away: one base
  class, and a basic Commission that is mostly two typed models and a
  prompt.

### The escape hatch, for obligations

- **You give.** A custom `_run` takes over duties the default loop
  performs automatically: checking cancellation, dispatching children,
  summing their costs, carrying provenance, depositing traces.
  Checklist discipline, not rails, and the costliest (cost rollup) is
  the easiest to slip on.
- **You get.** Full ownership of control flow, with the framework
  never inspecting the interior. The one-escape-hatch decision only
  works if the hatch is genuinely free; the price of that freedom is
  carrying the obligations yourself.
- **When it bites.** Per custom coordinator, at authoring time; a slip
  corrupts the subtree's receipts until noticed. Mitigations:
  `_succeed` / `_fail` for envelope assembly, the cost-rollup test
  recipe in commission-testing.md, and a runtime observation at the
  seam: `dispatch` logs a warning when a returned envelope's cost
  falls short of the provider spend the run witnessed in that subtree
  (observation only; over-reporting stays legal, since an author may
  add costs the provider door never saw).

## The Vibrantine Thesis

Everything above reduces to one thesis: **a bounded, contracted,
isolated unit is the right primitive for AI work, and everything else
composes above that unit without leaking back into it.**

The primitive exists for a consumer, and the consumer is meant to be a
model. A Commission is what lets an AI use complex behavior at the
cost of a tool call: the boundary carries everything the caller needs
and nothing it does not, trusted because every level behind it is
evaluable. If the thesis holds, the library stays small, stable, and
durable, and the interesting work happens above it, in applications
and coordinating models that can finally trust their parts.

The thesis is not proven; it is what building on the library finds
out, one consumer at a time. This document and the ruling record keep
that test honest. When a workload strains the design, the strain is
legible against a written ruling rather than argued from scratch, and
the presumption is always the same: the answer belongs above the
library, not inside it.
