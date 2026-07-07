# Changelog

All notable changes to Vibrantine are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The versioned surface is the
public contract exported from `vibrantine.__all__`, plus the supported side
doors its boundary docstring names (such as `vibrantine.testing`).

## [Unreleased]

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
  to `partial` with the full output preserved — never silent.
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

[Unreleased]: https://github.com/vibrantine/vibrantine/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vibrantine/vibrantine/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vibrantine/vibrantine/releases/tag/v0.1.0
