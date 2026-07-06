# Changelog

All notable changes to Vibrantine are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The versioned surface is the
public contract exported from `vibrantine.__all__`, plus the supported side
doors its boundary docstring names (such as `vibrantine.testing`).

## [Unreleased]

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

[Unreleased]: https://github.com/vibrantine/vibrantine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vibrantine/vibrantine/releases/tag/v0.1.0
