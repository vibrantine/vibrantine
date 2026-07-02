# Composition

How commissions, tools, and application code fit together. The durable answer to "how do commissions chain / nest / coordinate?" — the contract jacket at every joint, the patterns for composing inside an `invoke` body, the rules for information flow, the slots the framework gives you for output discipline and observability.

Sister doc: [`vision.md`](vision.md) covers *what* Vibrantine is and *why*. This doc covers *how the pieces fit together*.

## The contract boundary

**One typed input → one typed output.** What happens inside `invoke` is the commission's business.

Every commission's `invoke` returns a `CommissionResult` carrying:

- Typed payload (the actual output, or `None` on failure)
- Status (`"success" | "partial" | "failure"`)
- Error state (structured failure value, not an exception — see [Errors-as-state](#errors-as-state))
- Provenance (where this call ran, when, with what confidence)
- Cost (dollars consumed; rolls up structurally — see [Cost rollup](#cost-rollup))

The same jacket wraps every commission, every tool, every coordinator. This is the load-bearing simplification: the boundary is rigid, the interior is free.

The whole model reduces to two sentences: a commission is one typed function with an LLM somewhere inside, one input in and one result envelope out; and the parent is the only path between children, with no sibling channel. If a design keeps growing machinery that no longer reduces to those two sentences, doubt the machinery, not the core. The working code is the check: a concept the code does not need was conversational sediment, not architecture.

> **Implementation is not the invariant; the contract boundary is.** *How* a commission's interior works — deterministic Python or an LLM deciding its own control flow, a one-line body or a deep recursive tree, sophisticated planning or a fixed sequence — is the author's choice. It is never a contract property and the framework never inspects or branches on it. Everything the contract guarantees lives at the boundary (typed I/O, errors-as-state, cost rollup, provenance); everything inside is free to vary. Conflating the two — treating "how it's coordinated inside" as though it were a contract distinction — is a recurring drift. This line is the correction: when a question is really about the interior, it is not a contract question.

## Three types, not four

The framework recognises exactly three categories:

1. **Commissions** — typed I/O, contract jacket, LLM somewhere in subtree
2. **Tools** — typed I/O, contract jacket, deterministic (no LLM anywhere in subtree)
3. **Application code** — Python outside the framework. Cron jobs, web servers, CLI scripts, test harnesses. Calls into commissions; wears no contract.

There is no fourth "traffic controller," "graph," or "workflow" type. Patterns that look like they want a fourth type decompose cleanly into one of the three — usually a Tool doing deterministic compute (a marshaller, an aggregator, a reducer) or inline Python inside a parent commission's `invoke` body.

### The LLM-anywhere rule

> A unit is a **Commission** if an LLM call exists *anywhere in its subtree*; otherwise it's a **Tool**.

A deterministic Python coordinator that fans out to LLM-bearing children is a commission, not a tool — its subtree contains LLM judgment, even though its own body does not. A composite Tool (a tool that calls other tools) is still a Tool — the rule is about LLM presence, not composition depth.

### Shape A: Tool is just a Commission without an LLM

Tools subclass `Commission[InputT, OutputT]` directly. There is no separate `Tool` ABC. They wear the same jacket because they need the same caller discipline: typed I/O, errors-as-state, structural cost, provenance. The "is this a commission or a tool" distinction is a discipline, not a type. This was the explicit Shape A design call, and it held through the entire v0.5 tools layer — every tool's needs were already in `Commission`.

### Vibrantine "Tool" vs mainstream "tool"

Vibrantine's Tool is *implementation-defined* (no LLM in subtree). The mainstream LLM-agent meaning of "tool" is *interface-defined* (anything callable by an LLM, including sub-agents). The bridge is the **LLM-tool wrapper** — it exposes both Vibrantine Tools *and* Commissions as "LLM-facing tools." Disambiguate in prose where the audience could be confused.

## Internal composition

Every commission has the same outside: typed input, typed output, errors-as-values. What varies is what the `invoke` body does inside, and the only distinction worth a name is **who decides what runs next — the author, or an LLM.**

### Python coordinator — the author decides

The `invoke` body calls its children directly, in an order fixed in code. No LLM at this level; any judgment lives inside the children. "Going through the parent" is just Python variables flowing between function calls — no LLM, no context bloat, no cost penalty.

Two of today's commissions are this:

- `MorningBriefingCommission` — `asyncio.gather` over fetches, drop the failures, hand survivors to Synthesize.
- `SynthesizeCommission` — two LLM calls in an author-fixed sequence. The LLM produces content; it does not choose the steps. (It calls the model directly, not through the loop below.)

### LLM-loop commission — the LLM decides

The `invoke` body hands the LLM a toolbox and lets it choose which tool to call, when, and with what input, until it calls the framework-injected `conclude` tool. `AskCommission` is the example; `run_llm_loop` (`src/vibrantine/llm_tools.py`) is the reusable core.

### What this distinction is, and isn't

It's a design-time choice about one commission's interior — not a type, never branched on by the framework, and not a property of the tree (a Python coordinator can have an LLM-loop child and vice versa). Default to keeping control flow in Python: it's deterministic, cheap, and testable. Reach for the LLM loop only when the routing genuinely needs the model's judgment.

## The Commission base class

One comprehensive `Commission` base sits under every commission. Its default `invoke` *is* the complete LLM loop above — already wired with the cancellation checks, the budget stop, the capability-filtered tool menu, and the `conclude` exit — and it upholds every contract invariant. So a *basic* commission writes almost nothing: identity, I/O types, a system prompt, a toolbox, and one `build_user_message` method. A *custom* commission — one whose control flow isn't that loop, which includes every Python coordinator — overrides `invoke`, the single escape hatch. No subtypes, no menu. This is deliberately the foundation for hundreds of commissions, including ones authored by non-devs and by lesser-model agents, so the author-facing surface is kept as small as it can be.

### What a basic commission supplies

The whole of `ask.py`, minus its system-prompt string and docstrings:

```python
class AskInput(BaseModel):
    question: str = Field(description="The question to answer about the file.")
    file_path: Path = Field(description="Absolute path to the file to consult.")

class AskOutput(BaseModel):
    answer: str = Field(description="Natural-language answer to the question.")

class AskCommission(Commission[AskInput, AskOutput]):
    name: ClassVar[str] = "ask"
    description: ClassVar[str] = "Answer a question about a single file by reading its contents."
    input_type: ClassVar[type] = AskInput
    output_type: ClassVar[type] = AskOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT
    toolbox = (ReadTool(),)

    def build_user_message(self, input: AskInput, ctx: CallContext) -> str:
        return f"File path: {input.file_path}\nQuestion: {input.question}"
```

| The author supplies | What it is |
|---|---|
| `name`, `description` | identity (the `description` is what an LLM sees when this commission is wrapped as a tool) |
| `input_type`, `output_type` | the typed I/O shapes |
| `system_prompt` | the commission-layer prompt (see [System prompts](#system-prompts-three-layers)) |
| `toolbox` | the tools the loop's LLM may call |
| `build_user_message(input, ctx)` | turns the typed input into the loop's opening message; returns `str \| list[ContentPart]` (a bare `str` is sugar for one `TextPart`) |

The framework enforces this surface at class-definition time: `Commission.__init_subclass__` rejects a subclass missing any of `name` / `description` / `input_type` / `output_type`, or one that overrides *neither* `build_user_message` nor `invoke` (and so could never run). Inheritance satisfies the checks; an abstract intermediate can defer a slot with `@abstractmethod` (which counts as overriding it — opt-out by override, not by flag).

Two details that keep this surface honest:

- **`toolbox` is a class attribute** (default `()`), built once and shared across instances — so tools placed there must be safe to share. A stateful tool is built per-instance in an author `__init__` and passed up via `super().__init__(toolbox=...)`; the same kwarg injects fakes for tests. A basic commission otherwise needs no `__init__`.
- **`build_user_message` takes `ctx`** so future per-call information (the planned envelope layer, which lands *inside* `CallContext`) reaches it with no signature change. Its `str | list[ContentPart]` return is what buys multimodal input later — `TextPart` exists today; `ImagePart` is provisional until its first image-bearing consumer fixes its fields.

### What the base owns (the default `invoke`)

The author never writes any of this. In order, the default `invoke`:

1. resolves the model to a `Model` (identity + endpoint + facts) and builds a client for that model's endpoint (defaults provided; both injectable for tests — and because the endpoint travels with the `Model`, cloud and local providers share one path)
2. checks cancellation before spending
3. builds provenance
4. runs `run_llm_loop` over the toolbox (which enforces the loop guarantees below)
5. computes cost — own token usage at the model's price, plus any dispatched children's cost rolled up from the loop (see [Cost rollup](#cost-rollup))
6. maps the outcome to a `CommissionResult` — success or structured failure, never a raised exception
7. emits progress events along the way

### The single escape hatch

A commission whose control flow isn't the standard loop **overrides `invoke`**: a Python coordinator that fans to children (`MorningBriefingCommission`), or a fixed multi-pass flow (`SynthesizeCommission`'s two LLM calls). It still inherits the contract jacket and the protected helpers below; the loop machinery it doesn't use is simply unused. Override is the *only* extension point — optimise the common case, let the exceptions override.

### Protected authoring surface

A custom-`invoke` author builds a `CommissionResult` by hand and must do it the way the framework expects, so the base exposes a **protected surface**: members that are underscore-prefixed (external *callers* leave them alone) but stable for *subclasses* to rely on. Both shipped custom commissions (`SynthesizeCommission`, `MorningBriefingCommission`) use them.

| Member | Role |
|---|---|
| `_fail(kind, detail, *, retryable, provenance, cost)` | Build a structured failure `CommissionResult` (errors-as-values). |
| `_emit(ctx, phase, detail=None)` | Emit a `ProgressEvent`; no-op without a callback. |
| `_cost(in_tokens, out_tokens)` | `CostMetrics` from token counts at the resolved model's price. |
| `_prices()` | `(in, out)` USD-per-million for the resolved model; `(0.0, 0.0)` if unpriced. |
| `_resolved_client` | The lazily-built `AsyncOpenAI` client, for commissions that call the LLM directly. |
| `fits(estimated_tokens)` | Whether an input passes the size gate (already public — no underscore). |

The underscore marks *protected* (subclass-stable, caller-facing leave-alone), not "internal/unstable." Like the rest of the authoring surface it stays provisional until more consumers exercise it; promoting these to public names is deferred to the authoring-surface freeze.

### What the base guarantees

The base enforces every [cross-cutting discipline](#cross-cutting-disciplines) automatically — errors-as-state, cost rollup, budget, cancellation, capability intersection, progress — so a basic commission can't accidentally violate them. On top of those it guarantees the envelope- and loop-specific invariants:

- **Tri-state status.** Success → output, no error; failure → error, no output; partial → both. The failure shape is fixed: `ErrorState(kind, detail, retryable)` with `output=None`.
- **Size gate before spending.** A pre-flight `fits()` check on the initial system+user message; over-gate is a `validation` failure with zero cost.
- **Structured exit only.** Output crosses the boundary solely through the validated `conclude` tool (see [The conclude tool](#the-conclude-tool)); no free-form completion.
- **Tool failures stay in the loop.** A failing tool result is fed back to the LLM, not surfaced as a commission failure.
- **Bounded iteration.** The loop is capped by `max_iterations`; exhaustion is a retryable `internal` failure.
- **Cost is real.** Computed from actual token counts at the model's price (`$0` for unknown or free models), with dispatched children's cost rolled in.

## Information flow

**Parent is the only data path between siblings.** Children never see each other's existence; every result returns to the parent, which decides what's next.

This falls out of three structural choices:

- Typed envelope per call (`CommissionResult` is the only thing that crosses a child boundary)
- DI-at-construction with no back-channels (parent holds references to children; children hold none)
- No shared mutable state (no channels, no reducers, no `StateGraph`)

This is possession, not broadcast. Many children can *read* the same underlying state (a codebase, a corpus) in place, since shared reads do not race; what they may not do is *write* to it sideways. Anything that changes shared state funnels back through the parent, the single writer. Heavy read-only state is passed by handle (a path a child reads), not copied into a typed field. Reads look, writes carry. This is not a ban on side effects (a commission may act, gated by capabilities, per [Acting vs drafting](#acting-vs-drafting)); it is the parent-as-hub invariant applied to state a wide fan shares.

What this property buys:

- **Single locus of orchestration.** Reading the parent's `invoke` tells you the data flow.
- **Errors converge at a known point.** Each child's `ErrorState` arrives at the parent as a value.
- **Subtree intermediates stay private.** `B`'s intermediate values live in `B`'s memory and are gone when `B` returns. The parent only ever sees what made it into the typed envelope.

What it costs:

- No streaming between siblings
- No pipeline parallelism (worker A can't start feeding B until A returns)
- Large intermediates materialise in the parent's memory

None of those costs are felt at current workloads. Each is consciously deferred, with trigger conditions on record, until a real workload makes it felt.

### Pipeline-style flow

"Output of A becomes input of B" is two `invoke` calls with a translation step between them — inside the parent's `invoke` body (a Python coordinator), or inside the parent LLM's tool-use loop (an LLM-loop commission). The framework provides no auto-pipe; type translation between any two commissions is application-specific. A `Pipeline[I, O]` coordinator template captures the pattern (a Python coordinator with a `stages: list`) without breaking parent-as-hub.

## Sub-commissions as tools (LLM-tool wrapper)

A commission can be wired into another commission's toolbox at construction. From the consuming LLM's perspective, it's indistinguishable from a deterministic tool — typed input, typed output, a name, a description.

This unification is the deep claim of Shape A, and it composes recursively without compromising the contract:

- **DI at construction.** Parent explicitly wires which children are exposed to which other children. No runtime sibling discovery.
- **The LLM-tool wrapper is the only path.** No special "call sibling" mechanism.
- **Cost rolls up structurally on both paths.** A Python coordinator sums its children's `CommissionResult.cost` into its own (`MorningBriefingCommission`); the LLM-loop dispatch path accumulates each dispatched child's `cost` in `run_llm_loop` and folds it into the parent's `CommissionResult.cost`. So an LLM-loop commission reports the cost of every sub-commission it calls.
- **Budget propagates by slicing.** Parent gives child a slice of remaining budget when invoking; child reports actual cost on return; parent decrements.
- **Errors propagate as state.** Child returns `CommissionResult(status="failure")`; parent's LLM (or parent's Python) decides what to do.

**The "sibling-to-sibling" framing is misleading.** Once Parent puts `B` in `A`'s toolbox, `B` is no longer `A`'s sibling — `B` is one of `A`'s tools. The hierarchy is whatever construction wires.

**The toolbox is a declared attribute.** `Commission.toolbox: tuple[Commission, ...]` is set at construction via `super().__init__(toolbox=...)` — it is *not* a `ClassVar`, because it holds the child instances built in `__init__`. It is empty `()` for workers (and for coordinators that dispatch nothing, like `SynthesizeCommission`), and populated for any commission that dispatches children. For a Python coordinator it reads as an at-a-glance dependency list; for an LLM-loop commission it is *also* the source of the LLM's tool menu (the framework appends the `conclude` tool on top — see below). For an LLM-loop commission, the framework intersects the toolbox with `CallContext.capabilities` to decide what the LLM is actually offered (see [Capability set](#capability-set)).

### Tools as an API parameter, not a prompt layer

When an LLM-loop commission's LLM runs, the tool descriptions reach the LLM via the provider's `tools=` API parameter (Anthropic, OpenAI, etc.), not by injection into the system prompt. This keeps tool use consistent with what models are trained on across providers. Prose *about* tools (when to prefer X, how to use Y) lives in the system prompt layers (see [System prompts](#system-prompts-three-layers)); the tool *descriptions* themselves are an API mechanism.

### The conclude tool

Every LLM-loop commission has exactly one way for its LLM to signal completion: a framework-injected `conclude` tool. Its input schema equals the commission's `output_type`. When the LLM calls it, the framework packages the args into `CommissionResult(status="success", output=...)` and ends the loop. No free-form "the LLM said it was done" — completion is a structured tool call against the same type that the commission promised externally.

## System prompts: three layers

System prompts are first-class in the contract. Three layers, composed deterministically by the framework, most-general → most-specific (which also matches most-cacheable → least-cacheable for cache-stable-prefix discipline).

| Layer | Owner | Lifetime | Example |
|---|---|---|---|
| Application | The app (superagent) | Once per app session, flows through every commission | "You are part of a superagent built from typed commissions. Your outputs feed into other commissions." |
| Commission | The commission author (`ClassVar` on the subclass) | Once per commission class | "You are a SynthesisCommission. Take a list of source documents and produce a JSON object matching this schema. Cite every claim." |
| Envelope | The immediate caller (per-call, structured collection of named sections) | Per invocation | `{"stocklist": "...", "source_recency": "...", "tool_guidance": "..."}` |

The application prompt propagates through every commission and sub-commission in the tree, unchanged. The envelope is structured — a collection of named sections, not a single string — so a parent can add a section to a child's envelope without disturbing other sections. The framework assembles sections in a stable order for cache discipline.

**Slot in the contract:**

- `Commission.system_prompt: ClassVar[str | None]` — the commission-layer prompt (`None` = no commission-layer prompt)
- `CallContext.application_prompt: str | None` — flows unchanged through children
- `CallContext.envelope: dict[str, str]` (or ordered named sections — final shape pending) — per-call situational context

> Status: settled direction. Sub-questions on exact envelope shape, ordering rule, cache-control mechanism, and override semantics remain open.

## Output discipline

Context-window bloat from oversized child outputs is the dominant failure mode for any LLM-loop parent (a child's `CommissionResult` gets rendered as a `tool_result` block into the parent LLM's conversation). The framework addresses this at the contract layer.

### `max_output_tokens` and overflow policy

Every commission declares its output budget and what to do when exceeded.

- `Commission.max_output_tokens: int | None` — mirrors `max_input_tokens`; `None` (the default) means no enforcement
- `Commission.overflow_policy: OverflowPolicy` — picks from a menu; default `"flag"`

Both are class-defaulted but instance-shadowable: `Commission.__init__` accepts both as kwargs (sentinel-defaulted, so omission falls back to the class default and explicit `None` for `max_output_tokens` is a meaningful override). Same class, different policies in different environments.

The menu:

| Policy | Behaviour on overflow | When to pick |
|---|---|---|
| `reject` | Discard output; return `status="failure"`, `error.kind="output_too_large"` | Overflow = misbehaviour signal (e.g. a fan worker exceeded its declared scope) |
| `truncate_with_reference` | Chop output to fit; persist full output via the persistence backend; the truncated result carries the full output's `run_id` as its reference. *Stubbed — the chop + persist-reference mechanic is a near-term TODO; until it lands, selecting it degrades to `partial` (full output preserved, flagged), so it never breaks.* | Commissions where overflow is expected (e.g. coding commission editing a file) |
| `partial` | Truncate + return `status="partial"` with `ErrorState` noting truncation | Caller signalled through the result jacket; data still usable |
| `flag` | Pass output through unchanged; emit `ProgressEvent(phase="output_overflow", detail=...)`. Signal-not-data — observable only if someone's listening | Dev iteration: keep moving, find out later |

**Explicitly out of the menu:**

- *Summarise via LLM* — breaks deterministic compaction; introduces non-determinism in the fallback path
- *Soft-warn without record* — defeats the point of declaring a budget

### Enforcement boundary

Overflow enforcement lives in the `dispatch` helper, not inside `Commission.invoke`. Anywhere a child is invoked — `run_one` from the top, `run_llm_loop` from an LLM-loop parent, a Python coordinator dispatching its children — it goes through `dispatch`, so every result that crosses a commission boundary is checked against its commission's policy.

### Why the context bloat matters

The pressure is asymmetric to a Python coordinator — there, child results are just Python variables and go out of scope when the parent's `invoke` returns. LLM-loop parents accumulate child results in the LLM's conversation for the duration of the loop. Two or three oversized tool results and the parent's context is polluted, cost-per-turn climbs, the cache invalidates, and the LLM's judgment degrades.

This is why the budget lives in the contract rather than as an optional helper: an unbounded commission embedded in an LLM-loop parent is a liability the parent can't defend itself against.

## Persistence for inspection

Every commission invocation can persist a full *record* of its run to addressable storage, so post-hoc diagnostic work can answer "what did fan #7 actually return?" — load-bearing for complex commissions where partial failures need primitive-level diagnosis, and the substrate any inspection / replay UI reads from.

### What's stored

A persisted record contains four slots:

| Slot | What | Notes |
|---|---|---|
| `result` | The full `CommissionResult` | Foundation |
| `input` | The typed `InputT` the call was invoked with | Enables re-run-with-same-input |
| `ctx_snapshot` | Frozen subset of `CallContext`: `budget_usd`, `capabilities`, `concurrency`, `parent_run_id` | Runtime-only fields (`cancel`, `on_progress`, `backend`) aren't persisted — they're objects, not data |
| `llm_trace` | For LLM-loop commissions: the full messages + tool_calls + tool_results sequence | Stored as raw JSON for v1; typed shape lands once a consumer surfaces the real fields |

Child runs aren't embedded — they persist as independent records and are discovered by querying `list_references(parent_run_id=...)`. Avoids duplication, removes a bookkeeping path that would race under `asyncio.gather`.

### Identity and chain

Every persisted call has a unique identity and links to its parent:

- `CommissionResult.run_id: str | None` — UUID4 string, stamped by `dispatch` on every wrapped call. `None` only on results constructed outside the framework (in tests, for instance). Always present in practice
- `CommissionResult.parent_run_id: str | None` — populated by `dispatch` from the outer call's run_id; `None` for root invocations
- `CallContext.parent_run_id: str | None` — what the commission body sees; populated automatically when `dispatch` recurses (via a `ContextVar`, so `asyncio.gather` over child dispatches just works)

Identifiers are generated regardless of `persistence_mode` — they're cheap (UUID4 + ContextVar set/reset) and useful for logging even when nothing is persisted. Only the *record write* is gated on the mode.

Parent linkage is stored on the child, not on the parent. Walking up the chain is cheap (read `parent_run_id` off the record); walking down is a backend query.

### Mode and retention

`Commission.persistence_mode: PersistenceMode` declares the class default; `Commission.__init__` accepts a `persistence_mode` kwarg so the same class can run `dev` locally and `on_failure` in production.

| Mode | When it persists | Default retention |
|---|---|---|
| `off` | Never | N/A. **Default.** |
| `on_failure` | When `status="failure"` or `"partial"` | 7 days, no count cap |
| `dev` | Always | Ring buffer of last 100 runs (total across the backend) |
| `always` | Always | Indefinite — app owns cleanup |

Pruning happens on every `store`: the backend writes the new record then evicts anything past the mode's retention. No background task, no scheduling. Apps can call `delete` / `delete_older_than` on demand (an inspection UI's "clear history" button hits these).

### Backend

`PersistenceBackend` is a `Protocol` exposed at the library boundary; the application picks the implementation. The library ships a default filesystem backend; external apps consume the same protocol with no privileged access.

```python
class PersistenceBackend(Protocol):
    async def store(self, record: PersistedRecord) -> None: ...
    async def load(self, run_id: str) -> PersistedRecord | None: ...
    async def list_references(
        self, *, parent_run_id: str | None = None
    ) -> list[str]: ...
    async def delete(self, run_id: str) -> None: ...
    async def delete_older_than(self, cutoff: datetime) -> int: ...
```

The backend is wired at runtime via `CallContext.backend: PersistenceBackend | None`. `run_one` accepts it as a parameter and stuffs it in the context so children inherit it automatically. Backend is a *runtime* concern (dev vs prod swap, in-memory for tests), so it goes via context rather than commission construction — DI-at-construction is preserved for commission *dependencies* (sub-commissions, LLM client).

### Symmetry with overflow

`truncate_with_reference` (see [Output discipline](#output-discipline)) uses the same backend; the reference embedded in the truncated result is just the persisted call's `run_id`. No second addressing scheme.

## Tool result discipline

Beyond output budgeting, tools that return bounded data must follow the **truncatable-but-resumable** rule. The principle: large tool results are *cursors over data*, not whole-data snapshots.

- Pagination via `offset` + `limit` is canonical (`ReadTool` is the reference implementation)
- Truncation signalled explicitly (`truncated: bool` + counts like `total_lines`)
- Resumption path described in the tool's LLM-facing `description: ClassVar[str]`

Already codified in [`AGENTS.md § Tool result discipline`](../AGENTS.md). `GrepTool` is the known asymmetric case (truncatable but not resumable) — a consciously parked gap.

## Coordinator templates (v0.7+ work)

The named patterns of in-commission traffic control become standard-library commission classes:

- **`PlanFanReview[InputT, SubInputT, SubOutputT, OutputT]`** — LLM plan → Python fan via `asyncio.gather` → LLM review
- **`AgentLoop[InputT, OutputT]`** — LLM + tools + budget loop until the conclude tool fires (the canonical LLM-loop template)
- **`Pipeline[...]`** — sequential stages, each typed in/out
- **`RouteDispatch[...]`** — single LLM call picks a branch; one child invoked
- **`IterativeRefine[...]`** — LLM proposes, validates, refines, repeats until quality bar or max iterations

Each is a `Commission` subclass with a fixed Python skeleton and children injected at construction. Policy knobs (per-worker timeout, fan deadline, quorum) live as constructor arguments on the template, not on the base `Commission` contract.

**Discipline:** don't build templates speculatively. Build the second coordinator (after `MorningBriefingCommission`) when a real workload pulls for it, then look for what's actually shared.

## Failure modes and the layered-timeout discipline

Python coordinators that fan to children have a canonical "review waits on fans" hazard: one slow or hung child blocks the whole coordinator. The protection is layered and lives in plain Python:

1. **Worker-level timeouts.** Every I/O primitive bounds its own runtime (`FetchTool.timeout`, `ShellTool.timeout_seconds`, the LLM client's timeout). First line of defence.
2. **Per-child wrapper timeout.** The coordinator wraps each child's `invoke` in `asyncio.wait_for(...)`. Translates `TimeoutError` into a `CommissionResult(status="failure", error=ErrorState(kind="timeout"))`. Robust even if a child violates its own timeout contract.
3. **Overall fan deadline.** The coordinator wraps the entire `gather` in an outer timeout. Stragglers past the deadline are cancelled; the coordinator proceeds with whichever children completed.
4. **Quorum / partial-results policy.** If at least `min_successful_workers` succeeded, proceed to the next stage with the survivors; otherwise return a coordinator-level failure.

For LLM-loop commissions, an additional pattern lives **inside** the commission:

- **Soft deadline + prod.** If the LLM hasn't called its conclude tool after `N` seconds, inject a "please conclude with what you have" message into its own conversation. Possibly another nudge later. If still no conclude after the hard deadline, force-conclude with whatever partial state exists.

The prod lives inside the commission because the outer coordinator can't inject messages into a child's LLM conversation — it only sees `invoke` and `CommissionResult`. The hard deadline at the coordinator level is the safety net; the prod inside the commission is the cooperative nudge.

None of this requires new framework primitives. It's all `asyncio.wait_for` and small policy decisions on the coordinator template.

## Cross-cutting disciplines

The contract jacket's guarantees, summarised:

### Errors-as-state

Failures cross the `invoke` boundary as values (`ErrorState`), not exceptions. `ErrorState.kind` is the SSOT for failure categories — `validation`, `internal`, `rate_limit`, `timeout`, `budget_exceeded`, `cancelled`, `output_too_large`. Add a kind only when it represents a structurally distinct caller decision.

### Cost rollup

Cost is structural, not ambient. A child's cost rolls into the invoking parent's `CommissionResult.cost`; that rolls into the next level. Same rule at every depth — *by intent*. Aggregate cost is always knowable; tiering decisions become auditable rather than vibes-based.

**Resolved (2026-05-27):** the rollup is structural on both paths. A Python coordinator hand-sums child costs (`MorningBriefingCommission`, which does `sum(r.cost.estimated_usd ...)` + `fetch_cost + synth_result.cost`); the LLM-loop path accumulates them automatically. `run_llm_loop` adds each dispatched child's `CommissionResult.cost` into a `children_cost` running total (returned on `LoopOutcome`), which the default `invoke` folds into the call's `CommissionResult.cost` and which the in-loop budget check counts against `budget_usd`. So a recursive or sub-commission-bearing LLM-loop commission reports its whole subtree's cost and is bounded by its budget. `DeepResearchCommission` is the consumer that drove this.

### Budget semantics

Budgets are *allocated*, not drawn-down. A `CallContext.budget_usd` is what the caller is willing to spend on this commission; the commission stays within it and reports actual cost in `CommissionResult.cost`. No mid-run drawdown account, no reservation/refund protocol. Reclamation (a child returning unused budget for siblings) is application-layer if needed.

### Cooperative cancellation

`CancelToken.is_cancelled` is checked by commissions at safe points (between LLM calls, before issuing a request, between iterations). The caller never interrupts mid-byte. The contract guarantees a bounded response, not an immediate one.

### Observability via progress

`CallContext.on_progress` is an optional async callable. Telemetry, not control. The contract specifies neither frequency nor schema beyond "small and structured."

### Capability set

`CallContext.capabilities` carries an allow-list of tool names: `None` (the root default) means unrestricted; a set permits exactly those names, with the empty set permitting nothing. `run_llm_loop` enforces it by building the LLM's tool menu from the intersection of the commission's toolbox and this allow-list — a forbidden tool is never offered, and a stray call to one rides the existing unknown-tool path rather than a separate gate. Enforcement is at the LLM-choice layer only: a Python coordinator's hardcoded `dispatch` calls are not gated, since the author already fixed them. The ceiling flows down the tree via `ctx`; a parent narrows a child's grant by passing a smaller set.

### Acting vs drafting

A commission may **act** — its loop calls `Write` / `Shell` / an MCP tool and its typed output reports what it did — or merely **draft**, producing a decision or message for something else to act on. Both are valid; "drafts only" is not a contract property — it's the [interior, the author's choice](#the-contract-boundary).

Whether a commission *may* act is bounded by **`capabilities`** (above): withhold the tool and it can only draft; grant it and it can act. That is the lever — not a blanket rule that side effects live above the library.

For **high-stakes, irreversible** actions (send, push, public post), splitting the decision from the execution and putting a gate between them is a recommended *pattern* — it keeps the decision auditable as a value before anything irreversible happens. But the gate is **policy, and policy is the caller's** (the same inversion as [Power-user composition](#power-user-composition)): it can sit above the library, inside an actor as a confirmation tool, or as a drafter-plus-gate composition. The framework bounds authority and makes the result auditable; it does not decide when to gate.

### Concurrency cap

`CallContext.concurrency` is a per-coordinator hint, not tree-wide. A coordinator with `concurrency=4` that spawns four sub-coordinators each with `concurrency=4` can have sixteen leaves in flight. A tree-wide cap is consciously deferred until a nested coordinator earns it.

## Power-user composition

The framework guarantees the **contract jacket** at every joint: typed I/O, errors-as-state, cost rollup, budget propagation, cancellation. Those hold no matter how a user composes commissions.

The framework **does not guarantee** that any particular composition makes *sense*. If a power user wires `A` with `B` in its toolbox and `B`'s LLM calls `A` in turn (a runtime cycle the framework didn't anticipate), the contract still holds — cost rolls up, errors propagate, the budget caps the cycle eventually. The behaviour may be silly or wasteful; nothing in the framework breaks.

Clean inversion of responsibility:

- **Framework:** "These contracts will hold no matter what you do."
- **User:** "I take responsibility for composing things that make sense."

Composition policies — loop detection, topology validation, sensibleness checks — are application concerns. If they become felt needs, they belong in a higher layer, not in the contract.

## Commission as constrained query loop

A useful reframe (from reading the Claude Code internals): Claude Code has *one* cognitive primitive — a prompt-shaped, tool-mediated, stateful query loop — plus many ways to constrain it (different prompts, different tool pools, different `ToolUseContext` clones). Read in those terms:

> **A Commission is a pre-configured query loop with typed I/O.**

Each commission *is* a query loop: its system prompt, its tool surface, its expected output shape, all declared in advance. The Commission contract is "a way to declare a constrained query loop, with type discipline at the I/O boundary." Coordinators are loops that invoke other commissions as their tools.

This frame is consistent with everything built so far (Synthesize is a two-call loop, MorningBriefing is a loop that invokes sub-loops) but names what was previously implicit.

## What this rules out

- **No graph DSL surfaced to users.** Internal coordinator topology is expressed in Python inside the coordinator's `invoke` body, or via named coordinator templates. Graphs-as-data are not part of the framework's public surface.
- **No fourth "traffic controller" type.** Coordinator commissions and application code cover the space.
- **No back-channels.** Children don't see siblings, parents, or peers. All connections happen by construction-time wiring through the parent.
- **No shared mutable state across children.** No channels, reducers, or state objects connecting siblings.
- **No real-time mid-invocation sibling messaging.** Gossip-style information flow must discretise into rounds: each round is a fan of invocations whose outputs feed the next round's inputs through a coordinator.

The convergence with LangGraph stops here. They lifted topology into a graph data structure with channels and reducers; Vibrantine lifted it into typed Python composition with named templates. Their model is more expressive; this one has fewer concepts to hold in one head, and the contract jacket gives most of their guarantees without their machinery.

## See also

- [`vision.md`](vision.md) — what Vibrantine is and why; library scope, audience, use cases, standard-library taxonomy, economic engine, positioning
- `src/vibrantine/contract.py` — the contract types in code
