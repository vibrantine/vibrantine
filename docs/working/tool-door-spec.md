# The Tool Door: the execution seam, stubbed

## Status

**SPEC STUB, 2026-07-12.** Direction ratified in the post-Gatekeeper review
session; the build is scheduled for the next working session. The key
decisions below are settled and should not be relitigated at build time;
the open slots are listed at the bottom and are the build session's agenda.
The consumer that motivated the direction is the document-management
bundle (prompt-injection forensics is its core threat model), with Base
Coder as the second probe.

The name: the same job as the provider door, one seam over. The Gatekeeper
already keeps the register of every provider call; the tool door keeps the
register of every tool execution that passes through it. A logbook at the
workshop door, not a lock on the tools.

## Thesis

The Gatekeeper made the provider boundary trustworthy by giving it one
framework-owned choke point: every governed LLM call is logged, gated by
the fuses, and refused after a halt, no matter which interior made it.
Tool execution has no such point. A tool invoked by the LLM loop leaves a
trace only inside the loop's transcript; a tool invoked directly from a
custom `_run`'s Python leaves nothing anywhere. The forensic question
"show me every tool call this run made, with inputs and outputs" is
unanswerable today, and it is exactly the question a prompt-injection
postmortem asks, because the payload almost always arrives through a tool
(fetch, read).

The tool door is the missing half: one framework-owned point that tool
executions pass through, logging invocation and result and honoring the
run's stop signal, regardless of who invoked the tool.

## Settled decisions (ratified 2026-07-12)

1. **Ambient door, not vended tools.** Tools route *themselves* through
   the door from the inside: the tool's execute path finds the run's
   Gatekeeper through the same context mechanism `deposit_llm_trace` and
   `dispatch` already use. Tool acquisition is untouched: the toolbox
   stays plain dependency injection at construction, no run-time vending,
   no wrapper objects handed back by the run. This is what lets the door
   catch a *direct Python* call from a custom coordinator, not just
   loop-mediated calls, without changing the public contract of how a
   Commission gets its tools.

2. **Coverage promise, stated honestly.** Every call through a *shipped*
   tool is logged, because the library owns those tools and routes them
   through the door unconditionally. A custom tool is logged if its
   author opted in through the same public hook. The docs never claim
   "all tool calls are logged"; they claim the floor. Third-party
   authors who have their own logging, or none, are out of scope by
   design: opting out is legal, documented, and not the framework's
   problem.

3. **What the door enforces: log always, refuse after halt, nothing
   else.** After a fuse trips, a door-using tool refuses new executions
   the same way the provider door refuses new calls, so shipped tools
   stop working the moment the run halts instead of at their next
   cancellation checkpoint. The door does **not** enforce capabilities or
   the tool ceiling on direct Python calls. That would silently reverse
   a ratified ruling: permission gates the LLM's *menu*, never Python
   code ("Python coordinators' hardcoded dispatch calls are ungated by
   design"). Gate the menu, log the deed.

4. **Outside a run: harmless no-op.** A door-using tool invoked with no
   run in progress executes normally and logs nowhere, the same posture
   as `deposit_llm_trace` outside a dispatch. Tools stay usable as plain
   Python objects in scripts and tests.

5. **In-process guardrail, same escape as the provider door.** Custom
   Python that imports httpx directly steps around the door entirely.
   The library does not claim to close this and the docs say so in the
   same breath as the provider door's identical caveat. The promise is
   trust in what the framework owns, not a sandbox.

6. **Retention rides the call-row ownership rule.** Tool rows belong to
   the run, exactly like provider-call rows (ruled the same day): they
   die when the root record dies or by age, and nothing else touches
   them.

## Why this shape and not the alternatives

- **Vended/wrapped tools** (the run hands back door-wrapped callables,
  the way the catalog vends provider clients) was considered and
  rejected: it changes tool acquisition, which is settled public
  contract (DI at construction), and it still misses a tool object the
  author constructed and held before the run started. The ambient shape
  gets strictly more coverage for strictly less surface.
- **Execution-time permission enforcement** was considered and rejected
  as decision 3 records. The five-surface map keeps permission a menu
  concept; a door that polices code would blur capacity into permission.
- **Doing nothing** (tool calls are visible in the LLM transcript when
  the loop makes them) fails the forensic scenario twice: direct Python
  invocations are invisible, and transcripts only exist for nodes whose
  record persisted.

## Open slots (the build session's agenda)

- **The hook's name and shape.** Working assumption: an async context
  manager on the Gatekeeper mirroring `provider_call` (a
  `tool_call(name, ...)` that yields a report slot), plus whatever thin
  public re-export tool authors call. Whether the public face is a
  module-level function, a method the base tool class provides, or both,
  is a build-time call. It becomes frozen surface, so name it carefully.
- **Row shape and where rows land.** A sibling `tool_calls` table beside
  `calls`, or one generalized table? Working assumption: sibling table,
  keyed by `root_run_id` and the calling node's `run_id` like call rows,
  with tool name, timing, and status. Decide at build time whether the
  columns are the provider row's keys minus pricing or their own set.
- **Content versus metadata.** The forensic scenario wants inputs and
  outputs verbatim; tool payloads can be huge (a fetched page, a read
  file) and can carry secrets. Options: metadata plus sizes always, full
  content behind the record mode, content with a size cap. Unruled;
  this is the biggest open question and deserves the walkthrough.
- **The live accessor.** An `on_tool_call` kwarg mirroring
  `on_llm_call`, or one unified stream? Working assumption: mirror the
  existing kwarg; unification is a later simplification if the rows
  converge.
- **Which shipped tools route through it.** Enumerate at build time
  (fetch and the read/grep family at minimum; test doubles should route
  too so tests exercise the door).
- **Error capture.** A tool that raises should settle a failed row
  before the exception propagates; confirm the exact status vocabulary
  against the provider row's (`completed` / `failed` / `refused`).
- **Does a refused tool call ride the cancel path?** Working assumption:
  the door raises the same internal halt signal the provider door does,
  and the existing translation (loop catch, dispatch backstop) already
  speaks for it. Verify at build time that the vocabulary stays one.

## Relationship to the rest of the map

The tool door completes the symmetry the Gatekeeper started: `dispatch`
mediates Commission-to-Commission, `provider_call` mediates
Commission-to-provider, the tool door mediates Commission-to-world. All
three log to the same run-scoped register with the same ownership and
retention rules. On the five-surface map it is capacity machinery
(what the run observes and stops), not permission (what the menu
offers); decision 3 is that line, drawn explicitly.
