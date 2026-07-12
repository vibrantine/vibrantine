# Authoring Commissions

A Commission is one typed function with an LLM inside. You hand it a typed
input, and you always get a typed result envelope back: `success`, `partial`, or
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
including `vibrantine.tools` and `vibrantine.examples`, is importable but
provisional: use it, but expect movement. The runnable claims in this
document are machine-checked by `tests/test_external_authoring.py`, so they
fail loudly rather than rot silently.

**If you have only the installed package.** The worked examples ship inside
it, so specimen pointers into `src/vibrantine/examples/` resolve in your
environment's installed copy of `vibrantine.examples`. Two things do not
ship: the examples' colocated `tests/` folders, and the other docs this file
links to (`commission-testing.md`, `design.md`). Those live in the repo at
<https://github.com/vibrantine/vibrantine>.

---

# Part I: Tutorial

**What you'll build:** `DocTagCommission`. Give it the path to a document; it
reads the document and returns a one-sentence summary plus a handful of topic
tags. Small enough to finish in a sitting, real enough to need every part of
the pattern.

Each step ends with a pointer to the same step in `RecursiveResearchCommission`,
the worked example that ships with the library, so you can compare your small
version against a finished specimen.

**What you'll need:** Python 3.12+, [uv](https://docs.astral.sh/uv/), and an
[OpenRouter](https://openrouter.ai/) API key for the steps that run a live
model (steps 0 and 8 only; everything else works offline).

## Step 0: Proof of Life

Before writing anything, prove the install works and see the result envelope
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
from vibrantine import run_commission_sync
from vibrantine.tools import ReadTool
from vibrantine.tools.read import ReadInput
from pathlib import Path

result = run_commission_sync(ReadTool(), ReadInput(path=Path("pyproject.toml").resolve()))
print(result.status)          # success
print(result.output.content[:60])
print(result.cost)            # estimated_usd=0.0, deterministic work is free
print(result.provenance)      # where the data came from
```

```bash
uv run python poke.py
```

That object is the envelope every Commission returns, LLM or not. Status, typed
output, cost, provenance: the same envelope you are about to build your own
Commission around.

For the LLM-backed steps later, put your key in the environment. A clean way
is a git-ignored `.env` file:

```text
OPENROUTER_API_KEY=sk-or-...
```

and run those steps with `uv run --env-file .env ...`.

> One rule to carry through everything: invoke Commissions through `run_commission`
> / `run_commission_sync` (or `dispatch` from inside another Commission). The `_run`
> hook is the framework's to call; the entry points are where it stamps run
> ids and enforces output policy uniformly.

## The Shortcut: create_commission

Before the long way, know the short way. `create_commission` builds a
working LLM-loop Commission from the decisions no one can make for you:
what goes in, what comes out, what it is called, and what tools it may
touch. Everything else (catalog wiring, the system prompt,
cost and provenance plumbing) is manufactured for you.

```python
# recipe.py
from pydantic import BaseModel, Field
from vibrantine import create_commission, run_commission_sync

class RecipeInput(BaseModel):
    dish: str = Field(description="The dish to write a recipe for.")

class RecipeOutput(BaseModel):
    recipe: str = Field(description="A complete recipe, ingredients then steps.")

commission = create_commission(
    name="recipe_writer",
    description="Writes a recipe for a named dish.",
    input=RecipeInput,
    output=RecipeOutput,
)

result = run_commission_sync(commission, RecipeInput(dish="shakshuka"))
print(result.output.recipe)
```

Run it with `uv run --env-file .env python recipe.py`. That is a complete,
typed, budgetable, recordable Commission; it nests in another Commission's
toolbox like anything hand-written.

The factory is deterministic: nothing is fetched and nothing is spent at
construction time, so what you get is exactly what you asked for. The rest
of Part I builds a Commission by hand instead, because the subclass is the
exit ramp you take the day you need a custom interior, and knowing what the
factory manufactures is what makes it trustworthy. Nothing the factory
taught you changes when you subclass.

To see this example grow one idea at a time (tools, then a nested child,
then budgets and recorded runs), run the four rungs of
`vibrantine.examples.learning_ladder`:

```bash
uv run --env-file .env python -m vibrantine.examples.learning_ladder.rung_1
```

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
"""DocTag's boundary types stay beside the Commission that owns them."""

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

Designing the two types is its own small craft. For the input:

- **Substance first.** Split the fields into the substance (the thing the
  run exists to process; there is usually exactly one) and the steering
  (the knobs that shape *how*: a target length, an optional focus). Steering
  fields carry defaults.
- **Preconditions live in the type.** `Field(min_length=1)`, numeric bounds,
  and a `Literal` for closed choices make a bad request fail in the caller's
  code, before the work ever starts.
- **Name the work, not the prompt.** An input like `prompt: str` makes every
  caller learn how to ask; fields like `content` and `focus` let the
  Commission own the wording once, in `build_user_message`. Keep reusable
  primitives domain-neutral (`content`, not `email_thread`); a
  domain-specific Commission's boundary should be honest about its domain
  instead.

For the output:

- **The smallest shape the caller consumes.** One field is not under-design;
  it is an honest return type. Add a field only when a caller will read it.
- **If the result asserts facts, return the trail.** Reach for `Claim[T]`
  (an asserted value carried with the `Provenance` records that back it)
  when a caller should be able to audit, cite, or verify individual
  assertions: a synthesis across sources, a summary whose claims must be
  traceable. Source count is not the rule; traceability is.
- **The return shape is fixed per class.** `output_type` is identity, welded
  to the class. A vocabulary that varies per call (say, classification
  labels the caller picks) belongs on the *input*, where per-call values are
  expected.

**Specimen:** `src/vibrantine/examples/recursive_research/types.py` does exactly
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
"""DocTag Commission: read one document, return a summary and topic tags."""

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

**Specimen:** `src/vibrantine/examples/recursive_research/commission.py` (the
ClassVar block) and its `prompts/system.md`.

## Step 3: The Interior

Here is the one real design decision in every Commission: **who decides the
control flow?**

- **The model decides.** You provide a menu of tools (the toolbox), the
  framework runs the default LLM loop, and the model chooses what to call and
  when to conclude. You write no control flow at all. This is a *basic*
  Commission: you override `build_user_message` to turn your typed input into
  the loop's opening message, and that's it.
- **Your code decides.** You override `_run` and write the control flow
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
result envelope. The choice is never part of your contract, which means you can
change your mind later without breaking a single caller.

**Specimen:** RecursiveResearch is also a basic Commission; its entire "recursion"
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
        model: str | None = None,
    ) -> None:
        super().__init__(toolbox=(read or ReadTool(),), model=model)
```

This small constructor is a load-bearing convention:

- **Dependencies are injected with working defaults.** A caller who wants the
  normal thing writes `DocTagCommission()`. A test injects a fake. Nothing
  reaches around the constructor to get its dependencies.
- **`toolbox` is the single source of truth** for what the model may call.
  There is no other channel; if it's not in the tuple, the model cannot
  touch it.
- **A class-level `toolbox = (...)` is shared.** Declaring the tuple on the
  class body (as some worked examples do) means every instance of the
  Commission shares those same tool objects, so anything placed there must
  be stateless. A stateful tool (one holding a connection, a rate limiter,
  a cache) is built per-instance in `__init__` and passed via `toolbox=`,
  exactly as above.
- **`model=None` means "the run's default model".** A model here is a pure
  *name*, looked up in the run's catalog (`run_commission(models=[...])`) when the
  loop runs; the Model objects themselves are defined once, at the front
  door. Don't hardcode a model name in the class; let callers decide, and
  accept a `model=` override for when they do.

Now run it for real (this one needs the key):

```python
# tag_one.py
from pathlib import Path

from vibrantine import run_commission_sync

from doctag.commission import DocTagCommission
from doctag.types import DocTagInput

result = run_commission_sync(
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

**Specimen:** the RecursiveResearch constructor builds its own child researcher
and fetch tool the same way, including the `model=` pass-through.

## Step 5: The Guard Rails

A Commission is safe to delegate to because the caller can bound it. The
bounds are already on your Commission; this step is about knowing them.

- **Budget.** The `budget_usd=0.10` you passed above bounds the run twice
  over: it is the root's allocated grant, debited down the tree, and it
  arms the run's spend fuse, a running observed total at the provider
  door. If real spend passes it, you get `status="failure"` with
  `error.kind == "run_halted"`, a detail naming the fuse and the numbers,
  and the true cost of what was spent, not an exception and not a
  surprise bill. The loop also declines a turn up front when the turn's
  input cost alone would already break the ceiling; a run stopped short
  that way, before spend reaches the grant, reports
  `error.kind == "budget_exceeded"` instead (so does a mid-tree branch
  that merely exhausted its own slice). Be precise about what the bound
  promises: a call's exact cost is unknowable before it runs (the input
  estimate is a deliberate undercount, and output tokens are the model's
  choice), so the true spend can overshoot the grant by calls already in
  flight when the fuse trips. The result always reports the true spend,
  overshoot included. Size the grant to the work: budget behavior only
  degrades gracefully when the grant is several multiples of a single
  turn's cost, and a turn re-reads everything fetched so far, so a
  transcript holding a few 50k-char pages can cost more per turn than a
  tight grant leaves for wrapping up. A paid provider must return token
  usage for this accounting to be possible; if it omits usage during a
  dollar-accounted call (your grant, or the run's spend fuse, even in a
  subtree whose own grant was stripped), the call fails structurally
  instead of being counted as free. Explicitly free models (`0.0` prices)
  need no usage to enforce a dollar budget.
- **Iterations.** The loop gives up (as a failure, with cost) rather than
  spin forever; `max_iterations` is a constructor kwarg if the default is
  wrong for your job.
- **The other run fuses.** `run_commission(max_llm_calls=)` is an always-on
  backstop (default 1,000) that stops a runaway loop even on a free or
  local model the spend fuse cannot see; pass `None` to disable it.
  `run_commission(time_limit_seconds=)` is an opt-in wall-clock bound for
  unattended runs. Both halt the run the same way the spend fuse does:
  new provider calls are refused, in-flight calls finish and count, and
  the root reports `run_halted`.
- **Tool exposure.** `run_commission(tool_ceiling=["fetch", "read"])` caps what
  any model anywhere in the tree may be *offered*: the effective menu is
  always the Commission's toolbox, intersected with the branch's
  capability grant, intersected with this ceiling. Unlike a capability
  grant (which a coordinator may widen for its own children), the ceiling
  is run-wide and immutable, so it holds even through custom interiors.
  It is name-based: it bounds what a tool is called, not what its code
  does.
- **The call log.** Every provider call in the run lands in a log, one
  plain dict per settled or refused call (who called, which model,
  tokens, cost, how it ended). `run_commission(on_llm_call=rows.append)` streams
  the rows to you live; with a `SqliteBackend` on the run they also land
  in a `calls` table beside the run records at run end. This is where a
  halted run's story stays reconstructable.
- **The dispatch register.** Every invocation that crosses the contract
  boundary (tools and Commissions alike) gets a metadata row: run ids,
  Commission name, the node's self-declared `deterministic` flag, timing,
  status. Always on, content-free (the row's run_id joins the run records
  for the verbatim input and output). `run_commission(on_dispatch=rows.append)`
  streams it live; with a `SqliteBackend` it lands in a `dispatches`
  table. A tool author sets `deterministic = True` in the class body to
  mark "no LLM in my interior"; it is log metadata only, and nothing ever
  branches on it.
- **Stop means stop.** After a fuse trips, `dispatch` refuses to start
  new invocations: the refused call comes back as an ordinary failure
  value (your coordinator code keeps running and can still conclude),
  in-flight work finishes and is counted, and the register records a
  `refused` row for what never started.
- **Output size.** `max_output_tokens` plus an `overflow_policy` say what
  happens when the deliverable is oversized. DocTag's output is tiny, so the
  defaults are fine; when you do set a policy, know that `"partial"` flags
  the oversize through the envelope but does not trim it. The one policy that
  does trim, `"truncate_with_reference"`, needs two things from you: a
  `truncate_output` override (only the author knows how to shrink a typed
  output without invalidating it) and a persistence backend on the run, so
  the full version stays reachable by the run_id named on the envelope.
  Missing either, it degrades to `"partial"`: full output, flagged, never
  silent.
- **Cancellation.** The `CallContext` carries a cancel token that
  well-behaved Commissions check before expensive work.

The other half of trust is on the consuming side: handle the whole envelope,
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

**Specimen:** `RecursiveResearchCommission` sets `max_output_tokens` and
`overflow_policy="truncate_with_reference"` explicitly, and implements
`truncate_output` to keep cited claims over answer prose, with a comment
stating exactly what the policy does and does not protect.

## Step 6: Contract Tests

Prove the boundary without spending a cent. The trick: register a model in
the run's catalog whose "provider" is a script you wrote, through the same
`models=` parameter every run takes. The model's intelligence is not under
test; your Commission's behavior around the responses is.

The doubles for this are supported library surface, in `vibrantine.testing`:

- `scripted_model(scripted)`: a catalog entry served by your fake. Register
  it in `run_commission(models=[...])`; as the run's only entry it is the default
  model, so the Commission under test needs no `model=` at all.
- `ScriptedLLM(responses)`: the fake provider. Pops one response per LLM
  call, in order, and records every request it received in `calls` so you
  can assert on exactly what your Commission sent. Running past the end of
  the script fails the test loudly.
- `llm_response(...)`: builds one scripted reply, either tool calls or plain
  text, with token counts so cost and budget math run for real.
- `AlwaysCancelled`: a cancel token that is already cancelled, for proving
  your Commission checks before doing the work.
- `FIXTURE_MODEL`: a frozen, priced model (fixed context window and rates)
  for tests that assert on cost or size-gate numbers. It is deliberately not
  a real model id, so repricing or renaming a real model never silently
  changes your expected values; `scripted_model` reuses its id and rates by
  default.

```python
# src/doctag/tests/test_commission.py
"""Contract tests: scripted LLM, no API key, no network."""

from pathlib import Path

from vibrantine import run_commission_sync
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model

from doctag.commission import DocTagCommission
from doctag.types import DocTagInput


def test_concludes_with_typed_output(tmp_path: Path) -> None:
    # Script: the "model" reads the file, then concludes with a valid output.
    doc = tmp_path / "note.txt"
    doc.write_text("Meeting notes about the quarterly budget.", encoding="utf-8")
    scripted = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "read", {"path": str(doc)})]),
            llm_response(
                tool_calls=[
                    ("t2", "conclude", {"summary": "Budget meeting notes.", "tags": ["budget"]})
                ]
            ),
        ]
    )

    result = run_commission_sync(
        DocTagCommission(),
        DocTagInput(file_path=doc),
        models=[scripted_model(scripted)],
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.tags == ["budget"]
    assert result.cost.estimated_usd >= 0.0
```

```bash
uv run pytest
```

Notice what happened in that script: the double replaced only the *LLM*. The
`read` tool call went through the real `ReadTool` against a real temp file.
You scripted the model's decisions and everything else, dispatch, the run's
fuses and call log, tool execution, cost math, was live machinery.

This one test proves import, construction, the catalog seam, dispatch, tool
execution, conclusion, and the envelope. The full coverage bar for a shipped
Commission (validation failures, cancellation, malformed model responses,
budget behavior, tool menu shape) is listed in
[`commission-testing.md`](commission-testing.md); work through it as your
Commission grows up.

**Specimen:** `src/vibrantine/examples/recursive_research/tests/test_commission.py`
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

**Specimen:** `src/vibrantine/examples/recursive_research/BRIEF.md`.

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

from vibrantine import run_commission_sync

from doctag.commission import DocTagCommission
from doctag.types import DocTagInput

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set; eval cases skipped.",
    ),
]

# Pinned: a failing case means the Commission changed, not the default model.
EVAL_MODEL = "google/gemini-3.5-flash"

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

    result = run_commission_sync(
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

**Specimen:** `src/vibrantine/examples/recursive_research/tests/test_eval.py`,
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
| You write | a `build_user_message` method | an `async def _run` method |
| Control flow | the framework's LLM loop: it calls the model, lets it use your toolbox, ends when the model concludes | yours: plain Python, your own sequencing, fan-out, rounds |
| Use when | "send the model a prompt plus tools, get a typed answer" | the logic is a pipeline or coordinator the model shouldn't drive |
| You must uphold | nothing extra; the framework does it | errors-as-values, cancellation checks, cost reporting (all shown below) |

Overriding neither fails at class-definition time. Overriding both is
allowed but pointless (your `_run` wins and `build_user_message` goes
unused), so treat "pick one" as discipline.

On the basic path the framework builds the `CommissionResult` for you. On
the custom path **you** build it, every return, success included: a
`Provenance`, a `CostMetrics`, and for failures an `ErrorState`. That is the
price of owning the control flow. Two protected helpers assemble the
envelope from those parts: `self._succeed(output, provenance=..., cost=...)`
and `self._fail(kind, detail, retryable=..., provenance=..., cost=...)`;
the full helper table is in Part III.

## Multimodal input: images and audio in the opening message

`build_user_message` may return a list of typed parts instead of a plain
string, and that is the entire multimodal story. Everything else (the loop,
the envelope, budgets, persistence) behaves exactly as it does for text.

```python
from vibrantine import AudioPart, ContentPart, ImagePart, TextPart

class ChartCheck(Commission[ChartCheckInput, ChartCheckOutput]):
    ...  # identity ClassVars and system_prompt as usual

    def build_user_message(self, input: ChartCheckInput, ctx: CallContext) -> list[ContentPart]:
        return [
            TextPart(text=f"Does this chart support the claim? Claim: {input.claim}"),
            ImagePart(image_url=input.chart_url),
        ]
```

Three parts exist today, and each is the provider's own shape:

- `TextPart(text=...)`: a text span. Fields settled.
- `ImagePart(image_url=...)`: an image, as either an https URL or a `data:`
  URI. Use the URL form when the image is already hosted (the provider
  fetches it and your message stays small); use a `data:` URI (base64) when
  the bytes are local or private. Both forms are verified live.
- `AudioPart(data=..., format=...)`: base64 audio bytes plus the encoding
  tag (`"wav"` or `"mp3"`). Verified live.

Where the edges are, so you can rely on them:

- **Unknown parts never mistranslate.** The loop translates each part
  explicitly; a part it does not recognize fails the run structurally
  (`kind="validation"`) before the provider is contacted, so nothing is
  ever silently sent as the wrong modality and nothing is spent.
- **Gates measure text only, by design.** Non-text parts count zero toward
  the pre-flight size gate and the pre-turn budget floor. Those two checks
  must only ever *under*count, so they never decline a run the provider
  would have accepted. The true cost still lands: the provider bills media
  as ordinary input tokens, the post-turn budget check sees them, and
  `CostMetrics` reports them.
- **Model support is the model's problem, not the contract's.** Sending an
  image to a text-only model fails as a structured provider error that
  names the problem ("No endpoints found that support image input"). The
  library keeps no capability catalog to maintain or drift.
- **New modalities join the union additively.** There is no `VideoPart`
  today because no settled provider shape and no real consumer exist; when
  both do, it joins `ContentPart` without breaking anyone. The non-text
  parts' *fields* stay provisional until enough consumers confirm them; the
  union itself, and additive widening, are the stable promise.
- **Documents are not a content part.** A PDF is heavy read-only state:
  extract its text with a deterministic tool in your own repo and send
  ordinary text. A provider-native file part would join the union only if a
  real consumer proves extraction insufficient.

## Composition: calling children

A parent owns its children and depends on them only through the contract:

- **Inject children at construction** with working defaults, exactly like
  the tutorial's `ReadTool`.
- **Call children through `dispatch(child, child_input, ctx)`**, passing
  along the `ctx` you received. Never call a child's `_run` directly.
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

`capabilities` gates the LLM loop's tool menu, not your code: a Python
coordinator's child calls are written directly in `_run`, already chosen
by the author, so they never consult the allow-list. To narrow what a
*child's* model may reach, hand the child a narrower context (the `replace`
above).

Most compositions start as one of three shapes:

- **Pipeline**: one child feeds the next (`fetch -> summarize -> report`).
  Use it when each step depends on the previous step's output.
- **Fan out, then gather**: many children do similar work and the parent
  combines them (`plan -> workers -> review`). Use it when the task splits
  into independent parts.
- **Loop until done**: the parent repeats a small cycle with an explicit
  stop: a round cap, the budget, a deadline, or a typed "done" signal from a
  child.

These are authoring patterns, not framework types; write the plain Python
shape first and extract a template only when a second real coordinator
repeats it. And when a job grows, go **wide, not deep**: many siblings under
one coordinator beats many nested LLM levels, because siblings don't
compound each other's errors or stack each other's latency
(`design.md § Shallow trees`).

## Where the accumulating state goes

There is no framework memory or artifact slot to write into, deliberately:
a Commission stays evaluable as a function of its inputs. Accumulation
across steps lives in the coordinator's own local variables while it runs.
If a run must survive a process restart, the *caller* holds the state and
threads it back in through a typed input field (for example
`prior_claims: list[Claim[str]]`). The framework's persistence layer stores
run *records* for observability; assembling records into resumable state is
the caller's job, not the Commission's.

Not all state threads by value. Heavy read-only state (a corpus, a whole
codebase) passes by **handle**: the caller hands the run a path and the run
reads what it needs from the world; re-reading the world on the next run is
not hidden memory, it is re-reading. The rule of thumb is *reads look,
writes carry*: any number of runs may read shared state in place, but writes
to state a fan shares serialize through a single owner: workers draft the
change as a typed value, one owner applies it.

## Seeing a run

The framework emits through Python's standard `logging` module at its own
choke points, so watching a run costs one line of ordinary Python and no
vibrantine-specific setup:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

At INFO you get one line per LLM round-trip (model and token counts) and one
line per completed call (name, status, cost, run_id), at any nesting depth.
WARNING surfaces the things worth a human's attention (a Commission that
raised, a conclude that failed validation, a persistence backend that
errored, a custom `_run` whose returned cost under-reports the provider
spend the run witnessed in its subtree); DEBUG adds call starts and the
loop's self-corrections. The library
never installs handlers or writes files on its own.

Beyond watching: `on_progress` on the `CallContext` streams typed
`ProgressEvent`s to a callback for building live UIs, and the persistence
layer stores full structured records (input, result, cost, the LLM
transcript) for programmatic autopsy. The transcript lands automatically
on the default loop. External custom `_run` methods dispatch an LLM-bearing
child Commission rather than calling a provider directly: a raw call would
bypass the run's private Gatekeeper. Library-owned custom provider flows use
`deposit_llm_trace` internally; `SynthesizeCommission` is that internal
worked example. Switching records on is also one
line: `run_commission(..., backend=FilesystemBackend(root))` reaches every node
in the call tree, including children spawned mid-run. Wiring a backend is
the "I care about logs" signal, so it defaults to recording everything
(`record="always"`); pass `record="dev"` or `record="off"` when you want
the database to hold less.
Three tiers, all optional: log lines to watch, events to react, records
to query.

## Worked build: a corpus-research coordinator

The system: a custom **coordinator** that, for each round, dispatches a
**plan** Commission, **fans out** read-workers in parallel, **reviews** their
claims, then **consolidates** into a follow-up question; after the rounds, an
**assemble** Commission writes the report. Five basic Commissions plus one
custom coordinator.

(Contrast with the shipped `RecursiveResearchCommission`, which does research
with the *opposite* interior: an LLM loop deciding dispatch. Same job family,
different owner of control flow, identical boundary. That is the point.)

### The shared types

```python
from datetime import UTC, datetime
from typing import ClassVar
from pydantic import BaseModel, Field

from vibrantine import (
    Commission, CommissionResult, CallContext, CapabilitySet,
    Provenance, Claim, CostMetrics, dispatch,
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

    async def _run(self, input: ResearchInput, ctx: CallContext) -> CommissionResult[ResearchReport]:
        all_claims: list[Claim[str]] = []
        total_cost = 0.0
        question = input.question
        rounds = 0

        # workers read; narrow their tool access to read-only
        worker_ctx = replace(ctx, capabilities=CapabilitySet(tools=frozenset({"read", "sample", "grep"})))

        for _ in range(self._max_rounds):
            if ctx.cancel.is_cancelled:
                return self._fail(
                    "cancelled",
                    "Cancelled mid-research.",
                    retryable=False,
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
            # A child's existing ErrorState passes through unchanged; the
            # _fail helper builds a new one, so this return stays hand-built.
            return CommissionResult(
                status="failure",
                error=asm_res.error,
                provenance=self._provenance(),
                cost=CostMetrics(estimated_usd=total_cost),
            )

        return self._succeed(
            ResearchReport(summary=asm_res.output.summary, claims=all_claims, rounds=rounds),
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

result = run_commission_sync(
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
counterpart, read `src/vibrantine/examples/recursive_research/` end to end
(it is one page of code).

---

# Part III: Reference

## The five surfaces

Before the field tables, the ownership map they all hang off. A Commission
separates five concerns, each with a different owner:

| # | Surface | Answers | Owner | Fixed when |
|---|---|---|---|---|
| 1 | **Identity** (declaration) | What the Commission *is* | Commission author | Written into the class |
| 2 | **Capacity** (construction) | What this instance *can do*, and its built-in limits | Builder | Built into the instance, immutable |
| 3 | **Permission** (call-time context) | What this run is *allowed* to do | Caller | Per run |
| 4 | **Task** (payload) | What this run is *asked* to solve | Caller | Per run |
| 5 | **Result** (envelope) | What came *back*, and how to trust it | Framework + Commission | Returned by the call |

Read it as a sentence of ownership: the author owns what it is, the builder
owns what it can do, the caller owns both what it may do and what it must
solve, and the framework guarantees the shape of what comes back. Two
surfaces never bend: the declared boundary (identity's input and output
types) and the result envelope. Those two promises are the contract.
Every dial lives on the middle three surfaces.

The map sorts the tables below: the identity ClassVars are surface 1, the
behavior slots and constructor kwargs are surface 2, `CallContext` is
surface 3, your `InputT` value is surface 4, and the result envelope is
surface 5.

The trickiest split is money, because budgeting touches two surfaces:
`budget_usd` is **permission**, the caller's per-run grant ("this invocation
may spend $0.20"). The capacity-side ceilings (`max_iterations`,
`max_input_tokens`, `max_output_tokens`) are the builder's, wired at
construction. A run obeys the tightest bound, whoever set it.

Task and permission blur easily because both are caller-owned and per-run.
Keeping them apart is the point: the input says *solve this*; the context
says *and you may spend this much, touch these tools, and stop when I say*.

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
    PersistedRecord, PersistenceBackend, FilesystemBackend, SqliteBackend,  # persistence
    Model, DEFAULT_MODEL, openai_compatible, ollama,      # models
    run_commission, run_commission_sync, dispatch,                        # entry points
    create_commission,                                     # authoring factory
    ContentPart, TextPart, ImagePart, AudioPart,           # message content parts
    DEFAULT_MAX_ITERATIONS, estimate_tokens, deposit_llm_trace,  # authoring edge
)
```

A contract test (`tests/test_external_authoring.py`) parses this block and
asserts it names exactly `vibrantine.__all__`, so the one-import-line claim
above can never silently drift from the code.

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
| `GlobTool` | `GlobInput` | `pattern`, `base` | `max_matches` |
| `GrepTool` | `GrepInput` | `pattern`, `path` | `max_matches`, `ignore_case` |
| `ListDirTool` | `ListDirInput` | `path` | `max_entries` |
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
| `persistence_mode` | `None` | `PersistenceMode`; `None` = no opinion, follow the caller's `record=` default. An explicit mode, `"off"` included, beats the caller |
| `max_output_tokens` | `None` | Output cap; `None` = no enforcement |
| `overflow_policy` | `"partial"` | `OverflowPolicy`; enforced by `dispatch` |

One optional hook supports the `truncate_with_reference` policy:
`truncate_output(output, max_tokens)` returns a smaller, still-valid
`OutputT` that fits the cap (measured by `estimate_tokens` over the JSON
serialization), or `None` to decline. The base implementation declines;
dispatch then degrades the policy to `partial`. When the hook does chop,
dispatch force-persists the full result and the returned envelope names the
run_id it lives under.

Constructor kwargs (all keyword-only):

| kwarg | Default | Purpose |
|---|---|---|
| `model` | `None`, the run's default model | Which catalog entry the default loop uses: a pure name, looked up in `run_commission(models=[...])` when the loop runs. Unknown names fail fast |
| `max_iterations` | `10` | LLM-loop cap |
| `toolbox` | class default | Dependency-injection override |
| `max_input_tokens` | unset: the catalog entry's context window, at run time | Input size gate; explicit `None` disables it, an int pins it |
| `target_input_fraction` | `0.75` | Fraction of the window the gate allows |
| `persistence_mode` / `max_output_tokens` / `overflow_policy` | class default | Per-instance policy override (sentinel-based, so omission is not `None`) |

Protected helpers available to a custom `_run`. The underscore warns
*callers* off; for authors these are the supported interior surface,
provisional until the authoring-surface freeze (see
`design.md § Not built yet`):

| Helper | Use |
|---|---|
| `self._succeed(output, *, provenance, cost)` | Build a success result, the common return |
| `self._fail(kind, detail, *, retryable, provenance, cost)` | Build a structured failure result |
| `self._emit(ctx, phase, detail=None)` | Emit a `ProgressEvent` (no-op without a callback) |
| `self.fits(estimated_tokens)` | Size-gate check against the explicit constructor cap (an unset cap resolves from the run catalog entry at run time) |
| `estimate_tokens(text)` | Module-level chars/4 heuristic; `from vibrantine import estimate_tokens`. Unlike the underscore helpers above, this is frozen surface (the heuristic itself may be refined; the name and signature hold) |
| `deposit_llm_trace(messages)` | Module-level; `from vibrantine import deposit_llm_trace`. Frozen surface, like `estimate_tokens`. The default loop deposits automatically. Library-owned custom provider flows deposit each message history so it lands in the run's persisted record; external custom `_run` methods dispatch an LLM-bearing child rather than calling the provider directly |

## The result envelope

`CommissionResult[OutputT]` is the single value every call yields. Errors
are values, never exceptions.

| Field | Type | Notes |
|---|---|---|
| `status` | `CommissionStatus` | `"success"` / `"failure"` / `"partial"` |
| `output` | `OutputT \| None` | Populated on success and partial |
| `error` | `ErrorState \| None` | Populated on failure and partial |
| `provenance` | `Provenance` | Origin and trust of this run; on the custom path, required on every return, success included |
| `cost` | `CostMetrics` | This call's cost; children's dollars roll up structurally. `in_tokens` / `out_tokens` are the call's own LLM turns only (`None` when no LLM turn ran) |
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
  `timeout`, `budget_exceeded`, `cancelled`, `output_too_large`,
  `run_halted` (spoken only by the root result when a run fuse tripped)
- `confidence` (`ConfidenceLevel`): `verified`, `grounded`, `speculative`

### Status is not the verdict

`status` reports whether the Commission fulfilled its contract: a
deliverable of the promised type came back. It says nothing about what
the deliverable concluded. A review that finds ten bugs succeeded; a
search that establishes there are no matches succeeded; a validator that
rejects its input succeeded, because rejecting was the work it was
commissioned to do. Put the domain verdict in the output type (a
`passed: bool`, a `findings` list, whatever the promise names) so the
caller branches on it as data. Reserve `failure` for the contract not
being fulfilled: the work could not be done, never "the answer was no."

## Runtime conditions: CallContext

`CallContext` is a frozen dataclass carried alongside the input; copy it
with `dataclasses.replace` to hand a child a modified one.

| Field | Default | Enforced? |
|---|---|---|
| `budget_usd` | `None` | Yes: the LLM loop halts with `budget_exceeded` after a turn that overruns (and pre-flight, before a turn whose input cost alone would break the grant), dispatches each child with the remaining budget (never the full grant), and shows the model a `[budget]` spend line after each turn's tool results so it can wind down before the stop |
| `capabilities` | `CapabilitySet()` | Yes: the LLM's tool menu is `toolbox` intersected with `capabilities.tools` (`None` = unrestricted) |
| `cancel` | `NEVER_CANCELLED` | Yes: checked at natural breakpoints; returns `cancelled`. Also the run's breaker: a fuse trip (`max_llm_calls`, `time_limit_seconds`, the `budget_usd` spend fuse) flips this same signal |
| `on_progress` | `None` | Observability callback (`ProgressEvent`) |
| `parent_run_id` | `None` | Threaded by `dispatch`; read-only to bodies |
| `backend` | `None` | `PersistenceBackend` to write through |
| `record` | `None` | Recording default for every node whose `persistence_mode` is `None`; a node's explicit mode wins |

Provider-call concurrency is not a context field: it is a run-wide bound
(`run_commission(concurrency=)`), one room of chairs shared by the whole tree and
held around each provider call only, so coordinators may fan out freely.

## The meanings of None

The knobs use an unset sentinel so "caller said nothing" and "caller said
None" stay distinct, which means an explicit `None` keeps a real meaning,
and that meaning differs per knob. Each is documented where it lives; this
table is the one consolidated view, for re-entry after time away:

| Knob | `None` means | Leaving it unset means |
|---|---|---|
| `model=` | The run's default model | Same |
| `max_input_tokens=` | Size gate disabled (the standard tool shape) | Auto-resolve from the run catalog entry's context window, at run time |
| `max_output_tokens=` | No output cap | The class default (itself `None` unless the class says otherwise) |
| `persistence_mode=` | No opinion: follow the caller's `record` | The class default (itself `None` unless the class says otherwise) |
| `system_prompt` | This Commission needs none (tools, coordinators) | n/a: a class attribute, not a kwarg |
| `CallContext.budget_usd` | No spending ceiling | Same |
| `CallContext.record` | Recording stays off for nodes with no opinion | Same |
| `CapabilitySet.tools` | Unrestricted: the whole toolbox is on the menu | Same |
| `CostMetrics.in_tokens` / `out_tokens` (read side) | No LLM turn ran in this call | n/a; `0` means a turn ran and counted nothing |

Two adjacencies deserve care. `max_input_tokens` and `max_output_tokens`
sit side by side with different `None` stories: the input gate
distinguishes unset (auto) from `None` (off), while the output cap does
not. And `CapabilitySet.tools` inverts the usual polarity: `None` permits
everything, the empty set permits nothing.

## Entry points

Always invoke through an entry point, never the `_run` hook directly; the
entry points stamp `run_id`, thread `parent_run_id`, enforce
`overflow_policy`, and persist.

| Entry point | Shape | Use |
|---|---|---|
| `run_commission` | `async run_commission(commission, input, *, budget_usd=None, models=(), default_model=None, max_llm_calls=1000, time_limit_seconds=None, concurrency=16, tool_ceiling=None, capabilities=None, cancel=None, on_progress=None, on_llm_call=None, on_dispatch=None, backend=None, record=None)` | The only way into a run: builds the run's internal control object (fuses, room, call log, dispatch register, model catalog) and the root `CallContext`. Refuses when called inside a run |
| `run_commission_sync` | sync wrapper over `run_commission`, same kwargs | Scripts, REPL, tests |
| `dispatch` | `async dispatch(commission, input, ctx)` | The only way around inside a run: called from a custom `_run` with the ctx it received (or a `replace()` of it). Refuses outside a run, and refuses a hand-built context |

## The authoring factory

`create_commission(*, name, description, input, output, toolbox=(),
system_prompt=None, model=None, max_iterations=10)` returns an
ordinary basic Commission riding the default loop; run it through the entry
points like any other.

- Required arguments are the crafted parts: identity (`name`,
  `description`) and the typed contract (`input`, `output`, both Pydantic
  models).
- The default system prompt is the description plus the input schema's
  field descriptions and the `conclude` instruction; pass `system_prompt=`
  to replace it. The opening user message is the input serialized as JSON.
- `model` is a name resolved against the run's catalog when the loop runs
  (None is the run default); tests script the model through the catalog
  (`vibrantine.testing.scripted_model`).
- Construction is deterministic: no network, no spend, no LLM involved in
  building the Commission itself.
- The exit ramp is subclassing `Commission` (Part I); the factory covers
  the basic path only, never custom `_run` interiors.

## The default LLM loop

What a basic Commission rides:

- Composes your system prompt and opening message, calls the model with a
  tool menu built from `toolbox` intersected with `ctx.capabilities`.
- Injects a synthetic `conclude` tool whose schema is your `output_type`.
  Calling `conclude` is the model's only structured exit; you never parse
  free text.
- Dispatches tool calls through `dispatch`, feeds results back, and rolls
  child cost up into your result. Each child is dispatched with the
  remaining budget (the grant minus everything already spent), so ceilings
  only shrink down the tree.
- Under a budget, appends a one-line `[budget]` status (spent, grant,
  remaining) after each turn's tool results. Your system prompt can build
  on it: tell the model to keep a wrap-up reserve and conclude with what it
  has as the remainder approaches it, instead of running into the hard
  stop. Without a budget, no line is emitted.
- Stops on: `conclude`, budget exceeded (after an overrunning turn, or
  pre-flight when the next turn's estimated input cost alone would break
  the grant), `max_iterations`, cancellation, or the model returning no
  tool call.
- The pre-flight size gate and the pre-turn budget floor measure text only;
  image and audio parts count zero there, and their real cost lands in the
  post-turn check from the provider's reported usage
  (Part II § Multimodal input).

Any Commission placed in another Commission's toolbox is exposed to that
model with your `description` verbatim, which is why the description is
written as a selection prompt.

## Models and cost

- The run's models are defined once, at the front door:
  `run_commission(models=[...])` is the run's catalog, and it vends the provider
  clients. A Commission carries only a *name* (`model=`, None = the run
  default) resolved against the catalog when its loop runs; a name not in
  the catalog fails fast. Never hardcode a model in a Commission body.
- Each catalog entry is a *profile*: `name` (the catalog key, what a
  Commission references; defaults to `id`), `id` (the wire string),
  endpoint, prices, and `params` (provider call settings like temperature,
  merged verbatim into every call the profile serves). Two entries may
  share an `id` under different names: the same model at two temperatures
  is two profiles. Define a configuration once, in its profile; nodes just
  name the role.
- An empty catalog auto-registers the system default (`DEFAULT_MODEL`, a
  full profile object), so hello world configures nothing. Build entries
  with `Model(id=...)` for OpenRouter targets, `openai_compatible(id,
  address)` for any OpenAI-format endpoint, or `ollama(id)` for a local
  Ollama server.
- Pricing states: *priced*, *free* (a real $0, like local Ollama), or
  *unpriced* (unknown). Setting `budget_usd` on an unpriced model fails
  fast: the framework refuses to run a budget it cannot enforce. Prices
  must be finite and non-negative; `0.0`, not a missing price, means free.
- The default endpoint is OpenRouter, via the `openai` SDK with `base_url`
  swapped, keyed by `OPENROUTER_API_KEY`.
- `CostMetrics` carries raw token counts alongside the dollars:
  `in_tokens` / `out_tokens` cover the call's own LLM turns only. They never
  roll up (a token sum across mixed models misleads; USD is the rollup
  currency) and are `None` when no LLM turn ran, so tools and Python
  coordinators read as "no LLM involved" rather than "consumed nothing".

## Persistence

- `PersistenceBackend` is the protocol (`store` / `load` /
  `list_references` / `delete` / `delete_older_than`); supply any
  implementation.
- `FilesystemBackend(root)` is the shipped default: one JSON file per run,
  mode-aware pruning.
- `SqliteBackend(path)` is the shipped queryable option: one row per run in
  a single SQLite file, the same mode-aware pruning. Plain columns
  (run_id, parent_run_id, commission_name, mode, status, cost_usd,
  created_at) are query handles; the `record` column holds the full
  `PersistedRecord` JSON. There is no query API on top: open the file with
  any SQL tool and ask directly.
- `PersistedRecord` carries input, full result, a ctx snapshot, and an
  optional LLM trace.
- Modes: `off` / `on_failure` / `dev` / `always`. Wire a backend via
  `run_commission(..., backend=...)`; children inherit it automatically.
- A wired backend records everything by default: silence on `record=`
  means `"always"`, because handing the run a database is the "I care
  about logs" signal, and keeping less (`record="dev"`, `"on_failure"`,
  `"off"`) is the active choice. Without a backend nothing is recorded.
  `record=` is the caller's default for every node whose own
  `persistence_mode` is `None` (the class default). A node's explicit
  mode, `"off"` included, beats the caller's default; silence follows the
  room, a spoken choice is kept.

## Authoring discipline

- **Commission vs tool.** A Commission has an LLM call somewhere in its
  subtree; a tool has none. Both wear the same contract; the distinction is
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
- [ ] **Exactly one path**: `build_user_message` (basic) or `_run`
  (custom).
- [ ] **Typed I/O**: Pydantic models; every field has `description=`; depth
  at most 3, at most 20 fields.
- [ ] **`description` is written for an LLM** deciding whether to call this
  Commission.
- [ ] **Errors are returned, not raised**: `status="failure"` plus an
  `ErrorState` whose `kind` is one of the seven author-usable values
  (the eighth, `run_halted`, is spoken only by the framework when a run
  fuse trips; never manufacture it).
- [ ] **Custom path**: every `CommissionResult` you build carries a
  `Provenance` (success included) and a `CostMetrics`; `_succeed` and
  `_fail` assemble them correctly. (Basic path: the framework fills these
  for you.)
- [ ] **Custom `_run`**: check `ctx.cancel.is_cancelled` at breakpoints;
  call children via `dispatch(child, input, ctx)`, never `._run`; sum
  children's `cost.estimated_usd` (the framework logs a WARNING when a
  returned envelope's cost falls short of the provider spend it witnessed
  in that call's subtree); if you call the model yourself, deposit each
  transcript via `deposit_llm_trace` so records carry it.
- [ ] **Compose through constructors**: inject sub-Commissions and the model
  at construction; never reach into another Commission's internals.
- [ ] **State stays outside**: no memory held inside the Commission between
  calls; accumulation belongs to the caller or the coordinator's local
  scope.
- [ ] **Launch via `run_commission` / `run_commission_sync`**, never by calling `_run`
  directly.
- [ ] **Tested and evaluated**: contract tests with a `ScriptedLLM` from
  `vibrantine.testing`, a BRIEF with an efficacy bar, and eval cases per
  [`commission-testing.md`](commission-testing.md).

If all of these hold, the Commission is well-formed and composes with any
other Commission through the same contract, which is the entire point.
