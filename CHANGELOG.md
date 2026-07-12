# Changelog

All notable changes to Vibrantine are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The versioned surface is the
public contract exported from `vibrantine.__all__`, plus the supported side
doors its boundary docstring names (such as `vibrantine.testing`).

## [Unreleased]

### Added

- **The dispatch register**: every invocation that crosses the contract
  boundary (top-level, a coordinator's children, the LLM loop's tool
  calls) leaves one always-on metadata row at the dispatch seam: run_id,
  parent_run_id, Commission name, the node's self-declared
  `deterministic` flag, started/ended timestamps, and status (the
  envelope vocabulary plus `refused`). The run's complete node ledger,
  built for prompt-injection forensics: rows are metadata only and join
  the run records by run_id for verbatim content. Delivered live via
  `run_commission(on_dispatch=...)` (mirroring `on_llm_call`), and persisted at
  run end in one write (the root's own row included) to a new
  `dispatches` table beside `calls` through an optional duck-typed
  `store_dispatches`; rows share the call log's lifecycle, dying with the
  run's root record or by age. `Commission.deterministic` is a new
  ClassVar (default False) a tool author sets True in the class body to
  say "no LLM anywhere in my interior"; it is the node's own unverified
  word, recorded for filtering, and no framework behavior ever branches
  on it. All eleven shipped tools declare it.

- **Model profiles**: a catalog entry is now the one place a model
  configuration is done right. `Model` gains `name` (the catalog key and
  what a Commission references, defaulting to `id`) and `params` (provider
  call settings such as temperature or reasoning toggles, merged verbatim
  into every `chat.completions.create` the profile serves, in the default
  loop and in Synthesize's passes). Two entries may share a wire `id`
  under different names, so one model can play several roles ("fast-cheap",
  "fast-hot") differing only in settings; duplicate *names* stay the
  config error. Params are raw by design (the provider validates its own
  knobs), except the keys the framework owns (`model`, `messages`,
  `tools`, `tool_choice`, `response_format`), which the catalog refuses at
  the front door. `openai_compatible()`, `ollama()`, and
  `testing.scripted_model()` pass `name=` / `params=` through. Call-log
  rows gain `model_name` (the profile that made the call) beside `model`
  (the wire id), in `on_llm_call` dicts and the SQLite `calls` table.

- **The Run Gatekeeper**: every run gets one internal control object,
  created by `run_commission`, standing at the provider seam as `dispatch`'s
  mirror. It holds three resource fuses (an LLM-call backstop, default-on
  at 1,000; an opt-in `time_limit_seconds` checked at both seams; a spend
  fuse armed by `budget_usd` at the same number as the root grant), a
  tree-wide concurrency room (`concurrency=`, default 16, held around the
  provider call only, deadlock-free even at 1), an immutable name-based
  tool-exposure ceiling (`tool_ceiling=`, clamping every menu in the tree
  to toolbox ∩ branch grant ∩ ceiling), and an always-on in-memory
  provider-call log. A fuse trip flips the run's one stop signal (the
  same `CallContext.cancel` every checkpoint already honors), refuses new
  provider calls, lets in-flight calls finish and count, and surfaces at
  the root as the new `run_halted` `ErrorKind` with the fuse and numbers
  named and all provider-reported spend in the cost field. The rewrite is causal and
  runs before the root record is persisted: only a failure that descended
  from the trip is claimed, a root that still concluded keeps its result,
  an unrelated failure keeps its own error, and the stored record always
  matches the returned envelope. Causality is a stamp the framework sets
  where it translates a refusal (or a breaker-caused checkpoint exit)
  into an error value, riding the error object up the tree; it is never
  inferred from the failure's kind or text, so a coincidental trip cannot
  mask a root's own cancellation story, and a coordinator that propagates
  a child's refusal unchanged keeps the claim. Node-level allocation
  exhaustion stays `budget_exceeded`: the line between the kinds is
  scope, not resource type.
- **The run model catalog**: `run_commission(models=[...],
  default_model=...)`
  defines the run's models once; every Commission references an entry by
  name or takes the run default, unknown names fail fast, an empty
  catalog auto-registers the system default, and the catalog builds and
  vends the provider clients (one per distinct endpoint), so the
  framework owns provider access by construction.
- **The call-log accessor**: `run_commission(on_llm_call=...)` receives one
  plain dict per settled or refused provider call (caller, model,
  timestamps, tokens, cost, the node's grant, fuse state, how it ended).
  With a `SqliteBackend` wired, the rows also land at run end in a new
  `calls` table beside `records`, joined on run id (absorb, not
  replace), through an optional duck-typed `store_calls` the
  `PersistenceBackend` Protocol does not require.
- `run_commission` and `run_commission_sync` also gain `capabilities`,
  `cancel`, and `on_progress`, absorbing everything the hand-built-context
  entry used to provide.
- `vibrantine.testing.scripted_model(fake)`: a catalog entry whose
  provider is a `ScriptedLLM`, defaulting to `FIXTURE_MODEL`'s id and
  pricing so cost assertions keep their dollars. The testing seam now
  rides the same client-vending door as production.
- `Model.cost_usd(in_tokens, out_tokens)`: the one pricing formula in the
  library. Node cost rollups, the run's observed spend, and the budget
  gates all price through it, so their numbers can never drift apart; an
  unpriced side rates as $0 (gate on `is_priced` before trusting it for
  enforcement).
- `AudioPart` joins the `ContentPart` union: base64 `data` plus a `format`
  tag (`"wav"` or `"mp3"`), conforming to the provider's `input_audio`
  content-part shape. Exported from `vibrantine.__all__`; provisional in
  its fields, the same asterisk as `ImagePart`.
- Image and audio input are now verified capabilities rather than
  plumbed-but-unproven. Live probes against the default model confirmed:
  images work as both `data:` URIs and https URLs; audio works as WAV
  through the `input_audio` shape; media is billed as ordinary input
  tokens, which the existing post-turn `CostMetrics` accounting captures
  unchanged; and a model lacking the modality fails as a clear structured
  provider error ("No endpoints found that support image input") with
  nothing spent. `ImagePart`'s single `image_url` field survived its first
  consumer unchanged and keeps its provisional asterisk.
- authoring.md Part II gains a multimodal-input section: the parts
  vocabulary, the URL vs `data:` URI choice, additive union widening, and
  the text-only size-gate / budget-floor posture. The Part III
  one-import-line block now mirrors `vibrantine.__all__` exactly, and a
  contract test parses the doc to keep it that way.

### Fixed

- Dollar-accounted paid calls now fail when a provider omits token usage
  (or the entry carries no prices) instead of silently settling at $0.
  Dollar-accounted means a node grant or the run's spend fuse is armed,
  so a grant-stripped subtree under a budgeted run can no longer starve
  the fuse's accounting; explicitly free models retain valid `0.0`
  pricing, and any model that omits usage now draws a warning (free
  models' token counts previously flatlined silently). Run limits and
  catalog prices also reject negative or non-finite values before they
  can disable accounting.
- SQLite call rows now belong to the run, not the node: they are removed
  when the run's root record is deleted or pruned, or by age when their
  run never stored a root record. Deleting or ring-buffer-pruning a child
  record no longer punches holes in a retained run's audit log, a
  retained record keeps its whole call log across `delete_older_than`,
  and the calls table no longer grows unboundedly under `on_failure`
  recording. `on_llm_call` receives a copy so an observer cannot mutate
  the Gatekeeper's persisted audit row.
- A run that halts before any provider call was made now says so in the
  `run_halted` detail instead of advising the caller to wire a backend
  they may already have wired.
- A `_run` that raised after making governed provider calls (a bubbled
  halt refusal, or any exception dispatch converts to a failure) now
  reports a best-effort cost floor summed from the node's own settled
  calls in the run log, instead of $0. Children's envelopes unwind with
  the stack, so the floor excludes their spend; the run log still holds
  every row.
- The budget documentation overstated `budget_usd` as a "hard ceiling".
  Enforcement is per-turn and a turn's exact cost is unknowable before it
  runs, so the true spend can overshoot the grant by up to about one
  turn's cost per tree level (observed live: $0.1032 spent of a $0.1000
  grant across a three-level tree). The docs and docstrings now state
  that bound; the result envelope always reported the true spend and is
  unchanged.

### Changed

- **Breaking:** the entry points are renamed: `run_one` becomes
  `run_commission` and `invoke_sync` becomes `run_commission_sync`. Verb
  plus object says what the door does (run this Commission as the root of
  a new governed run), and the sync twin now derives from the async name,
  so learning one name gives both. Migration: rename call sites; keywords,
  return values, and behavior are unchanged.
- **Breaking (behavior): stop means stop.** After a run fuse trips,
  `dispatch` refuses to start new invocations: the refused call returns
  an ordinary breaker-stamped failure value (kind `cancelled`, so the
  root's causal `run_halted` rewrite claims it unchanged) and a `refused`
  register row records what never started. Previously a post-trip child
  ran until its own cancellation checkpoint or next provider call, so
  non-LLM work (file writes, fetches) could continue after a halt; now
  the stop is structural at the seam, whatever the interior looks like.
  In-flight work still finishes and is counted, loop wind-down still
  concludes (`conclude` is loop-internal, not a dispatch), and a
  coordinator's own Python after a refused dispatch keeps running: the
  structured exit is values, not a kill.
- **Breaking:** `KNOWN_MODELS` is retired and `DEFAULT_MODEL` is now the
  default profile *object* (a full `Model`) rather than an id string. The
  dict had shrunk to a one-entry vestige whose only job was feeding the
  empty-catalog default; that entry and the id constant were two
  definitions of one fact, now folded into the single owned seam a future
  config-loaded default routes through. Callers using `DEFAULT_MODEL` as a
  string read `DEFAULT_MODEL.name` (or `.id`). `openai_compatible()`'s
  first parameter is renamed `name` → `id` to match `Model` and
  `ollama()` now that `name` means the profile name; positional callers
  are unaffected.
- **Breaking:** `run_commission` is the only way into a run and `dispatch`
  the only way around inside one, each refusing the other's job: nested
  `run_commission` refuses ("you are inside a run; use dispatch"),
  `dispatch` outside a run refuses ("you are outside a run; use
  run_commission"), and `dispatch` refuses a context that does not carry
  the run in progress, so the run object can never be swapped mid-tree.
- **Breaking:** `Commission(client=...)` is removed; it was the
  raw-client escape sitting in the framework's own front door.
  `Commission(model=...)` narrows to a pure name (`str | None`) looked up
  in the run's catalog when the loop runs, `models.resolve()` and its
  silent bare-OpenRouter fallback retire, and an unset `max_input_tokens`
  now resolves from the catalog entry's context window at run time. As a
  consequence, `fits()` and the `max_input_tokens` property judge the
  explicit cap only: with no explicit cap, `fits()` always answers True
  and the property reads None, because the real gate now runs at run time
  against the catalog entry. A caller sizing work outside a run (an
  external pre-chunker) should assign `max_input_tokens` a number it owns;
  the property is assignable, exactly like the constructor argument.
- **Breaking:** the advisory `CallContext.concurrency` field (which
  nothing read) retires in favor of the run-wide room, and `run_halted`
  joins the frozen `ErrorKind` vocabulary.
- **Breaking:** a wired backend now records everything by default:
  `run_commission(backend=...)` with `record=` unset behaves as
  `record="always"`, because handing the run a database is the "I care
  about logs" signal and keeping less is the active choice. Without a
  backend nothing changes (nothing is recorded); a node's explicit
  `persistence_mode` still beats the default. Callers who wired a backend
  expecting records off must now pass `record="off"`.
- The default loop translates opening-message parts with one explicit
  branch per modality and rejects an opening message it must not send (a
  part it cannot translate, or an empty parts list, which providers refuse
  as an empty content array) as a structured `validation` failure before
  the provider is contacted, so nothing is spent; a recorded run still
  deposits its transcript on this exit path. Previously the translation's
  else branch assumed every non-text part was an image: a part carrying an
  `image_url` field was silently sent as one, and anything else crashed
  the run with a raw AttributeError.

## [0.5.0] - 2026-07-09

### Changed

- **Breaking:** the Commission override hook `invoke` is renamed `_run`.
  The old name was the one attribute that looked like the call API but
  silently skipped the framework wrapping (run_id stamping, overflow
  enforcement, records) when called directly; the leading underscore now
  marks the hook as the framework's to call. Migration: rename a custom
  Commission's `async def invoke` override to `async def _run`; the
  signature and contract are unchanged. Callers already routing through
  `run_one` / `invoke_sync` / `dispatch` are unaffected. A subclass still
  overriding `invoke` fails at class definition with a message naming the
  rename.

- **Breaking:** `create_commission`'s `tools=` keyword is renamed
  `toolbox=`, matching the `Commission` constructor and the class
  attribute. The two doors now use one word for the tools a Commission
  owns, kept distinct from `CapabilitySet.tools` (the allow-list of
  permitted tool *names*). Migration: rename `create_commission(...,
  tools=(...))` to `toolbox=(...)`; the value and behavior are unchanged.

### Added

- Definition-time agreement check between a Commission's generic
  parameters and its identity ClassVars: a subclass whose
  `Commission[InputT, OutputT]` parameters disagree with its
  `input_type` / `output_type` fails when the class is defined, instead
  of running with type checkers and the runtime seeing different
  contracts. TypeVars, `Any`, and unparameterized bases are skipped.

- `_succeed`, the success-envelope counterpart to `_fail` on the
  protected authoring tier: a custom `_run` builds its most common
  return with one call instead of hand-assembling a `CommissionResult`.
  The base loop's own success return rides it. Documented in
  authoring.md Part III alongside the other protected helpers.

### Fixed

- The default model and sole `KNOWN_MODELS` entry pointed at
  `google/gemini-3-flash-preview`, which OpenRouter no longer serves, so
  an out-of-the-box call failed with a model-not-found error. Repointed
  `DEFAULT_MODEL` and the catalog to `google/gemini-3.5-flash` (live;
  1,048,576-token context; $1.50 / $9.00 per million in/out). Model
  identifiers are catalog data, so this is not a major-version change; it
  does change the default's price, so a caller relying on the previous
  rates should pass an explicit `model=`.

## [0.4.0] - 2026-07-07

### Fixed

- RecursiveResearch's system prompt now shows a concrete example claim
  (an object with `value`, `source_urls`, `confidence`). Live runs on
  the default model repeatedly emitted claims as bare strings, and each
  failed `conclude` re-sends the whole transcript, which at tight
  budgets cost the run its graceful wind-down. With the example, a
  grant that previously died `budget_exceeded` completes with a full
  cited answer.

### Added

- Pre-turn budget gate in the default LLM loop: before each provider
  call, the loop prices a floor for the turn's input (message text and
  tool-call arguments through the `estimate_tokens` heuristic; images
  and tool schemas excluded so the estimate only undercounts) and
  declines the call with `budget_exceeded` when spend so far plus that
  floor already exceeds the grant. Previously the budget was only
  checked after a turn returned, so one more turn over a large
  transcript on an expensively priced model could overshoot the grant
  by whole dollars before enforcement fired. The post-turn check
  remains; unbudgeted runs are unaffected.

- Mid-run budget visibility for the default LLM loop: when `budget_usd`
  is set, a one-line `[budget]` status (spent, grant, remaining) follows
  each turn's tool results, using the same ledger the `budget_exceeded`
  hard stop checks. This lets a system prompt instruct a graceful
  wind-down (keep a wrap-up reserve, conclude with partial results)
  instead of the run being killed blind; RecursiveResearch's prompt now
  does exactly that. Unbudgeted runs see no new messages.

## [0.3.0] - 2026-07-07

### Fixed

- The default LLM loop no longer sends an empty system message for a
  Commission with no `system_prompt`; some providers reject empty system
  content, and it carried nothing.
- `GrepTool` no longer aborts a whole directory walk when a listed file
  vanishes before it is read (a race or broken entry); the file is
  skipped like any other unreadable entry, and a missing file still
  surfaces as `validation` on a direct path.
- `FetchTool` now validates the URL up front (absolute, http or https)
  and classifies non-2xx responses by the caller's retry decision: 429
  fails as `rate_limit` (retryable), 5xx as `internal` (retryable), and
  any other non-2xx as `validation` (non-retryable, the URL as given
  yields no document). Previously every non-2xx surfaced as `internal`
  and a malformed URL surfaced as a retryable transport error, against
  the pattern every sibling tool follows for caller mistakes.
- The default LLM loop now dispatches each child with the remaining budget
  (the grant minus everything already spent on own turns and prior
  children), never an unchanged copy of the caller's grant. Previously,
  children delegated between the loop's budget checks each inherited the
  full grant as their own ceiling, so a delegating tree could spend a
  multiple of its budget before enforcement fired.

### Added

- The `truncate_with_reference` overflow policy now does its real work
  (previously a stub that degraded to `partial`). When an output exceeds
  `max_output_tokens`, dispatch asks the Commission's new
  `truncate_output(output, max_tokens)` hook for a smaller, still-valid
  output, force-persists the full result under the run's `run_id` (record
  mode `always`), and returns the chopped output as `partial` with the
  run_id named in the error detail. Without a backend, without a hook
  override (the base declines), or on a failed store, the policy degrades
  to `partial` with the full output preserved, never silent.
  `RecursiveResearchCommission` is the first consumer: it implements the
  hook (keeping cited claims over answer prose) and now defaults to
  `truncate_with_reference`.
- `create_commission`: a deterministic authoring factory. Builds a basic
  LLM-loop Commission from the crafted decisions (name, description, typed
  input/output, tools); the system prompt, opening message, and all
  plumbing are manufactured. No LLM is involved in construction. Exported
  from the top-level `vibrantine` namespace.
- The learning ladder (`vibrantine.examples.learning_ladder`): four
  runnable rungs, each the previous plus one idea. One Commission, then a
  tool grant, then a nested child Commission, then budgets and recorded
  runs queried in plain SQL.

## [0.2.0] - 2026-07-07

### Added

- `SqliteBackend`: a second shipped `PersistenceBackend`, one row per run in
  a single SQLite file with plain columns as query handles and the full
  record as JSON. No query API on top; the database file is the query
  surface.
- `CostMetrics` gains optional `in_tokens` / `out_tokens`: raw token counts
  for the call's own LLM turns. Counts never roll up (dollars remain the
  rollup currency) and stay `None` when no LLM turn ran. Additive; existing
  consumers are unaffected.

## [0.1.0] - 2026-07-07

First tagged release: the first fixed point a consumer can pin.

### Added

- Core Commission contract: one typed input, one `CommissionResult` envelope,
  `CallContext` for runtime conditions. Errors are values, never exceptions.
- Entry points `run_one`, `invoke_sync`, and `dispatch`: run_id stamping,
  parent threading, overflow enforcement, recording.
- Default LLM loop with a synthetic `conclude` tool, budget enforcement, and
  capability gating; OpenRouter default endpoint with per-Commission model
  and client overrides (keyless endpoints supported).
- Deterministic tools for file, shell, fetch, search, and filesystem work.
- Cost and provenance on every result, with structural child-cost rollup.
- Persistence: the `PersistenceBackend` protocol, `FilesystemBackend`, and
  records carrying inputs, results, and full LLM transcripts. Recording
  switches on with `run_one(..., record="always")`; a node's explicit
  `persistence_mode` overrides the caller's default.
- Observability in three tiers: stdlib logging at framework choke points to
  watch, `on_progress` events to react, persisted records to query.
- Testing seam: `client=` injection plus `vibrantine.testing` with
  `ScriptedLLM`, `llm_response`, and `AlwaysCancelled`.
- Worked example Commissions under `vibrantine.examples` (Ask, Summarize,
  Synthesize, MorningBriefing, RecursiveResearch) and the interactive demo
  runner: `python -m vibrantine.examples`.

[0.5.0]: https://github.com/vibrantine/vibrantine/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/vibrantine/vibrantine/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/vibrantine/vibrantine/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/vibrantine/vibrantine/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vibrantine/vibrantine/releases/tag/v0.1.0
