# The Runtime–Commission Boundary

Status: implemented for the next release. This document records the approved
correction to the runtime–Commission boundary and the contract the
implementation follows.

## Executive Summary

An application invokes a Commission through `run_commission`. That call creates
one temporary Vibrantine runtime around the principal Commission and its whole
subtree.

The clean ownership model is:

```text
Application
    |
    | principal Commission + typed input + run policy
    v
Vibrantine runtime
    |
    | bounded grants and signals
    v
Principal Commission
    |
    +-- Subcommission
    +-- Subcommission
```

The application supplies persistence to the Vibrantine runtime. The runtime
records the work. The Commission interior does not receive or operate the
application's persistence backend.

Before this correction, `CallContext.backend` and `CallContext.record` crossed
that boundary. They were used by `dispatch`, not by any shipped Commission.
The implementation moves that persistence plumbing behind the runtime
boundary. The application-facing `run_commission` shape stays intact.

## The Four Roles

### Application

The application decides:

- which principal Commission to invoke;
- what typed input to provide;
- which models, budgets, limits, and capabilities govern the run;
- whether and where run records are persisted; and
- what happens with the returned `CommissionResult`.

The application creates the persistence backend and passes it to
`run_commission`. It does not pass the backend to the Commission constructor.

### Vibrantine runtime

The runtime is created internally by each call to `run_commission` and lasts
only for that call. It owns:

- the run's private Gatekeeper;
- the model catalog and provider clients;
- resource fuses and provider-call concurrency;
- the provider-call ledger;
- the dispatch-tree ledger;
- run identifiers and parent-child links;
- output-policy enforcement; and
- persistence of records and ledgers.

The runtime surrounds the entire Commission tree. It is not itself a
Commission.

### Principal Commission

The principal Commission is the root work unit selected by the application. It
owns the task's control flow:

- decomposing the work;
- dispatching children;
- accumulating state during the invocation;
- handling child results;
- rolling up cost and provenance; and
- returning one `CommissionResult`.

It does not own the Gatekeeper or persistence.

### Subcommission

A subcommission is an ordinary Commission invoked inside the principal
Commission's tree. It runs under the same runtime, Gatekeeper, and persistence
binding as the principal.

A subcommission calls no `run_commission` of its own. It is entered through
`dispatch`.

## One Runtime, One Tree

One call to `run_commission` means:

- one root invocation;
- one temporary runtime;
- one Gatekeeper;
- one Commission tree; and
- one runtime persistence binding.

Every child result remains independently recorded and linked by
`parent_run_id`, but children do not create small runtimes or separate
databases.

Separate application tasks create separate runtimes. Those runtimes may all
write to the same application-owned backend.

## The Boundary Rule

The rule ratified by this document is:

> Application services are supplied to the Vibrantine runtime, not exposed to
> the Commission interior.

A Commission-facing context may contain:

- a value the Commission needs to make a decision, such as its budget grant;
- a bounded authority grant, such as capabilities;
- a read-only signal, such as cancellation; or
- a narrow one-way port, such as progress emission.

It should not contain a broad application service object with its own storage,
lifetime, query, or administrative operations.

If a Commission genuinely needs to interact with an external service to
perform its task, that interaction should cross a typed Tool or Commission
boundary. The application supplies the implementation explicitly through the
Commission's toolbox rather than leaving an application client in general
runtime context.

`on_progress` remains in `CallContext`. Progress is task knowledge only the
Commission may have, and the callback is a narrow one-way port: the Commission
may emit an event but receives no application service or state through it.

## Persistence Flow

The application-facing call remains simple:

```python
backend = SqliteBackend(path)

result = await run_commission(
    principal,
    input,
    backend=backend,
    record="always",
)
```

The intended internal flow is:

1. `run_commission` creates the private runtime.
2. The runtime retains `backend` and the recording default.
3. `dispatch` invokes the principal Commission.
4. The principal dispatches children inside the same runtime.
5. `dispatch` records each completed node through the private runtime.
6. The root persists the provider-call and dispatch ledgers.
7. `run_commission` closes the runtime and returns the root result.

At no point does a Commission body need the backend.

## Corrected Boundary Leak

Before implementation, `CallContext` exposed:

```python
backend: PersistenceBackend | None
record: PersistenceMode | None
```

The backend protocol includes storage, loading, listing, and deletion. A custom
Commission could therefore reach application-owned persistence even though
persistence was unrelated to its task.

This is a modularity problem before it is a security problem. Trusted
components should still receive only the dependencies needed to fulfill their
contract.

A repository search found no shipped Commission reading `ctx.backend` or
`ctx.record`. Their operational uses were inside `dispatch`, confirming that
they were runtime plumbing rather than Commission runtime conditions.

## Implemented Contract Change

Remove `backend` and `record` from the Commission-facing `CallContext`.

Keep `backend=` and `record=` on `run_commission`, because they are application
run policy.

The implementation carries those values on the existing private Gatekeeper,
available to `dispatch` but not through the context given to `_run`. It
preserves the governing invariant:

> Supported Commission code cannot obtain the persistence backend from its
> input or `CallContext`.

This correction does not make Python code a security sandbox. Custom code in the
same process can still import internals or access the filesystem if the
application grants that environment. The change restores the supported
architectural boundary; it does not claim hostile-code containment.

## Recording Policy Precedence

The application has final control over full node-record persistence:

1. A non-`None` `run_commission(record=...)` value governs every node.
2. When the application leaves `record=None`, a node's explicit
   `persistence_mode` supplies that node's default.
3. When neither has an opinion, wiring a backend means `"always"` and having
   no backend means `"off"`.

`Commission.persistence_mode` therefore remains useful as a recommendation
for an application that has not chosen a run-wide policy. It may not override
an explicit application decision.

An explicit application `record="off"` also prevents
`truncate_with_reference` from force-persisting a full output. That overflow
policy must degrade to `partial` with the full output retained, rather than
silently violating the application's persistence decision.

`record=` governs full node records. Provider-call and dispatch-ledger
persistence remains the separate behavior of a wired backend.

## What Does Not Change

- The application still calls `run_commission`.
- The application still chooses the persistence backend.
- A single backend may hold records from many principal runs.
- Children still produce independent records linked by run id.
- Provider calls still land in the call ledger.
- Every sanctioned invocation still lands in the dispatch ledger.
- Full LLM transcripts still land in persisted node records.
- `CommissionResult` remains the value returned to the caller.
- Commissions remain stateless across invocations.
- `dispatch` remains the only path around inside a run.
- `on_progress` remains a narrow Commission-facing emission port.

## Superseded Ruling Detail

The previous persistence ruling said:

> backends wired at construction [are ruled out] (the backend is a runtime
> concern, so it travels in the call context).

That prohibition remains correct: a backend is supplied at invocation time,
not attached to a Commission at construction.

The ratified replacement for the parenthetical is:

> the backend is a runtime concern, so it stays in the private runtime created
> by `run_commission` and never enters the Commission-facing context.

The ruling record now carries this explicit re-ruling. It is not an
implementation detail. `CallContext` is
part of the frozen public surface, and its exact fields are locked in
`tests/test_public_api.py`. The correction must land before the next release
and follow the repository's versioning and public-surface rules.

## Acceptance Criteria

- An application supplies one backend to `run_commission`.
- The principal Commission and every child are recorded through that backend.
- Provider-call and dispatch ledgers remain complete for the whole tree.
- Full default-loop prompts and responses remain present in persisted records.
- `CallContext` given to Commission code exposes neither the backend nor the
  recording default.
- `on_progress` remains available as a one-way event callback.
- An explicit application `record=` value overrides every node's
  `persistence_mode`.
- `record="off"` prevents overflow handling from force-persisting a full
  output.
- No Commission constructor accepts a backend.
- Subcommissions use the principal run's runtime through `dispatch`.
- Nested `run_commission` remains refused.
- Persistence failure remains visible without allowing an exception to escape
  the Commission boundary.
- Existing persistence, overflow-reference, call-ledger, dispatch-ledger, and
  MCP integration tests are adapted and continue to pass.

## Settled Decisions

1. Commission isolation means application persistence stays behind the
   Vibrantine runtime boundary.
2. `on_progress` remains a narrow one-way callback in `CallContext`.
3. The application has final authority over recording policy.
4. The correction lands before the next release, with the required
   public-surface and versioning treatment.
