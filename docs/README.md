# Vibrantine Docs

Index of the docs directory. Start with the root [`README.md`](../README.md)
for the public front door, then read `vision.md` and `composition.md` for the
architecture.

## Public front door

- [`../README.md`](../README.md): the professional overview: what Vibrantine
  is, why Commissions, what ships today, and where to go next.

## Architecture source of truth

The authoritative pair. Stable, frequently referenced, the single source of
truth for what Vibrantine is and how its pieces fit together.

- [`vision.md`](vision.md): what Vibrantine is and why: use cases, library
  scope, distribution layering, bounded agency, the bet.
- [`composition.md`](composition.md): how the pieces fit: contract jacket,
  three-type model, Python-coordinator / LLM-loop interiors, information
  flow, output discipline, persistence, coordinator templates.

## Guides

Audience-facing guides, not architecture.

- [`building-a-commission.md`](building-a-commission.md): the current
  authoring guide; builds a set of Commissions from first principles.
- [`authoring-from-an-external-repo.md`](authoring-from-an-external-repo.md):
  what a separate repo gets when it imports `vibrantine` to build
  Commissions: importable surface, contract, types, run flow, authoring
  rules. Line-exact references against source, machine-checked in CI.
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
