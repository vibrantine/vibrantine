# Changelog

All notable changes to Vibrantine are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The versioned surface is the
public contract exported from `vibrantine.__all__`, plus the supported side
doors its boundary docstring names (such as `vibrantine.testing`).

## [Unreleased]

### Added

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

### Changed

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
