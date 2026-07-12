# The Dispatch Register: every sanctioned call, logged at the one seam

## Status

**BUILT, 2026-07-12.** Ruled in full and shipped the same day. Same-day
supersession of the ambient
tool-door stub. The register moved from inside each tool to `dispatch`,
the seam every sanctioned invocation already passes; the user made the
re-ruling explicitly, with the code in front of us, so this is a
recorded decision and not drift. Decision 1 below carries what changed
and why. The settled decisions are locked, and the seven working
recommendations were walked one at a time and ruled (see the rulings
section); building followed the same day.

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

## Rulings (walkthrough of 2026-07-12, one at a time)

1. **Every dispatch is logged**, not only flagged ones. Rows for
   everything make the register the run's complete node ledger; "tools
   only" is a WHERE clause on the flag column, not a schema decision.
   You can filter a complete ledger down; you can't reconstruct rows
   never written.
2. **Metadata only**: run_id, parent_run_id, commission name, the flag,
   timing, status. Content stays on `records` (which the record-default
   flip already persists whenever a backend is wired), with `record=`
   as its one switch and the row's run_id as the reference into it. A
   content payload can be huge (media parts, whole documents), and a
   register that carries weight inherits pressure to be switchable,
   which breaks its continuity promise. Ruled after weighing an
   on/off-switch-plus-reference shape and finding it is the existing
   two-table design restated.
3. **Refuse-after-halt at dispatch, no knob.** After a trip, nothing
   new starts; in-flight work finishes and counts. The structured exit
   the author might want is already carried by values: a refused
   dispatch returns an ordinary failure envelope (the parent's own code
   keeps running), loops conclude via wind-down, the root envelope
   carries everything out, and post-halt life belongs to the caller
   above the run. An in-run "continue with zero LLM calls" mode was
   considered and rejected: it contradicts what a capacity halt means.
   A `halt_mode=` knob waits for a real consumer hurting.
4. **The flag is `deterministic: ClassVar[bool] = False`**, declared in
   the class body beside `name` and `description`, not a constructor
   kwarg: determinism is a property of what the author wrote, so the
   Commission kwargs lock does not grow. A `kind` tag was rejected as
   the type split returning as vocabulary. Default False errs safe (an
   undeclared tool merely filters less sharply; a wrong True would lie).
5. **The table is `dispatches`**, beside `calls`: one row per finished
   dispatch (no two-phase start/settle until live visibility of long
   runs earns it), columns root_run_id, run_id, parent_run_id,
   commission_name, deterministic, started_at, ended_at, status; same
   batching, deletion, and aging as `calls`, via a duck-typed
   `store_dispatches`.
6. **The live accessor is `on_dispatch=`**, mirroring `on_llm_call`.
   Merging the two streams stays a possible later simplification; today
   the rows differ in shape and two symmetric names are cheaper to hold.
7. **One write.** The root's own row is settled from the result already
   in hand at the persist point, so the whole register lands in one
   batch; its ended_at runs microseconds early, which nobody will care
   about. The two-write alternative (precision at the cost of a second
   plumbing path) was declined.

Build notes riding the rulings: the dispatch refusal uses a sibling
builder with the same breaker-born stamp (`stop_signal_error`'s
"Provider call refused" wording stays true at the provider door), so
the causal root rewrite claims refused-descended failures unchanged.
Row status speaks the envelope vocabulary (success / failure / partial)
plus `refused`.

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
