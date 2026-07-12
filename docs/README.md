# Vibrantine Docs

Index of the docs directory. Start with the root [`README.md`](../README.md)
for the public front door, then read `design.md` for the design record.

## Public front door

- [`../README.md`](../README.md): the source of truth: what Vibrantine
  is, why Commissions, what ships today, and where to go next.

## Design record

- [`design.md`](design.md): why the library is shaped the way it is, what
  that shape costs, and what is planned but not built. The goal and the
  two-sentence core, every settled decision with its reason and what it
  rules out, what the library refuses to do, the trades, what is not
  built yet, and the thesis.

## Guides

Audience-facing guides, not architecture.

- [`authoring.md`](authoring.md): the one document about building
  Commissions, in three parts. Part I: a step-by-step tutorial building a
  first Commission in an external project against the frozen public surface,
  every code block verified end to end against a live model. Part II:
  composition, the custom-coordinator path, and where state lives. Part III:
  the contract reference, machine-checked in CI by
  `tests/test_external_authoring.py`.
- [`commission-testing.md`](commission-testing.md): how to test Commissions
  at two levels: contract behavior with fake clients, and heuristic
  evaluation against explicit success/failure criteria.
- [`pre-release-checklist.md`](pre-release-checklist.md): public-reference
  release checklist covering release posture, security/privacy passes,
  validation gates, external consumer proof, and the final wrap. The
  authoring-standard and per-Commission audit material it once carried now
  lives with its owners; the checklist points instead of restating.

## Working notes ([`working/`](working/))

Working material, not live guidance; this folder shrinks as material
promotes into the live docs or retires. Currently one note:
[`standard-commission-folder-structure.md`](working/standard-commission-folder-structure.md),
the decision record for the standard folder-sized Commission layout, its
sketches, and its open threads.

The concept drafts that used to live under `working/concepts/` promoted
into `authoring.md` (the five-surface ownership map, the boundary-type
design moves, and the composition shapes) and retired.

Process records, retired drafts, and external research notes live outside
the repo.
