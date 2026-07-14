# README rebuild: one front door, several short rooms

Working scaffold for the v0.6.0 README structure: a concise landing
page plus linked depth pages. **PROMOTED 2026-07-14**: README-draft.md
became the root `README.md`; commission-model.md and running.md moved to
`docs/`; the design split (design.md + design-decisions.md) landed the
same day. This file remains as the record of the rebuild until the
v0.6.0 tag closes its open questions.

**Ruling 2026-07-14 (user):** depth lives in linked pages. The root
README stays the landing page; the 0.5 README's depth moved whole into
`commission-model.md` and `running.md` here. Nothing lost, nothing
crammed.

**Thesis re-ruling 2026-07-14 (user, same evening):** the core thesis
was re-centered: a Commission abstracts complex behavior so that AI can
use it with a minimalistic footprint. AI is the intended consumer;
trust is the enabler that makes discarding the interior safe. Every
live doc and every draft here now carries a THESIS REVIEW marker; the
worklist is `notes/working/thesis-review.md`. The locked landing
wording is subject to an explicit re-rule under this thesis before
promotion.

## Principles

1. **The front door sells one idea.** README.md answers "what is this,
   why would I want it, show me" in under two minutes, then hands off.
2. **One page, one question.** Each page answers a single reader
   question, named in its first line.
3. **No duplication with the standing docs.** authoring.md,
   commission-testing.md, and design.md own building, testing, and
   why-shaped. The new pages fill the gap between the front door and
   those documents.
4. **Links at decision points, not link farms.** A handful of
   load-bearing inline links; every page ends by naming where each kind
   of reader goes next.

## Page map

| Page | Question it answers | Status |
| --- | --- | --- |
| `README-draft.md` -> root `README.md` | What is this and why would I use it? | PROMOTED 2026-07-14; headline re-ruled (compression leads), Why re-tilted |
| `commission-model.md` -> `docs/` | How does the Commission model work, conceptually? | PROMOTED 2026-07-14; model-first opening, tool-descriptor example |
| `running.md` -> `docs/` | What do I control and see when I run a tree? | PROMOTED 2026-07-14; gained the `[budget]` line sentence |
| `docs/authoring.md` | How do I build a Commission? | standing; light thesis pass still owed |
| `docs/commission-testing.md` | How do I prove one works and works well? | standing; gained the scripted-seam example 2026-07-14; light thesis pass still owed |
| `docs/design.md` | Why is the library shaped this way? | REPLACED 2026-07-14 by the split: design.md (argument) + design-decisions.md (ruling record) |

Reader journey: README (decide) -> commission-model (understand) ->
authoring (build) -> running (operate) -> commission-testing (trust) ->
design (interrogate).

## Where the 0.5 depth went

Executed 2026-07-14 against the post-audit root README:

- Core Idea, Five Surfaces, Result Envelopes, Composition, the three
  categories, Implementation Styles, and the hand-written subclass
  example -> `commission-model.md`.
- One Run One Set of Controls, Observability, What You Must Actually
  Hold -> `running.md`.
- Testing Without an API Key's runnable example ->
  `docs/commission-testing.md` (live now, not staged here).
- Why / pitch / Minimal Example / Installation / What Is Not ->
  already on the landing draft.
- Development + Contributing -> compact combined section on the
  landing draft. Closing thought kept as the landing page's last lines.
- Current Status's two feature lists -> NOT yet homed; see open
  questions.

## Promotion checklist (executed 2026-07-14)

- Root `README.md` replaced by README-draft.md; `commission-model.md`
  and `running.md` moved to `docs/`. DONE.
- Draft-relative links flipped; both heading anchors into authoring.md
  re-verified. DONE.
- `docs/README.md` index re-synced with all four new/changed pages. DONE.
- AGENTS.md re-synced: thesis line, consult-the-record instruction,
  section pointers re-aimed at design-decisions.md (code docstrings
  too). DONE.
- Still open: consider a CI link/anchor check.

## Parked (user-raised, not this release)

- Flowcharts in the Example section: a visual of a Commission tree
  (possibly the migration or data-room example from the opening).

## Open questions

- **Current Status depth.** The landing draft carries only the
  At-a-Glance status line. The 0.5 README's tagged-vs-main feature
  lists collapse at tag day anyway; candidate homes are the CHANGELOG
  (release notes are its job) or a short "in the box" list on the
  landing page. Needs a ruling at tag day.
- **Hand-written example placement.** RATIFIED 2026-07-14: stays in
  commission-model.md (user read-through approved the page whole).
- **Promotion timing.** RESOLVED 2026-07-14: promoted before the
  v0.6.0 tag. The Current Status restructure and install-pin bump
  (v0.5.0 -> v0.6.0) still happen at the tag.
