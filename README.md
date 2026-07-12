# Vibrantine

A Python component model for building reliable AI agents from typed, isolated
units of work.

Vibrantine turns delegated AI work into **Commissions**: bounded work orders
with typed input, typed output, structured failure, provenance, and cost. A
Commission can be small, like "summarize these notes," or it can coordinate a
tree of child Commissions. The boundary stays the same.

Vibrantine is for agentic systems where work should be inspectable, composable,
testable, and safe to nest.

## Why Vibrantine?

Vibrantine is built toward one goal: **agentic behavior that is effective,
reliable, and maintainable.** Effective: real work that needs judgment, not
just retrieval or templating. Reliable: results you can depend on without
watching every step. Maintainable: change one part without fearing the rest.

AI-agent systems become hard to reason about when every part can read shared
state, mutate shared context, or hand vague prose to another agent. Errors,
assumptions, tool misuse, and wasted budget can compound without a clear place
to inspect or recover.

One-off prompts are useful for experiments, but a prompt string is not yet a
software component. It does not provide a typed interface, reusable boundary,
structured failure, cost tracking, provenance, or disciplined composition.

Vibrantine takes a different path:

- Typed inputs instead of hand-shaped prompt blobs.
- Typed outputs instead of prose the caller has to parse and hope is right.
- Structured result envelopes instead of unhandled failures.
- Parent-mediated composition instead of sibling chatter.
- Bounded blast radius instead of uncontrolled shared state.
- Cost and provenance that roll upward through the call tree.

The goal is not to make AI work less flexible. The goal is to put the
flexibility inside boundaries that ordinary software can inspect, test, reuse,
and safely compose.

## The Core Idea

A Commission is a bounded act of AI-bearing work.

You do not chat with a Commission. You issue a work order: investigate this,
summarize that, review this patch, classify these sources, draft this reply, or
verify this claim.

The point of a Commission is the work it performs. The typed result is what
makes that work safe to delegate: it gives the activity a clear beginning, a
clear end, and a value the caller can inspect.

```text
typed task
  -> bounded work
  <- CommissionResult[typed result]
```

Every Commission has:

- one declared input type that frames the task,
- one declared output type that defines the deliverable,
- one result envelope that records success, failure, cost, and provenance,
- and an interior where the activity happens.

Inside the boundary, a Commission may plan, search, read, call tools, invoke
child Commissions, revise, verify, or loop until it can responsibly conclude.
The outside stays the same.

That is the central thesis: if every act of delegated work has the same reliable
boundary, larger agentic behavior can be built by nesting smaller units without
losing the ability to inspect, test, budget, and recover.

## Result Envelopes

Every Commission returns a `CommissionResult[T]`.

```python
if result.status == "success":
    use(result.output)
elif result.status == "partial":
    review(result.output, result.error)
else:
    handle(result.error)
```

The envelope carries:

- `status`: `success`, `partial`, or `failure`.
- `output`: the typed payload, or `None` on failure.
- `error`: a structured error value rather than an uncaught exception.
- `provenance`: where the result came from and how grounded it is.
- `cost`: the cost attributed to the run, rolled up through child calls.

Failures are values. Partial results are first-class. Cost and provenance are
part of the structure, not an afterthought.

The envelope is also the whole error channel: no exception crosses the call
boundary. Whatever the interior raises arrives as a `failure` envelope, so
the one `status` check above really is the complete error handling story.

## Composition

Composition in Vibrantine is delegated work with receipts.

A parent Commission calls a child Commission, receives one `CommissionResult`,
inspects it, and decides what to do next. Children do not talk sideways. They do
not write to shared hidden state. They do not need to know who their siblings
are.

```text
caller
  -> parent Commission
       -> child A -> CommissionResult
       -> child B -> CommissionResult
       -> child C -> CommissionResult
     parent combines those results
  <- one parent CommissionResult
```

This model is deliberately restrictive. The restriction is what makes larger
systems easier to debug: the data path is visible, failures arrive as values,
and cost/provenance roll upward through the tree.

## Commissions, Tools, and Application Code

Vibrantine recognizes three categories:

| Category | Role |
| --- | --- |
| **Commission** | Typed input/output plus LLM judgment somewhere in its subtree. |
| **Tool** | The same contract, but deterministic throughout: no LLM call anywhere in its subtree. |
| **Application code** | Everything above the library: persistence policy, user surfaces, scheduling, long-term state, notification, and product workflow. |

There is no fourth "workflow" or "traffic controller" type in the library.
Larger behavior is built from Commissions, tools, and ordinary application
code.

The Commission/tool split is a contract fact, not a style note: a tool
promises there is no LLM anywhere in its subtree, so a caller always knows
which parts of a tree can exercise judgment and which cannot.

## Implementation Styles

A Commission always has the same outside: typed task in, result envelope out.
The inside is deliberately open.

In this Python implementation, most Commissions start from one of two authoring
hooks:

- Override `build_user_message` to use the built-in **LLM loop**, where the
  model chooses steps from a toolbox until it can produce the declared output.
- Override `_run` to own the control flow yourself.

Whichever hook a Commission uses, the same boundary machinery runs outside
it: inputs and outputs are validated, exceptions become failure envelopes,
and every run is recordable. The guarantees belong to the boundary, not to
the author's diligence.

Those hooks are not a limit on patterns. A custom interior can be a pipeline,
fan-out/gather, review loop, search process, external service call, verifier,
budget handoff, child-Commission coordinator, deterministic procedure, or a mix
of those. If the subtree includes LLM judgment, it is a Commission. If the whole
subtree is deterministic, it is a tool.

For successful completion, an LLM-loop Commission must produce the declared
output type. It cannot simply say "done" in prose.

The Commission model itself is not Python-specific. The current package is a
Python library, but the underlying contract is language-neutral: typed task,
bounded work, structured result envelope, parent-mediated composition, cost,
and provenance. A TypeScript implementation could uphold the same contract with
different host-language ergonomics.

## What You Must Actually Hold

Most of the discipline in Vibrantine is enforced by the machine: identity
checks fire at class definition, inputs and outputs are validated at the
boundary, exceptions become failure envelopes, unknown model names fail
fast. You do not have to remember those rules; breaking them is loud and
immediate.

A few things are conventions you carry in your head instead. This is the
honest, complete list.

1. **The underscore vocabulary.** A leading underscore means internal, with
   named exceptions. `_run` is implement-only: you write it, you never call
   it (callers always go through `run_commission`, `run_commission_sync`, or `dispatch`,
   which is where the boundary guarantees live). `_succeed`, `_fail`, and
   `_emit` are supported author helpers despite the underscore; they stay
   protected until the authoring-surface freeze promotes them.

2. **What `None` means, knob by knob.** `None` always means "no limit" or
   "no opinion," never zero. `budget_usd=None` is no grant and no spend
   fuse. `max_llm_calls=None` disarms the call backstop (the default, 1000,
   is armed). `time_limit_seconds=None` is no deadline. `tool_ceiling=None`
   is no ceiling, while an empty list is a ceiling that exposes nothing.
   Unrestricted capabilities permit everything, while an empty allow-list
   permits nothing. `record=None` follows the backend's default, and a
   node's `persistence_mode=None` means "no opinion, follow the caller."

3. **Three words for tool restriction, three owners.** `toolbox` is what a
   Commission owns: part of its identity, set at construction.
   `capabilities` is what a branch is permitted: a grant that can narrow as
   it passes down the tree. `tool_ceiling` is what the whole run may ever
   expose: immutable, set once at `run_commission`. The menu a model actually sees
   is the intersection of all three.

4. **Money speaks three dialects.** `budget_exceeded` means one node's
   grant ran out; it surfaces from that node and the tree above it decides
   what to do. `run_halted` means a run-wide fuse tripped (spend, calls, or
   time); it surfaces at the root and names the fuse. And the bound is real
   but soft: in-flight work finishes and counts, so a halted run can
   overshoot by roughly one turn per level of depth, with true spend always
   reported. If a number must never be exceeded, enforce it above the
   library.

5. **Persistence has an order of precedence.** Nothing records without a
   backend; wiring one is the "I care about logs" signal and defaults to
   recording everything. From there, a node's explicit `persistence_mode`
   beats the caller's `record=`, which beats the wired default.

Everything not on this list is either enforced by the machine or written
down where the machine checks it (`docs/authoring.md` is machine-verified
in CI).

## Minimal Example

This example sketches a small research-brief Commission. It accepts a question
and source notes, then returns a typed brief. `create_commission` builds a
working Commission from the decisions no one can make for you: what goes in,
what comes out, what it is called. Everything else is manufactured.

```python
from pydantic import BaseModel, Field

from vibrantine import create_commission, run_commission_sync


class ResearchBriefInput(BaseModel):
    question: str = Field(description="The question the brief should answer.")
    source_notes: list[str] = Field(
        description="Source notes or excerpts to ground the brief.",
    )


class ResearchBriefOutput(BaseModel):
    answer: str = Field(description="The direct answer to the question.")
    key_claims: list[str] = Field(description="Important claims made in the answer.")


research_brief = create_commission(
    name="research_brief",
    description=(
        "Create a grounded research brief from supplied source notes. "
        "Returns an answer and its key claims."
    ),
    input=ResearchBriefInput,
    output=ResearchBriefOutput,
)

result = run_commission_sync(
    research_brief,
    ResearchBriefInput(
        question="What are the main risks in this proposal?",
        source_notes=[
            "The project depends on an unstable third-party API.",
            "The estimated budget assumes no additional compliance review.",
            "The team has not yet validated demand with target users.",
        ],
    ),
    budget_usd=0.10,
)

if result.status == "success" and result.output is not None:
    print(result.output.answer)
    print(result.output.key_claims)
else:
    print(result.error)
```

That is a complete Commission: typed, budgetable, recordable, and it nests
in another Commission's toolbox like anything hand-written.

## The Same Boundary, Written by Hand

The factory covers the basic path. The day a Commission needs a custom
interior (its own tools, a prompt file, steering fields, hand-shaped
messages), the exit ramp is subclassing `Commission`. The boundary the
caller sees does not change. The same brief, grown up a little:

```python
from typing import ClassVar

from vibrantine import CallContext, Commission


class ResearchBriefCommission(Commission[ResearchBriefInput, ResearchBriefOutput]):
    name: ClassVar[str] = "research_brief"
    description: ClassVar[str] = (
        "Create a grounded research brief from supplied source notes. "
        "Use when the caller needs a concise answer based only on provided "
        "material. Returns an answer, key claims, and open questions."
    )
    input_type: ClassVar[type] = ResearchBriefInput
    output_type: ClassVar[type] = ResearchBriefOutput
    system_prompt: ClassVar[str | None] = (
        "Write grounded research briefs. Use only the supplied source notes. "
        "Separate confident conclusions from open questions."
    )

    def build_user_message(
        self,
        input: ResearchBriefInput,
        ctx: CallContext,
    ) -> str:
        notes = "\n\n".join(
            f"Source note {index + 1}:\n{note}"
            for index, note in enumerate(input.source_notes)
        )

        return (
            f"Question: {input.question}\n"
            f"Audience: {input.audience}\n"
            f"Target length: {input.target_length}\n\n"
            f"{notes}"
        )
```

The models and the calling code are the ones from the factory version; the
input model just grows two defaulted fields (`audience` and `target_length`)
for the new interior to read. Same `run_commission_sync`, same envelope handling.
The implementation inside the Commission can evolve, and the caller still
depends on the same input and output boundary. Subclassing and the rest of
the custom-interior path are covered in
[docs/authoring.md](docs/authoring.md).

## Installation

Vibrantine is not published to PyPI yet.

Releases are git tags (`vX.Y.Z`; see `CHANGELOG.md`). Pin a tag rather than
`main`, so your dependency stays fixed while `main` moves:

```bash
uv add "vibrantine @ git+https://github.com/vibrantine/vibrantine.git@v0.5.0"
```

Or from a local checkout:

```bash
git clone https://github.com/vibrantine/vibrantine.git
cd vibrantine
uv sync
```

LLM-backed Commissions use OpenRouter by default. Set `OPENROUTER_API_KEY` in
the environment before running them. Deterministic tools do not need a key.

## Current Status

Vibrantine is early-stage software; releases are git tags recorded in
`CHANGELOG.md`, and the project is not yet on PyPI. Right now the
repository is in transition: `main` has moved past the latest tag, so this
section keeps two honest lists, one per side of that gap: what pinning the
latest tag gets you, and what has landed since and ships with the next
release.

In the latest tagged release:

- Core `Commission` contract.
- `CommissionResult` envelope.
- Typed input/output discipline with Pydantic v2.
- `run_commission`, `run_commission_sync`, and `dispatch` entry points.
- `create_commission`: a deterministic authoring factory that builds a basic
  LLM-loop Commission from the crafted decisions (name, description, typed
  input/output, tools).
- LLM-loop support with a synthetic `conclude` tool.
- Budget enforcement end to end: children are dispatched with the remaining
  grant, a pre-turn gate declines unaffordable turns up front, and a
  `[budget]` status line gives the model mid-run spend visibility so a
  prompt can instruct a graceful wind-down.
- A working `truncate_with_reference` overflow policy: the author's typed
  `truncate_output` hook shrinks the output, and the full result is
  persisted under the run_id named in the error detail.
- Deterministic tools for file, shell, fetch, search, and filesystem work.
- Cost and provenance on results, with child cost rollup and raw token
  counts.
- Optional persistence with full LLM transcripts in the records; two shipped
  backends (JSON files, SQLite).
- Observability in three tiers: stdlib logging to watch, progress events to
  react, persisted records to query.
- A public testing seam: `vibrantine.testing`, which scripts the model so
  the full machinery runs for real, without an API key.
- Worked Commissions including `Ask`, `Summarize`, `Synthesize`,
  `MorningBriefing`, `RecursiveResearch`, the learning ladder
  (`vibrantine.examples.learning_ladder`: four runnable rungs, each the
  previous plus one idea), and an interactive demo runner
  (`python -m vibrantine.examples`).

On `main` since the latest tag, unreleased:

- Run-wide governance at the provider seam: every LLM call in a run passes
  one internal control object created by `run_commission`, carrying three resource
  fuses (an always-on LLM-call backstop, an opt-in time limit, and a spend
  fuse armed by the budget), a tree-wide concurrency room, an immutable
  tool-exposure ceiling, and an always-on provider-call log (`on_llm_call`
  live, a queryable `calls` table beside the run records when SQLite is
  wired). A tripped fuse halts the run loudly and structurally: new
  invocations are refused at the dispatch seam, in-flight work finishes
  and counts, and the root reports a `run_halted` failure naming the
  fuse, with all provider-reported spend included. A dollar-accounted
  paid call that omits token usage fails instead of counting as free.
- The dispatch register: every invocation that crosses the contract
  boundary (tools and Commissions alike) leaves an always-on metadata row
  (lineage, the node's self-declared `deterministic` flag, timing,
  status), the run's complete node ledger for forensics. Live via
  `on_dispatch`, queryable in a `dispatches` table beside the run
  records; content stays in the records, joined by run id.
- The run model catalog: model profiles are defined once at
  `run_commission(models=[...])` and referenced by name from Commissions; a profile
  bundles the wire id, endpoint, prices, and provider call settings
  (`params`), so one model can serve several roles ("fast-cheap",
  "deep-thinker") that differ only in settings. The catalog vends the
  provider clients, unknown names fail fast, and an empty catalog
  auto-registers the system default. This retired the old `client=`
  injection (breaking): scripted tests now register
  `vibrantine.testing.scripted_model(...)` in the catalog like any other
  profile.
- Multimodal input matured: audio joins image as a typed content part, each
  translated explicitly per modality at the provider boundary, both
  verified live.
- A cost-honesty check at the dispatch seam: when a hand-written
  coordinator's envelope reports less cost than the provider spend its
  subtree actually incurred, the run logs a warning naming the shortfall.

Still settling:

- Authoring surface ergonomics.
- Richer resource accounting for broad/deep workloads.

The SemVer promise is deliberately tight: the public contract exported from
`vibrantine.__all__` is the dependency surface. The worked example Commissions
under `vibrantine.examples`, the tools, and authoring helpers are useful, but
may remain provisional until more real consumers exercise them. One honest
caveat inside the frozen surface: model *shapes* are protected, but the
*contents* of `DEFAULT_MODEL` (its id, pricing, context window) are data
that changes as models come and go, without a major version.

## What Vibrantine Is Not

Vibrantine is not:

- a chatbot framework,
- a shared-state graph runtime,
- a multi-agent roleplay system,
- a scheduler,
- a memory layer,
- a UI framework,
- or a personal assistant runtime.

Those things can be built above Vibrantine. The library itself is the component
layer: typed work units, deterministic tools, structured results, and
compositional discipline.

## Documentation

Start here:

- [docs/design.md](docs/design.md): the design record: why the library is
  shaped the way it is, what that shape costs, and what is planned but not
  built.
- [docs/authoring.md](docs/authoring.md): the one document about building
  Commissions: a step-by-step tutorial with every code block verified end to
  end against a live model, the composition patterns, and the full contract
  reference, machine-checked against the library in CI so the doc cannot
  silently drift from the code.

Working notes live in [docs/working/](docs/working/); they promote into the
live docs or retire.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run basedpyright
```

Unit tests script model calls and do not require an API key. Integration
tests are marked and skip when `OPENROUTER_API_KEY` is absent.

## Contributing

Contributions should preserve the central contract:

- typed input and typed output,
- errors as values,
- parent-mediated composition,
- no hidden shared state,
- cost and provenance on every result,
- stateful product concerns kept above the library.

Useful contribution areas include deterministic tools, well-scoped
Commissions, examples, tests, documentation, and real consumers that stress the
contract.

## Contact

Future project contact: contact@vibrantine.com. This address is a placeholder
and does not currently exist.

## License

MIT. See [LICENSE](LICENSE).

## Closing Thought

Vibrantine is for building AI systems where every delegated piece of work has a
boundary, a receipt, and a way to fail safely.

The aim is not to remove judgment from AI systems. The aim is to make judgment
composable.
