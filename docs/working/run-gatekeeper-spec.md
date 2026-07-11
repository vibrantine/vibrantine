# The Run Gatekeeper: the provider seam, settled

## Status

**BUILT, 2026-07-12**, in five commits on main (Gatekeeper + run_halted +
one front door; the model catalog and client= removal; the tool ceiling;
the calls table and on_llm_call accessor; this docs sweep). The
deferred-to-build slots were filled as follows: the internal object is
`_gatekeeper.Gatekeeper`; the call-count backstop shipped at 1,000 and the
room at 16 as specified; the calls table's columns are the log row's keys
plus `root_run_id`; the testing fake is `testing.scripted_model` (a catalog
entry carrying its ScriptedLLM); the log accessor is the `on_llm_call`
kwarg (plain dict rows, no new public type); client= was a straight
removal. One refinement ratified 2026-07-12 and promoted into design.md:
the root rewrite to `run_halted` is causal and claims failed roots only.
Only a failure that descended from the trip is rewritten (rewritten inside
the root's dispatch, before the record is persisted, so record and envelope
agree); a root that still concluded despite a trip keeps its result,
because winding down is the designed response to a trip; an unrelated root
failure coincident with a trip keeps its own error.

Below is the settled decision record as built, kept for the reasoning.
Settled 2026-07-11 after a full walkthrough with the author. This replaces
the earlier draft of the same name: the reasoning that survived is carried
over, and everything else was ruled in that session. The stated trigger
("the first consumer needs a budget it can trust") had fired: Base Coder,
the tier-1 consumer, is being built now.

The name: a Gatekeeper controls one door. It holds a call until a chair frees,
turns a call back when a fuse has tripped, and keeps the register of who
passed. A bouncer with a guest list and a capacity count, not a judge.

## Thesis

The framework mediates one Commission calling another (`dispatch`) but not a
Commission calling the provider: the LLM call goes straight from a
Commission's own client to the provider, mediated by nothing. So every
run-wide guarantee (a spend limit, a concurrency bound, a call log) has
nowhere to live except authoring discipline. The Gatekeeper is the missing
mirror seam: one object per run, standing at the provider door.

It is mechanism, not policy. It only trips what the caller set, and nothing it
holds decides anything the caller did not configure.

## The object

One internal object, created by `run_one`, carried to every node by reference
inside `CallContext`, consulted by every governed LLM call and by `dispatch`.

Three invariants:

1. **Calls report in. Control flows out. Data never flows back into a node's
   decision.** A node adapting to its remaining budget reads its own allocated
   slice, never the Gatekeeper's running total. The log is observational only.

2. **Shared state vs grants.** The object holds only what is the same for
   every node by reference: the fuses, the room, the stop signal, the log, the
   model catalog. Anything a node owns its own slice of (budget grant,
   capability grant, model choice) stays a distributed value on the context.
   The test: *does every node see the same one, or its own one?* A grant in
   the shared object reopens the "everyone owns the same money" footgun.

3. **Never swapped mid-tree, and enforced.** `dispatch` verifies that the
   child context carries the very same run object as the run in progress, and
   refuses otherwise. `replace()` on the context can rebuild grants; it cannot
   smuggle in a fresh room, fresh fuses, or a wider ceiling. A subtree
   tightens via a grant, never via a replacement object.

| Concern | Where it lives | Why |
| --- | --- | --- |
| Concurrency room | Shared (Gatekeeper) | Info-free global backpressure |
| Stop signal (cancel + breaker) | Shared (Gatekeeper) | Info-free global signal |
| Tool-exposure ceiling | Shared (Gatekeeper) | Stateless bound, checked against a constant |
| Spend fuse | Shared (Gatekeeper) | Value-carrying, used only as a trip |
| LLM-call count fuse | Shared (Gatekeeper) | Info-free counter, used only as a trip |
| Time limit | Shared (Gatekeeper) | Info-free global bound |
| Provider-call log | Shared (Gatekeeper) | The chokepoint sees every call |
| Model catalog | Shared (Gatekeeper) | One registry of profiles per run; vends clients |
| Budget grant | Distributed | Carries value between siblings |
| Capability grant | Distributed | Varies per branch |
| Model choice | Distributed | Which catalog entry this node uses |

**No public name.** The object has no importable type and no `__all__` entry.
The dev configures it entirely through `run_one` keyword arguments and never
holds it: a user who could construct one could pass the same one to two runs
(shared counters across unrelated runs) or hand a subtree a fresh one (the
fuse escape the no-swap rule exists to close). Configuration reuse is plain
Python (a dict of kwargs, splatted into `run_one`); a public config bundle is
an additive later step if a real consumer hurts without one. The cure for
"hidden" is observable, not constructible: everything the object does is
loud (a `run_halted` failure naming the fuse) and reconstructable (the log).

## One front door

`run_one` is the only way into a run, and `dispatch` is the only way around
inside one. Both directions are enforced with a refusal:

- **`run_one` called inside a run refuses**: "you are inside a run; use
  dispatch." The obvious honest mistake (calling the entry point whose name
  you know) would otherwise spawn a fresh run object and silently escape
  every fuse. Nothing is lost: any Commission that works as a principal works
  as a child through `dispatch`, same object, no special design. Joining the
  outer run instead was considered and rejected as ambiguous (whose
  `budget_usd` wins?); refusal is louder and teaches the habit.
- **`dispatch` called outside a run refuses**: "you are outside a run; use
  run_one." A hand-built `CallContext` used as an entry point would be a run
  with no Gatekeeper at all.

The abilities the hand-built-context entry used to provide (root capability
grant, cancellation, progress callback) move onto `run_one` as ordinary
kwargs, mapping one-to-one onto the context fields it already builds.

The skeleton (names and numbers provisional; the shape is settled):

```python
async def run_one[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    *,
    # money: one number, two jobs
    budget_usd: float | None = None,        # root grant AND spend fuse
    # the model catalog
    models: Sequence[Model] = (),           # defined once; empty = system default
    default_model: str | None = None,       # None: the single entry if exactly
                                            # one, else models.DEFAULT_MODEL
    # fuses
    max_llm_calls: int | None = 1_000,      # default-ON backstop; None disables
    time_limit_seconds: float | None = None,  # opt-in deadline, both seams
    # the room
    concurrency: int = 16,                  # provider calls in flight, whole tree
    # authority
    tool_ceiling: Sequence[str] | None = None,  # tree-wide LLM-exposure ceiling
    capabilities: CapabilitySet | None = None,  # root branch grant, as today
    # control and observability
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    backend: PersistenceBackend | None = None,
    record: PersistenceMode | None = None,
) -> CommissionResult[OutputT]:
```

Hello world stays one line: `run_one(commission, input)` gets the default
model, the call-count backstop, a room of 16, no budget, no deadline.
`invoke_sync` mirrors whatever this becomes.

## The fuses

Three, all resource-shaped: each bounds something the run consumes, never how
it is composed.

- **LLM-call count**: default-on, high (order of 1,000, calibrated at build),
  overridable, `None` disables. Price-blind, so it also catches a free or
  local model spiral. The always-on sanity check for the user who configured
  nothing.
- **Time limit**: opt-in. Checked at both seams (the provider call and
  `dispatch`), so a halt fires between LLM calls, not only at one.
- **Spend**: arms when `budget_usd` is set, at the same number as the root
  grant.

Structural limits (max depth, invocation count) were considered twice and
rejected twice: they bound composition rather than resources, and a deep
recursion that makes no LLM call is basically free. The user can pull the
plug on their own sloppy code; an unattended run sets a time limit.

**What a trip does.** A trip flips the run's stop signal, which is the same
cancellation path the loop already checks: `CallContext.cancel` and the
breaker are one mechanism, not two (the caller's token and the breaker are
combined into the one signal nodes see). New provider calls are refused;
in-flight calls finish and are fully counted. It stops the bleeding, not the
bug.

**What the caller sees.** The root result is a failure with a new
`run_halted` `ErrorKind` (retryable=False). The detail names the fuse and the
numbers, written for an AI-agent reader who has no other context, e.g.:
"spend fuse tripped: observed spend $4.98 of the $5.00 limit; 2 in-flight
calls completed for $0.31 more; full call log under run <id>." The result's
cost field reports true total spend, which is what makes "every dollar
reported" checkable. Node-level allocation exhaustion keeps
`budget_exceeded`: the line between the two kinds is scope (whole-run
teardown vs one branch out of its slice), not resource type.

**The spend fuse, honestly.** It refuses new calls once observed spend
passes the limit (strictly past: spending exactly the budget is within
budget, the same reading as the node-level checks, and what lets a $0
budget run free models); calls already in flight complete, and a call's cost is
unknown until it does. Overshoot is therefore bounded in *number of calls*
(about one concurrency window), not in *dollars*. The promise is "cannot
spiral, stops hard, bounded in calls, every dollar reported", which is why
the term is spend fuse, not hard ceiling. Custom fan-out is not auto-bounded
by allocation (only the default path allocates), so the fuse earns its keep
exactly there.

## The room

One room, N chairs, shared by the whole tree, so nested fan-out cannot
multiply past N (four children each fanning out four would otherwise mean
sixteen simultaneous calls that nobody chose). Default 16: high enough not to
throttle legitimate parallelism or trip provider rate limits in normal use,
calibrated at build. Overridable per run.

The correctness detail: **count leaf work, not coordinators.** A slot is
acquired only around the provider call and released before dispatching
children, so a waiting parent holds nothing and the room is deadlock-free
even at a limit of 1. Replaces the advisory `CallContext.concurrency`, which
nothing reads.

**v1 gates LLM calls only.** Network tools stay ungated: they cost time, not
money; a hung tool is the time fuse's job; and a custom tool wrapping a paid
external API is spend the framework cannot see, the dev's own escape (see
Trust boundary). Widening the gate later is additive.

## The tool ceiling

An **LLM-exposure ceiling**: it governs what a model may be *offered*, not a
whole-run execution ceiling. Set once at `run_one` (`tool_ceiling=`),
immutable, never varying by branch. Effective menu = toolbox ∩ branch grant
∩ ceiling, at the one line where the loop already builds the menu
(`run_llm_loop`'s `permitted` intersection). It closes the widening hole
mechanically: custom code can rebuild a branch grant via `replace` but not
the ceiling reference, so even a grant widened to unrestricted stays clamped.

It is **name-based, not effect-based**: it governs what a tool is called, not
what it does. A custom tool named `read` that secretly writes sails through.
That is consistent with the trust boundary, and it is the dev's own doing; a
Commission permitted to mint its own tools can likewise break the abstraction
from inside.

Execution-level enforcement at `dispatch` (rejecting a direct
`dispatch(WriteTool(), ...)` outside the ceiling) was considered and not
adopted: the framework has no runtime signal separating a tool leaf from a
reasoning Commission (both are `Commission`, keyed by `name`), and it would
not close the raw-Python escape anyway. Reopen only with a real consumer and
an effect-class model on the table.

## The model catalog

Define the run's models **once**, at the front door; every Commission
references one by name, or takes the run default. People typically run one to
three models depending on use case; the catalog is where they are defined
right, once, and then linked to. DRY and enforcement from one mechanism:
because the catalog builds and vends the clients, the framework owns provider
access by construction, which is what makes the seam real rather than
advisory. Model *choice* stays distributed: each node carries which entry it
uses, a value on the context, never a copy of the catalog.

Rulings:

- **`Commission(client=...)` is removed.** It was the raw-client escape
  sitting in the framework's own front door; while it existed, the seam was
  advisory by construction. Dev phase, so the cost is accepted.
- **`Commission(model=...)` narrows to a pure name**, looked up in the run's
  catalog when the loop runs. `Model` objects go in the catalog, not on
  Commissions.
- **Unknown names fail fast.** A Commission naming a model not in the catalog
  is an immediate, loud failure. Today's silent `resolve()` fallback to a
  bare OpenRouter model is pre-catalog behavior and retires with it.
- **Empty catalog auto-registers the system default** (`models.DEFAULT_MODEL`),
  so hello world configures nothing.
- **`default_model=None` with exactly one entry makes that entry the
  default** (naming it twice is busywork); otherwise the system default seam.
- **Per-branch model grants are dropped from v1** (a branch restricted to
  certain entries, parallel to the tool ceiling). No consumer has asked;
  additive later.
- **The testing seam moves to the catalog level.** Today it is `client=`; the
  replacement's exact shape (a fake catalog, or a catalog entry that vends a
  fake client) is a build-time detail. The requirement: tests never need the
  removed kwarg.

Multi-model budgeting stays a currency problem, not a location one: USD is
model-agnostic, so distributed allocation already spans models. Free and
local models price at zero (the parked local-model accounting item).

## The log

Every provider call passes one door, so the door keeps the register. One row
per provider call: run id, Commission name, model, timestamps, token counts,
cost, the calling node's grant at that moment, fuse state, and how the call
ended (completed, failed, refused by a fuse). Exact columns are a build-time
detail.

**Always-on in memory**: the fuses read the same counters, so the trust
promise never depends on a config flag. When a backend is wired, rows land in
a new **`calls`** table beside the existing **`records`** table (one row per
Commission call: input, result, transcript). The two join on run id and
answer different questions: "every provider call Summarize made this week,
and what it cost" is one simple query on `calls`; "what exactly did Summarize
receive and return in run X" is a lookup in `records`. **Absorb, not
replace**: the existing persistence system survives and gains a sibling; a
user or agent gets the full picture with basic SQL.

The log is retrievable by the caller after the run through a public accessor
(shape at build) and is observational only: nodes never read it to decide
anything. Out of scope: using it to cross-check reported cost for bypass
detection (a new feature, not this).

## Trust boundary

The Gatekeeper is an **in-process control** over the framework's own paths,
**not a sandbox**. Custom Python can step around it: a raw client, raw file
writes, sockets, a subprocess. Those are deliberate escapes the framework
does not claim to prevent, and true prevention needs host-level isolation.
It defends against compromised reasoning and composition, not compromised
execution infrastructure. This is the canonical statement; other sections
refer here.

## Budget, unchanged beneath the fuse

Budget stays **distributed**: each node debits its allocated slice, a parent
hands out slices of its remaining. It must not move into the Gatekeeper
(sibling A's spend would change what B sees, reintroducing nondeterminism).
Three tiers:

1. **Allocation** (distributed, deterministic).
2. **Wind-down**: on the default path each child gets `remaining`, shrinking
   to zero, then failing fast.
3. **The spend fuse** (Gatekeeper): a running total used *only* as a trip.
   It never reallocates or refunds; allocation stays the everyday path.

The stateless/stateful split explains the sort: the tool ceiling is a
constant set, so it lives in the shared object as a bound; accumulated spend
carries value, so it lives there only as a trip, never as a ledger nodes
read.

## Write sink, still demoted

Carried over unchanged: a run-wide write sink shares an implementation shape
with the log but not the semantics. A lock serializes writes, but
**serialization is not sibling isolation** (the external world is shared
mutable state even behind a write-only sink). The parent-owned single-writer
pattern (workers return typed proposals, one owner applies them) stays the
default; an effect-sink injection seam is at most an open application-level
possibility.

## Crafter note

Carried over: a runtime Commission-crafter's LLM calls still route through
the seam, so the ceiling and fuses backstop it, turning its worst case from
unbounded runaway into bounded waste. The parked containment rule stands (a
crafter emits only basic Commissions, whose tool access runs through the menu
where the ceiling enforces).

## Surface-cost inventory

The lock tests exist to make surface changes deliberate; this is the full
list of trips this design signs up for:

- `run_halted` joins the frozen `ErrorKind` Literal.
- `Commission.__init__` loses `client=`; `model=` narrows to a name.
- `run_one` and `invoke_sync` gain: `models`, `default_model`,
  `max_llm_calls`, `time_limit_seconds`, `concurrency`, `tool_ceiling`,
  `capabilities`, `cancel`, `on_progress`.
- `CallContext.concurrency` (advisory, unread) retires; the context gains an
  internal run-object reference, not a public name.
- `dispatch` gains two refusals: called outside a run, and a swapped run
  object.
- The persistence layer gains the `calls` table and the log accessor.
- No new public type: the Gatekeeper itself stays internal.

## Deferred to build time

Open by design, recorded so they are not mistaken for forgotten decisions:

- Exact fuse thresholds and the room's final default (the orders of magnitude
  above are the intent).
- The `calls` table's column list.
- The testing fake's shape.
- The internal object's name.
- The log accessor's shape.
- `client=` removal mechanics (straight removal expected; dev phase).

## Rejected along the way

Kept so they stay rejected for the recorded reason:

- **Structural fuses** (depth, invocation count): bound composition, not
  resources; LLM-free recursion is basically free, and the plug is right
  there.
- **A public Gatekeeper / RunServices type**: no consumer needs to hold it;
  kwargs suffice; a constructible run object reopens the swap and reuse
  holes.
- **Join-the-outer-run for nested `run_one`**: forgiving but ambiguous;
  refusal is louder and loses nothing.
- **A dispatch-enforced execution ceiling**: no runtime signal separates tool
  leaves from reasoning Commissions, and it would not close the raw-Python
  escape.
- **Replacing the records system with the provider log**: they answer
  different questions; absorb instead.
- **Per-branch model grants**: no consumer; additive later.
- **Budget scopes, reserves, and settlement protocols** (from the Base Coder
  probe): reserves and sequential accounting are local coordinator
  arithmetic; settlement between siblings is the rejected drawdown ledger.
