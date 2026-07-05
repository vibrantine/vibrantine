# Vibrantine Docs

Index of the docs directory. Start with the root [`README.md`](../README.md)
for the public front door, then read `design.md` for the architecture.

## Public front door

- [`../README.md`](../README.md): the professional overview: what Vibrantine
  is, why Commissions, what ships today, and where to go next.

## Architecture source of truth

- [`design.md`](design.md): the design record and single source of truth.
  The goal and the two-sentence core, every settled decision with its
  reason and what it rules out, what the library refuses to do, the
  trades, what is not built yet, and the thesis.

## Guides

Audience-facing guides, not architecture.

- [`authoring.md`](authoring.md): the one document about building
  Commissions, in three parts. Part I: a step-by-step tutorial building a
  first Commission in an external project against the frozen public surface,
  every code block verified end to end against a live model. Part II:
  composition, the custom-coordinator path, and where state lives. Part III:
  the contract reference, machine-checked in CI by
  `tests/test_external_authoring.py`.
- [`commission-testing.md`](commission-testing.md): how to test commissions
  at two levels: contract behavior with fake clients, and heuristic
  evaluation against explicit success/failure criteria.
- [`pre-release-checklist.md`](pre-release-checklist.md): public-reference
  release checklist covering security/privacy passes, commission audits,
  examples, external consumer proof, and final validation.

## Working drafts ([`working/concepts/`](working/concepts/))

Aspirational and fenced: concept drafts that may feed a later
tutorial/reference layer. The root README owns the front-door role; this
folder shrinks as material promotes into live docs or retires. See its
[`README.md`](working/concepts/README.md) for contents.

Process records, retired drafts, and external research notes live outside
the repo.
