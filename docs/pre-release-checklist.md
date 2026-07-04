# Pre-Release Checklist

This checklist is for the current public-reference cut: a repository that can
be shown, cloned, and imported from git, but is not yet a PyPI launch or a
stable framework promise.

The goal is not polish for its own sake. The goal is to make the repo safe to
show, clear about its maturity, and honest about which surfaces are stable,
provisional, or future work.

## Release Posture

- [ ] Keep `pyproject.toml` at a pre-release version unless a package release is
  intentional.
- [ ] Keep README installation guidance git-based until PyPI publishing is
  intentional.
- [ ] Make the README maturity language match the repo's real state: early,
  referenceable, not fully launched.
- [ ] Keep `contact@vibrantine.com` marked as a placeholder until the address
  exists.
- [ ] Confirm the package metadata has no personal contact email unless that is
  deliberate.
- [ ] Confirm the GitHub repository description and visibility match the README
  posture.

## Security And Privacy Passes

Repeat these passes before sharing the repository more widely.

- [ ] Search tracked files for secrets:
  - API keys, bearer tokens, passwords, provider tokens, private URLs.
  - `.env` values accidentally copied into docs, tests, or fixtures.
- [ ] Search tracked files for personal identifiers:
  - private email addresses,
  - local absolute paths,
  - machine usernames,
  - private repo names.
- [ ] Confirm `.env` is ignored and `.env.example` contains only empty public
  variable names.
- [ ] Confirm no committed fixtures contain real provider responses with
  embedded credentials or private payloads.
- [ ] Review docs for private/process material:
  - internal handoff notes,
  - scratch planning that should remain outside the repo,
  - references to private projects that are not needed to understand
    Vibrantine.
- [ ] Review GitHub-visible branches and tags for pre-cleanup history that
  should not be public.
- [ ] Review generated artifacts:
  - no `dist/`,
  - no caches,
  - no accidental persisted run records,
  - no test output directories.
- [ ] Review filesystem and process tools for clear authority language:
  - `ShellTool`,
  - `WriteTool`,
  - `EditTool`,
  - `DeleteTool`,
  - `MoveTool`,
  - `FetchTool`.
- [ ] Confirm destructive or externally powerful tools describe that gating and
  confirmation are application policy.
- [ ] Confirm `FilesystemBackend` rejects path-like `run_id` values that escape
  its root.

Suggested local searches:

```bash
rg -n -uu "OPENROUTER_API_KEY|sk-|Bearer |password|secret|token" .
rg -n -uu "@gmail\.com|@hotmail\.com|@outlook\.com|Users/|C:\\Users\\" .
git status -sb
```

## Validation Gates

- [ ] `uv run pytest`
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run basedpyright`
- [ ] `uv build`
- [ ] Optional before package release: `uv build --no-sources`
- [ ] Fresh git dependency smoke test from a separate temporary project.

The fresh-install smoke test should prove that a consumer can import
Vibrantine without relying on the checkout layout.

## Commission-By-Commission Audit

Use the template below for every included commission. The point is to review
each commission as a component: typed boundary, interior, dependencies, failure
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

### SummariseCommission

- [ ] Check that the prompt faithfully describes the target lengths.
- [ ] Check the output schema is intentionally simple and uncited.
- [ ] Check size-gate behavior through the base `Commission.invoke`.
- [ ] Check cancellation before the LLM loop.
- [ ] Check malformed provider response behavior through the default loop.
- [ ] Check tests cover success, validation/size limits, budget behavior where
  relevant, and injected-client execution.
- [ ] Decide whether British `Summarise` spelling is intentional for the public
  surface or should be revisited before broader release.

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

- [ ] Check fan-out fetch behavior and cost rollup.
- [ ] Check partial result semantics when some URLs fail.
- [ ] Check all-fetch-failed behavior.
- [ ] Check cancellation before fetch, between fetch and synthesize, and before
  write where practical.
- [ ] Check write failure returns a structured failure carrying real accumulated
  cost.
- [ ] Check progress events are emitted and child progress can bubble through
  the shared callback.
- [ ] Decide whether this remains a worked coordinator in the library or moves
  toward examples later.

### DeepResearchCommission

- [x] Check recursive construction terminates structurally at `max_depth=0`.
- [x] Check leaf toolboxes omit `deep_research` and include only fetch.
- [x] Check recursive child costs roll up through the default LLM loop.
- [x] Check output cap / overflow policy is appropriate for sub-answer
  rendering. Decision: `partial` flags oversized sub-answers without trimming
  them (a warning light, not a guard rail); accepted until
  `truncate_with_reference` lands. Covered by
  `test_oversized_sub_answer_reaches_parent_flagged_partial`.
- [x] Check prompt guidance discourages unsupported claims.
- [x] Check tests cover delegation, leaf behavior, cost rollup, and tool menu
  shape.
- [ ] Decide whether this is a worked example, a provisional commission, or a
  future examples candidate.
- [ ] Add heuristic eval cases for the efficacy bar in
  `src/vibrantine/commissions/deep_research/BRIEF.md`.

### EmailHandlerCommission

- [ ] Decide whether it should remain importable under `vibrantine.commissions`
  for the public reference cut.
- [ ] If kept, make the provisional/stubbed status unmistakable in docs.
- [ ] Check stub handlers cannot be mistaken for production email behavior.
- [ ] Check tests continue to validate LLM-loop routing and child cost rollup.
- [ ] Consider moving it to examples or a `probes` area if it reads too much
  like a shipped standard commission.

## Standard Commission Format

Create an authoring standard before the set of commissions grows much larger.
Done in `docs/working/standard-commission-folder-structure.md`, proven by the
DeepResearch package migration, with testing/evaluation policy in
`docs/commission-testing.md`.

- [x] Define where input/output Pydantic models live.
- [x] Define where system prompts live.
- [x] Define how `description` is written for LLM tool selection.
- [x] Define how nested commissions and tools are declared.
- [x] Define constructor injection conventions for child commissions, tools,
  `model`, and test clients.
- [x] Define how tests are organized.
- [x] Define how prompt/evaluation notes are recorded.

Possible compact module layout:

```text
src/vibrantine/commissions/my_commission.py
tests/test_my_commission.py
docs/commissions/my_commission.md
```

Possible folder layout for larger commissions:

```text
src/vibrantine/commissions/my_commission/
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

Use the folder layout only when the commission has enough prompt, type, test,
private tool, private child, or evaluation material to earn the extra files.

## Nested Commission Pattern

Commissions that own children should follow one consistent constructor pattern.
Private deterministic tools live under the commission package's `tools/`
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

Checklist for nested commissions:

- [ ] Parent owns its children.
- [ ] Private deterministic tools live in `tools/`; private LLM-bearing
  children live in `subcommissions/`.
- [ ] Children are invoked through `dispatch`, not direct `invoke`.
- [ ] Child dependencies are injectable for tests.
- [ ] Child costs roll up structurally.
- [ ] Child progress uses the same callback path.
- [ ] No sibling messaging, shared state, or back-channel coordination.

## Testing And Improvement Standard

Every shipped or example commission should have tests at the level of risk it
carries. See [`commission-testing.md`](commission-testing.md) for the full
standard.

- [ ] Unit tests use fake LLM clients and require no API key.
- [ ] Integration tests are marked `@pytest.mark.integration`.
- [ ] Integration tests skip when credentials are absent.
- [ ] Tests cover validation failures.
- [ ] Tests cover cancellation.
- [ ] Tests cover malformed provider responses for LLM-backed commissions.
- [ ] Tests cover budget behavior where the commission spends money.
- [ ] Tests cover cost rollup where child commissions/tools are used.
- [ ] Tests cover capability/tool menu shape for LLM-loop commissions.
- [ ] Tests cover partial results where partial is an expected state.
- [ ] LLM-driven commissions have heuristic evaluation cases with explicit
  success and failure criteria.
- [ ] Evaluation cases record their scoring method: deterministic check,
  heuristic assertion, human review, or judge-model rubric.
- [ ] Prompt changes update or add at least one targeted regression, scripted
  fake conversation, or evaluation case when practical.

Optional improvement notes per commission:

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

Add examples before wider sharing. Keep them small, runnable, and honest about
credentials.

Suggested first examples:

```text
examples/
  ask_file.py
  summarise_text.py
  synthesize_sources.py
  tools_read_write.py
```

Checklist:

- [ ] At least one deterministic tool example that needs no API key.
- [ ] At least one LLM-backed example that clearly requires
  `OPENROUTER_API_KEY`.
- [ ] Examples use `invoke_sync` or `run_one`, not direct `invoke`.
- [ ] Examples handle `success`, `partial`, and `failure` results explicitly
  enough to teach the result envelope.
- [ ] Examples avoid protected helpers and frozen-internal assumptions.
- [ ] README links to the examples once they exist.

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
- [ ] Author at least one custom LLM-backed commission.
- [ ] Author or wrap at least one deterministic tool-like commission.
- [ ] Import the frozen surface from `vibrantine`.
- [ ] Import provisional tools from `vibrantine.tools` only when consciously
  accepting provisional status.
- [ ] Do not import underscore-prefixed modules or helpers.
- [ ] Do not depend on `dispatch` internals.
- [ ] Test with an injected fake client.
- [ ] Run tests without any API key.
- [ ] Add one optional integration test that skips without credentials.

## Visualization Notes

Do not block this public-reference cut on visualization. Capture it as future
work.

Useful future output:

```text
DeepResearch
|-- DeepResearch(depth=1)
|   |-- DeepResearch(depth=0)
|   |   `-- FetchTool
|   `-- FetchTool
`-- FetchTool
```

Potential future tasks:

- [ ] Generate a tree from `commission.toolbox`.
- [ ] Include max depth and child names.
- [ ] Mark tools vs LLM-backed commissions.
- [ ] Optionally show cost after a run using persisted records.
- [ ] Consider Mermaid output for docs.

## Final Wrap Checklist

- [ ] Security/privacy passes complete.
- [ ] Commission-by-commission audit complete or consciously deferred.
- [ ] Examples folder exists or is explicitly deferred.
- [ ] External consumer repo smoke test complete or explicitly deferred.
- [ ] README reflects current maturity.
- [ ] Docs index points to this checklist.
- [ ] CI green after final push.
- [ ] Local validation gates green.
- [ ] Worktree clean.
- [ ] Latest pushed commit is the one intended for sharing.
