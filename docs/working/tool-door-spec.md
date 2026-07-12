# The Dispatch Register: every sanctioned call, logged at the one seam

## Status

**SPEC, RE-RULED 2026-07-12.** Same-day supersession of the ambient
tool-door stub. The register moved from inside each tool to `dispatch`,
the seam every sanctioned invocation already passes; the user made the
re-ruling explicitly, with the code in front of us, so this is a
recorded decision and not drift. Decision 1 below carries what changed
and why. The settled decisions are locked; the working recommendations
are the build session's walkthrough agenda, one at a time.

The consumer that motivated the direction is unchanged: the
document-management bundle (prompt-injection forensics is its core
threat model), with Base Coder as the second probe.

The name changed with the seam. "Tool door" claimed a door on the
tools; the register is a logbook at dispatch, where tools and
Commissions alike already check in.

## Thesis

The Gatekeeper made the provider boundary trustworthy by giving it one
framework-owned choke point: every governed LLM call is logged, gated by
the fuses, and refused after a halt, no matter which interior made it.
Everything else a run does has no such register. A tool invoked by the
LLM loop leaves a trace only in mode-gated records; a pure-Python
coordinator's child calls leave the same or less. The forensic question
"show me every call this run made, with inputs and outputs" is
unanswerable when recording is off or a node opted out, and it is
exactly the question a prompt-injection postmortem asks, because the
payload almost always arrives through a tool (fetch, read).

The register is the missing half, and its choke point already exists:
`dispatch` mediates every sanctioned invocation, the LLM loop's tool
calls and a Python coordinator's child calls alike. It already stamps
run ids, re-wraps the breaker, and converts raised exceptions. It just
keeps no always-on register and refuses nothing after a halt. Give it
both.

## Settled decisions (ratified 2026-07-12)

1. **The register lives at dispatch, not inside the tools.** This
   re-rules the stub's "ambient door" (ratified earlier the same day
   against *vended tools*, never against this shape). The ambient
   door's coverage promise was a floor: shipped tools always, custom
   tools only by opt-in through a new public hook. The dispatch
   register covers every sanctioned call, custom tools included, with
   no hook, no opt-in plumbing, and no routing edits inside eleven
   tools. What it gives up is logging of a direct `_run` call that
   bypasses dispatch, which is already the contract-illegal path
   (dispatch is `_run`'s one sanctioned caller). A mis-declared row
   beats a missing one, and no new frozen surface beats both.

2. **A self-declared flag on the Commission base marks the
   deterministic ones.** Dispatch cannot otherwise tell a tool from a
   Commission, deliberately ("three categories, no fourth"). The flag
   is the node's own word, unverified, and it is log metadata only:
   **no framework behavior ever branches on it.** It exists so a
   forensic query can say "tool calls only"; the moment dispatch treats
   flagged nodes differently, the fourth category has arrived through
   the back door. Name and default are a build-time call (frozen
   surface; the Commission kwargs lock will trip and must be updated
   deliberately).

3. **Coverage promise, stated honestly.** Every invocation that crosses
   the contract boundary is logged, because they all pass dispatch.
   What escapes: a direct `_run` call (contract-illegal, the same
   posture as a smuggled context) and custom Python that never wears
   the contract at all (raw httpx, raw file writes; the provider door's
   identical caveat, stated in the same breath). The promise is trust
   in what the framework owns, not a sandbox.

4. **Outside a run: nothing changes.** `dispatch` already refuses
   outside a run, and tools stay usable as plain Python objects in
   scripts and tests, executing without logging. The posture the stub
   wanted is already enforced by the existing front-door rules.

5. **Retention rides the call-row ownership rule.** Register rows
   belong to the run, exactly like provider-call rows: they die when
   the root record dies or by age (the orphan sweep), and nothing else
   touches them.

## Working recommendations (the build session's walkthrough agenda)

Each of these needs its own explicit yes before build; the
recommendation is recorded so the walkthrough starts somewhere.

- **Log every dispatch, or only flagged ones?** Recommended: every
  dispatch. A pure-Python coordinator with recording off is invisible
  today too, and rows for everything make the register the run's
  complete node ledger. "Tools only" is a WHERE clause on the flag
  column, not a schema decision.
- **Metadata or content?** Recommended: metadata only (commission name,
  flag, run_id, parent_run_id, timing, status), joining `records` for
  verbatim input and output. The record-default flip already made a
  wired backend persist every node's content; the register's job is the
  always-on skeleton, not a second content store. Confirming this
  dissolves the stub's biggest open question.
- **Refuse-after-halt at dispatch.** Recommended: yes. After a fuse
  trips, dispatch refuses new invocations with the same breaker-stamped
  vocabulary the provider door uses (a refused row, then the failure
  value). Uniform for every node, and wind-down survives: `conclude` is
  loop-internal, not a dispatch. Today a post-trip child runs to its
  own cancellation checkpoint; this makes the stop structural. Note:
  `stop_signal_error`'s detail says "Provider call refused" and needs
  generalized wording or a sibling builder.
- **The flag's name and shape.** `deterministic: bool` states the
  honest interior fact; a `kind` tag reads better in SQL. Frozen
  surface, so name it carefully.
- **Row shape and table name.** A sibling table beside `calls` (working
  name: `dispatches`), keyed by root_run_id and the node's run_id, with
  a duck-typed store method like `store_calls`. One row settled at
  completion, carrying started_at and ended_at like provider rows; a
  two-phase start/settle shape only if live visibility of long tool
  runs earns it.
- **The live accessor.** Mirror `on_llm_call` (working name:
  `on_dispatch=`); unification into one stream is a later
  simplification if the rows converge.
- **The root's own row.** The root persists the run's logs from inside
  its own dispatch, before its own status is final and before the
  run_halted rewrite reads the persistence verdict. Decide the
  ordering: persist the register after the rewrite, or accept the root
  row landing in a second write.

## Why this shape and not the alternatives

- **The ambient in-tool door** (the stub's shape: tools route
  themselves through a Gatekeeper hook found by contextvar) was
  superseded as decision 1 records: opt-in coverage with a new frozen
  public hook, against universal coverage at a seam that already
  exists. Its one advantage, catching contract-illegal direct `_run`
  calls, was a bonus, never the promise.
- **Vended/wrapped tools** (the run hands back door-wrapped callables)
  stays rejected for the stub's original reasons: it changes tool
  acquisition, which is settled public contract (DI at construction),
  and it misses a tool object the author constructed and held before
  the run started.
- **Execution-time permission enforcement** stays rejected. Permission
  gates the LLM's *menu*, never Python code; a register that policed
  code would blur capacity into permission. Gate the menu, log the
  deed.
- **Doing nothing** fails the forensic scenario: records are mode-gated
  and per-node, so a node that opted out, or a run with recording off,
  leaves holes exactly where a postmortem needs continuity.

## Relationship to the rest of the map

`dispatch` mediates Commission-to-Commission and now keeps the register
of it; `provider_call` mediates Commission-to-provider and keeps its
own. Both log to the same run-scoped store with the same ownership and
retention rules, and after a halt both refuse in the same vocabulary.
On the five-surface map the register is capacity machinery (what the
run observes and stops), never permission (what the menu offers); the
flag is identity's self-description, and it gates nothing.
