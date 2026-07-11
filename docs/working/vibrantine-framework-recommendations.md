# Recommendations for Vibrantine

## Status

These are design recommendations discovered while specifying Base Coder, a
general-purpose coding Commission. They are proposals for consideration, not a
Vibrantine roadmap. API names are illustrative.

The recommendations assume the contract and composition model documented in
[`reference/vibrantine-authoring.md`](reference/vibrantine-authoring.md): typed
inputs and outputs, model-directed basic Commissions, code-directed custom
coordinators, immutable call contexts, bounded execution, and caller-owned
durable state.

## Executive Summary

The strongest candidates for Vibrantine itself are:

1. First-class budget scopes, reserves, and concurrent commitments.
2. Monotonic capability narrowing that cannot widen a parent grant.
3. Tree-wide concurrency enforcement for nested fan-out.
4. Documentation distinguishing Commission execution status from domain goal
   disposition.
5. Cookbook patterns for risk adjudication, rolling-wave coordination, and
   single-writer fan-out.

Repository maps, mutation ledgers, test policy, coding modes, and Git-specific
rules should remain application concerns.

## Design Test

A proposal belongs in Vibrantine core when it solves a recurring composition or
safety problem across domains and is difficult for each application to
implement correctly. Domain workflow and efficacy policy should remain in
Commissions and their callers.

The guiding principle is:

> Bound contracts, resources, effects, and termination without scripting every
> independent decision inside those bounds.

## 1. Budget Scopes and Protected Reserves

### Problem

The default model loop can pass remaining budget to children and report rolled
up cost. A custom coordinator still needs to allocate grants, preserve landing
room, sum sequential child costs, and reason conservatively about concurrent
calls. Each application implementing this independently risks oversubscription
or abrupt `budget_exceeded` exits without a useful domain handoff.

Exact turn cost is unknown before completion, so a nominal ceiling may be
overshot. Nested and concurrent work make the accounting harder.

### Recommendation

Provide a shared budget object or scope carried by `CallContext` with support
for:

- Remaining spend inspection.
- Protected reserves.
- Explicit child sub-budgets.
- Shared accounting across sequential children.
- Conservative commitments for calls in flight.
- Structural release or settlement when calls complete.
- Actual cost rollup, including overshoot.

An illustrative shape:

```python
budget = ctx.budget

with budget.reserve(verification_usd=0.20):
    child_ctx = ctx.with_budget(maximum_usd=0.40)
    result = await dispatch(child, child_input, child_ctx)
```

The API should make it difficult for multiple children to each believe they own
the same remaining grant. Concurrent reservations should fail or shrink before
dispatch rather than only after all children return.

### Scope

Monetary accounting is the first target because Vibrantine already reports it.
Deadlines, action rounds, and no-progress limits may remain application policy
unless multiple consumers demonstrate a stable general abstraction.

### Acceptance Criteria

- Sequential children cannot each receive the full unspent parent grant.
- Concurrent commitments cannot knowingly exceed spendable budget after
  reserves.
- A reserve cannot be consumed by ordinary child work.
- Actual overshoot is retained in reported cost.
- Existing `budget_usd` callers remain compatible or have a direct migration.

## 2. Monotonic Capability Narrowing

### Problem

`CallContext` is immutable and can be copied with a replacement capability set.
That is flexible, but application code can accidentally replace a narrow parent
grant with a wider child grant. Dynamic orchestration increases this risk.

Vibrantine capability menus also govern model-visible tools, while child
dispatches written directly in a custom coordinator are author-chosen and do
not consult that menu. These are distinct authority surfaces.

### Recommendation

Add an explicit narrowing operation that always intersects with the current
grant:

```python
child_ctx = ctx.narrow_capabilities(tools={"read", "grep"})
```

The operation should preserve the existing meaning of unrestricted capability
sets while guaranteeing that a child cannot gain a tool absent from its parent.

Consider documenting or representing separate dimensions for:

- Tools exposed to a model-directed loop.
- Child Commissions available to dynamic orchestration.
- Effect classes enforced by tools or external adapters.

The latter two need evidence from real consumers before becoming a large
authorization API. Monotonic tool narrowing is useful immediately and remains
small.

### Acceptance Criteria

- Narrowing is mathematically monotonic.
- An unrestricted parent may be narrowed to a finite set.
- A finite parent cannot be widened by a child.
- Nested narrowing composes predictably.
- The behavior is covered for empty and unrestricted sets.

## 3. Tree-Wide Concurrency Enforcement

### Problem

The current concurrency value is a per-coordinator hint rather than a tree-wide
limit. Nested fan-out can therefore multiply work beyond the caller's intended
concurrency, increasing provider pressure, cost commitments, and latency
variance.

### Recommendation

Carry a shared tree-level concurrency controller in the call context and make
dispatch cooperate with it. A root grant of four should bound the relevant
concurrent work across descendants rather than permit every coordinator to fan
out by four independently.

The design must avoid nested-dispatch deadlocks. A parent waiting for children
should not retain a scarce execution slot that those children require. The
limiter may need to count active provider or leaf work rather than stack frames.

### Acceptance Criteria

- Nested fan-out respects the root limit.
- Cancellation releases acquired capacity.
- Failed dispatches do not leak capacity.
- Parent-child waiting cannot deadlock at a limit of one.
- Progress and records expose enough information to diagnose queueing.

## 4. Domain Disposition Versus Execution Status

### Problem

Applications often have controlled outcomes such as `blocked`, `needs_input`,
`needs_approval`, or `no_change_needed`. These describe the domain goal, not a
failure to execute the Commission contract.

Adding every domain disposition to `CommissionStatus` or `ErrorKind` would make
the universal envelope application-specific.

### Recommendation

Document the two levels explicitly:

- `CommissionResult.status` states whether the Commission returned its intended
  envelope successfully, partially, or not at all.
- The typed output states whether the domain goal completed, suspended,
  blocked, or reached another controlled disposition.

A Commission can therefore successfully return a typed blocked outcome when
identifying and explaining that blocker is part of its contract. Framework
failure remains appropriate when cancellation, budget exhaustion, provider
failure, malformed output, overflow, or internal error prevents the intended
result.

This recommendation needs documentation and examples, not new universal status
values.

## 5. Risk Adjudicator Recipe

### Pattern

Document an independent policy-decision composition:

```text
action proposal -> deterministic policy -> adjudicator -> allow | deny | escalate
```

The proposal contains exact operations and targets, necessity, expected
effects, reversibility, alternatives, and evidence. A fresh read-only
Commission evaluates the proposal inside a caller-defined delegable envelope.

The adjudicator may interpret delegated authority but cannot create authority
the caller never granted. An allowance binds to the exact proposal and
machine-enforceable conditions. Repository content is evidence, not governing
instruction.

This should remain a recipe because policy, effect classification, and human
escalation boundaries are domain-specific.

## 6. Single-Writer Fan-Out Recipe

Extend the existing "reads look, writes carry" guidance with a worked pattern:

- Parallel workers inspect through narrowed read-only contexts.
- Workers return typed findings or patch proposals.
- One owner serializes accepted mutations.
- Verification and review operate on the gathered final state.

The example should show failure-as-value handling, child cost rollup,
cancellation, concurrency, and how to prevent sibling assumptions from becoming
shared mutable state.

This pattern applies to code, documents, configuration, and structured data,
but does not require a framework type.

## 7. Rolling-Wave Coordinator Recipe

Add a worked coordinator that repeats:

```text
orient -> choose next bounded intent -> act -> verify -> decide
```

It should demonstrate a coarse roadmap, one concrete active slice, evidence-led
replanning, conditional review, no-progress stopping, and a controlled partial
domain outcome before a hard budget stop.

The coordinator should own state and transitions while a model-directed child
retains freedom over tools and tactics inside each bounded intent. This would
make Vibrantine's managed-agency philosophy concrete without prescribing a
universal agent loop.

## 8. Controlled Landing Recipe

Document how a custom coordinator preserves a verification and handoff reserve,
checkpoints application state after each round, and returns a useful typed
partial outcome before framework exhaustion where possible.

This recipe complements first-class budget scopes. Even with framework support,
the application still decides what constitutes useful partial work and what
state is needed to resume.

## Keep Outside Vibrantine Core

The following Base Coder decisions should remain application policy unless
future non-coding consumers establish a broader abstraction:

- Repository maps and incremental code indexing.
- Workspace baselines, mutation ledgers, and stale-file fingerprints.
- Test-driven development and verification ladders.
- Coding-specific direct, deliberate, and coordinated modes.
- Git, dependency, migration, generated-file, and formatter policy.
- Coding acceptance criteria and semantic review rubrics.
- Autonomous versus supervised user-experience defaults.
- The exact Risk Adjudicator policy and non-delegable action list.

## Suggested Sequence

1. Clarify domain disposition versus execution status in documentation.
2. Add monotonic capability narrowing.
3. Design budget scopes and concurrent commitments using at least two real
   custom coordinators.
4. Add the risk-adjudication and single-writer fan-out recipes.
5. Add tree-wide concurrency after defining deadlock-safe semantics.
6. Add rolling-wave and controlled-landing examples once budget scopes settle.

## Open Questions

- Should a budget scope be mutable shared runtime state, an immutable context
  backed by a shared ledger, or a dispatch-owned service?
- How should concurrent budget commitments account for provider turns whose
  output cost is unknown?
- Should dynamic child-Commission authority join `CapabilitySet` or remain a
  separate orchestration policy?
- What unit should a tree-wide concurrency limit count to avoid nested waits?
- Which controlled-landing behavior belongs in dispatch and which must remain
  application-authored?

## Adjudication (2026-07-11)

Weighed point by point in discussion. Verdicts below; none has been built, and
`design.md` is deliberately untouched pending review. The one framework-level
finding (items 1 to 3) is written up separately in
[`run-gatekeeper-spec.md`](run-gatekeeper-spec.md).

Base Coder is treated here as a **tier-1 consumer**, actively being built in its
**own separate repository** that depends on Vibrantine as a library. That places
every recipe (items 5 to 8) in Base Coder, not in Vibrantine core: recipes live
with the consumer, and only a genuine framework gap flows back.

- **1. Budget scopes and reserves.** Rejected from core. The useful residue (a
  run-wide stop on runaway spend) becomes the Gatekeeper's spend fuse, not a
  stateful budget object or a reserve/settlement protocol. Reserves and sequential accounting are local
  coordinator arithmetic; settlement between siblings is the rejected drawdown
  ledger. Not building the report's version.
- **2. Monotonic capability narrowing.** Subsumed and strengthened by the
  Gatekeeper's capability ceiling (stateless, enforced on every governed path,
  clamps even widened branch grants; in-process, not a sandbox). A `narrow_capabilities` helper stays optional ergonomics, not
  the safety mechanism. Core piece lives in the Gatekeeper.
- **3. Tree-wide concurrency.** Accepted. Becomes the Gatekeeper's shared room,
  counting leaf work (not stack frames) for deadlock safety. In the spec.
- **4. Domain disposition vs execution status.** Accepted. Docs-only: one section
  distinguishing `CommissionResult.status` (did the envelope return) from the
  typed output (did the domain goal land). The only concrete deliverable owed,
  parked until after review.
- **5. Risk adjudicator recipe.** Base Coder artifact, not core. Its "interpret
  delegated authority, never manufacture it" rule is the live acceptance test for
  the item-2 capability ceiling. Not building in core; watch for the gap.
- **6. Single-writer fan-out recipe.** Base Coder artifact. Its "one owner
  serializes writes" core stays the recommended default pattern; the run-wide
  write sink was later demoted (serialization is not sibling isolation), so
  nothing enters core. The recipe stays with the consumer.
- **7. Rolling-wave coordinator recipe.** Base Coder artifact, the flagship
  demonstration of the code-directed / model-directed binary. No new framework
  need ("no-progress stopping" is a domain judgment, stays in the coordinator).
  Not building in core.
- **8. Controlled landing recipe.** Base Coder artifact, written with item 7. A
  coordinator lands against its own allocated grant (deterministic, readable), not
  against the Gatekeeper's hidden total; the fuse is the backstop below that.
  Confirms the services-vs-grants split from the consumer side. Not building in
  core.

Through-line: the report's one genuine core contribution was items 1 to 3, and
they are a single organ (the Run Gatekeeper / shared-services object), not three
features. Item 4 is a free docs win. Items 5 to 8 are Base Coder recipes the
framework only watches for gaps; none surfaced one. A good probe: it correctly
sensed that budget, capability, and concurrency wanted something in core, and the
discussion found its actual shape.
