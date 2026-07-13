# README rebuild: one front door, several short rooms

Working scaffold for splitting the single long README into a concise,
linked set of pages. Files here are stubs and drafts; they promote into
`README.md` and `docs/` or retire. Nothing in this directory is live
documentation.

## The problem being solved

The current README is accurate and comprehensive, and that is now its
flaw: a first-time reader meets ~600 lines before they know whether the
library is for them. Comprehensiveness should survive; it just should not
all live behind one scroll.

## Principles

1. **The front door sells one idea.** README.md answers "what is this,
   why would I want it, show me" in under two minutes, then hands off.
2. **One page, one question.** Each page answers a single reader
   question, named in its first line. A reader deciding whether to adopt
   never wades through operator detail; an operator never rereads the
   pitch.
3. **No duplication with the standing docs.** authoring.md,
   commission-testing.md, and design.md already own building, testing,
   and why-it-is-shaped-this-way. New pages fill the gap between the
   front door and those documents; they never restate them.
4. **Links at decision points, not link farms.** Every page ends by
   naming where each kind of reader goes next.

## Proposed page map

| Page | Question it answers | Status |
| --- | --- | --- |
| `README.md` (rewritten) | What is this and why would I use it? | stub here |
| `docs/commission-model.md` (new) | How does the Commission model work, conceptually? | stub here |
| `docs/running.md` (new) | What controls and visibility do I get when I run a tree? | stub here |
| `docs/authoring.md` (existing) | How do I build a Commission? | unchanged |
| `docs/commission-testing.md` (existing) | How do I prove one works and works well? | unchanged |
| `docs/design.md` (existing) | Why is the library shaped this way? | unchanged |

Reader journey: README (decide) -> commission-model (understand) ->
authoring (build) -> running (operate) -> commission-testing (trust) ->
design (interrogate).

## Disposition of the current README, section by section

| Current section | Goes to | Treatment |
| --- | --- | --- |
| Title + one-paragraph pitch | README | keep, tighten |
| Why Vibrantine? | README | condense to ~half |
| The Core Idea | commission-model | move; README keeps two sentences |
| Five Surfaces, Five Owners | commission-model | move whole |
| Result Envelopes | commission-model | move; README keeps the 5-line status snippet |
| Composition | commission-model | move whole |
| Commissions, Tools, and Application Code | commission-model | move whole |
| Implementation Styles | commission-model | move; authoring.md already covers the hooks in depth, keep this the short conceptual version |
| One Run, One Set of Controls | running | move whole |
| Observability | running | move whole |
| What You Must Actually Hold | running | move whole (it is operator knowledge) |
| Minimal Example | README | keep, it is the "show me" |
| The Same Boundary, Written by Hand | commission-model or cut | flag: authoring.md owns subclassing; candidate to cut down to a link |
| Testing Without an API Key | README (short) | keep a 10-line teaser + link; full version already in commission-testing.md's orbit |
| Installation | README | keep |
| Current Status | README | keep; restructure stays a tag-day job |
| What Vibrantine Is Not | README | keep, it is cheap and orienting |
| Documentation | README | becomes the journey table |
| Development / Contributing / Contact / License / Closing Thought | README | keep, tighten |

## Flagged placement decisions (need sign-off)

1. **This scaffold lives at `docs/working/readme-building/`.** Chosen to
   match the stated topology (working notes promote or retire). The
   alternative was gitignored `notes/`; rejected because these drafts
   are future public pages, not process notes.
2. **New pages land in `docs/` beside the standing three.** No new
   subdirectory; six documents do not need a hierarchy.
3. **Page names**: `commission-model.md` and `running.md` are
   placeholders; rename freely before promotion.

## Parked (user-raised, not this release)

- Flowcharts in the Example section: a visual of a Commission tree
  (possibly the migration or data-room example from the opening) to
  replace or accompany prose. Deferred by the user; revisit after the
  landing text settles.

## Open questions

- Does "The Same Boundary, Written by Hand" survive anywhere, or does a
  link to authoring.md carry it?
- Should commission-model.md absorb "What Vibrantine Is Not," or does
  that stay on the front door?
- Tag-day interaction: promote before or after cutting v0.6.0? (The
  Current Status section is restructured at the tag either way.)
