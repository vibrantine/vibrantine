# Vibrantine Commissions

## Vision

A component model for AI agents. Each Commission is a typed, contracted, isolated unit of LLM-driven work: independently authored, independently testable, composable through declared interfaces rather than emergent coordination. LangGraph treats agents as nodes in a shared-state graph; CrewAI treats them as members of a natural-language collaboration; Vibrantine treats them as **components**.

The thesis: the right primitive for AI work is a bounded, contracted, isolated unit, and everything else (persistence, autonomy, conversation, scheduling, cost ledgers, user surfaces) composes above that primitive without leaking back in.

Read `docs/design.md` for what Vibrantine is, why, and how the pieces fit together. `docs/README.md` indexes the rest of the docs directory.

## Library scope

A publishable library, not an application layer. The boundary and its rationale live in [`docs/design.md § What the library refuses to do`](docs/design.md). The operational rule for contributors: coordination layers beyond parent-mediated composition, user-facing surfaces (conversation, notifications), cross-invocation state, scheduling/initiative, tier-routing policy, and cost-ledger UI are **application concerns: keep them out of the library.**

## The contract is sacred

Structural invariants. Breaking one is an architectural decision, not a quick fix.

- **One interior hook**: `async _run(input, ctx) -> CommissionResult`, called only by `dispatch`; invoke through `run_one` / `invoke_sync` / `dispatch`. Extend `CallContext` for new orchestration concerns, never the `_run` signature.
- **Errors are values.** No exception crosses the call boundary. Failures return `CommissionResult(status="failure"|"partial", error=ErrorState(...))`.
- **Tree-structured invocations.** A Commission only waits on its own children. No peer messaging, shared state, or back-channels.
- **Cost and provenance are first-class.** Every result carries `CostMetrics` and `Provenance`. Costs roll up structurally on both the Python-coordinator and LLM-loop paths (see `design.md § Cost and provenance are structural`).
- **Stateless across invocations.** No cross-invocation memory. The persistence layer stores run *records* for inspection; resumable state stays above the library.
- **Prompts are internal.** Each Commission owns its system prompt; callers choose *which* Commission to invoke.

`ErrorState.kind` SSOT: `"validation" | "internal" | "rate_limit" | "timeout" | "budget_exceeded" | "cancelled" | "output_too_large" | "run_halted"`. Add a kind only when it represents a structurally distinct caller decision. `run_halted` is spoken only by the root result when a run fuse tripped; node-level allocation exhaustion stays `budget_exceeded` (the line between them is scope, not resource type).

**Vocabulary-append rule.** The closed `Literal` vocabularies (`ErrorKind`, `CommissionStatus`, `ConfidenceLevel`, `PersistenceMode`, `OverflowPolicy`) are part of the frozen contract. Adding or removing a member is a **major version bump**: downstream `match`/dispatch code is written against the exact set. `tests/test_contract.py` locks each vocabulary to its documented members, so a change can't land without updating the lock test (and, by that signal, the docs and the version).

## Surface vs. interior: minimize the boundary mercilessly

Complexity is judged at the public boundary, not in the interior. A Commission's interior can be forty careful lines where five sloppy ones would do; that is never a contract question. But every name in `vibrantine.__all__`, every `Commission.__init__` kwarg, and every `CallContext` field is permanent user-facing cognitive load and a SemVer commitment. The default answer to any new surface element is **no**.

- A surface addition needs a real, named consumer and a statement of what the user must now hold in their head. Convenience, symmetry, and anticipation do not qualify. When a fix can be built as interior complexity or as new surface, the interior wins every time (worked examples: the shell tool's encoding fallback and the sample tool's streaming pass each added real interior code and zero surface).
- The three surfaces above are pinned by exact lock tests in `tests/test_public_api.py`. Growing one fails the suite until the lock is edited in the same commit; that failure is the moment to justify the growth, not an obstacle to silence. Same mechanism as the vocabulary-append rule.
- This guards against a known failure mode of LLM-driven development specifically: each added knob, field, or helper reads as simple in isolation, and the sum is an unholdable surface. If you are an agent reading this while about to add one: the burden of proof is on the addition.

See `docs/design.md § The public surface is minimized mercilessly` for the decision record.

## Commission vs. tool

When the question is "should this be a Commission?", apply the LLM-anywhere rule:

> A **Commission** has an LLM call somewhere in its subtree. A **Tool** has none. If the entire subtree can be done deterministically, including truncation, summarization heuristics, or wrapping a primitive (shell exec, file write, HTTP GET), it's a Tool, not a Commission. A deterministic Python coordinator with LLM-bearing children is still a Commission; the LLM is in the subtree.

Shared tools live in `src/vibrantine/tools/`; private deterministic tools
owned by a folder-sized Commission may live under that Commission's
`tools/` slot. Both subclass `Commission[InputT, OutputT]` the same way
(`max_input_tokens=None`, no model arg): identical contract, no LLM
anywhere in the subtree. See
[`docs/design.md § Three categories, no fourth`](docs/design.md) for
the rationale and the Commission / Tool / application-code relationship.

## Description prose: write for the LLM by default

A `description: ClassVar[str]` is reused **verbatim** by `as_llm_tool` (`src/vibrantine/llm_tools.py`) as the OpenAI tool descriptor whenever a Commission or Tool is wrapped into another LLM-loop's toolbox. Since composability is the point (any Commission can be wired into a toolbox), **write every description as LLM-facing selection prose by default**, not as a human-facing label.

Follow the same five-element pattern as mature agent-harness tool prose (opening sentence, when to use, input semantics, return shape, edge/recovery guidance), taking the elements that change the *caller's* decision:

- **Required:** opening sentence (what it does), when-to-call (usage), return-shape (what comes back).
- **Edge cases / recovery guidance:** only when *caller-actionable*. A Commission's failures already return as a structured `CommissionResult` error envelope (rendered to the calling LLM by `_render_tool_result`), so recovery prose is usually redundant. This is the one place a Commission legitimately carries less than a raw tool, which returns rawer results.

A one-sentence description is the exception, justified only when a Commission genuinely can never be LLM-called. `RecursiveResearchCommission` is the worked example: opening + `Usage:` bullets + return-shape inline, no edge/recovery because none is caller-actionable.

## CallContext fields: what's load-bearing, what's not

The full surface is on `CallContext`, but not every field changes behavior today. Authors should know which are enforced and which are stubs:

- **`budget_usd`**: enforced by the default LLM loop (`run_llm_loop` halts after a turn with `kind="budget_exceeded"`) and by Synthesize's custom two-pass invoke (pre-flight estimate + post-call actuals). Information-only for tools like Fetch (HTTP costs $0). Coordinators allocate slices through it. The loop allocates too: each child it dispatches receives the remaining budget (grant minus spend so far), never a full copy of the grant. Enforcement needs a priced model: if a budget is set but the model isn't in `KNOWN_MODELS` (so cost can't be computed), the default loop and Synthesize fail fast with `kind="validation"` rather than running with a silently unenforced budget; register the model or invoke without a budget.
  A paid provider must also return token usage: if it omits usage on a dollar-accounted call (a node grant or the run's spend fuse is armed, including a grant-stripped subtree under a budgeted run), that call fails instead of being counted as free. Explicit `0.0` prices remain the supported free/local-model case.
- **`cancel`**: enforced everywhere. Commissions check `ctx.cancel.is_cancelled` at natural breakpoints and return `kind="cancelled"`.
- **`on_progress`**: emitted by Synthesize (`synthesis_pass`, `structured_pass`), NewsDigest (`fetching`, `synthesizing`), and MorningBriefing (`sections`, `executive_summary`, `written`). Coordinators forward `on_progress` to their children, so worker events bubble up under the original callback.
- **`capabilities`**: allow-list of tool names, enforced by `run_llm_loop` (not by Commission bodies): the LLM's tool menu is the intersection of the Commission's toolbox and this set. `None` = unrestricted (root default); a set permits exactly those names, empty set permitting nothing. Python coordinators' hardcoded `dispatch` calls are ungated by design.

There is no per-context concurrency field: provider-call concurrency is a run-wide bound (the Gatekeeper's room, `run_one(concurrency=)`), held around each provider call and shared by the whole tree. Coordinators may `asyncio.gather` freely; their LLM children queue at the room, not at the coordinator.

New Commissions should consult the enforced fields (`budget_usd`, `cancel`, `on_progress`) at their natural breakpoints; `capabilities` is enforced automatically by `run_llm_loop`. `cancel` is also the run's breaker: a fuse trip (`run_one`'s `max_llm_calls` / `time_limit_seconds` / `budget_usd`) flips the same signal, so checking it is how a Commission participates in run teardown.

## Schema discipline (Pydantic types)

Enforce from line one; these exist for cross-provider portability of typed outputs:

- Every `Field` has a populated `description=...`. No bare fields.
- Nesting depth ≤ 3 in any payload type.
- ≤ 20 fields per type.
- No recursive schemas.
- `Literal[...]` for all enum-shaped fields (status, kind, confidence). Never bare strings.

## Prompt discipline

System/user split matters from day one for prompt caching:

- **System message**: stable per Commission class. Cached across all invocations.
- **User message**: per-call input formatted from the typed `InputT`.

Retrofitting cache discipline later is painful.

## Tool result discipline

Tools whose result size can grow unboundedly must be **truncatable but resumable**, never lossy. A caller (typically an LLM) hitting a result bound should be able to fetch more, not be left guessing what was discarded.

- **Bound the output.** Every potentially-unbounded result needs a default cap (lines, matches, entries). Don't return arbitrary-size data and hope the consumer handles it.
- **Signal truncation explicitly.** A `truncated: bool` on the output, plus enough metadata (`total_lines`, equivalent counts) for the caller to know what's beyond the slice.
- **Provide a resumption mechanism.** Pagination via `offset` + `limit` is the canonical shape; see `ReadTool`. Where pagination doesn't fit cleanly (e.g. recursive match streams), document the alternative (narrowing the input, raising the cap) in the tool's description prose, and treat the absence as design debt rather than a permanent answer.
- **Describe the resumption path in the LLM-facing prose.** The tool's `description: ClassVar[str]` must tell the calling LLM what `truncated=True` means and how to get more. Without it, an LLM treats truncation as "the rest doesn't exist."

The principle: large tool results are *cursors over data*, not whole-data snapshots. Truncated state is recoverable; binned state is gone.

`ReadTool` is the canonical implementation. `GrepTool` enforces bounds but is the known asymmetric case: truncatable but not yet resumable, a consciously parked gap.

## Stack

- **Python**: 3.12
- **Packaging**: `uv` (sync, lock, run)
- **Validation**: `pydantic >= 2`
- **HTTP**: `httpx` (async)
- **Tests**: `pytest`, `pytest-asyncio`
- **LLM**: any OpenAI-compatible endpoint via the `openai` SDK with `base_url` swapped. A `Model` bundles identity, endpoint, and pricing facts (`models.py`); `ollama()` targets a local server and `openai_compatible()` covers any other provider. The run's models are defined once, in `run_one(models=[...])` (the catalog, which vends the clients); LLM-using Commissions accept `model` as a constructor argument, a pure catalog name, and are immutable post-construction. Unknown names fail fast. `max_input_tokens` derives at run time from the catalog entry's context window; `target_input_fraction` defaults to 0.75.
- **Lint + format**: `ruff` (replaces black, isort, flake8)
- **Types**: `basedpyright` in strict mode

## API keys

`OPENROUTER_API_KEY` is the only secret. Stored in `.env` (gitignored). A committed `.env.example` lists required variable names with empty values as the public template.

- **Library**: the run's catalog vends the `AsyncOpenAI` clients lazily at the first provider call, one per distinct endpoint, reading the entry's `api_key_env` (default `OPENROUTER_API_KEY`) from the environment at that point; a keyless `Model` (`api_key_env=None`, e.g. local Ollama) skips the check entirely. Tests register `vibrantine.testing.scripted_model(...)` entries in `run_one(models=[...])` instead. There is no `api_key` argument anywhere, and a missing key surfaces at the first provider call with the env var named, not at construction. The library never reads `.env` itself; that's a dev/application concern.
- **Dev + tests**: `uv run --env-file .env <cmd>`, or export in your shell. Both pick it up the same way.
- **Test policy**: unit tests mock the OpenAI client and require no key. Integration tests are marked `@pytest.mark.integration` and skip when the key is absent. Never commit fixtures containing real API responses with embedded keys.
  LLM-driven Commissions should also grow heuristic evaluation cases with
  explicit success and failure criteria once they are more than mechanical
  contract probes; see `docs/commission-testing.md`.

## Commands

```
uv sync                         # install + lock
uv run pytest                   # run tests
uv run pytest -xvs path::test   # one test, fail-fast, verbose
uv run ruff check .             # lint
uv run ruff format .            # format
uv run basedpyright             # type-check
```

## Project layout

```
src/
  vibrantine/
    __init__.py                       # public boundary: __all__ is the SemVer surface
    contract.py                       # core contract types, the Commission ABC, PersistenceBackend Protocol
    orchestrator.py                   # run_one + invoke_sync entry points
    dispatch.py                       # wraps _run: run_id + parent_run_id + overflow + record + raise backstop
    llm_tools.py                      # LLM-tool wrapper + the default LLM loop
    factory.py                        # create_commission authoring factory
    models.py                         # Model objects (identity + endpoint + pricing); KNOWN_MODELS + DEFAULT_MODEL
    persistence.py                    # shipped backends: FilesystemBackend + SqliteBackend
    testing.py                        # supported test doubles for the client= seam
    examples/                         # worked example Commissions (importable, provisional)
      ask.py summarize.py synthesize.py
      email_handler.py                # provisional validator (LLM-loop routing probe)
      morning_briefing/               # heterogeneous coordinator tree (folder standard)
      recursive_research/             # recursive LLM-loop worked example
      learning_ladder/                # four runnable rungs, one idea each
      demo/                           # python -m vibrantine.examples: menu + chat runner
    tools/                            # std-lib deterministic tools (provisional)
      _helpers.py                     # shared provenance + failure builders
      read.py write.py edit.py glob.py list_dir.py fetch.py
      grep.py sample.py move.py delete.py shell.py
tests/
```

`src/` layout (PyPA convention): the package can only be imported via an installed entry, not by accident from the repo root. Editable installs (`uv sync`) wire this up automatically.

Application-specific result types live alongside the Commission or function that produces them, not in `contract.py`. `contract.py` is reserved for the core contract.

## Principles

1. **Smallest viable thing.** Fewer files, fewer abstractions, fewer parameters, fewer dependencies. The default answer to "should we add a layer here?" is no. The contract has to be holdable in one head; nothing the library does should require more. The same test gates the SSOT, not just the code: before a coined concept enters the docs, check it reduces to the contract's two-sentence core (one typed function with an LLM inside and one result envelope out; the parent as the only path between children). A named concept the working code doesn't need is conversational sediment, not architecture.

2. **Names and types do the explaining.** A well-named function with a typed signature documents itself. Don't write docstrings that paraphrase the signature. Reserve prose for what names and types can't express.

3. **One-line WHY at every public surface.** Module file opens with a sentence on why this file exists in the architecture, not a table of contents. Public class or function: one line on the motivation or constraint that brought it into being. Private helpers and obvious internals: skip unless surprising.

4. **Optimize for the inexperienced reader.** If a beginner has to pause and parse, rewrite longhand. No nested comprehensions doing real work, no metaclass tricks, no decorator stacks. Idioms like walrus and pattern matching are fine when they're the clearest form, not when they're flexing.

5. **Refactor over patch.** When the spec evolves, edit affected code coherently. No compat shims (no released users), no feature flags for half-built behavior, no "old code, remove later" blocks. Clean refactor is always cheaper than the patch debt.

6. **Minimum dependencies.** New dependencies (runtime or dev) need an explicit case tied to a concrete unmet need. Reach order: standard library, then what's already in the stack, then consider adding. Default to refusing. Re-evaluate periodically: a dependency that earned its slot early in the build may be redundant later, and the refactor cost beats carrying the drag.

**Style:** American spelling throughout; it matches Python's identifier convention (`SynthesizeCommission`), so no prose/code split to maintain.

## Deferred work

What is deliberately not built, each item with the trigger that builds it, lives in [`docs/design.md § Not built yet`](docs/design.md). Don't start an item on that list before its trigger arrives, and don't add machinery the list doesn't name without recording the decision there first. Anything above the library boundary stays out permanently (see § Library scope).

## Git commits

Conventional Commits. Plain factual messages: no emojis, no AI attribution (no `Co-Authored-By: Claude`, no "Generated with Claude Code" trailers, no mention of the assistant). State what changed and, when non-obvious, why.

- **Subject**: `type(scope): subject`, imperative mood, ≤ 72 chars. Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `build`.
- **Body** (optional): wrap at 72 chars. Skip when the subject is self-explanatory. When present, explain motivation or constraint, not a paraphrase of the diff.
Example:

```
feat(types): add Commission and CommissionStatus models

Introduce the Pydantic models that downstream phases will
consume. Status is a string enum to keep JSON output stable
across OpenRouter calls.
```

## Further reading

Three documents cover Vibrantine, and each has one job:

- `README.md` (root): the source of truth: what Vibrantine is, what ships today, and why you would use it.
- `docs/design.md`: the design record: the goal and the two-sentence core, every settled decision with its reason and what it rules out, what the library refuses to do, the trades, what is not built yet, and the thesis.
- `docs/authoring.md`: the builder's manual, machine-checked in CI.

[`docs/README.md`](docs/README.md) indexes everything else: the testing standard, the release checklist, and the working notes under `docs/working/`. Process records and retired drafts live outside the repo.
