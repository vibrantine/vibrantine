# Authoring Commissions

A Commission is one typed function with an LLM inside. You hand it a typed
input, and you always get a typed result jacket back: `success`, `partial`, or
`failure`, never a raised exception, always with the cost attached. Everything
in this document exists to make that promise trustworthy: the types make the
work order precise, the tests make the boundary provable, and the evals make
the quality claim falsifiable.

That is also the whole contract, stated as a rule:

> **A Commission is one typed input in, one `CommissionResult` out. What
> happens inside is entirely yours.**

This is the one document about building Commissions, in three parts:

- **Part I: Tutorial.** Build one small, real Commission from scratch in your
  own project, step by step. Every code block has been executed end to end,
  including the live-model steps. Start here.
- **Part II: Beyond one Commission.** The second authoring path (your code
  owns the control flow), composition, and where state lives.
- **Part III: Reference.** The public surface, the contract tables, the
  closed vocabularies, and the authoring checklist. Look things up here.

**Stability promise.** Names exported from `vibrantine` itself (the import
block in Part III) are the frozen, SemVer-protected surface. Everything else,
including `vibrantine.tools` and `vibrantine.commissions`, is importable but
provisional: use it, but expect movement. The runnable claims in this
document are machine-checked by `tests/test_external_authoring.py`, so they
fail loudly rather than rot silently.

---

# Part I: Tutorial

**What you'll build:** `DocTagCommission`. Give it the path to a document; it
reads the document and returns a one-sentence summary plus a handful of topic
tags. Small enough to finish in a sitting, real enough to need every part of
the pattern.

Each step ends with a pointer to the same step in `DeepResearchCommission`,
the worked example that ships with the library, so you can compare your small
version against a finished specimen.

**What you'll need:** Python 3.12+, [uv](https://docs.astral.sh/uv/), and an
[OpenRouter](https://openrouter.ai/) API key for the steps that run a live
model (steps 0 and 8 only; everything else works offline).

## Step 0: Proof of Life

Before writing anything, prove the install works and see the result jacket
with your own eyes.

Create a project and add Vibrantine as a git dependency:

```bash
uv init --package doctag
cd doctag
uv add "vibrantine @ git+https://github.com/vibrantine/vibrantine.git"
uv add --dev pytest
```

Now run a deterministic tool through the public entry point. No API key
needed; `ReadTool` is a Commission that happens to contain no LLM:

```python
# poke.py
from vibrantine import invoke_sync
from vibrantine.tools import ReadTool
from vibrantine.tools.read import ReadInput
from pathlib import Path

result = invoke_sync(ReadTool(), ReadInput(path=Path("pyproject.toml").resolve()))
print(result.status)          # success
print(result.output.content[:60])
print(result.cost)            # estimated_usd=0.0, deterministic work is free
print(result.provenance)      # where the data came from
```

```bash
uv run python poke.py
```

That object is the jacket every Commission returns, LLM or not. Status, typed
output, cost, provenance: the same envelope you are about to build your own
Commission around.

For the LLM-backed steps later, put your key in the environment. A clean way
is a git-ignored `.env` file:

```text
OPENROUTER_API_KEY=sk-or-...
```

and run those steps with `uv run --env-file .env ...`.

> One rule to carry through everything: invoke Commissions through `run_one`
> / `invoke_sync` (or `dispatch` from inside another Commission), never by
> calling `.invoke()` directly. The entry points are where the framework
> stamps run ids and enforces output policy uniformly.

## Step 1: The Promise

A Commission starts with its contract, not its prompt. Write the input and
output types first, because everything else in this tutorial is interior and
replaceable; these two models are the part your callers will depend on.

Lay out the package (this is the standard folder shape; you'll fill the rest
in as you go):

```text
src/doctag/
  __init__.py
  types.py
  commission.py
  prompts/
    system.md
  tests/
    __init__.py
    test_commission.py
    test_eval.py
  BRIEF.md
```

```python
# src/doctag/types.py
"""DocTag's boundary types stay beside the commission that owns them."""

from pathlib import Path

from pydantic import BaseModel, Field


class DocTagInput(BaseModel):
    """The work order: one document to read and tag."""

    file_path: Path = Field(description="Absolute path of the document to read.")


class DocTagOutput(BaseModel):
    """The deliverable: what the document is about."""

    summary: str = Field(description="One sentence stating what the document is about.")
    tags: list[str] = Field(
        description="3 to 8 lowercase topic tags for the document's own subject matter.",
    )
```

Two things to notice:

- Every field carries a `Field(description=...)`. Those descriptions are not
  decoration; the LLM loop shows them to the model when it fills in your
  output, so they are part of the prompt. Write them as instructions.
- The output is deliberately small. A Commission promises a deliverable, not
  a transcript of its work.

**Specimen:** `src/vibrantine/commissions/deep_research/types.py` does exactly
this and nothing more.

## Step 2: The Identity

Next, who this Commission is: its `name`, its `description`, and its system
prompt. The `description` is LLM-facing. When your Commission later sits in
some parent's toolbox, a model reads this text to decide whether to call it,
so write it like tool documentation, not marketing.

The system prompt lives in its own file, because prompts are the part you
will edit most:

```markdown
<!-- src/doctag/prompts/system.md -->
You are a document tagging specialist.

Read the document at the path given in the task using the `read` tool, then
conclude with a one-sentence summary and 3 to 8 lowercase topic tags.

Rules:
- Tag the document's own subject matter. Do not tag topics the document
  merely quotes, cites, or rejects.
- If the read result says `truncated: true`, keep reading with a higher
  `offset` until you have seen the whole document.
- Never invent content you did not read.
```

Now the class skeleton that carries the identity:

```python
# src/doctag/commission.py
"""DocTag commission: read one document, return a summary and topic tags."""

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from vibrantine import CallContext, Commission, Model
from vibrantine.tools import ReadTool

from doctag.types import DocTagInput, DocTagOutput

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8")


class DocTagCommission(Commission[DocTagInput, DocTagOutput]):
    """Read one document and return a one-sentence summary plus topic tags."""

    name: ClassVar[str] = "doc_tag"
    description: ClassVar[str] = (
        "Reads one document from disk and returns a one-sentence summary "
        "plus 3 to 8 lowercase topic tags.\n"
        "\n"
        "Usage:\n"
        "- `file_path` must be an absolute path to a readable text file.\n"
        "- Tags describe the document's own subject matter, not material "
        "it merely quotes or rejects."
    )
    input_type: ClassVar[type] = DocTagInput
    output_type: ClassVar[type] = DocTagOutput
    system_prompt: ClassVar[str | None] = _PROMPT
```

The four identity attributes (`name`, `description`, `input_type`,
`output_type`) are enforced at class-definition time: leave one out and the
class fails to even define, with a message saying what's missing. Malformed
Commissions fail at authoring time, not at first run.

**Specimen:** `src/vibrantine/commissions/deep_research/commission.py` (the
ClassVar block) and its `prompts/system.md`.

## Step 3: The Interior

Here is the one real design decision in every Commission: **who decides the
control flow?**

- **The model decides.** You provide a menu of tools (the toolbox), the
  framework runs the default LLM loop, and the model chooses what to call and
  when to conclude. You write no control flow at all. This is a *basic*
  Commission: you override `build_user_message` to turn your typed input into
  the loop's opening message, and that's it.
- **Your code decides.** You override `invoke` and write the control flow
  yourself: fan out over a list, call children in a fixed order, whatever the
  job needs. The model is something you call, not something that drives.
  This is a *custom* Commission; Part II is about building these.

DocTag is a natural fit for the first kind: the job is "read, maybe page
through a long file, then conclude", and the model can drive that itself.
Add the hook:

```python
    def build_user_message(self, input: DocTagInput, ctx: CallContext) -> str:
        return f"Document to tag: {input.file_path}"
```

That's the whole interior. The framework's loop feeds this message and your
system prompt to the model, offers it the toolbox plus a `conclude` tool
shaped like your output type, and keeps going until the model concludes or a
guard rail stops it.

Whichever way you choose, it is invisible from outside: same input type, same
result jacket. The choice is never part of your contract, which means you can
change your mind later without breaking a single caller.

**Specimen:** DeepResearch is also a basic Commission; its entire "recursion"
is toolbox contents plus this same one-line hook. For a your-code-decides
specimen, see `MorningBriefingCommission`, or Part II below.

## Step 4: The Toolbox

The model can only call what you put on the menu. DocTag needs exactly one
capability: reading files. Wire it in through the constructor:

```python
    def __init__(
        self,
        *,
        read: ReadTool | None = None,
        model: str | Model | None = None,
        client: "AsyncOpenAI | None" = None,
    ) -> None:
        super().__init__(toolbox=(read or ReadTool(),), model=model, client=client)
```

This small constructor is a load-bearing convention:

- **Dependencies are injected with working defaults.** A caller who wants the
  normal thing writes `DocTagCommission()`. A test injects a fake. Nothing
  reaches around the constructor to get its dependencies.
- **`toolbox` is the single source of truth** for what the model may call.
  There is no other channel; if it's not in the tuple, the model cannot
  touch it.
- **`model=None` means "the system default".** Don't hardcode a model name in
  the class; let callers (and the one loaded default) decide, and accept a
  `model=` override for when they do.

Now run it for real (this one needs the key):

```python
# tag_one.py
from pathlib import Path

from vibrantine import invoke_sync

from doctag.commission import DocTagCommission
from doctag.types import DocTagInput

result = invoke_sync(
    DocTagCommission(),
    DocTagInput(file_path=Path("README.md").resolve()),
    budget_usd=0.10,
)
print(result.status)
print(result.output)
print(f"cost: ${result.cost.estimated_usd:.4f}")
```

```bash
uv run --env-file .env python tag_one.py
```

**Specimen:** the DeepResearch constructor builds its own child researcher
and fetch tool the same way, including the `model=`/`client=` pass-through.

## Step 5: The Guard Rails

A Commission is safe to delegate to because the caller can bound it. The
bounds are already on your Commission; this step is about knowing them.

- **Budget.** The `budget_usd=0.10` you passed above is a hard ceiling. If
  the loop's spending reaches it, you get `status="failure"` with
  `error.kind == "budget_exceeded"` and the true cost of what was spent,
  not an exception and not a surprise bill.
- **Iterations.** The loop gives up (as a failure, with cost) rather than
  spin forever; `max_iterations` is a constructor kwarg if the default is
  wrong for your job.
- **Output size.** `max_output_tokens` plus an `overflow_policy` say what
  happens when the deliverable is oversized. DocTag's output is tiny, so the
  defaults are fine; when you do set a policy, know that `"partial"` flags
  the oversize through the jacket but does not trim it.
- **Cancellation.** The `CallContext` carries a cancel token that
  well-behaved Commissions check before expensive work.

The other half of trust is on the consuming side: handle the whole jacket,
not just the happy path.

```python
if result.status == "success":
    use(result.output)
elif result.status == "partial":
    # usable output plus an error explaining what's incomplete about it
    review(result.output, result.error)
else:
    handle(result.error)  # kind, detail, retryable
```

`result.error.retryable` tells you whether trying again could help (a
timeout) or cannot (a validation failure). Nothing in this block can raise;
that is the contract.

**Specimen:** `DeepResearchCommission` sets `max_output_tokens` and
`overflow_policy` explicitly, with a comment stating exactly what the policy
does and does not protect.

## Step 6: Contract Tests

Prove the boundary without spending a cent. The trick: inject a fake client
whose "model" is a script you wrote. The model's intelligence is not under
test; your Commission's behavior around the responses is.

```python
# src/doctag/tests/test_commission.py
"""Contract tests: fake LLM client, no API key, no network."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from openai import AsyncOpenAI

from vibrantine import invoke_sync

from doctag.commission import DocTagCommission
from doctag.types import DocTagInput


def llm_response(tool_calls: list[tuple[str, str, dict[str, Any]]]) -> SimpleNamespace:
    """One scripted chat-completions response."""
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=tc_id,
                            type="function",
                            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                        )
                        for tc_id, name, args in tool_calls
                    ],
                )
            )
        ],
    )


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.completions = SimpleNamespace(
            _responses=list(responses),
            calls=[],
        )
        self.completions.create = self._create
        self.chat = SimpleNamespace(completions=self.completions)

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.completions.calls.append(kwargs)
        return self.completions._responses.pop(0)


def test_concludes_with_typed_output(tmp_path: Path) -> None:
    # Script: the "model" reads the file, then concludes with a valid output.
    doc = tmp_path / "note.txt"
    doc.write_text("Meeting notes about the quarterly budget.", encoding="utf-8")
    fake = FakeClient(
        [
            llm_response([("t1", "read", {"path": str(doc)})]),
            llm_response(
                [("t2", "conclude", {"summary": "Budget meeting notes.", "tags": ["budget"]})]
            ),
        ]
    )

    result = invoke_sync(
        DocTagCommission(client=cast(AsyncOpenAI, fake)),
        DocTagInput(file_path=doc),
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.tags == ["budget"]
    assert result.cost.estimated_usd >= 0.0
```

```bash
uv run pytest
```

Notice what happened in that script: the fake replaced only the *LLM*. The
`read` tool call went through the real `ReadTool` against a real temp file.
You scripted the model's decisions and everything else was live machinery.

This one test proves import, construction, injection, dispatch, tool
execution, conclusion, and the jacket. The full coverage bar for a shipped
Commission (validation failures, cancellation, malformed model responses,
budget behavior, tool menu shape) is listed in
[`commission-testing.md`](commission-testing.md); work through it as your
Commission grows up.

**Specimen:** `src/vibrantine/commissions/deep_research/tests/test_commission.py`
runs this exact pattern across a recursive tree, including budget and
cost-rollup coverage.

## Step 7: The BRIEF

Before measuring quality, write down what quality *means* for this
Commission. That lives in `BRIEF.md`, next to the code, and it is short:

```markdown
<!-- src/doctag/BRIEF.md -->
# DocTag

Reads one document and returns a one-sentence summary plus 3 to 8 lowercase
topic tags. Basic Commission: default LLM loop over a toolbox of `read`.

## Efficacy Bar

Success criteria:

- The summary states what the document is about in one sentence.
- Tags reflect the document's own subject matter.
- Long documents are paged through before concluding.

Failure criteria:

- Tags reflect material the document merely quotes, cites, or rejects.
- The summary asserts content that is not in the document.

Eval cases:

- `subject_not_quoted_material`: memo with a planted trap topic. See
  `tests/test_eval.py`.
```

The BRIEF is the quality contract in plain language. The eval cases in the
next step exist to turn its sentences into pass/fail.

**Specimen:** `src/vibrantine/commissions/deep_research/BRIEF.md`.

## Step 8: The Evals

Contract tests scripted the model, so they can never tell you whether DocTag
is *good at tagging*. An eval case runs a real model and grades the output
against criteria you wrote in advance.

Three habits make evals trustworthy (the full reasoning is in
[`commission-testing.md`](commission-testing.md)):

- **Pin everything except the thing under test.** A named model, a fixture
  document you control. Then a failing eval means your Commission changed.
- **Plant targets and traps.** A target is a fact the output must carry. A
  trap is a nearby wrong answer a sloppy read would pick up. A case with no
  way to fail teaches nothing.
- **Write the criteria before the first run.** Criteria written after seeing
  output always pass.

Register a marker so evals stay out of your default test run:

```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "eval: graded live-model efficacy runs; skip without credentials.",
]
```

Then the case. The fixture memo is about rainwater harvesting; the trap is a
rejected solar proposal it quotes. Per the BRIEF, "solar" must not surface
as a tag:

```python
# src/doctag/tests/test_eval.py
"""Eval cases: a live model, pinned fixtures, criteria written in advance."""

import os
from pathlib import Path

import pytest

from vibrantine import invoke_sync

from doctag.commission import DocTagCommission
from doctag.types import DocTagInput

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set; eval cases skipped.",
    ),
]

# Pinned: a failing case means the commission changed, not the default model.
EVAL_MODEL = "google/gemini-3-flash-preview"

FIXTURE_MEMO = """\
Project Bluegum: Rainwater Harvesting Pilot: Status Memo

The Bluegum pilot installed rooftop rainwater collection across 40 council
buildings this quarter. Storage capacity reached 1.2 megalitres and the
treated water now supplies irrigation for three public parks.

For context, the council previously considered and rejected a rooftop solar
farm proposal for the same buildings, citing grid-connection costs. That
proposal is closed and is not part of Project Bluegum.

Next quarter the pilot expands to 25 additional sites.
"""


def test_tags_reflect_subject_not_quoted_material(tmp_path: Path) -> None:
    doc = tmp_path / "memo.txt"
    doc.write_text(FIXTURE_MEMO, encoding="utf-8")

    result = invoke_sync(
        DocTagCommission(model=EVAL_MODEL),
        DocTagInput(file_path=doc),
        budget_usd=0.10,
    )

    # Transcript for human review; run with -s to see it.
    print(f"\nsummary: {result.output.summary if result.output else result.error}")
    print(f"tags:    {result.output.tags if result.output else '-'}")

    assert result.status == "success", result.error
    assert result.output is not None
    tags = [t.lower() for t in result.output.tags]
    # Target: the document's actual subject.
    assert any("rainwater" in t or "harvest" in t or "water" in t for t in tags), tags
    # Trap: the rejected proposal the memo merely mentions.
    assert not any("solar" in t for t in tags), tags
    assert "bluegum" in result.output.summary.lower()
```

```bash
uv run --env-file .env pytest -m eval -s
```

The `print` lines matter more than they look: the assertions catch what you
predicted, and skimming the transcript occasionally catches what you didn't.
When a criterion turns out to be wrong (a good answer fails it), fix the
criterion and record why; that history is how prompt changes stop being
vibes-only.

**Specimen:** `src/vibrantine/commissions/deep_research/tests/test_eval.py`,
three cases including a source-conflict case graded by crude heuristic plus
human transcript review.

You now have the complete pattern: a typed promise, an identity, an interior
someone chose on purpose, an injected toolbox, guard rails, a provable
boundary, a written quality bar, and a falsifiable quality check. Every
Commission, however large, is this same shape.

---

# Part II: Beyond One Commission

## The two authoring paths, side by side

Every Commission subclasses `Commission[InputT, OutputT]` and picks exactly
one path:

| | **Basic Commission** | **Custom Commission** |
|---|---|---|
| You write | a `build_user_message` method | an `async def invoke` method |
| Control flow | the framework's LLM loop: it calls the model, lets it use your toolbox, ends when the model concludes | yours: plain Python, your own sequencing, fan-out, rounds |
| Use when | "send the model a prompt plus tools, get a typed answer" | the logic is a pipeline or coordinator the model shouldn't drive |
| You must uphold | nothing extra; the framework does it | errors-as-values, cancellation checks, cost reporting (all shown below) |

Overriding neither fails at class-definition time. Overriding both is
allowed but pointless (your `invoke` wins and `build_user_message` goes
unused), so treat "pick one" as discipline.

On the basic path the framework builds the `CommissionResult` for you. On
the custom path **you** build it, every return, success included: a
`Provenance`, a `CostMetrics`, and for failures an `ErrorState`. That is the
price of owning the control flow.

## Composition: calling children

A parent owns its children and depends on them only through the contract:

- **Inject children at construction** with working defaults, exactly like
  the tutorial's `ReadTool`.
- **Call children through `dispatch(child, child_input, ctx)`**, passing
  along the `ctx` you received. Never call a child's `.invoke` directly.
- **Fan out with `asyncio.gather`** over dispatches when children are
  independent.
- **Sum children's costs** into the `CostMetrics` you return. A child's cost
  already includes its own subtree, so rollup is plain addition. (Basic
  Commissions get this automatically.)
- **Narrow a child's context** by copying it: `CallContext` is immutable, so
  `replace(ctx, capabilities=CapabilitySet(tools=frozenset({"read"})))` hands
  a child a read-only tool menu without affecting anyone else.
- **No sibling channels.** Children never talk to each other; everything a
  child needs arrives in its typed input from the parent.

## Where the accumulating state goes

There is no framework memory or artifact slot to write into, deliberately:
a Commission stays evaluable as a function of its inputs. Accumulation
across steps lives in the coordinator's own local variables while it runs.
If a run must survive a process restart, the *caller* holds the state and
threads it back in through a typed input field (for example
`prior_claims: list[Claim[str]]`). The framework's persistence layer stores
run *records* for observability; assembling records into resumable state is
the caller's job, not the Commission's.

## Worked build: a corpus-research coordinator

The system: a custom **coordinator** that, for each round, dispatches a
**plan** Commission, **fans out** read-workers in parallel, **reviews** their
claims, then **consolidates** into a follow-up question; after the rounds, an
**assemble** Commission writes the report. Five basic Commissions plus one
custom coordinator.

(Contrast with the shipped `DeepResearchCommission`, which does research
with the *opposite* interior: an LLM loop deciding dispatch. Same job family,
different owner of control flow, identical boundary. That is the point.)

### The shared types

```python
from datetime import UTC, datetime
from typing import ClassVar
from pydantic import BaseModel, Field

from vibrantine import (
    Commission, CommissionResult, CallContext, CapabilitySet,
    Provenance, Claim, CostMetrics, ErrorState, dispatch,
)
from vibrantine.tools import ReadTool, SampleTool, GrepTool
import asyncio
from dataclasses import replace


class PlanInput(BaseModel):
    question: str = Field(description="The current research question to break down.")
    known_so_far: list[str] = Field(description="One-line summaries of what is already established.")

class PlanOutput(BaseModel):
    subquestions: list[str] = Field(description="Focused sub-questions to investigate in parallel this round.")

class WorkerInput(BaseModel):
    subquestion: str = Field(description="The single focused question this worker must answer.")
    sources: list[str] = Field(description="Paths or URLs the worker may read.")

class WorkerOutput(BaseModel):
    claims: list[Claim[str]] = Field(description="Grounded findings, each citing the source it came from.")

class ReviewInput(BaseModel):
    question: str = Field(description="The round's research question, for judging relevance.")
    claims: list[Claim[str]] = Field(description="All claims gathered this round, to be filtered.")

class ReviewOutput(BaseModel):
    kept: list[Claim[str]] = Field(description="Claims that are relevant and adequately grounded.")

class ConsolidateInput(BaseModel):
    question: str = Field(description="The original research question.")
    claims: list[Claim[str]] = Field(description="All claims kept so far across rounds.")

class ConsolidateOutput(BaseModel):
    summary: str = Field(description="Interim synthesis of what is now known.")
    followup_question: str | None = Field(description="The next question to pursue, or null if the research is complete.")

class AssembleInput(BaseModel):
    question: str = Field(description="The original research question.")
    claims: list[Claim[str]] = Field(description="Every kept claim across all rounds.")

class ResearchInput(BaseModel):
    question: str = Field(description="The research question to answer.")
    sources: list[str] = Field(description="Paths or URLs forming the corpus to read.")

class ResearchReport(BaseModel):
    summary: str = Field(description="The final synthesized answer.")
    claims: list[Claim[str]] = Field(description="Every grounded claim the report rests on.")
    rounds: int = Field(description="How many plan-fan-review rounds were run.")
```

### The basic leaves

Only the read-worker is shown in full; `PlanCommission`, `ReviewCommission`,
`ConsolidateCommission`, and `AssembleCommission` follow the identical shape
from Part I. They need no `toolbox` because they only reason over the text
they're given.

```python
class ReadWorkerCommission(Commission[WorkerInput, WorkerOutput]):
    name: ClassVar[str] = "deep_read_worker"
    description: ClassVar[str] = (
        "Deep-read the given sources to answer ONE focused sub-question. "
        "Returns grounded claims, each citing the source it came from."
    )
    input_type: ClassVar[type] = WorkerInput
    output_type: ClassVar[type] = WorkerOutput
    system_prompt: ClassVar[str | None] = (
        "You are a careful reader. Use the read/sample/grep tools to gather evidence "
        "from the sources, then conclude with a list of claims. Every claim must cite "
        "the source span it came from. Do not assert beyond the evidence."
    )
    toolbox = (ReadTool(), SampleTool(), GrepTool())

    def build_user_message(self, input: WorkerInput, ctx: CallContext) -> str:
        listed = "\n".join(f"- {s}" for s in input.sources)
        return f"Sub-question:\n{input.subquestion}\n\nSources you may read:\n{listed}"
```

### The custom coordinator

The children are injected at construction; the accumulating `all_claims` is
a plain local list, per the state rule above.

```python
class CorpusResearchCommission(Commission[ResearchInput, ResearchReport]):
    name: ClassVar[str] = "corpus_research"
    description: ClassVar[str] = (
        "Answer a research question by iteratively planning, reading sources in parallel, "
        "reviewing findings, and forming follow-up questions, then assembling a cited report."
    )
    input_type: ClassVar[type] = ResearchInput
    output_type: ClassVar[type] = ResearchReport

    def __init__(self, *, plan, worker, review, consolidate, assemble, max_rounds: int = 2, **kw):
        super().__init__(**kw)              # forward model=, budget knobs, etc. to the base
        self._plan = plan
        self._worker = worker
        self._review = review
        self._consolidate = consolidate
        self._assemble = assemble
        self._max_rounds = max_rounds

    def _provenance(self) -> Provenance:
        return Provenance(source=self.name, fetched_at=datetime.now(UTC), confidence="grounded")

    async def invoke(self, input: ResearchInput, ctx: CallContext) -> CommissionResult[ResearchReport]:
        all_claims: list[Claim[str]] = []
        total_cost = 0.0
        question = input.question
        rounds = 0

        # workers read; narrow their tool access to read-only
        worker_ctx = replace(ctx, capabilities=CapabilitySet(tools=frozenset({"read", "sample", "grep"})))

        for _ in range(self._max_rounds):
            if ctx.cancel.is_cancelled:
                return CommissionResult(
                    status="failure",
                    error=ErrorState(kind="cancelled", detail="Cancelled mid-research.", retryable=False),
                    provenance=self._provenance(),
                    cost=CostMetrics(estimated_usd=total_cost),
                )

            # PLAN
            plan_res = await dispatch(
                self._plan,
                PlanInput(question=question, known_so_far=[c.value for c in all_claims]),
                ctx,
            )
            total_cost += plan_res.cost.estimated_usd
            if plan_res.status != "success" or not plan_res.output.subquestions:
                break

            # FAN-OUT: workers in parallel
            worker_results = await asyncio.gather(*[
                dispatch(self._worker, WorkerInput(subquestion=sq, sources=input.sources), worker_ctx)
                for sq in plan_res.output.subquestions
            ])
            total_cost += sum(r.cost.estimated_usd for r in worker_results)
            # a failed worker drops out; the round proceeds with the rest (errors-as-values)
            round_claims = [c for r in worker_results if r.status == "success" for c in r.output.claims]

            # REVIEW
            review_res = await dispatch(self._review, ReviewInput(question=question, claims=round_claims), ctx)
            total_cost += review_res.cost.estimated_usd
            all_claims.extend(review_res.output.kept if review_res.status == "success" else round_claims)
            rounds += 1

            # CONSOLIDATE into a follow-up question (or stop)
            cons_res = await dispatch(self._consolidate, ConsolidateInput(question=input.question, claims=all_claims), ctx)
            total_cost += cons_res.cost.estimated_usd
            if cons_res.status != "success" or not cons_res.output.followup_question:
                break
            question = cons_res.output.followup_question

        # ASSEMBLE the report
        asm_res = await dispatch(self._assemble, AssembleInput(question=input.question, claims=all_claims), ctx)
        total_cost += asm_res.cost.estimated_usd
        if asm_res.status != "success":
            return CommissionResult(
                status="failure",
                error=asm_res.error,
                provenance=self._provenance(),
                cost=CostMetrics(estimated_usd=total_cost),
            )

        return CommissionResult(
            status="success",
            output=ResearchReport(summary=asm_res.output.summary, claims=all_claims, rounds=rounds),
            provenance=self._provenance(),
            cost=CostMetrics(estimated_usd=total_cost),
        )
```

### Wiring and running it

Construction is where you compose the pieces and choose the model; the
coordinator never hardcodes either:

```python
research = CorpusResearchCommission(
    plan=PlanCommission(),
    worker=ReadWorkerCommission(),
    review=ReviewCommission(),
    consolidate=ConsolidateCommission(),
    assemble=AssembleCommission(),
    max_rounds=2,
)

result = invoke_sync(
    research,
    ResearchInput(question="How did the project's persistence design evolve?",
                  sources=["/abs/docs", "/abs/src"]),
    budget_usd=2.00,
)

if result.status == "success":
    print(result.output.summary)
    print(f"{len(result.output.claims)} claims over {result.output.rounds} rounds; "
          f"cost ~ ${result.cost.estimated_usd:.2f}")
else:
    print(f"{result.error.kind}: {result.error.detail}")
```

That's the whole system. Note what you never did: you never touched the
framework's internals, never parsed model output by hand, never raised an
exception across a boundary, and never stored state inside a Commission.

For the design rationale behind all of this, read
[`design.md`](design.md); for the shipped LLM-decides
counterpart, read `src/vibrantine/commissions/deep_research/` end to end
(it is one page of code).

---

# Part III: Reference

## The public surface

Everything you may depend on, in one import line (in `vibrantine.__all__`,
SemVer-protected):

```python
from vibrantine import (
    Commission, CommissionResult, CommissionStatus,        # contract + envelope
    CallContext, CapabilitySet, CancelToken, NEVER_CANCELLED, ProgressEvent,  # runtime
    Provenance, ConfidenceLevel, Claim, CostMetrics,       # provenance / claims / cost
    ErrorState, ErrorKind,                                 # failure model
    OverflowPolicy, PersistenceMode,                       # policy vocabularies
    PersistedRecord, PersistenceBackend, FilesystemBackend,# persistence
    Model, KNOWN_MODELS, DEFAULT_MODEL, openai_compatible, ollama,  # models
    run_one, invoke_sync, dispatch,                        # entry points
)
```

The std-lib **tools** are importable from `vibrantine.tools` (provisional,
but ready to drop into a toolbox):

```python
from vibrantine.tools import (
    ReadTool, WriteTool, EditTool, DeleteTool, MoveTool,
    GlobTool, GrepTool, ListDirTool, SampleTool, ShellTool, FetchTool,
    # each ships its own *Input / *Output models too
)
```

Their input fields, for when you call a tool directly (inside an LLM loop
the model fills them from the generated schema):

| Tool | Input model | Required fields | Optional fields |
|---|---|---|---|
| `ReadTool` | `ReadInput` | `path` | `offset`, `limit` |
| `WriteTool` | `WriteInput` | `path`, `content` | `create_only` |
| `EditTool` | `EditInput` | `path`, `old_string`, `new_string` | `replace_all` |
| `DeleteTool` | `DeleteInput` | `path` | (none) |
| `MoveTool` | `MoveInput` | `source`, `target` | `overwrite` |
| `GlobTool` | `GlobInput` | `pattern` | `base` |
| `GrepTool` | `GrepInput` | `pattern`, `path` | `max_matches`, `ignore_case` |
| `ListDirTool` | `ListDirInput` | `path` | (none) |
| `SampleTool` | `SampleInput` | `path` | `head_lines`, `tail_lines` |
| `ShellTool` | `ShellInput` | `command` | `cwd`, `timeout_seconds`, `max_output_chars` |
| `FetchTool` | `FetchInput` | `url` | `headers`, `timeout_seconds`, `offset`, `max_chars` |

## The Commission contract

`Commission[InputT, OutputT]` is an ABC in `vibrantine.contract`. Required
identity ClassVars (all four, or class definition fails):

| Attribute | Type | Meaning |
|---|---|---|
| `name` | `ClassVar[str]` | Stable identifier; also the tool name when placed in a toolbox |
| `description` | `ClassVar[str]` | LLM-facing selection prose |
| `input_type` | `ClassVar[type]` | Your `InputT` Pydantic model |
| `output_type` | `ClassVar[type]` | Your `OutputT` Pydantic model |

Behavior slots (class attributes, instance-overridable via constructor):

| Attribute | Default | Notes |
|---|---|---|
| `system_prompt` | `None` | The Commission's own prompt; `None` is fine for tools and coordinators |
| `toolbox` | `()` | What the LLM loop may dispatch; instance override via `toolbox=` kwarg |
| `persistence_mode` | `"off"` | `PersistenceMode` |
| `max_output_tokens` | `None` | Output cap; `None` = no enforcement |
| `overflow_policy` | `"partial"` | `OverflowPolicy`; enforced by `dispatch` |

Constructor kwargs (all keyword-only):

| kwarg | Default | Purpose |
|---|---|---|
| `model` | `None`, resolving to `DEFAULT_MODEL` | Which LLM the default loop uses |
| `client` | `None`, lazy OpenRouter client | Inject a test or alternative `AsyncOpenAI` |
| `max_iterations` | `10` | LLM-loop cap |
| `toolbox` | class default | Dependency-injection override |
| `max_input_tokens` | model context window, else `None` | Input size gate |
| `target_input_fraction` | `0.75` | Fraction of the window the gate allows |
| `persistence_mode` / `max_output_tokens` / `overflow_policy` | class default | Per-instance policy override (sentinel-based, so omission is not `None`) |

Protected helpers available to a custom `invoke`. The underscore warns
*callers* off; for authors these are the supported interior surface,
provisional until the authoring-surface freeze (see
`design.md § Not built yet`):

| Helper | Use |
|---|---|
| `self._fail(kind, detail, *, retryable, provenance, cost)` | Build a structured failure result |
| `self._emit(ctx, phase, detail=None)` | Emit a `ProgressEvent` (no-op without a callback) |
| `self._cost(in_tokens, out_tokens)` | Model-priced `CostMetrics` |
| `self._prices()` | `(in, out)` USD per million tokens for the model |
| `self._resolved_client` | The lazily-built LLM client |
| `self.fits(estimated_tokens)` | Size-gate check |
| `estimate_tokens(text)` | Module-level chars/4 heuristic; import from `vibrantine.contract` |

## The result envelope

`CommissionResult[OutputT]` is the single value every call yields. Errors
are values, never exceptions.

| Field | Type | Notes |
|---|---|---|
| `status` | `CommissionStatus` | `"success"` / `"failure"` / `"partial"` |
| `output` | `OutputT \| None` | Populated on success and partial |
| `error` | `ErrorState \| None` | Populated on failure and partial |
| `provenance` | `Provenance` | Origin and trust of this run; on the custom path, required on every return, success included |
| `cost` | `CostMetrics` | This call's cost; children roll up structurally |
| `run_id` / `parent_run_id` | `str \| None` | Stamped by `dispatch`; leave unset |

Supporting types, constructed directly:

```python
ErrorState(kind="internal", detail="human-readable, actionable", retryable=False)
Provenance(source="my_commission", fetched_at=datetime.now(UTC), confidence="grounded")  # all three required
CostMetrics(estimated_usd=0.0)
Claim(value=..., sources=[Provenance(...)], confidence="grounded")  # an assertion plus its receipts
```

Closed vocabularies (these exact strings; the sets are frozen, and changing
a member is a major version bump):

- `status`: `success`, `partial`, `failure`
- `ErrorState.kind` (`ErrorKind`): `validation`, `internal`, `rate_limit`,
  `timeout`, `budget_exceeded`, `cancelled`, `output_too_large`
- `confidence` (`ConfidenceLevel`): `verified`, `grounded`, `speculative`

## Runtime conditions: CallContext

`CallContext` is a frozen dataclass carried alongside the input; copy it
with `dataclasses.replace` to hand a child a modified one.

| Field | Default | Enforced? |
|---|---|---|
| `budget_usd` | `None` | Yes: the LLM loop halts with `budget_exceeded` after a turn that overruns |
| `capabilities` | `CapabilitySet()` | Yes: the LLM's tool menu is `toolbox` intersected with `capabilities.tools` (`None` = unrestricted) |
| `cancel` | `NEVER_CANCELLED` | Yes: checked at natural breakpoints; returns `cancelled` |
| `on_progress` | `None` | Observability callback (`ProgressEvent`) |
| `concurrency` | `4` | Per-coordinator hint; not tree-wide yet |
| `parent_run_id` | `None` | Threaded by `dispatch`; read-only to bodies |
| `backend` | `None` | `PersistenceBackend` to write through |

## Entry points

Always invoke through an entry point, never `commission.invoke(...)`
directly; the entry points stamp `run_id`, thread `parent_run_id`, enforce
`overflow_policy`, and persist.

| Entry point | Shape | Use |
|---|---|---|
| `run_one` | `async run_one(commission, input, *, budget_usd=None, backend=None)` | The normal async path; builds a default `CallContext` |
| `invoke_sync` | sync wrapper over `run_one` | Scripts, REPL, tests |
| `dispatch` | `async dispatch(commission, input, ctx)` | Inside a custom `invoke`, or when you build the `CallContext` yourself (capabilities, cancellation, progress) |

## The default LLM loop

What a basic Commission rides:

- Composes your system prompt and opening message, calls the model with a
  tool menu built from `toolbox` intersected with `ctx.capabilities`.
- Injects a synthetic `conclude` tool whose schema is your `output_type`.
  Calling `conclude` is the model's only structured exit; you never parse
  free text.
- Dispatches tool calls through `dispatch`, feeds results back, and rolls
  child cost up into your result.
- Stops on: `conclude`, budget exceeded, `max_iterations`, cancellation, or
  the model returning no tool call.

Any Commission placed in another Commission's toolbox is exposed to that
model with your `description` verbatim, which is why the description is
written as a selection prompt.

## Models and cost

- `DEFAULT_MODEL` is the system default seam; every Commission uses it
  unless its caller passes `model=`. Never hardcode a model in a Commission
  body.
- A bare string resolves through `KNOWN_MODELS` to identity, endpoint, and
  pricing. For uncatalogued targets use `openai_compatible(name, address)`
  for any OpenAI-format endpoint, or `ollama(id)` for a local Ollama server.
- Pricing states: *priced*, *free* (a real $0, like local Ollama), or
  *unpriced* (unknown). Setting `budget_usd` on an unpriced model fails
  fast: the framework refuses to run a budget it cannot enforce.
- The default endpoint is OpenRouter, via the `openai` SDK with `base_url`
  swapped, keyed by `OPENROUTER_API_KEY`.

## Persistence

- `PersistenceBackend` is the protocol (`store` / `load` /
  `list_references` / `delete` / `delete_older_than`); supply any
  implementation.
- `FilesystemBackend(root)` is the shipped default: one JSON file per run,
  mode-aware pruning.
- `PersistedRecord` carries input, full result, a ctx snapshot, and an
  optional LLM trace.
- Modes: `off` / `on_failure` / `dev` / `always`. Wire a backend via
  `run_one(..., backend=...)`; children inherit it automatically.

## Authoring discipline

- **Commission vs tool.** A Commission has an LLM call somewhere in its
  subtree; a tool has none. Both wear the same jacket; the distinction is
  discipline, not a separate type. Tools use `max_input_tokens=None` and no
  `model`.
- **Description prose.** Written for the LLM that decides whether to call
  you: what it does, when to call, what it returns.
- **Schema discipline.** Every Pydantic field has a `description=`; nesting
  depth at most 3; at most 20 fields per model. These keep typed outputs
  portable across providers.
- **Tool-result discipline.** Unbounded tool results must be truncatable
  *and* resumable, with the resumption path described in the tool's prose.

## The authoring checklist

Before you ship a Commission, confirm:

- [ ] **Four ClassVars set**: `name`, `description`, `input_type`,
  `output_type`.
- [ ] **Exactly one path**: `build_user_message` (basic) or `invoke`
  (custom).
- [ ] **Typed I/O**: Pydantic models; every field has `description=`; depth
  at most 3, at most 20 fields.
- [ ] **`description` is written for an LLM** deciding whether to call this
  Commission.
- [ ] **Errors are returned, not raised**: `status="failure"` plus an
  `ErrorState` whose `kind` is one of the seven allowed values.
- [ ] **Custom path**: every `CommissionResult` you build carries a
  `Provenance` (success included) and a `CostMetrics`. (Basic path: the
  framework fills these for you.)
- [ ] **Custom `invoke`**: check `ctx.cancel.is_cancelled` at breakpoints;
  call children via `dispatch(child, input, ctx)`, never `.invoke`; sum
  children's `cost.estimated_usd`.
- [ ] **Compose through constructors**: inject sub-commissions and the model
  at construction; never reach into another Commission's internals.
- [ ] **State stays outside**: no memory held inside the Commission between
  calls; accumulation belongs to the caller or the coordinator's local
  scope.
- [ ] **Launch via `run_one` / `invoke_sync`**, never by calling `invoke`
  directly.
- [ ] **Tested and evaluated**: contract tests with a fake client, a BRIEF
  with an efficacy bar, and eval cases per
  [`commission-testing.md`](commission-testing.md).

If all of these hold, the Commission is well-formed and composes with any
other Commission through the same contract, which is the entire point.
