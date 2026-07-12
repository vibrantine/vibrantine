# Pre-Release Checklist

This checklist is for the current public-reference cut: a repository that can
be shown, cloned, and imported from git, but is not yet a PyPI launch or a
stable framework promise.

The goal is not polish for its own sake. The goal is to make the repo safe to
show, clear about its maturity, and honest about which surfaces are stable,
provisional, or future work.

## Release Posture

Last pass: 2026-07-10.

- [x] Keep `pyproject.toml` at a pre-release version unless a package release is
  intentional. (0.5.0, matching the intentional v0.5.0 tag; Unreleased
  changes sit on main awaiting the next cut.)
- [x] Keep README installation guidance git-based until PyPI publishing is
  intentional. (Pinned to the v0.5.0 tag.)
- [x] Make the README maturity language match the repo's real state: early,
  referenceable, not fully launched. ("Early-stage software ... not yet on
  PyPI.")
- [x] Keep `contact@vibrantine.com` marked as a placeholder until the address
  exists.
- [x] Confirm the package metadata has no personal contact email unless that is
  deliberate. (`authors` carries a name only.)
- [x] Confirm the GitHub repository description and visibility match the README
  posture. (PUBLIC as of this pass; description matches the pyproject
  one-liner, and the README's git-install command now works for everyone.)

## Security And Privacy Passes

Repeat these passes before sharing the repository more widely. Last pass:
2026-07-10 (with the repository already public), all clean; details noted
per item.

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
  should not be public. (Remote holds `main` and the `v0.1.0` through
  `v0.5.0` tags; nothing else.)
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

Suggested local searches (`git grep` searches tracked files only; the old
`rg -uu` form also read ignored files and would print the real `.env` key
into the terminal):

```bash
git grep -nIE "OPENROUTER_API_KEY|sk-|Bearer |password|secret|token" -- .
git grep -nIE "@gmail\.com|@hotmail\.com|@outlook\.com|/Users/|C:\\\\Users" -- .
git status -sb
```

## Validation Gates

Last pass: 2026-07-10, all green.

- [x] `uv run pytest` (393 passed, 7 skipped)
- [x] `uv run ruff check .`
- [x] `uv run ruff format --check .`
- [x] `uv run basedpyright`
- [x] `uv build` (wheel inspected: zero test files, `prompts/*.md` carried)
- [x] Optional before package release: `uv build --no-sources` (clean)
- [x] Fresh git dependency smoke test from a separate temporary project.
  (A `uv init` project added vibrantine via the public
  `git+https://github.com/vibrantine/vibrantine.git` URL pinned to a commit
  SHA, imported the full 36-name frozen surface including the multimodal
  `ContentPart` members, ran `ReadTool` through `run_commission_sync` with no API
  key, and imported the prompt-bearing `RecursiveResearchCommission` from
  the installed package. Note: `uv add git+...@<branch>` chokes on branch
  names containing slashes; pin a commit SHA or tag instead.)

The fresh-install smoke test should prove that a consumer can import
Vibrantine without relying on the checkout layout.

## Commission-By-Commission Audit

Ruled 2026-07-10: the shipped Commissions live in `vibrantine.examples`,
outside the frozen surface, so the per-Commission audit is opportunistic,
run when an example is next touched, not a release gate. The retired
per-Commission sections, and the decisions they recorded, are in git
history (pruned 2026-07-13).

Template for auditing one:

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

## Authoring Standards

Owned elsewhere; this checklist only points:

- Folder layout, slots, `BRIEF.md`, colocated tests:
  [`working/standard-commission-folder-structure.md`](working/standard-commission-folder-structure.md).
- Constructor injection, composition, and the nested-Commission pattern:
  [`authoring.md`](authoring.md), Part II.
- Test and evaluation coverage bars, including the cost-rollup recipe:
  [`commission-testing.md`](commission-testing.md).

## Examples

Satisfied by `vibrantine.examples` (2026-07-05): the demo runner
(`python -m vibrantine.examples`), the four-rung learning ladder, and the
worked Commissions, importable but outside the frozen surface. Examples go
through the entry points (never the `_run` hook), handle all three envelope
statuses, and the no-key proof of life is authoring.md Step 0.

## External Consumer Proof

The 2026-07-10 fresh git-dependency smoke (Validation Gates above) proves
the install mechanics. Base Coder, the tier-1 consumer in its own repo, is
the standing proof beyond that. Before each release, confirm it still
consumes the current surface cleanly:

- [ ] Consumes Vibrantine through a git dependency.
- [ ] Imports the frozen surface from `vibrantine`; imports from
  provisional areas (`vibrantine.tools`) are conscious choices.
- [ ] No underscore-prefixed imports, no `dispatch` internals.
- [ ] Tests script the model through the run catalog
  (`vibrantine.testing.scripted_model`) and run without an API key.

## Final Wrap Checklist

- [x] Security/privacy passes complete. (Latest pass 2026-07-10, run with
  the repository already public; repeat at each future sharing moment.)
- [x] Commission-by-Commission audit complete or consciously deferred.
  (Consciously deferred 2026-07-10: the Commissions moved to
  `vibrantine.examples`, outside the frozen surface; audit opportunistically
  when an example is next touched. The retired per-Commission sections are
  in git history.)
- [x] Examples folder exists or is explicitly deferred. `vibrantine.examples`
  landed 2026-07-05; all shipped Commissions live there, importable but not
  part of the frozen surface.
- [x] External consumer repo smoke test complete or explicitly deferred.
  (The 2026-07-10 fresh git-dependency smoke covers the mechanics; Base
  Coder is the persistent external consumer going forward.)
- [x] README reflects current maturity. (Checked 2026-07-10.)
- [x] Docs index points to this checklist.
- [x] CI green after final push. (2026-07-10, both platforms.)
- [x] Local validation gates green. (2026-07-10.)
- [x] Worktree clean.
- [x] Latest pushed commit is the one intended for sharing.
