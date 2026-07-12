# Standard Commission Folder Structure

This note records the chosen standard layout for folder-sized Commissions,
the decisions behind it, and what remains open. It supersedes the earlier
alternatives analysis (summarized at the end).

## The Standard

A Commission is a single module until it outgrows one. When it does, it
becomes a package with this available shape; optional slots such as
`models.py`, `prompts/`, `tools/`, and `subcommissions/` are omitted until
earned:

```text
src/vibrantine/examples/recursive_research/
  __init__.py            # re-exports the public class + I/O types; no behavior
  commission.py          # the Commission subclass: identity, toolbox, control flow
  types.py               # input/output/intermediate Pydantic models
  models.py              # optional, provisional: the model menu (LLM seats)
  prompts/
    system.md            # the stable system prompt; more files as steps earn them
  tools/
    __init__.py          # empty; private deterministic tools owned here
    render_state.py      # no LLM anywhere in its subtree
  subcommissions/
    __init__.py          # empty; importability only, never a facade
    plan.py              # small child: a single module
    doc_reader/          # folder-sized child: this exact shape again, recursively
      __init__.py
      commission.py
      types.py
      prompts/
      tools/
        __init__.py
      subcommissions/
      tests/
      BRIEF.md
  tests/
    test_commission.py   # travels with the package
  BRIEF.md               # the plain-language home base
```

The recursion is the whole standard. Every entry in `subcommissions/` is
itself a Commission, and follows the same module-or-package rule with the
same escalation criteria. `tools/` is the companion slot for deterministic
private capabilities: no LLM anywhere in their subtree, still wired through
the parent's `toolbox` when the parent's LLM may call them.

## Design Regime: Built For Massive Recursion

The standard assumes Commissions will be fleshed out slowly, over years,
largely by agentic AI adding options and capabilities. Under that regime a
deep tree of subcommissions is not a failure to be prevented; it is the
expected result of sustained growth. The layout is designed so that a tree
of any depth stays workable:

- **Nobody ever needs the whole tree.** An agent improving a depth-6
  subcommission needs that one package (uniform slots, so it knows where
  everything is) and its parent's expectations of it (the contract types it
  must keep satisfying). Nothing else.
- **The contract bounds the blast radius.** Every edit is local to one
  package, and every package is independently verifiable through its own
  colocated tests.
- **The consumer never feels the depth.** One import line at the top,
  regardless of how much mass accretes underneath:

  ```python
  from vibrantine.examples.recursive_research import RecursiveResearchCommission
  ```

- **Navigation is level by level, the way the runtime works.** Each BRIEF
  orients one level only. Ten levels of identical shape are far cheaper to
  navigate than three levels of varied shape, and a glob for
  `commission.py` or `BRIEF.md` enumerates the whole neighborhood when
  someone wants a map.

Filesystem depth is still not invocation depth. A directory is earned by
authoring size and private ownership, never by call-graph position.
RecursiveResearch calling itself three levels deep at runtime is still one
package, because the recursion reuses one class.

## The Slots

Every folder-sized Commission has the same slots. The less the layout
varies, the less a human or an AI assistant has to guess.

- **`__init__.py`** re-exports the public Commission class and its public
  input/output types. No behavior, no cleverness. One public Commission
  class per package.
- **`commission.py`** holds the `Commission[...]` subclass: identity
  ClassVars, toolbox construction, and either the `build_user_message` hook
  (basic Commission) or the custom `_run` (custom Commission). Ordinary
  helper functions live here or beside their single consumer. Helpers are
  not subcommissions.
- **`types.py`** holds the Pydantic models: the public input/output pair
  and intermediate payload models that cross internal boundaries.
- **`models.py`** (optional, provisional) holds the Commission's model
  menu: a frozen dataclass declaring the tree's LLM seats (the
  Commission's own loop plus one seat per LLM-bearing subcommission). A
  seat is a pure catalog name resolved against the run's model catalog
  (a name paints that whole subtree), or the child's own menu type
  (fine-grained, recursively); the profiles the names resolve to live in
  `run_commission(models=[...])`, never here. The parent's `__init__` resolves
  each seat with one fallback chain: named seat, then menu `default`,
  then the run's default. Dumb data, no behavior. The Commission
  declares its seats (identity); the caller fills them (capacity);
  nothing in a Commission body names a literal model. Earned when a tree
  grows a second seat worth naming; a single-seat Commission keeps the
  plain `model=` kwarg.
- **`prompts/`** holds prompt text as markdown, one file per stable
  prompt-bearing step, `system.md` by convention for the main prompt. Long
  prompts diff better as markdown and stay out of Python noise. A
  module-sized Commission keeps its prompt as an inline constant instead.
- **`tools/`** holds private deterministic tools this Commission owns:
  substantial capabilities that are too large to be helpers, but do not
  contain an LLM anywhere in their subtree. A small tool is a single module;
  a larger private tool may earn its own module cluster only when the
  Commission has truly outgrown a file. The `__init__.py` is empty and
  exists for importability; it must never become a registry. Reusable tools
  promote out to the shared tools layer; LLM-bearing children belong in
  `subcommissions/` instead.
- **`subcommissions/`** holds the children this Commission owns: full
  Commissions, and only Commissions. A small child is a single module; a
  folder-sized child is this same package shape, recursively. The
  `__init__.py` is empty and exists for importability; it must never grow
  into a second public surface.
- **`tests/`** holds the package's tests. A module-sized child's tests
  live in its parent's `tests/`; when the child escalates to a package, its
  tests move inside it.
- **`BRIEF.md`** is the plain-language home base (next section).

## BRIEF.md

Every folder-sized Commission carries a `BRIEF.md`. The name is deliberate:

- In real-world commissioning (art, design, architecture), the brief is the
  plain-language statement of what was commissioned, for whom, and within
  what constraints. It extends the Commission coinage.
- READMEs multiply and blur. A coined filename means exactly one thing in
  this codebase, so both humans and dev agents pattern-match it correctly
  instead of treating it as generic repo documentation.

A BRIEF covers, in plain language: purpose, maturity and release status,
the input and output in one breath, what each subcommission is for (one
level only, never the whole tree), known limitations and failures, and
anything a maintainer must not break. For LLM-driven Commissions it also
names the success criteria, failure criteria, and evaluation cases that define
whether the Commission is effective. Claims that matter should be enforced by
tests or active heuristic evaluation, because prose drifts under years of
agentic edits.

## Tests Are Colocated

Tests live inside the Commission package, not in the repository's flat
`tests/` directory. Two reasons:

- **Self-containment.** An agent editing a package finds the tests beside
  the code. A deep tree's tests do not pile up in one distant directory.
- **Promotion stays one move.** Because a package carries its code, types,
  prompts, brief, and tests, lifting it out is a single `git mv` plus an
  import fix, not a coordinated multi-directory refactor.
- **Evaluation travels with behavior.** LLM-driven Commissions can keep small
  heuristic eval cases beside the prompt and code they protect.

The packaging configuration excludes in-package `tests/` directories from
the built distribution (verified at the v0.5.0 wheel inspection: zero test
files, `prompts/*.md` carried). The repository's existing flat `tests/`
continues to hold tests for module-sized Commissions and the framework
itself.

## Promotion: Reuse Is The Trigger, Not Depth

A subcommission stays private to its parent while its parent is its only
plausible consumer. The moment it has a second consumer, promote it to a
public sibling package under `src/vibrantine/examples/`. Because it is
already in standard shape, promotion is a directory move, not a rewrite.

The same reuse trigger applies to private tools. A deterministic tool that
has a second consumer promotes to the shared tools layer (or, in an
external repo, that repo's shared tools package). If the tool grows an LLM
anywhere in its subtree, it is promoted conceptually first: it becomes a
Commission, then follows the subcommission promotion rule.

Depth alone is not a smell. A deep chain of genuinely single-consumer
subcommissions is legitimate and stays put. Reuse is the only flattening
force, and it is sufficient: real trees stay shallower than their call
graphs because the useful pieces keep getting promoted.

## What Stays Out

- **No generated index, registry, or tree-overview file.** The uniform
  slots make the tree enumerable by glob; an authored overview is exactly
  the artifact that rots under agentic edits. The structure is the map.
- **No internal layers** (`services/`, `engine/`, `adapters/`, `domain/`).
  The named slots already cover the contract-shaped pieces: deterministic
  private capabilities go in `tools/`, LLM-bearing children go in
  `subcommissions/`, and ordinary helpers stay next to their consumer.
- **No mirroring of the call graph.** Directories come from authoring size
  and ownership only.
- **No alias tables or orientation modules** that hide real class names.
  The constructor and `toolbox` remain the source of truth for behavior.

## Superseded Alternatives

The earlier draft weighed five layouts: minimal package, prompt-centered,
step-centered, component-centered, and README-first. The recursive standard
absorbs them: the minimal package is the base shape, `prompts/` is a fixed
slot rather than an escalation, the component-centered layout became the
`subcommissions/` rule once it was recognized that LLM-bearing components
are Commissions in themselves, and the README-first audit requirement
became BRIEF.md. A private `tools/` slot was later added for deterministic
capabilities that are larger than helpers but still not Commissions. A
`steps/` directory was dropped: a step with real independent logic is
either a helper (stays in `commission.py`), a deterministic tool (belongs
in `tools/`), or a Commission (belongs in `subcommissions/`). A local
`contract.py` was rejected for colliding with `src/vibrantine/contract.py`;
`types.py` stands.

## Open Decisions

- The module-to-package escalation threshold: currently judgment-based
  (multiple prompt-bearing steps, several children, or a prompt long enough
  to deserve its own file). Does it need a sharper rule?
- Where private raw LLM response models live: `types.py` by default, or
  beside the code that consumes them?
- Whether `BRIEF.md` is required for every folder-sized Commission or only
  for provisional and near-public ones. Current lean: required; the slot
  being always present is worth more than the writing cost.
- The model menu (`models.py`) is provisional until proven by a real
  consumer. The first wiring landed in `recursive_research/models.py`
  (`RecursiveResearchModelMenu`), which proved the plumbing but is a
  single-class tree. Two questions this note once held open were settled
  by the model catalog (2026-07-12): the menu's relation to model
  ownership (a seat is the distributed model *choice*, a bare catalog
  name; the profile it resolves to is defined once in
  `run_commission(models=[...])`), and per-seat client injection (dead: model
  access flows only through the run's catalog, and `client=` is gone).
  What remains open is the seat-name vocabulary (per child class vs per
  subtree role), which needs a true multi-seat Commission to answer.

## What Bites Next

The open threads in the order they will actually block work:

1. **Model-menu seat vocabulary** waits on a genuine multi-seat
   Commission; there is no honest test case yet.
2. **Shared-type promotion friction** (EmailHandler's `IncomingEmail`)
   only matters when a subcommission carrying a shared type is actually
   promoted; recorded in the sketch findings.

Settled by the first package migration: prompts load from package resources
with `importlib.resources`, and the wheel excludes colocated `tests/` while
including `prompts/*.md`.

## Sketch Findings

Before adoption, each existing Commission was sketched into the standard
without moving code: MorningBriefing (custom coordinator), RecursiveResearch
(recursive LLM loop), and EmailHandler (parent plus stub children). The
verdicts all landed, so the full sketches retired to git history (pruned
2026-07-13); the layouts they proposed are now visible in
`src/vibrantine/examples/` itself. What the pass established:

- **The escalation threshold sharpened.** Having children does not earn a
  package (MorningBriefing in its original shape); an owned prompt, private
  children, or an unscannable module do. MorningBriefing later crossed the
  threshold exactly this way: the 2026-07-06 reinterpretation gave it private
  children (Weather, NewsDigest), which is what earned the package.
- **Module-sized subcommissions keep their boundary types beside their
  class**, not hoisted into the parent's `types.py`. The same logic
  applies to private tools and partially settles the raw-LLM-models open
  decision: types live with their owner.
- **Shared types are the first promotion friction.** A type used by both
  parent and child lives with the parent; promoting the child means
  moving or splitting it. Acceptable, but "promotion is one git mv"
  carries this caveat.
- **`prompts/` cannot be adopted before the loading decision.** Both LLM
  sketches hit it immediately; settled since by the first package
  migration (`importlib.resources`, per What Bites Next above).
