# Vibrantine Docs

Index of the docs directory. Start with the root [`README.md`](../README.md)
for the public front door, then read `commission-model.md` to understand
the model and `design.md` for the design argument.

## Public front door

- [`../README.md`](../README.md): the source of truth: what Vibrantine
  is, why Commissions, what ships today, and where to go next.

## Design

- [`design.md`](design.md): the design argument: what the library is
  for, the model that delivers it, why the boundary can be trusted,
  what the library refuses to do, and what the guarantees cost.
- [`design-decisions.md`](design-decisions.md): the ruling record:
  every settled decision in one fixed shape (the decision, why, what it
  rules out), plus the not-built list with the trigger that earns each
  build. Consult it before changing anything at a boundary.

## Guides

Audience-facing guides, not architecture.

- [`commission-model.md`](commission-model.md): how the Commission
  model works, conceptually. The boundary, the five surfaces, the
  result envelope, and composition; what a Commission looks like to
  the model that holds it as a tool.
- [`authoring.md`](authoring.md): the one document about building
  Commissions, in three parts. Part I: a step-by-step tutorial building a
  first Commission in an external project against the frozen public surface,
  every code block verified end to end against a live model. Part II:
  composition, the custom-coordinator path, and where state lives. Part III:
  the contract reference, machine-checked in CI by
  `tests/test_external_authoring.py`.
- [`running.md`](running.md): what you control and what you can see
  when you run a Commission tree: budgets, fuses, observability, and
  the short list of conventions an operator holds in their head.
- [`commission-testing.md`](commission-testing.md): how to test Commissions
  at two levels: contract behavior with fake clients, and heuristic
  evaluation against explicit success/failure criteria.
- [`pre-release-checklist.md`](pre-release-checklist.md): public-reference
  release checklist covering release posture, security/privacy passes,
  validation gates, external consumer proof, and the final wrap. The
  authoring-standard and per-Commission audit material it once carried now
  lives with its owners; the checklist points instead of restating.

## Working notes ([`working/`](working/))

Working implementation material, not claims about what currently ships. A
working document authorizes boundary work only when the ruling record links to
it; this folder shrinks as material promotes into live docs or retires. Current
notes:

- [`standard-commission-folder-structure.md`](working/standard-commission-folder-structure.md):
  the decision record for the standard folder-sized Commission layout, its
  sketches, and its open threads.
- [`commission-as-local-mcp-spec.md`](working/commission-as-local-mcp-spec.md):
  ratified implementation plan for exposing an explicit set of Commissions as
  tools on a repository-local MCP server.
- [`compose-sonnet-commission-spec.md`](working/compose-sonnet-commission-spec.md):
  the deliberately unmistakable first Commission and compatibility smoke test
  for the local MCP adapter.
- [`external-mcp-tools-for-commissions-spec.md`](working/external-mcp-tools-for-commissions-spec.md):
  parked proposal for application-owned MCP connections and explicitly bound
  Tool proxies supplied to selected Commission toolboxes.

The concept drafts that used to live under `working/concepts/` promoted
into `authoring.md` (the five-surface ownership map, the boundary-type
design moves, and the composition shapes) and retired.

Process records, retired drafts, and external research notes live outside
the repo.
