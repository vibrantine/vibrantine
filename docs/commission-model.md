# The Commission Model

How Vibrantine's unit of work is shaped, and why that shape makes AI
work safe to delegate and nest. This page is concepts only: building
Commissions is [docs/authoring.md](authoring.md), and running them
is [running.md](running.md).

A Commission is a bounded act of AI-bearing work. From the outside it
behaves like an ordinary typed function call: a typed task goes in, a
structured result comes out, and the framework, not the author's
diligence, keeps that true. To a calling model it is exactly one tool:
a name, a description, an input schema, and a structured receipt
coming back, however much work runs inside. That caller is the one the
design aims at. A Commission compresses complex behavior into a
minimal footprint so an AI can use it well, and the same contract
serves a human calling from a script. Everything below is what makes
discarding the interior safe, and what lets larger agentic behavior be
built by nesting smaller units without losing the ability to inspect,
test, budget, and recover.

## The Boundary

You do not chat with a Commission. You issue a work order: investigate
this, summarize that, review this patch, classify these sources, draft
this reply, or verify this claim.

The point of a Commission is the work it performs. The typed result is
what makes that work safe to delegate: it gives the activity a clear
beginning, a clear end, and a value the caller can inspect.

```text
typed task
  -> bounded work
  <- CommissionResult[typed result]
```

Every Commission has:

- one declared input type that frames the task,
- one declared output type that defines the deliverable,
- one result envelope that records success, failure, cost, and
  provenance,
- and an interior where the activity happens.

Inside the boundary, a Commission may plan, search, read, call tools,
invoke child Commissions, revise, verify, or loop until it can
responsibly conclude. The outside stays the same.

## Five Surfaces, Five Owners

A Commission separates five concerns, and each has a different owner.
Every knob in the library lives on exactly one of these surfaces, so
this map is the one to hold:

| # | Surface | Answers | Owner | Fixed when |
| --- | --- | --- | --- | --- |
| 1 | **Identity** (declaration) | What the Commission *is* | Commission author | Written into the class |
| 2 | **Capacity** (construction) | What this instance *can do*, and its built-in limits | Builder | Built into the instance, immutable |
| 3 | **Permission** (call-time context) | What this run is *allowed* to do | Caller | Per run |
| 4 | **Task** (payload) | What this run is *asked* to solve | Caller | Per run |
| 5 | **Result** (envelope) | What came *back*, and how to trust it | Framework + Commission | Returned by the call |

Read it as a sentence of ownership: the author owns what it is, the
builder owns what it can do, the caller owns both what it may do and
what it must solve, and the framework guarantees the shape of what
comes back.

Two surfaces never bend: the declared boundary (identity's input and
output types) and the result envelope. Those two promises are the
contract. Every dial lives on the middle three surfaces.

## The Result Envelope

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
- `cost`: the cost attributed to the run, rolled up through child
  calls.

Failures are values. Partial results are first-class. Cost and
provenance are part of the structure, not an afterthought.

The envelope is also the whole error channel: no exception crosses the
call boundary. Whatever the interior raises arrives as a `failure`
envelope, so the one `status` check above really is the complete error
handling story.

## Composition

Composition in Vibrantine is delegated work with receipts.

A parent Commission calls a child Commission, receives one
`CommissionResult`, inspects it, and decides what to do next. Children
do not talk sideways. They do not write to shared hidden state. They do
not need to know who their siblings are.

```text
caller
  -> parent Commission
       -> child A -> CommissionResult
       -> child B -> CommissionResult
       -> child C -> CommissionResult
     parent combines those results
  <- one parent CommissionResult
```

This model is deliberately restrictive. The restriction is what makes
larger systems easier to debug: the data path is visible, failures
arrive as values, and cost/provenance roll upward through the tree.

When the parent's interior is the LLM loop, each child in its toolbox
is presented to the model as an ordinary typed tool: name, description,
and input schema go in, and only the returned envelope enters the
parent's context. The child's interior transcript, however many steps
it took, never travels upward. A coordinator's context grows with the
children it consults, not with the work performed beneath them.

Concretely, this is everything a coordinating model sees of a
research-brief child in its toolbox (schema abbreviated):

```json
{
  "type": "function",
  "function": {
    "name": "research_brief",
    "description": "Create a grounded research brief from supplied source notes.",
    "parameters": {
      "type": "object",
      "properties": {
        "question": {"type": "string"},
        "source_notes": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["question", "source_notes"]
    }
  }
}
```

That descriptor is the child's whole footprint in the parent's context
until its envelope returns. Behind it might sit a single prompt or a
subtree of a hundred steps; the parent pays the same either way, this
block plus the receipt.

## Commissions, Tools, and Application Code

Vibrantine recognizes three categories:

| Category | Role |
| --- | --- |
| **Commission** | Typed input/output plus LLM judgment somewhere in its subtree. |
| **Tool** | The same contract, but deterministic throughout: no LLM call anywhere in its subtree. |
| **Application code** | Everything above the library: persistence policy, user surfaces, scheduling, long-term state, notification, and product workflow. |

There is no fourth "workflow" or "traffic controller" type in the
library. Larger behavior is built from Commissions, tools, and ordinary
application code.

The Commission/tool split is a contract fact, not a style note: a tool
promises there is no LLM anywhere in its subtree, so a caller always
knows which parts of a tree can exercise judgment and which cannot.

## The Interior Is Open

A Commission always has the same outside: typed task in, result
envelope out. The inside is deliberately open.

In this Python implementation, most Commissions start from one of two
authoring hooks:

- Override `build_user_message` to use the built-in **LLM loop**, where
  the model chooses steps from a toolbox until it can produce the
  declared output.
- Override `_run` to own the control flow yourself.

Whichever hook a Commission uses, the same boundary machinery runs
outside it: inputs and outputs are validated, exceptions become failure
envelopes, and every run is recordable. This is the machinery behind
the promise this page opened with: the guarantees belong to the
boundary, not to the author's diligence.

Those hooks are not a limit on patterns. A custom interior can be a
pipeline, fan-out/gather, review loop, search process, external service
call, verifier, budget handoff, child-Commission coordinator,
deterministic procedure, or a mix of those. If the subtree includes LLM
judgment, it is a Commission. If the whole subtree is deterministic, it
is a tool.

For successful completion, an LLM-loop Commission must produce the
declared output type. It cannot simply say "done" in prose.

The Commission model itself is not Python-specific. The current package
is a Python library, but the underlying contract is language-neutral:
typed task, bounded work, structured result envelope, parent-mediated
composition, cost, and provenance. A TypeScript implementation could
uphold the same contract with different host-language ergonomics.

## The Same Boundary, Written by Hand

The factory covers the basic path. The day a Commission needs a custom
interior (its own tools, a prompt file, steering fields, hand-shaped
messages), the exit ramp is subclassing `Commission`. The boundary the
caller sees does not change:

```python
from typing import ClassVar

from vibrantine import CallContext, Commission


class ResearchBriefCommission(Commission[ResearchBriefInput, ResearchBriefOutput]):
    name: ClassVar[str] = "research_brief"
    description: ClassVar[str] = (
        "Create a grounded research brief from supplied source notes."
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
        return f"Question: {input.question}\n\n{notes}"
```

The calling code does not change: same entry points, same envelope
handling. The implementation inside the Commission can evolve, and the
caller still depends on the same input and output boundary. The full
custom-interior path, from steering fields to child coordination, is
[docs/authoring.md](authoring.md).

## Where to Go Next

- Build one: [docs/authoring.md](authoring.md).
- Run a tree with budgets, fuses, and full visibility:
  [running.md](running.md).
- Prove one works: [docs/commission-testing.md](commission-testing.md).
- Interrogate the design: [docs/design.md](design.md), and the settled
  rulings behind it: [docs/design-decisions.md](design-decisions.md).
