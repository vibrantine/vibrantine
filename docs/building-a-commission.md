# Building a Commission — a self-contained guide

Everything a third party needs to build a commission against Vibrantine, with **zero knowledge of the library's internals**. Read only this. It depends only on the **frozen public surface** — the names exported from `vibrantine` — so nothing here relies on internal helpers that may move.

The running example is a real, non-trivial system: a **deep-reading research commission** that runs `plan → fan-out → review`, **consolidates** into a follow-up question, runs `plan → fan-out → review` again, then **assembles a report**. By the end you can build it from this page alone.

**What Vibrantine is.** Vibrantine is a component model for building AI agents. The unit is a *commission*: a bounded, typed unit of work that uses an LLM somewhere inside it, with a strict contract at its edges. You build agents by *composing* commissions — small ones nest inside larger ones through the same contract — the way you compose functions or UI components, not by wiring a shared-state graph. This guide shows you how to write one, and how to compose several into a multi-stage system.

---

## 1. The one rule

> **A commission is one typed input → one `CommissionResult`. What happens inside is entirely yours.**

That is the whole contract. The framework guarantees things *at the boundary* — typed I/O, errors returned as values (never raised), cost and provenance on every result. **Inside `invoke`, you are free**: plain Python, an LLM loop, parallel fan-out, many rounds — the framework never inspects how you do it.

Two consequences you must internalize before writing code:

1. **Errors are values, not exceptions.** You return a `CommissionResult` with `status="failure"`; you do not raise. (If your code raises anyway, the framework converts it to a failure — but don't rely on that.)
2. **State lives outside the commission.** A commission does not hold memory between calls. Anything that accumulates across runs (a knowledge base, progress, prior findings) is held by the *caller* and passed back in through the typed input. The framework persists run *records* for you, but it never owns your domain state. (See §9 — this is load-bearing and shapes how the example is built.)

---

## 2. Install and import

- **Python ≥ 3.12**, `pydantic` v2. Install as a git or local dependency (it is not on PyPI):

```toml
dependencies = ["vibrantine @ git+https://github.com/vibrantine/vibrantine.git"]
```

- **To run an LLM-backed commission** you need an API key in the environment: `OPENROUTER_API_KEY` (the default provider). Pure non-LLM tools need no key. Local models (Ollama) need no key (see §8).

**The entire public surface — this single import line is everything you may depend on:**

```python
from vibrantine import (
    Commission, CommissionResult, CommissionStatus,           # the contract + the envelope
    CallContext, CapabilitySet, CancelToken, NEVER_CANCELLED, ProgressEvent,  # runtime conditions
    Provenance, ConfidenceLevel, Claim, CostMetrics,          # provenance, claims, cost
    ErrorState, ErrorKind,                                    # the failure model
    OverflowPolicy, PersistenceMode,                          # policy vocabularies
    PersistedRecord, PersistenceBackend, FilesystemBackend,   # persistence
    Model, KNOWN_MODELS, DEFAULT_MODEL, openai_compatible, ollama,  # model vocabulary
    run_one, invoke_sync, dispatch,                           # entry points
)
```

Ready-made **tools** (deterministic file/web primitives) come from `vibrantine.tools` — drop them into a commission's toolbox:

```python
from vibrantine.tools import ReadTool, SampleTool, GrepTool, GlobTool, ListDirTool, FetchTool
# also: WriteTool, EditTool, DeleteTool, MoveTool, ShellTool
```

What each does — and its main input field(s). You rarely construct these inputs yourself: inside an LLM loop the model fills them from each tool's schema. The table is so you know what's on the menu and what each one reads or does:

| Tool | What it does | Main input field(s) |
|---|---|---|
| `ReadTool` | Read a file's contents (supports pagination) | `path` |
| `SampleTool` | Peek at the head/tail of a file without loading it all | `path` |
| `GrepTool` | Search file contents by regex | `pattern`, `path` |
| `GlobTool` | Find files matching a glob pattern | `pattern` |
| `ListDirTool` | List the entries in a directory | `path` |
| `FetchTool` | Fetch the contents of a URL | `url` |
| `WriteTool` | Create or overwrite a file | `path`, `content` |
| `EditTool` | Replace a string in a file | `path`, `old_string`, `new_string` |
| `MoveTool` | Move or rename a file | `source`, `target` |
| `DeleteTool` | Delete a file | `path` |
| `ShellTool` | Run a shell command | `command` |

---

## 3. The two kinds of commission

Every commission subclasses `Commission[InputT, OutputT]`. You pick exactly one of two authoring paths:

| | **Basic commission** | **Custom commission** |
|---|---|---|
| You write | a `build_user_message` method | an `async def invoke` method |
| Control flow | the framework's built-in LLM loop (it calls the model, lets it use your tools, and ends when the model is done) | **yours** — plain Python, your own sequencing, fan-out, rounds |
| Use when | "send the model a prompt + tools, get a typed answer" | the logic is a pipeline/coordinator the model shouldn't drive |
| You must uphold | nothing extra — the framework does it | errors-as-values, cancellation checks, cost reporting (all shown in §7) |

**In the example:** the five leaf commissions (plan, read-worker, review, consolidate, assemble) are **basic** — each is "prompt + tools → typed output." The top-level coordinator that sequences the rounds is **custom** — it decides what runs next, so it owns `invoke`.

---

## 4. The skeleton every commission must have

Set these four class attributes, or the class fails to define (the framework checks at definition time):

```python
class MyCommission(Commission[MyInput, MyOutput]):
    name: ClassVar[str] = "my_commission"        # stable id; also the tool-name if another commission calls this one
    description: ClassVar[str] = "..."           # LLM-facing: written for a model deciding whether to call this (see §10)
    input_type: ClassVar[type] = MyInput         # your input Pydantic model
    output_type: ClassVar[type] = MyOutput       # your output Pydantic model
```

Then override **one** of:
- `def build_user_message(self, input, ctx) -> str` → basic path (you ride the loop).
- `async def invoke(self, input, ctx) -> CommissionResult[MyOutput]` → custom path.

Overriding neither is an error (it could never run). That's the entire enforcement; the rest is discipline.

**Optional behaviour knobs** (class attributes or constructor keyword args — all have safe defaults):

| Knob | Default | What it does |
|---|---|---|
| `system_prompt` | `None` | The commission's own prompt (basic path). `None` is fine for a custom coordinator. |
| `toolbox` | `()` | Tools/sub-commissions the **LLM loop** may call. A *custom* coordinator doesn't need this — it dispatches children directly (§7). |
| `model` (ctor) | system default | Which LLM to use (§8). Never hardcode a model; pass it in. |
| `max_output_tokens` / `overflow_policy` | `None` / `"flag"` | Cap output size and choose what happens on overflow. |
| `persistence_mode` (ctor) | `"off"` | Whether runs are saved (§ persistence, end). |

---

## 5. Typed inputs and outputs

Inputs and outputs are **Pydantic v2 models**. Two rules keep them portable across LLM providers:

- **Every field has a `description=`.** The model reads these.
- **Keep them shallow:** nesting depth ≤ 3, ≤ 20 fields per model.

```python
from pydantic import BaseModel, Field

class WorkerInput(BaseModel):
    subquestion: str = Field(description="The single focused question this worker must answer.")
    sources: list[str] = Field(description="Paths or URLs the worker may read to answer it.")
```

When a **basic** commission rides the loop, the framework gives the model a synthetic `conclude` tool whose schema **is your `output_type`** — so the model's only way to finish is to produce a valid `OutputT`. You get typed output for free; you never parse text.

---

## 6. What you return and receive: `CommissionResult`

Every call yields one `CommissionResult`. **Who builds it depends on your path:**

- **Basic path** — the framework builds it *for you*. It fills `status`, `output`, `provenance`, and `cost`, and if a tool errors or the model can't finish, it returns a `status="failure"` result automatically. You never construct one and you don't set provenance/cost yourself — you only *read* the result (from the entry point, or from a child you dispatched). So a basic commission handles "the file couldn't be read" simply by not crashing: the failure comes back as a `CommissionResult` the caller inspects.
- **Custom path** — *you* build and return it yourself, including a `Provenance` and `CostMetrics` on every return (success included), and you return `status="failure"` with an `ErrorState` for your own error cases.

The "you" in the table below is the **custom path**.

| Field | Type | Who sets it |
|---|---|---|
| `status` | `"success" \| "partial" \| "failure"` | you |
| `output` | `OutputT \| None` | you (present on success/partial) |
| `error` | `ErrorState \| None` | you (present on failure/partial) |
| `provenance` | `Provenance` | you — **required on every return, including success** |
| `cost` | `CostMetrics` | you |
| `run_id`, `parent_run_id` | `str \| None` | **the framework** — leave unset |

Supporting types (construct these directly; they're all in the import line):

```python
ErrorState(kind="internal", detail="human-readable, actionable", retryable=False)
Provenance(source="my_commission", fetched_at=datetime.now(UTC), confidence="grounded")  # all three required
CostMetrics(estimated_usd=0.0)
Claim(value=..., sources=[Provenance(...)], confidence="grounded")  # an assertion + its receipts
```

**Closed vocabularies — these exact strings, nothing else:**
- `status`: `success`, `partial`, `failure`
- `ErrorState.kind` (`ErrorKind`): `validation`, `internal`, `rate_limit`, `timeout`, `budget_exceeded`, `cancelled`, `output_too_large`
- `confidence` (`ConfidenceLevel`): `verified`, `grounded`, `speculative`

`Claim[T]` is how you carry a grounded fact: a `value` plus the `sources` (provenances) that back it. Use it whenever output needs to be auditable — exactly what a research worker produces.

---

## 7. Running commissions, and calling children

**To launch a commission from ordinary code**, use an entry point — never call `.invoke(...)` yourself (the entry points stamp the run id, enforce limits, and persist):

```python
result = await run_one(MyCommission(), MyInput(...), budget_usd=0.50)   # async
result = invoke_sync(MyCommission(), MyInput(...), budget_usd=0.50)      # sync wrapper, for scripts/tests
```

**Inside a custom `invoke`, to call a child commission**, use `dispatch`, passing along the `ctx` you received:

```python
child_result = await dispatch(child_commission, child_input, ctx)
```

`dispatch(commission, input, ctx)` returns the child's `CommissionResult`. To run many children **in parallel**, gather dispatches:

```python
import asyncio
results = await asyncio.gather(*[dispatch(worker, inp, ctx) for inp in inputs])
```

**Cost rolls up by addition.** A child that ran an LLM loop already includes its own sub-costs in `result.cost`. A custom coordinator simply **sums its children's costs** into the `CostMetrics` it returns. (Basic commissions get this automatically.)

**`CallContext` (`ctx`)** carries the runtime conditions. You receive it and pass it down. The fields you'll use:

| `ctx` field | Meaning |
|---|---|
| `budget_usd` | A per-call dollar ceiling. An LLM-loop child stops itself if it would overrun. A coordinator that wants a *total* cap tracks cumulative cost and stops. |
| `capabilities` | Which tools children's LLMs may use. `CapabilitySet(tools=frozenset({"read","sample"}))` restricts; the default allows all. |
| `cancel` | Cooperative cancellation. Check `ctx.cancel.is_cancelled` at natural breakpoints and return early. |
| `on_progress` | Optional callback; call it with a `ProgressEvent(commission_name=..., phase=..., detail=...)` to report progress. |
| `backend` | Where runs persist, if persistence is on. Inherited by children automatically. |

`CallContext` is immutable. To hand a child a *modified* context (e.g. narrower tools), copy it:

```python
from dataclasses import replace
worker_ctx = replace(ctx, capabilities=CapabilitySet(tools=frozenset({"read", "sample", "grep"})))
```

---

## 8. Choosing the model

Pass `model=` when constructing a commission. Omit it to use the system default.

```python
MyCommission()                                   # system default model
MyCommission(model="google/gemini-3-flash-preview")   # a known model id (resolves to the right endpoint + pricing)
```

For a model the library doesn't catalogue, build a `Model`:

```python
from vibrantine import openai_compatible, ollama
MyCommission(model=openai_compatible("my-model", "https://my-gateway/v1"))  # any OpenAI-format endpoint
MyCommission(model=ollama("llama3.1"))                                      # a local Ollama server (free, no key)
```

**Pricing note that affects budgets:** a model can be *priced*, *free* (`$0`, e.g. local Ollama), or *unpriced* (unknown). If you set a `budget_usd` on an **unpriced** model, the call fails fast — the framework refuses to run a budget it can't enforce. Either register real pricing or run without a budget.

---

## 9. Where the accumulating state goes (read before building the example)

The deep-reading system accumulates findings across rounds. **That accumulation is held in the coordinator's own Python variables**, and — if the whole run must survive a process restart — by the **caller**, threaded through the typed input. There is no framework "memory" or "artifact" you write into. This is deliberate: commissions stay evaluable as functions of their inputs.

Concretely: the coordinator keeps `all_claims` in a local list while it runs. If you need resumability, add a field like `prior_claims: list[Claim[str]]` to the input, have the caller persist results and pass prior findings back in on the next call. The framework gives you `FilesystemBackend` to persist run *records*, but assembling those into resumable state is the caller's job, not the commission's.

---

## 10. The worked build: a deep-reading research commission

The system: a custom **coordinator** that, for each round, dispatches a **plan** commission, **fans out** read-workers in parallel, **reviews** their claims, then **consolidates** into a follow-up question; after the rounds, an **assemble** commission writes the report. Five basic commissions + one custom coordinator.

### 10.1 The shared types

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


# --- leaf I/O ---
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

# --- top-level I/O ---
class ResearchInput(BaseModel):
    question: str = Field(description="The research question to answer.")
    sources: list[str] = Field(description="Paths or URLs forming the corpus to read.")

class ResearchReport(BaseModel):
    summary: str = Field(description="The final synthesized answer.")
    claims: list[Claim[str]] = Field(description="Every grounded claim the report rests on.")
    rounds: int = Field(description="How many plan-fan-review rounds were run.")
```

### 10.2 The basic commissions (each rides the loop)

Only the read-worker is shown in full; `PlanCommission`, `ReviewCommission`, `ConsolidateCommission`, and `AssembleCommission` follow the identical shape — four ClassVars, a `system_prompt`, and a `build_user_message`; they need no `toolbox` because they only reason over the text they're given.

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


class PlanCommission(Commission[PlanInput, PlanOutput]):
    name: ClassVar[str] = "research_planner"
    description: ClassVar[str] = "Break a research question into focused, parallelizable sub-questions."
    input_type: ClassVar[type] = PlanInput
    output_type: ClassVar[type] = PlanOutput
    system_prompt: ClassVar[str | None] = (
        "Decompose the question into 3-6 focused sub-questions that can be investigated "
        "independently. Avoid overlap with what is already known. Conclude with the list."
    )
    def build_user_message(self, input: PlanInput, ctx: CallContext) -> str:
        known = "\n".join(f"- {k}" for k in input.known_so_far) or "(nothing yet)"
        return f"Question:\n{input.question}\n\nAlready known:\n{known}"

# ReviewCommission, ConsolidateCommission, AssembleCommission: same pattern.
```

Each basic commission gets its typed output for free: the loop hands the model a `conclude` tool whose schema is the commission's `output_type`, and rolls up tool costs into the result.

### 10.3 The custom coordinator (it owns the control flow)

The children are injected at construction — the coordinator depends on them through the contract, never reaching inside them. The accumulating `all_claims` is a plain local list (per §9). (This guide's `DeepResearchCommission` is a from-scratch exercise, unrelated to the shipped `DeepResearch` commission, which uses the opposite interior style: an LLM loop deciding dispatch.)

```python
class DeepResearchCommission(Commission[ResearchInput, ResearchReport]):
    name: ClassVar[str] = "deep_research"
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

            # FAN-OUT — workers in parallel
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

            # CONSOLIDATE → follow-up question (or stop)
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

### 10.4 Wiring and running it

Construction is where you compose the pieces and choose the model — the coordinator never hardcodes either:

```python
research = DeepResearchCommission(
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
          f"cost ≈ ${result.cost.estimated_usd:.2f}")
else:
    print(f"{result.error.kind}: {result.error.detail}")
```

That's the whole system. Note what you never did: you never touched the framework's internals, never parsed model output by hand, never raised an exception across a boundary, and never stored state inside a commission.

---

## 11. The authoring checklist

Before you ship a commission, confirm:

- [ ] **Four ClassVars set** — `name`, `description`, `input_type`, `output_type`.
- [ ] **Exactly one path** — `build_user_message` (basic) **or** `invoke` (custom).
- [ ] **Typed I/O** — Pydantic models; every field has `description=`; depth ≤ 3, ≤ 20 fields.
- [ ] **`description` is written for an LLM** that's deciding whether to call this commission (what it does, when to use it, what it returns).
- [ ] **Errors are returned, not raised** — `status="failure"` + an `ErrorState` whose `kind` is one of the seven allowed values.
- [ ] **Custom path:** every `CommissionResult` you build carries a `Provenance` (success included) and a `CostMetrics`. *(Basic path: the framework fills `status`/`provenance`/`cost` and surfaces failures for you — you set none of these.)*
- [ ] **Custom `invoke`**: check `ctx.cancel.is_cancelled` at breakpoints; call children via `dispatch(child, input, ctx)` (never `.invoke`); sum children's `cost.estimated_usd`.
- [ ] **Compose through constructors** — inject sub-commissions and the model at construction; never reach into another commission's internals.
- [ ] **State stays outside** — no memory held inside the commission between calls; accumulation belongs to the caller / the coordinator's local scope.
- [ ] **Launch via `run_one` / `invoke_sync`**, never by calling `invoke` directly.

If all ten hold, the commission is well-formed and composes with any other commission through the same contract — which is the entire point.
