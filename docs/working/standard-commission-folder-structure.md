# Standard Commission Folder Structure

This note records the chosen standard layout for folder-sized commissions,
the decisions behind it, and what remains open. It supersedes the earlier
alternatives analysis (summarized at the end).

## The Standard

A commission is a single module until it outgrows one. When it does, it
becomes a package with this available shape; optional slots such as
`models.py`, `prompts/`, `tools/`, and `subcommissions/` are omitted until
earned:

```text
src/vibrantine/commissions/deep_research/
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
itself a commission, and follows the same module-or-package rule with the
same escalation criteria. `tools/` is the companion slot for deterministic
private capabilities: no LLM anywhere in their subtree, still wired through
the parent's `toolbox` when the parent's LLM may call them.

## Design Regime: Built For Massive Recursion

The standard assumes commissions will be fleshed out slowly, over years,
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
  from vibrantine.commissions.deep_research import DeepResearchCommission
  ```

- **Navigation is level by level, the way the runtime works.** Each BRIEF
  orients one level only. Ten levels of identical shape are far cheaper to
  navigate than three levels of varied shape, and a glob for
  `commission.py` or `BRIEF.md` enumerates the whole neighborhood when
  someone wants a map.

Filesystem depth is still not invocation depth. A directory is earned by
authoring size and private ownership, never by call-graph position.
DeepResearch calling itself three levels deep at runtime is still one
package, because the recursion reuses one class.

## The Slots

Every folder-sized commission has the same slots. The less the layout
varies, the less a human or an AI assistant has to guess.

- **`__init__.py`** re-exports the public commission class and its public
  input/output types. No behavior, no cleverness. One public commission
  class per package.
- **`commission.py`** holds the `Commission[...]` subclass: identity
  ClassVars, toolbox construction, and either the `build_user_message` hook
  (basic commission) or the custom `invoke` (custom commission). Ordinary
  helper functions live here or beside their single consumer. Helpers are
  not subcommissions.
- **`types.py`** holds the Pydantic models: the public input/output pair
  and intermediate payload models that cross internal boundaries.
- **`models.py`** (optional, provisional) holds the commission's model
  menu: a frozen dataclass declaring the tree's LLM seats (the
  commission's own loop plus one seat per LLM-bearing subcommission). A
  seat accepts a single model (paints that whole subtree) or the child's
  own menu type (fine-grained, recursively). The parent's `__init__`
  resolves each seat with one fallback chain: named seat, then menu
  `default`, then the system default. Dumb data, no behavior. The
  commission declares its seats (identity); the caller fills them
  (capacity); nothing in a commission body names a literal model. Earned
  when a tree grows a second seat worth naming; a single-seat commission
  keeps the plain `model=` kwarg.
- **`prompts/`** holds prompt text as markdown, one file per stable
  prompt-bearing step, `system.md` by convention for the main prompt. Long
  prompts diff better as markdown and stay out of Python noise. A
  module-sized commission keeps its prompt as an inline constant instead.
- **`tools/`** holds private deterministic tools this commission owns:
  substantial capabilities that are too large to be helpers, but do not
  contain an LLM anywhere in their subtree. A small tool is a single module;
  a larger private tool may earn its own module cluster only when the
  commission has truly outgrown a file. The `__init__.py` is empty and
  exists for importability; it must never become a registry. Reusable tools
  promote out to the shared tools layer; LLM-bearing children belong in
  `subcommissions/` instead.
- **`subcommissions/`** holds the children this commission owns: full
  commissions, and only commissions. A small child is a single module; a
  folder-sized child is this same package shape, recursively. The
  `__init__.py` is empty and exists for importability; it must never grow
  into a second public surface.
- **`tests/`** holds the package's tests. A module-sized child's tests
  live in its parent's `tests/`; when the child escalates to a package, its
  tests move inside it.
- **`BRIEF.md`** is the plain-language home base (next section).

## BRIEF.md

Every folder-sized commission carries a `BRIEF.md`. The name is deliberate:

- In real-world commissioning (art, design, architecture), the brief is the
  plain-language statement of what was commissioned, for whom, and within
  what constraints. It extends the Commission coinage.
- READMEs multiply and blur. A coined filename means exactly one thing in
  this codebase, so both humans and dev agents pattern-match it correctly
  instead of treating it as generic repo documentation.

A BRIEF covers, in plain language: purpose, maturity and release status,
the input and output in one breath, what each subcommission is for (one
level only, never the whole tree), known limitations and failures, and
anything a maintainer must not break. Claims that matter should be enforced
by tests, because prose drifts under years of agentic edits.

## Tests Are Colocated

Tests live inside the commission package, not in the repository's flat
`tests/` directory. Two reasons:

- **Self-containment.** An agent editing a package finds the tests beside
  the code. A deep tree's tests do not pile up in one distant directory.
- **Promotion stays one move.** Because a package carries its code, types,
  prompts, brief, and tests, lifting it out is a single `git mv` plus an
  import fix, not a coordinated multi-directory refactor.

Follow-up: the packaging configuration must exclude in-package `tests/`
directories from the built distribution. The repository's existing flat
`tests/` continues to hold tests for module-sized commissions and the
framework itself.

## Promotion: Reuse Is The Trigger, Not Depth

A subcommission stays private to its parent while its parent is its only
plausible consumer. The moment it has a second consumer, promote it to a
public sibling package under `src/vibrantine/commissions/`. Because it is
already in standard shape, promotion is a directory move, not a rewrite.

The same reuse trigger applies to private tools. A deterministic tool that
has a second consumer promotes to the shared tools layer (or, in an
external repo, that repo's shared tools package). If the tool grows an LLM
anywhere in its subtree, it is promoted conceptually first: it becomes a
commission, then follows the subcommission promotion rule.

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
are commissions in themselves, and the README-first audit requirement
became BRIEF.md. A private `tools/` slot was later added for deterministic
capabilities that are larger than helpers but still not commissions. A
`steps/` directory was dropped: a step with real independent logic is
either a helper (stays in `commission.py`), a deterministic tool (belongs
in `tools/`), or a commission (belongs in `subcommissions/`). A local
`contract.py` was rejected for colliding with `src/vibrantine/contract.py`;
`types.py` stands.

## Open Decisions

- Prompt loading mechanics: read `prompts/*.md` at import time (via
  `importlib.resources`) or copy into a Python constant at authoring time?
  Packaging must include prompt files reliably either way.
- The module-to-package escalation threshold: currently judgment-based
  (multiple prompt-bearing steps, several children, or a prompt long enough
  to deserve its own file). Does it need a sharper rule?
- Where private raw LLM response models live: `types.py` by default, or
  beside the code that consumes them?
- Whether `BRIEF.md` is required for every folder-sized commission or only
  for provisional and near-public ones. Current lean: required; the slot
  being always present is worth more than the writing cost.
- The model menu (`models.py`) is provisional until proven by a real
  consumer. The first wiring landed inline in `deep_research.py`
  (`DeepResearchModelMenu`), which proved the plumbing but is a
  single-class tree. Open within it: the seat-name vocabulary (per child
  class vs per subtree role, which needs a true multi-seat commission to
  answer), whether a menu ever needs per-seat client injection alongside
  the model, and how the menu relates to the planned catalog/grant
  model-ownership design.

## What Bites Next

The open threads in the order they will actually block work:

1. **Prompt loading mechanics** blocks the first real move of a prompt
   into `prompts/` (DeepResearch is the natural candidate). Everything
   else in the standard can proceed without it.
2. **The packaging exclude for colocated tests** is needed the moment the
   first commission package materializes, not before.
3. **Model-menu seat vocabulary** waits on a genuine multi-seat
   commission; there is no honest test case yet.
4. **Shared-type promotion friction** (EmailHandler's `IncomingEmail`)
   only matters when a subcommission carrying a shared type is actually
   promoted; recorded in the sketch findings.

## The Sketches

Each existing commission, sketched into the standard without moving code.
The audit questions asked of every sketch:

- What file would a human open first?
- What file would an AI assistant edit for a prompt change?
- Where would a new input field go?
- Where would a new subcommission or private tool be declared?
- Where would a known prompt failure be recorded?
- Does the folder make the commission easier to understand, or just wider?

### MorningBriefing (custom coordinator)

Today: one module with two payload types, the commission class, and a
private `_render_markdown` helper. Its children (`FetchTool`,
`SynthesizeCommission`) are public siblings, injected at construction.

```text
src/vibrantine/commissions/morning_briefing/
  __init__.py          # exports MorningBriefingCommission + I/O types
  commission.py        # MorningBriefingCommission + _render_markdown
  types.py             # MorningBriefingInput, MorningBriefingOutput
  tests/
    test_commission.py # today's tests/test_morning_briefing.py
  BRIEF.md
```

- No `prompts/`: a Python coordinator with no LLM of its own.
- No `tools/`: `FetchTool` and `WriteTool` are public shared tools, not
  private tools owned by this package.
- No `subcommissions/`: the LLM-bearing child is a public sibling; nothing
  here is privately owned.
- `_render_markdown` stays in `commission.py`: a helper, not a commission.

Audit: a human opens BRIEF.md. A prompt change points *outside* the
package: the only prompt in this tree belongs to `synthesize`, which owns
it (prompts are internal). A new input field goes in `types.py`. A new
subcommission or private tool is declared in `commission.py`'s `__init__`
(import, kwarg, toolbox entry) and lives under `subcommissions/` or
`tools/` only if private. A known failure is recorded in BRIEF.md.

Verdict: the package is barely earned. Owning children does not by itself
justify the folder; the module form remains fine until the file stops
being scannable.

### DeepResearch (recursive LLM loop)

Today: one module with one long prompt constant, two payload types, and
recursive construction (each instance builds a shallower child of the
same class).

```text
src/vibrantine/commissions/deep_research/
  __init__.py          # exports DeepResearchCommission + I/O types
  commission.py        # DeepResearchCommission
  types.py             # ResearchInput, ResearchOutput
  models.py            # DeepResearchModelMenu (provisional slot, first consumer)
  prompts/
    system.md          # today's _RESEARCH_SYSTEM_PROMPT
  tests/
    test_commission.py # today's tests/test_deep_research.py
  BRIEF.md
```

- No `subcommissions/`: the recursive child is this same class (no second
  file), and `FetchTool` is a public sibling. Runtime depth N, one
  package: the regime rule in the flesh.
- No `tools/`: `FetchTool` is a shared public tool, not privately owned by
  this package.
- `prompts/system.md` would be the library's first externalized prompt,
  so an actual move must first settle the prompt-loading open decision.

Audit: a human opens BRIEF.md; an assistant edits `prompts/system.md` for
a prompt change; a new input field goes in `types.py`; a new
subcommission or private tool is declared in `commission.py`'s
constructor; a known prompt failure goes in BRIEF.md.

Verdict: the strongest fit; earns every slot except the private-owned
`tools/` and `subcommissions/` slots.

### EmailHandler (parent plus stub children)

Today: one module holding three commission classes and five payload
types; its most important fact (provisional, handlers are stubs) lives in
the module docstring.

```text
src/vibrantine/commissions/email_handler/
  __init__.py          # exports EmailHandlerCommission + I/O types only
  commission.py        # EmailHandlerCommission
  types.py             # IncomingEmail, EmailHandlerInput, Route, EmailHandlerOutput
  prompts/
    system.md          # today's _SYSTEM_PROMPT
  tools/
    __init__.py        # empty
    notify_user.py     # NotifyUserTool + NotifyInput/Output
  subcommissions/
    __init__.py        # empty
    draft_reply.py     # DraftReplyCommission + DraftReplyInput/Output
  tests/
    test_commission.py # today's tests/test_email_handler.py
  BRIEF.md             # provisional status, and what each stub probes
```

- The only sketch that exercises both private-owned slots:
  `subcommissions/` for the LLM-bearing `DraftReplyCommission`, and
  `tools/` for the deterministic `NotifyUserTool`. Both are module-sized,
  and each keeps its boundary types beside its class.
- `IncomingEmail` is shared: it appears in the parent's input and inside
  `DraftReplyInput`. It stays in the parent's `types.py` and the child
  imports it. Fine while the child is private, but promoting
  `draft_reply` would have to take the shared type along or split it.
  First concrete case of promotion friction.

Audit: same answers as above, with two sharpenings: the provisional
framing moves from module docstring to BRIEF.md, and "where is a new
subcommission or private tool declared" now has a two-part answer (a
module under `subcommissions/` or `tools/`, plus the constructor wiring in
`commission.py`).

Verdict: the sketch where the layout adds the most: the stubs stop
masquerading as one flat file of six equal classes.

### Findings

- **The escalation threshold sharpened.** Having children does not earn a
  package (MorningBriefing); an owned prompt, private children, or an
  unscannable module do.
- **Module-sized subcommissions keep their boundary types beside their
  class**, not hoisted into the parent's `types.py`. The same logic
  applies to private tools and partially settles the raw-LLM-models open
  decision: types live with their owner.
- **Shared types are the first promotion friction.** A type used by both
  parent and child lives with the parent; promoting the child means
  moving or splitting it. Acceptable, but "promotion is one git mv"
  carries this caveat.
- **`prompts/` cannot be adopted before the loading decision.** Both LLM
  sketches hit it immediately; it is the next decision the standard
  actually blocks on.
