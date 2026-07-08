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

## Implementation Styles

A Commission always has the same outside: typed task in, result envelope out.
The inside is deliberately open.

In this Python implementation, most Commissions start from one of two authoring
hooks:

- Override `build_user_message` to use the built-in **LLM loop**, where the
  model chooses steps from a toolbox until it can produce the declared output.
- Override `_run` to own the control flow yourself.

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

## Minimal Example

This example sketches a small research-brief Commission. It accepts a question
and source notes, then returns a typed brief. `create_commission` builds a
working Commission from the decisions no one can make for you: what goes in,
what comes out, what it is called. Everything else is manufactured.

```python
from pydantic import BaseModel, Field

from vibrantine import create_commission, invoke_sync


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

result = invoke_sync(
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
for the new interior to read. Same `invoke_sync`, same envelope handling.
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

Vibrantine is early-stage software. The current release is v0.5.0, tagged in
this repository and recorded in `CHANGELOG.md`; the project is not yet on
PyPI.

Available in v0.5.0:

- Core `Commission` contract.
- `CommissionResult` envelope.
- Typed input/output discipline with Pydantic v2.
- `run_one`, `invoke_sync`, and `dispatch` entry points.
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
- A public testing seam: `client=` injection plus `vibrantine.testing`.
- Worked Commissions including `Ask`, `Summarize`, `Synthesize`,
  `MorningBriefing`, `RecursiveResearch`, the learning ladder
  (`vibrantine.examples.learning_ladder`: four runnable rungs, each the
  previous plus one idea), and an interactive demo runner
  (`python -m vibrantine.examples`).

Still settling:

- Authoring surface ergonomics.
- Richer resource accounting for broad/deep workloads.

The SemVer promise is deliberately tight: the public contract exported from
`vibrantine.__all__` is the dependency surface. The worked example Commissions
under `vibrantine.examples`, the tools, and authoring helpers are useful, but
may remain provisional until more real consumers exercise them. One honest
caveat inside the frozen surface: the model catalog's *shapes* are protected,
but the *contents* of `KNOWN_MODELS` and the id behind `DEFAULT_MODEL` are
catalog data that changes as models come and go, without a major version.

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
  Commissions: a verified step-by-step tutorial, the composition patterns,
  and the full contract reference.

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

Unit tests mock model calls and do not require an API key. Integration tests
are marked and skip when `OPENROUTER_API_KEY` is absent.

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
