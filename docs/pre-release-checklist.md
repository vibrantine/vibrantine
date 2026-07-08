# Pre-Release Checklist

This checklist is for the current public-reference cut: a repository that can
be shown, cloned, and imported from git, but is not yet a PyPI launch or a
stable framework promise.

The goal is not polish for its own sake. The goal is to make the repo safe to
show, clear about its maturity, and honest about which surfaces are stable,
provisional, or future work.

## Release Posture

Last pass: 2026-07-07.

- [x] Keep `pyproject.toml` at a pre-release version unless a package release is
  intentional. (0.2.0, matching the intentional v0.2.0 tag.)
- [x] Keep README installation guidance git-based until PyPI publishing is
  intentional.
- [x] Make the README maturity language match the repo's real state: early,
  referenceable, not fully launched.
- [x] Keep `contact@vibrantine.com` marked as a placeholder until the address
  exists.
- [x] Confirm the package metadata has no personal contact email unless that is
  deliberate. (`authors` carries a name only.)
- [x] Confirm the GitHub repository description and visibility match the README
  posture. (Private; description matches the pyproject one-liner. Note for
  the sharing step: while the repo is private, the README's git-install
  command only works for collaborators.)

## Security And Privacy Passes

Repeat these passes before sharing the repository more widely. Last pass:
2026-07-07, all clean; details noted per item.

- [x] Search tracked files for secrets:
  - API keys, bearer tokens, passwords, provider tokens, private URLs.
  - `.env` values accidentally copied into docs, tests, or fixtures.
  - (Every hit is an env-var name, a doc placeholder like `sk-or-...`, or
    the fake `sk-test` in tests.)
- [x] Search tracked files for personal identifiers:
  - private email addresses,
  - local absolute paths,
  - machine usernames,
  - private repo names.
- [x] Confirm `.env` is ignored and `.env.example` contains only empty public
  variable names.
- [x] Confirm no committed fixtures contain real provider responses with
  embedded credentials or private payloads. (The only fixture module is
  `recursive_research/tests/fixture_pages.py`, fictional by construction.)
- [x] Review docs for private/process material:
  - internal handoff notes,
  - scratch planning that should remain outside the repo,
  - references to private projects that are not needed to understand
    Vibrantine.
  - (The concept drafts promoted into `authoring.md` and retired on
    2026-07-07; `docs/working/` holds one deliberate decision record.)
- [x] Review GitHub-visible branches and tags for pre-cleanup history that
  should not be public. (Remote holds `main`, the current work branch, and
  the `v0.1.0`/`v0.2.0` tags; nothing else.)
- [x] Review generated artifacts:
  - no `dist/`,
  - no caches,
  - no accidental persisted run records,
  - no test output directories.
  - (None tracked; `.gitignore` covers each class.)
- [x] Review filesystem and process tools for clear authority language:
  - `ShellTool`,
  - `WriteTool`,
  - `EditTool`,
  - `DeleteTool`,
  - `MoveTool`,
  - `FetchTool`.
- [x] Confirm destructive or externally powerful tools describe that gating and
  confirmation are application policy. (Delete already said it; the same
  statement added to Shell and Write on 2026-07-07.)
- [x] Confirm `FilesystemBackend` rejects path-like `run_id` values that escape
  its root, and `SqliteBackend` binds `run_id` as a query parameter rather
  than interpolating it. (Both hold; escape rejection is pinned by
  `tests/test_persistence.py::test_*_rejects_run_id_that_escapes_root`.)

Suggested local searches:

```bash
rg -n -uu "OPENROUTER_API_KEY|sk-|Bearer |password|secret|token" .
rg -n -uu "@gmail\.com|@hotmail\.com|@outlook\.com|Users/|C:\\Users\\" .
git status -sb
```

## Validation Gates

Last pass: 2026-07-07, all green.

- [x] `uv run pytest` (359 passed, 6 skipped)
- [x] `uv run ruff check .`
- [x] `uv run ruff format --check .`
- [x] `uv run basedpyright`
- [x] `uv build` (wheel excludes colocated `tests/`, carries `prompts/*.md`)
- [ ] Optional before package release: `uv build --no-sources`
- [x] Fresh git dependency smoke test from a separate temporary project.
  (A `uv init` project added vibrantine as a git dependency, imported the
  frozen surface, ran `ReadTool` through `invoke_sync` with no API key, and
  imported the prompt-bearing `RecursiveResearchCommission` from the built
  package. Note: `uv add git+...@<branch>` chokes on branch names containing
  slashes; pin a commit SHA or tag instead.)

The fresh-install smoke test should prove that a consumer can import
Vibrantine without relying on the checkout layout.

## Commission-By-Commission Audit

Use the template below for every included Commission. The point is to review
each Commission as a component: typed boundary, interior, dependencies, failure
shape, costs, tests, and release status.

### Audit Template

```md
## Commission Name

Purpose:
Input:
Output:
Interior style:
Toolbox / children:
Prompt:
Failure behavior:
Budget / cost behavior:
Cancellation / progress:
Tests:
Known limitations:
Release decision:
```

### SummarizeCommission

- [ ] Check that the prompt faithfully describes the target lengths.
- [ ] Check the output schema is intentionally simple and uncited.
- [ ] Check size-gate behavior through the base `Commission._run`.
- [ ] Check cancellation before the LLM loop.
- [ ] Check malformed provider response behavior through the default loop.
- [ ] Check tests cover success, validation/size limits, budget behavior where
  relevant, and injected-client execution.
- [x] Decide whether British `Summarise` spelling is intentional for the public
  surface or should be revisited before broader release. Decision (2026-07-05):
  American spelling everywhere, consistent with typical coding conventions.
  Renamed to `SummarizeCommission` and swept remaining British spellings
  (behaviour, honour) across prose.

### SynthesizeCommission

- [ ] Check source grounding and provenance reattachment.
- [ ] Check empty-source validation.
- [ ] Check negative and out-of-range source indices fail structurally.
- [ ] Check malformed provider responses fail as `CommissionResult` values.
- [ ] Check budget pre-flight and post-call behavior.
- [ ] Check cost accounting includes actual token usage on failures after a
  provider call.
- [ ] Check `_ClaimRaw` and `_SynthesizeRaw` keep `Field(description=...)`.
- [ ] Check tests cover success, bad structured output, source-index issues,
  budget exhaustion, cancellation, and unpriced-model behavior.

### AskCommission

- [ ] Check the user message clearly binds one `file_path` and one `question`.
- [ ] Check `ReadTool` pagination instructions are strong enough for an LLM to
  fetch more when `truncated=True`.
- [ ] Check capability assumptions: `ReadTool` is the only declared tool.
- [ ] Check file access behavior remains a tool concern, not an application
  policy layer.
- [ ] Check tests cover tool menu shape, conclusion, read failure, pagination,
  budget, cancellation, and integration skip behavior.

### MorningBriefingCommission

Reinterpreted 2026-07-06 as the substantive worked example for the
author-decided pole of composition: a heterogeneous three-level tree (a
Weather leaf, N configured NewsDigest coordinators, an executive-summary
Summarize call) behind one contract boundary, living at
`src/vibrantine/examples/morning_briefing/` under the folder standard with
`subcommissions/` and colocated tests. See its BRIEF.md.

- [ ] Check section fan-out behavior and two-level cost rollup (sections and
  their fetches).
- [ ] Check two-level partial semantics: failed source makes a section
  partial; failed section is skipped and named; failed executive summary
  degrades the briefing instead of killing it; all-sections-failed fails.
- [ ] Check budget slicing: per-section shares with one reserved for the
  executive summary; digests slice their share across fetches.
- [ ] Check cancellation before sections and between sections and the
  executive summary.
- [ ] Check write failure returns a structured failure carrying real
  accumulated cost.
- [ ] Check progress events bubble from all three levels through the shared
  callback.
- [ ] Check the configured-reuse pattern reads well: one NewsDigest class,
  N instances, parent labels the sections (instances share the class-level
  name).
- [x] Decide whether this remains a worked coordinator in the library or moves
  toward examples later. Decision (2026-07-05): moved to `vibrantine.examples`
  with all shipped Commissions.

### RecursiveResearchCommission

- [x] Check recursive construction terminates structurally at `max_depth=0`.
- [x] Check leaf toolboxes omit `recursive_research` and include only fetch.
- [x] Check recursive child costs roll up through the default LLM loop.
- [x] Check output cap / overflow policy is appropriate for sub-answer
  rendering. Decision: `truncate_with_reference` chops oversized sub-answers
  (claims kept over prose) and persists the full version when a backend is
  wired; without one it degrades to `partial` (full output, flagged). Covered
  by `test_oversized_sub_answer_chopped_when_backend_wired` and
  `test_oversized_sub_answer_reaches_parent_flagged_partial`.
- [x] Check prompt guidance discourages unsupported claims.
- [x] Check tests cover delegation, leaf behavior, cost rollup, and tool menu
  shape.
- [x] Decide whether this is a worked example, a provisional Commission, or a
  future examples candidate. Decision: worked example. It demonstrates the
  composition pattern (recursion through the toolbox, structural termination,
  cost rollup) and is not a supported general-use surface. Moved to
  `vibrantine.examples` on 2026-07-05, when the examples area landed and all
  shipped Commissions moved into it. Renamed from DeepResearch to
  RecursiveResearch on 2026-07-05 because the old name is claimed by other
  AI research products; the new name states the pattern it demonstrates.
- [x] Add heuristic eval cases for the efficacy bar in
  `src/vibrantine/examples/recursive_research/BRIEF.md`. Three cases in
  `tests/test_eval.py` (direct source, broad decomposable, source conflict)
  run a live pinned model over fictional fixture sources served through the
  real `FetchTool` via `httpx.MockTransport`; marked `eval`, skip without
  credentials. All three passed live on 2026-07-05.

### EmailHandlerCommission

- [ ] Decide whether it should remain importable under `vibrantine.examples`
  for the public reference cut.
- [ ] If kept, make the provisional/stubbed status unmistakable in docs.
- [ ] Check stub handlers cannot be mistaken for production email behavior.
- [ ] Check tests continue to validate LLM-loop routing and child cost rollup.
- [ ] Consider moving it to examples or a `probes` area if it reads too much
  like a shipped standard Commission.

## Standard Commission Format

Create an authoring standard before the set of Commissions grows much larger.
Done in `docs/working/standard-commission-folder-structure.md`, proven by the
RecursiveResearch package migration, with testing/evaluation policy in
`docs/commission-testing.md`.

- [x] Define where input/output Pydantic models live.
- [x] Define where system prompts live.
- [x] Define how `description` is written for LLM tool selection.
- [x] Define how nested Commissions and tools are declared.
- [x] Define constructor injection conventions for child Commissions, tools,
  `model`, and test clients.
- [x] Define how tests are organized.
- [x] Define how prompt/evaluation notes are recorded.

Possible compact module layout:

```text
src/vibrantine/examples/my_commission.py
tests/test_my_commission.py
docs/commissions/my_commission.md
```

Possible folder layout for larger Commissions:

```text
src/vibrantine/examples/my_commission/
  __init__.py
  commission.py
  types.py
  models.py            # optional/provisional: model menu
  prompts/
    system.md
  tools/              # optional: private deterministic tools
    __init__.py
  subcommissions/     # optional: private LLM-bearing children
    __init__.py
  tests/
    test_commission.py
  BRIEF.md
```

Use the folder layout only when the Commission has enough prompt, type, test,
private tool, private child, or evaluation material to earn the extra files.

## Nested Commission Pattern

Commissions that own children should follow one consistent constructor pattern.
Private deterministic tools live under the Commission package's `tools/`
slot when they are too substantial to stay beside their consumer; private
LLM-bearing children live under `subcommissions/`. Both are still wired in
`commission.py`, and `toolbox` remains the source of truth for what an LLM
loop can call.

```python
def __init__(
    self,
    *,
    child: ChildCommission | None = None,
    tool: SomeTool | None = None,
    model: str | Model | None = None,
    client: AsyncOpenAI | None = None,
) -> None:
    resolved_child = child or ChildCommission(model=model, client=client)
    resolved_tool = tool or SomeTool()
    super().__init__(toolbox=(resolved_child, resolved_tool), model=model, client=client)
```

Checklist for nested Commissions:

- [ ] Parent owns its children.
- [ ] Private deterministic tools live in `tools/`; private LLM-bearing
  children live in `subcommissions/`.
- [ ] Children are invoked through `dispatch`, not direct `invoke`.
- [ ] Child dependencies are injectable for tests.
- [ ] Child costs roll up structurally.
- [ ] Child progress uses the same callback path.
- [ ] No sibling messaging, shared state, or back-channel coordination.

## Testing And Improvement Standard

Every shipped or example Commission should have tests at the level of risk it
carries. See [`commission-testing.md`](commission-testing.md) for the full
standard.

- [ ] Unit tests use fake LLM clients and require no API key.
- [ ] Integration tests are marked `@pytest.mark.integration`.
- [ ] Integration tests skip when credentials are absent.
- [ ] Tests cover validation failures.
- [ ] Tests cover cancellation.
- [ ] Tests cover malformed provider responses for LLM-backed Commissions.
- [ ] Tests cover budget behavior where the Commission spends money.
- [ ] Tests cover cost rollup where child Commissions/tools are used.
- [ ] Tests cover capability/tool menu shape for LLM-loop Commissions.
- [ ] Tests cover partial results where partial is an expected state.
- [ ] LLM-driven Commissions have heuristic evaluation cases with explicit
  success and failure criteria.
- [ ] Evaluation cases record their scoring method: deterministic check,
  heuristic assertion, human review, or judge-model rubric.
- [ ] Prompt changes update or add at least one targeted regression, scripted
  fake conversation, or evaluation case when practical.

Optional improvement notes per Commission:

```text
success criteria:
failure criteria:
known failures:
prompt changes tried:
example inputs:
example bad outputs:
candidate eval cases:
```

## Examples Folder

Satisfied, but not by the repo-root `examples/` directory this section
originally planned: runnable examples shipped inside `vibrantine.examples`
instead, as the demo runner (`python -m vibrantine.examples`) and the
learning ladder (`vibrantine.examples.learning_ladder`, four runnable
rungs of one idea each), importable and colocated with the worked
Commissions.

- [x] Deterministic tool example that needs no API key (authoring.md
  Step 0 runs `ReadTool` as proof of life).
- [x] LLM-backed examples that clearly require `OPENROUTER_API_KEY`
  (the ladder rungs; the demo runner checks for the key up front).
- [x] Examples use `invoke_sync` / `run_one` / `dispatch`, not the
  `_run` hook.
- [x] Examples handle `success`, `partial`, and `failure` results
  explicitly enough to teach the result envelope.
- [x] Examples avoid protected helpers and frozen-internal assumptions.
- [x] README and authoring.md link to the demo runner and the ladder.

## External Consumer Repo

Create a separate personal repo to prove external authoring works as intended.
This is the best test that the frozen public surface is real.

Possible shape:

```text
vibrantine-example-commissions/
  pyproject.toml
  src/example_commissions/
    research_brief.py
    classify_note.py
  tests/
    test_research_brief.py
```

Checklist:

- [ ] Consume Vibrantine through a git dependency.
- [ ] Author at least one custom LLM-backed Commission.
- [ ] Author or wrap at least one deterministic tool-like Commission.
- [ ] Import the frozen surface from `vibrantine`.
- [ ] Import provisional tools from `vibrantine.tools` only when consciously
  accepting provisional status.
- [ ] Do not import underscore-prefixed modules or helpers.
- [ ] Do not depend on `dispatch` internals.
- [ ] Test with an injected `ScriptedLLM` from `vibrantine.testing`.
- [ ] Run tests without any API key.
- [ ] Add one optional integration test that skips without credentials.

## Visualization Notes

Do not block this public-reference cut on visualization. Capture it as future
work.

Useful future output:

```text
RecursiveResearch
|-- RecursiveResearch(depth=1)
|   |-- RecursiveResearch(depth=0)
|   |   `-- FetchTool
|   `-- FetchTool
`-- FetchTool
```

Potential future tasks:

- [ ] Generate a tree from `commission.toolbox`.
- [ ] Include max depth and child names.
- [ ] Mark tools vs LLM-backed Commissions.
- [ ] Optionally show cost after a run using persisted records.
- [ ] Consider Mermaid output for docs.

## Final Wrap Checklist

- [x] Security/privacy passes complete. (Latest pass 2026-07-07; repeat at
  the actual sharing moment.)
- [ ] Commission-by-Commission audit complete or consciously deferred.
- [x] Examples folder exists or is explicitly deferred. `vibrantine.examples`
  landed 2026-07-05; all shipped Commissions live there, importable but not
  part of the frozen surface.
- [ ] External consumer repo smoke test complete or explicitly deferred.
- [ ] README reflects current maturity.
- [x] Docs index points to this checklist.
- [ ] CI green after final push.
- [ ] Local validation gates green.
- [ ] Worktree clean.
- [ ] Latest pushed commit is the one intended for sharing.
