# Authoring a Commission from an External Repo

What a separate repo gets when it imports `vibrantine` to build commissions: the importable surface, the contract you subclass, the types you exchange, how to run a commission, and the authoring rules. Every reference is `file:line`, so the doc stays checkable against source.

> **Generated 2026-05-27, refreshed 2026-05-31** against `vibrantine` `0.1.0.dev0`. Code line references are exact at refresh time but drift as source moves — navigate by the symbol name when a line looks off. The runnable claims in this doc (no-key tool execution, client injection, errors-as-values, the override rule, helper signatures, tool input fields) are machine-checked by `tests/test_external_authoring.py`, so they fail loudly rather than rot silently.

---

## 0. Readiness & stability — read this first

The package has a **split stability promise** (`src/vibrantine/__init__.py:1-17`): the *outputs* you produce are frozen; the *ergonomics of authoring* are not. Build commissions today — friction you hit in the authoring surface is itself the input that drives the freeze, which is deferred until real consumers (like *this* repo) validate the surface.

| Depend on freely (frozen bones) | Use, but expect movement (provisional) |
|---|---|
| `CommissionResult` + all envelope/support types (`contract.py`) | `build_user_message` signature, `ContentPart`/`ImagePart` (§7) |
| Closed `Literal` vocabularies (`ErrorKind` etc.) | The basic-vs-custom split and toolbox/policy slot ergonomics |
| `run_one` / `invoke_sync` / `dispatch` (entry points) | The protected `_`-helpers' names (promote-to-public pending) |
| `Commission` as the base class + the 4 required ClassVars | `truncate_with_reference` (stubbed → `partial`; real mechanic pending) |
| `PersistenceBackend` protocol + `FilesystemBackend` | Std-lib commissions/tools as *canonical* examples (they're provisional consumers) |

---

## 1. Installation

Not published to PyPI (private, `version = "0.1.0.dev0"` — `pyproject.toml:3`). Consume it as a **git or local-path dependency**:

```toml
# pyproject.toml of the external repo
dependencies = [
    "vibrantine @ git+https://github.com/vibrantine/vibrantine.git",
    # or, local:  "vibrantine @ file:///abs/path/to/vibrantine"
]
```
```bash
# uv equivalents
uv add "vibrantine @ git+https://github.com/vibrantine/vibrantine.git"
uv add --editable ../vibrantine   # local sibling checkout
```

- **Python:** `>=3.12` (`pyproject.toml:9`). Uses 3.12 generics (`class Commission[InputT, OutputT]`).
- **Runtime deps:** `pydantic>=2`, `httpx`, `openai` (`pyproject.toml:10-14`).
- **Typing:** ships `py.typed` (`src/vibrantine/py.typed`), so your type-checker sees the real types.
- **Credentials:** importing needs none (the LLM client is lazy — `contract.py:538-551`). *Running* an LLM-backed commission needs `OPENROUTER_API_KEY` in the environment (`contract.py:549`), or inject your own `AsyncOpenAI` via the `client=` constructor arg. Pure tools (file/HTTP) need no key.

---

## 2. The public surface

The curated boundary is `vibrantine.__all__` (`src/vibrantine/__init__.py:43-73`). **In `__all__` = SemVer-protected; anything else (including std-lib commissions and tools) is importable from its submodule but provisional.**

```python
from vibrantine import (
    Commission, CommissionResult, CommissionStatus,        # contract + envelope
    CallContext, CapabilitySet, CancelToken, NEVER_CANCELLED, ProgressEvent,  # runtime
    Provenance, ConfidenceLevel, Claim, CostMetrics,       # provenance / claims / cost
    ErrorState, ErrorKind,                                 # failure model
    OverflowPolicy, PersistenceMode,                       # policy vocabularies
    PersistedRecord, PersistenceBackend, FilesystemBackend,# persistence
    run_one, invoke_sync, dispatch,                        # entry points
)
```

The std-lib **tools** are importable from `vibrantine.tools` (`src/vibrantine/tools/__init__.py:30-67`) — provisional, but ready to drop into a toolbox:

```python
from vibrantine.tools import (
    ReadTool, WriteTool, EditTool, DeleteTool, MoveTool,
    GlobTool, GrepTool, ListDirTool, SampleTool, ShellTool, FetchTool,
    # each ships its own *Input / *Output models too
)
```

Their input fields aren't obvious from the names — and they don't all match the worked example's `AskInput` (which uses `file_path`; the tools use `path`). Construct the `*Input` models with these fields (look at each `*Output` in source for the return shape):

| Tool | Input model | Required fields | Optional fields |
|---|---|---|---|
| `ReadTool` | `ReadInput` | `path` | `offset`, `limit` |
| `WriteTool` | `WriteInput` | `path`, `content` | `create_only` |
| `EditTool` | `EditInput` | `path`, `old_string`, `new_string` | `replace_all` |
| `DeleteTool` | `DeleteInput` | `path` | — |
| `MoveTool` | `MoveInput` | `source`, `target` | `overwrite` |
| `GlobTool` | `GlobInput` | `pattern` | `base` |
| `GrepTool` | `GrepInput` | `pattern`, `path` | `max_matches`, `ignore_case` |
| `ListDirTool` | `ListDirInput` | `path` | — |
| `SampleTool` | `SampleInput` | `path` | `head_lines`, `tail_lines` |
| `ShellTool` | `ShellInput` | `command` | `cwd`, `timeout_seconds`, `max_output_chars` |
| `FetchTool` | `FetchInput` | `url` | `headers`, `timeout_seconds`, `offset`, `max_chars` |

(You only build these directly when calling a tool yourself; inside an LLM loop the model fills them from each tool's generated schema.)

---

## 3. The `Commission` contract — the thing you subclass

`Commission[InputT, OutputT]` (`contract.py:311`) is an `ABC`. One typed input, one typed result, runtime conditions in `CallContext`.

### 3.1 Required identity (ClassVars)

Set all four, or class definition fails (`contract.py:322-325`, enforced at `352-391`):

| Attribute | Type | Meaning |
|---|---|---|
| `name` | `ClassVar[str]` | Stable identifier; also the tool name when wrapped into a toolbox |
| `description` | `ClassVar[str]` | **LLM-facing selection prose** (§8, §11) |
| `input_type` | `ClassVar[type]` | Your `InputT` Pydantic model |
| `output_type` | `ClassVar[type]` | Your `OutputT` Pydantic model |

### 3.2 Behaviour slots

| Attribute | Default | Notes | Ref |
|---|---|---|---|
| `system_prompt` | `None` | Commission-layer prompt; `None` is fine for tools | `contract.py:330` |
| `toolbox` | `()` | Sub-commissions/tools this commission may dispatch; instance-overridable via `toolbox=` kwarg | `contract.py:339` |
| `persistence_mode` | `"off"` | `PersistenceMode`; instance-shadowable | `contract.py:343` |
| `max_output_tokens` | `None` | Output budget; `None` = no enforcement | `contract.py:344` |
| `overflow_policy` | `"flag"` | `OverflowPolicy`; enforced by `dispatch` | `contract.py:345` |

### 3.3 The two authoring paths

The override rule is enforced at definition time (`contract.py:374-386`): you must override **at least one** of these. Overriding *neither* fails at class-definition time (`TypeError` — "could never run"). Overriding *both* is allowed and does **not** error: your `invoke` takes effect and `build_user_message` is left unused, so treat "pick one path" as authoring discipline, not a guarantee the framework enforces.

- **Basic commission** — override `build_user_message(self, input, ctx) -> str | list[ContentPart]` (`contract.py:432`). You ride the framework's default `invoke` (the full LLM loop, §8). This is the common case. Worked example: `src/vibrantine/commissions/ask.py`.
- **Custom commission** — override `async def invoke(self, input, ctx) -> CommissionResult[OutputT]` (`contract.py:450`) when your control flow is *not* the standard loop (e.g. a deterministic coordinator, a multi-pass pipeline). You then uphold the contract invariants yourself (errors-as-values, cancellation checks, cost reporting). Worked examples: `commissions/synthesize.py` (two-pass), `commissions/morning_briefing.py` (coordinator).

### 3.4 Constructor

`Commission.__init__` (`contract.py:388-430`) — all keyword-only:

| kwarg | Default | Purpose |
|---|---|---|
| `model` | `None` → `DEFAULT_MODEL` | Override the LLM model (§9) |
| `client` | `None` → lazy OpenRouter client | Inject a test/alt `AsyncOpenAI` |
| `max_iterations` | `10` | LLM-loop cap |
| `toolbox` | class default | DI override (sub-commissions/tools) |
| `max_input_tokens` | model context window, else `None` | Input size gate |
| `target_input_fraction` | `0.75` | Fraction of the window the gate allows |
| `persistence_mode` / `max_output_tokens` / `overflow_policy` | class default | Per-instance policy override (`_UNSET`-sentinel, so omission ≠ `None`) |

### 3.5 Protected helpers (provisional, subclass-stable)

Available to your `invoke` override; the underscore marks *caller-facing leave-alone*, not unstable. See `docs/composition.md § The Commission base class` ("Protected authoring surface").

| Helper | Use | Ref |
|---|---|---|
| `self._fail(kind, detail, *, retryable, provenance, cost)` | Build a structured failure result | `contract.py:570` |
| `self._emit(ctx, phase, detail=None)` | Emit a `ProgressEvent` (no-op without a callback) | `contract.py:619` |
| `self._cost(in_tokens, out_tokens)` | Model-priced `CostMetrics` | `contract.py:565` |
| `self._prices()` | `(in, out)` USD/million for the model | `contract.py:553` |
| `self._resolved_client` | Lazily-built LLM client | `contract.py:538` |
| `self.fits(estimated_tokens)` | Size-gate check | `contract.py:624` |
| `estimate_tokens(text)` | Module-level char/4 heuristic — **import from `vibrantine.contract`** (it's a module function, not a `self._` method, and not re-exported in `__all__`) | `contract.py:292` |

---

## 4. The result envelope — what you return / receive

`CommissionResult[OutputT]` (`contract.py:110-137`) is the single value every call yields. **Errors are values, not exceptions** — nothing raises across the `invoke` boundary.

| Field | Type | Notes |
|---|---|---|
| `status` | `CommissionStatus` | `"success"` / `"failure"` / `"partial"` (`contract.py:50`) |
| `output` | `OutputT \| None` | Populated on success and partial |
| `error` | `ErrorState \| None` | Populated on failure and partial |
| `provenance` | `Provenance` | Origin + trust of this run |
| `cost` | `CostMetrics` | This call's cost; **children roll up structurally** |
| `run_id` | `str \| None` | UUID4 stamped by `dispatch` |
| `parent_run_id` | `str \| None` | Immediate caller's run_id |

Supporting types (all in `contract.py`, all frozen):

- `ErrorState` (`88`): `kind: ErrorKind`, `detail: str`, `retryable: bool`.
- `ErrorKind` (`51-59`, closed): `validation`, `internal`, `rate_limit`, `timeout`, `budget_exceeded`, `cancelled`, `output_too_large`.
- `Provenance` (`72`): `source`, `fetched_at`, `confidence: ConfidenceLevel` — **all three required, no defaults**. A custom `invoke` constructs one for *every* return (success included), so a success path needs e.g. `Provenance(source="...", fetched_at=datetime.now(UTC), confidence="grounded")`. See §12.
- `ConfidenceLevel` (`49`, closed): `verified`, `grounded`, `speculative`.
- `Claim[T]` (`98`): `value: T`, `sources: list[Provenance]`, `confidence`.
- `CostMetrics` (`82`): `estimated_usd: float`.

> **Closed `Literal` vocabularies are frozen** (`ErrorKind`, `CommissionStatus`, `ConfidenceLevel`, `PersistenceMode`, `OverflowPolicy`). Adding/removing a member is a major version bump (`AGENTS.md § Vocabulary-append rule`; locked by `tests/test_contract.py`). Write `match`/dispatch code against the exact sets above.

---

## 5. Runtime conditions — `CallContext`

`CallContext` (`contract.py:238-259`) is a frozen dataclass carried alongside the input. Extend orchestration via new fields here, never via the `invoke` signature. Which fields are load-bearing today (`AGENTS.md § CallContext fields`):

| Field | Default | Enforced? |
|---|---|---|
| `budget_usd` | `None` | **Yes** — the LLM loop halts with `budget_exceeded` after a turn that overruns |
| `capabilities` | `CapabilitySet()` | **Yes** — the LLM's tool menu = `toolbox ∩ capabilities.tools` (`None` = unrestricted) |
| `cancel` | `NEVER_CANCELLED` | **Yes** — checked at natural breakpoints; returns `cancelled` |
| `on_progress` | `None` | Observability callback (`ProgressEvent`) |
| `concurrency` | `4` | Per-coordinator hint; **not** tree-wide yet |
| `parent_run_id` | `None` | Threaded by `dispatch`; read-only to bodies |
| `backend` | `None` | `PersistenceBackend` to write through (§10) |

Related exported types: `CapabilitySet` (`contract.py:142`), `CancelToken` Protocol + `NEVER_CANCELLED` (`161`, `175`), `ProgressEvent` (`178`).

---

## 6. Running a commission — entry points

**Always invoke through an entry point, never `commission.invoke(...)` directly** — the entry points stamp `run_id`, thread `parent_run_id`, enforce `overflow_policy`, and persist (`src/vibrantine/dispatch.py:1-19`).

| Entry point | Signature | Ref |
|---|---|---|
| `run_one` | `async run_one(commission, input, *, budget_usd=None, backend=None) -> CommissionResult` | `orchestrator.py:21` |
| `invoke_sync` | sync wrapper over `run_one` (for scripts/REPL/tests) | `orchestrator.py:40` |
| `dispatch` | `async dispatch(commission, input, ctx, *, llm_trace=None)` — the low-level path when you build the `CallContext` yourself | `dispatch.py:48` |

`run_one` builds a default `CallContext` for you; reach for `dispatch` when you need to set capabilities, cancellation, progress, or concurrency explicitly.

---

## 7. Building the opening message (basic commissions)

`build_user_message` returns `str | list[ContentPart]` (`contract.py:432-449`):

- A bare `str` is sugar for a single text part — the common case.
- `list[ContentPart]` for multimodal. `ContentPart = TextPart | ImagePart` (`contract.py:289`); `TextPart` (`271`), `ImagePart` (`278`, **provisional** — fields finalized by the first image consumer).
- It receives `ctx`, so future per-call context (the planned envelope layer) reaches it without a signature change.

> `ContentPart` / `TextPart` / `ImagePart` are **not** in `__all__` — they live behind the provisional authoring surface. Returning a plain `str` keeps you on the most stable path.

---

## 8. The default LLM loop (what a basic commission rides)

The default `invoke` delegates to `run_llm_loop` (`src/vibrantine/llm_tools.py:105`). What it does for you:

- Composes the system prompt + opening message, calls the model with `tools=` built from your `toolbox` (each rendered by `as_llm_tool`, `llm_tools.py:54`) intersected with `ctx.capabilities`.
- Injects a synthetic **`conclude`** tool whose schema = your `output_type` (`make_conclude_tool`, `llm_tools.py:66`). The LLM calling `conclude` is the *only* structured exit — no free-form "I'm done."
- Dispatches tool calls through `dispatch`, feeds results back, and **rolls child cost up** into your `CommissionResult.cost` (`llm_tools.py:198-202`, `312`; folded in `contract.py:514-517`).
- Stops on: `conclude`, budget exceeded, `max_iterations`, cancellation, or the LLM returning no tool call.

**Commission-as-tool:** any commission placed in another commission's `toolbox` is exposed to that LLM via `as_llm_tool`, which uses your `description` **verbatim**. So `description` is a selection prompt — write it for the calling model (see §11). Worked recursive example: `commissions/deep_research/`.

---

## 9. Models & cost

- **Default model seam:** `DEFAULT_MODEL` (`src/vibrantine/models.py:70`) — every commission uses it unless its caller passes `model=`. Never hardcode a model in a commission body; route through this seam.
- **Known-models table:** `KNOWN_MODELS` (`models.py`) maps an identifier to a `Model` — identity (`id`), endpoint (`base_url`, `api_key_env`), and facts (`context_window`, `input_usd_per_million`, `output_usd_per_million`, all nullable). Pass a bare string and it resolves through the table (`resolve()`); for a model the table doesn't catalogue, construct a `Model` directly or use a factory — `openai_compatible(name, address, …)` for any OpenAI-format endpoint (private deployment, self-hosted gateway), or `ollama(id, …)` for a local Ollama server. Both are conveniences over the `Model` constructor; building models is a caller concern, so an orchestration layer can supply its own factory just as easily. Unknown identifiers are allowed — you get a `None` context window and **`None` pricing, which means *unpriced*, distinct from a genuinely free local model priced at `$0`**. An unpriced model under-reports tree cost; register it for accurate accounting, or pass an explicit `max_input_tokens`.
- Default endpoint is OpenRouter (`contract.py:548`), accessed through the `openai` SDK with `base_url` swapped.

---

## 10. Persistence (optional)

- **Protocol:** `PersistenceBackend` (`contract.py:219-234`) — `store` / `load` / `list_references` / `delete` / `delete_older_than`. Your repo can supply any implementation (KV, SQLite, in-memory).
- **Default:** `FilesystemBackend(root, *, dev_ring_buffer_size=100, on_failure_retention_days=7)` (`src/vibrantine/persistence.py:24`) — one JSON file per run, mode-aware pruning.
- **Record shape:** `PersistedRecord` (`contract.py:188`) — input, full result, ctx snapshot, optional `llm_trace`.
- **Modes:** `PersistenceMode` (`contract.py:60`): `off` / `on_failure` / `dev` / `always`. Wire a backend via `run_one(..., backend=...)`; children inherit it automatically.

---

## 11. Authoring discipline (from `AGENTS.md`)

Rules that keep a commission well-formed and portable:

- **Commission vs. tool** (`AGENTS.md § Commission vs. tool`): a commission has an LLM call somewhere in its subtree; a tool has none. Both wear the same `Commission` jacket — the distinction is discipline, not a separate type. Tools use `max_input_tokens=None` and no `model` arg ("Shape A").
- **Description prose** (`AGENTS.md § Description prose: write for the LLM by default`): write `description` as LLM-facing selection prose by default (opening + when-to-call + return-shape; edge/recovery only when caller-actionable). It becomes a tool descriptor the moment the commission is wrapped into a toolbox.
- **Schema discipline** (`AGENTS.md § Schema discipline`): every Pydantic `Field` has a populated `description=`; nesting depth ≤ 3; ≤ 20 fields per type. These exist for cross-provider portability of typed outputs.
- **Vocabulary-append rule** (`AGENTS.md`): the closed `Literal` sets are frozen; changing a member is a major bump.
- **Tool-result discipline** (`AGENTS.md § Tool result discipline`): unbounded tool results must be truncatable *and* resumable, with the resumption path described in the tool's prose.

---

## 12. Minimal worked example

A complete basic commission an external repo could write today:

```python
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine import Commission, CallContext, invoke_sync
from vibrantine.tools import ReadTool


class AskInput(BaseModel):
    question: str = Field(description="The question to answer about the file.")
    file_path: Path = Field(description="Absolute path to the file to consult.")


class AskOutput(BaseModel):
    answer: str = Field(description="Natural-language answer to the question.")


class AskCommission(Commission[AskInput, AskOutput]):
    name: ClassVar[str] = "ask"
    description: ClassVar[str] = (
        "Answer a question about a single file by reading its contents.\n"
        "Usage: call with a file_path and a question about that one file.\n"
        "Returns an `answer` grounded in the file's contents."
    )
    input_type: ClassVar[type] = AskInput
    output_type: ClassVar[type] = AskOutput
    system_prompt: ClassVar[str | None] = (
        "You answer questions about a single file. Use the `read` tool to load "
        "it (paginate if truncated), then call `conclude` with one `answer`."
    )
    toolbox = (ReadTool(),)

    def build_user_message(self, input: AskInput, ctx: CallContext) -> str:
        return f"File path: {input.file_path}\nQuestion: {input.question}"


# Run it (needs OPENROUTER_API_KEY, or inject client= for tests):
result = invoke_sync(
    AskCommission(),
    AskInput(question="What does this module do?", file_path=Path("/abs/file.py")),
    budget_usd=0.10,
)
if result.status == "success":
    print(result.output.answer)
else:
    print(result.error.kind, "-", result.error.detail)
```

For a **custom** commission, override `invoke` instead of `build_user_message` and return a `CommissionResult` yourself — both the success and the failure path. The success path is the one the framework can't build for you: you construct the `Provenance` and `CostMetrics` by hand (note `Provenance` has no defaults — `fetched_at` is required), use `self._fail(...)` for errors, check `ctx.cancel.is_cancelled` at breakpoints, and report `cost`. Follow the invariants in `docs/composition.md § The Commission base class`; worked example: `commissions/synthesize.py`.

```python
from datetime import UTC, datetime
from vibrantine import Commission, CommissionResult, CallContext, Provenance

class MyCommission(Commission[MyInput, MyOutput]):
    # ... identity ClassVars, max_input_tokens = None for a tool-shaped body ...
    async def invoke(self, input: MyInput, ctx: CallContext) -> CommissionResult[MyOutput]:
        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled", "Cancelled before work began", retryable=False,
                provenance=Provenance(source=self.name, fetched_at=datetime.now(UTC),
                                      confidence="grounded"),
                cost=self._cost(0, 0),
            )
        # ... do the work ...
        return CommissionResult(
            status="success",
            output=MyOutput(...),
            provenance=Provenance(source=self.name, fetched_at=datetime.now(UTC),
                                  confidence="grounded"),
            cost=self._cost(in_tokens, out_tokens),  # or CostMetrics(estimated_usd=...) for a pure tool
        )
```

---

## Reference index

| Concern | File | Symbol(s) |
|---|---|---|
| Public boundary / `__all__` | `src/vibrantine/__init__.py:50-86` | the 27 names + the in/out rule (`:1-17`) |
| Commission base | `src/vibrantine/contract.py:311` | `Commission`, `__init_subclass__:347`, `__init__:388`, `build_user_message:432`, `invoke:450`, helpers `538-628` |
| Result envelope + types | `src/vibrantine/contract.py` | `CommissionResult:110`, `ErrorState:88`, `Provenance:72`, `Claim:98`, `CostMetrics:82`, vocabularies `49-66` |
| Runtime context | `src/vibrantine/contract.py:238` | `CallContext`, `CapabilitySet:142`, `CancelToken:161`, `ProgressEvent:177` |
| Message parts | `src/vibrantine/contract.py` | `TextPart:266`, `ImagePart:273`, `ContentPart:289` |
| Entry points | `src/vibrantine/orchestrator.py` | `run_one:21`, `invoke_sync:40` |
| Dispatch + overflow | `src/vibrantine/dispatch.py` | `dispatch:48`, `_apply_overflow_policy:182` |
| LLM loop | `src/vibrantine/llm_tools.py` | `as_llm_tool:54`, `make_conclude_tool:66`, `run_llm_loop:105` |
| Models & cost | `src/vibrantine/models.py` | `Model`, `KNOWN_MODELS`, `DEFAULT_MODEL`, `resolve()`, `openai_compatible()`, `ollama()` |
| Persistence | `src/vibrantine/persistence.py:24` | `FilesystemBackend`; `PersistedRecord` (`contract.py:188`), `PersistenceBackend` (`contract.py:219`) |
| Tools layer | `src/vibrantine/tools/__init__.py:30-67` | 11 tools + their I/O models |
| Authoring rules | `AGENTS.md` | Commission-vs-tool, Description prose, Schema discipline, Tool-result discipline |
| Design rationale | `docs/composition.md` | The Commission base class, Three types, Output discipline, Cost rollup |
