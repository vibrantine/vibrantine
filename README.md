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

That is the central bet: if every act of delegated work has the same reliable
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
| **Tool** | The same contract jacket, but deterministic throughout: no LLM call anywhere in its subtree. |
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
- Override `invoke` to own the control flow yourself.

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
and source notes, then returns a typed brief.

```python
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from vibrantine import CallContext, Commission, invoke_sync


class ResearchBriefInput(BaseModel):
    question: str = Field(
        min_length=1,
        description="The question the brief should answer.",
    )
    source_notes: list[str] = Field(
        min_length=1,
        description="Source notes or excerpts to ground the brief.",
    )
    audience: Literal["technical", "executive", "general"] = Field(
        default="general",
        description="The intended audience for the brief.",
    )
    target_length: Literal["short", "medium", "long"] = Field(
        default="medium",
        description="The desired length of the brief.",
    )


class ResearchBriefOutput(BaseModel):
    answer: str = Field(description="The direct answer to the question.")
    key_claims: list[str] = Field(description="Important claims made in the answer.")
    open_questions: list[str] = Field(
        default_factory=list,
        description="Questions that remain unresolved or uncertain.",
    )


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


result = invoke_sync(
    ResearchBriefCommission(),
    ResearchBriefInput(
        question="What are the main risks in this proposal?",
        source_notes=[
            "The project depends on an unstable third-party API.",
            "The estimated budget assumes no additional compliance review.",
            "The team has not yet validated demand with target users.",
        ],
        audience="executive",
        target_length="short",
    ),
    budget_usd=0.10,
)

if result.status == "success" and result.output is not None:
    print(result.output.answer)
    print(result.output.key_claims)
else:
    print(result.error)
```

The implementation inside the Commission can evolve. The caller still depends
on the same input and output boundary.

## Installation

Vibrantine is not published to PyPI yet.

Use it from a git dependency:

```bash
uv add "vibrantine @ git+https://github.com/vibrantine/vibrantine.git"
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

Vibrantine is early-stage software. The project is pre-v0.1 and not yet on
PyPI.

Available on `main`:

- Core `Commission` contract.
- `CommissionResult` envelope.
- Typed input/output discipline with Pydantic v2.
- `run_one`, `invoke_sync`, and `dispatch` entry points.
- LLM-loop support with a synthetic `conclude` tool.
- Deterministic tools for file, shell, fetch, search, and filesystem work.
- Cost and provenance on results, with child cost rollup.
- Optional filesystem persistence.
- Worked Commissions including `Ask`, `Summarise`, `Synthesize`,
  `MorningBriefing`, `DeepResearch`, and provisional validation examples.

Still settling:

- Authoring surface ergonomics.
- Public testing seam for LLM-loop Commissions.
- Logging and structured run tracing.
- Proof-of-life examples and release packaging.
- Budget handoff and richer resource accounting for broad/deep workloads.

The SemVer promise is deliberately tight: the public contract exported from
`vibrantine.__all__` is the dependency surface. Standard-library Commissions,
tools, and authoring helpers are useful, but may remain provisional until more
real consumers exercise them.

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

- [docs/design.md](docs/design.md): the design record: what Vibrantine is,
  why it exists, and how Commissions, tools, and application code fit
  together.
- [docs/authoring.md](docs/authoring.md): the one document about building
  Commissions: a verified step-by-step tutorial, the composition patterns,
  and the full contract reference.

Working concept drafts that may feed future tutorial/reference docs live in
[docs/working/concepts/](docs/working/concepts/).

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
