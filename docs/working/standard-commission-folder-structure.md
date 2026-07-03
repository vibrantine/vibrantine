# Standard Commission Folder Structure Planning

This is a working note for choosing a standard layout for folder-sized
commissions. It focuses on commissions large enough to outgrow a single module:
long prompts, several payload types, child commissions/tools, prompt notes,
examples, or tests whose intent is hard to see from filenames alone.

The goal is not to invent ceremony. The goal is to make a commission easy to
read, author, test, and safely modify by both a human maintainer and an AI
assistant.

## The Clear Issues

### 1. The Reader Needs A Map Before They Need Detail

A folder-sized commission has several kinds of information:

- the contract boundary: input, output, result shape, error vocabulary;
- the public commission class: name, description, toolbox, prompt hook or
  custom invoke;
- prompt text and prompt rationale;
- child commissions and tools;
- tests and scripted fake conversations;
- examples or evaluation notes.

If those are scattered by accident, the reader has to reconstruct the system
from file names. That is friction for humans and a real risk for AI assistants,
which will happily edit the first plausible file unless the structure makes the
right file obvious.

### 2. Python Import Shape And Authoring Shape Pull Apart

The import surface wants a small, stable public face:

```python
from vibrantine.commissions.research_brief import ResearchBriefCommission
```

The authoring surface wants room:

- types separate enough to scan;
- prompts separate enough to edit without Python noise;
- implementation separate enough that child orchestration is not buried under
  schema declarations;
- tests close enough in naming that drift is easy to spot.

The standard needs to satisfy both without making every commission feel like a
mini-framework.

### 3. Prompt Files Are Tempting, But They Change The Failure Modes

Keeping prompts in `.md` files is pleasant for humans. It makes long prompts
easier to read, diff, and discuss. It also introduces choices:

- Is the prompt loaded at import time or copied into a Python constant?
- Does packaging include the prompt file reliably?
- Can tests assert the prompt text without brittle filesystem assumptions?
- Does the public class still make its stable system prompt obvious?

Inline prompts are boring and robust. External prompt files are nicer once a
prompt is long enough to deserve its own artifact.

### 4. AI Authors Need Repeated Slots, Not Cleverness

A human can infer that `schema.py`, `models.py`, and `types.py` are probably
related. An AI assistant does better when every folder repeats the same slots:

- `types.py` means typed boundary models;
- `prompt.md` means system prompt text;
- `commission.py` means the public commission implementation;
- `README.md` means intent, limitations, and examples;
- tests mirror the package name.

The less the layout varies, the less an assistant has to guess.

### 5. Folder-Sized Does Not Mean Unlimited Internal API

The contract still wants the smallest viable thing. A folder layout should not
be permission to create layers like `services/`, `engine/`, `adapters/`, and
`domain/` unless the commission has actually earned them.

The standard should say where to put common pieces and when to stop.

### 6. Filesystem Depth Is Not Invocation Depth

A supercommission may invoke a child, which invokes a child, which invokes a
child. The runtime tree can be deep without the folders becoming deep. Mirroring
the call graph in the filesystem would make reuse awkward and make ordinary
refactors feel like package moves.

Design for 5-20 related commissions in one neighborhood, not for 20 nested
folders. Keep the filesystem shallow unless a second axis of organization earns
its own directory.

## Evaluation Criteria

Use these questions to judge each layout:

- Can a new human reader find the input, output, prompt, toolbox, and invoke
  path in under one minute?
- Can an AI assistant identify the safe edit target from the user's wording?
- Does the package expose one clear commission class?
- Does the layout scale from one long prompt to several internal steps?
- Are tests and examples named so drift is visible?
- Does the layout avoid new abstractions that the contract does not need?
- Can packaging include every runtime artifact without special tricks?

## Alternative A: Minimal Package

```text
src/vibrantine/commissions/research_brief/
  __init__.py
  commission.py
  types.py
  prompt.md
  README.md

tests/
  test_research_brief.py
```

### Shape

- `__init__.py` re-exports the public class and public input/output types.
- `commission.py` contains the `Commission[...]` subclass and private helpers.
- `types.py` contains Pydantic input/output/intermediate payload models.
- `prompt.md` contains the stable system prompt if it is too large for a Python
  constant.
- `README.md` records purpose, release status, limitations, and short examples.
- `tests/test_research_brief.py` mirrors the public package.

### Strengths

- Easy to explain and remember.
- Fits most folder-sized commissions without extra vocabulary.
- Gives AI assistants stable edit targets.
- Keeps runtime code small: one implementation file, one type file.
- Makes the public class easy to find.

### Weaknesses

- `commission.py` can still become too large if the commission has several
  meaningful internal phases.
- `types.py` can become a mixed bag if it holds public models, private raw LLM
  models, and child result models.
- Prompt notes and evaluation notes may accumulate in `README.md` unless there
  is a later place for them.

### Best Fit

The default for the first folder-sized standard. Use it for a commission that
has one primary prompt, one public class, and a handful of Pydantic models.

## Alternative B: Prompt-Centered Package

```text
src/vibrantine/commissions/research_brief/
  __init__.py
  commission.py
  types.py
  prompts/
    system.md
    review.md
  README.md

tests/
  test_research_brief.py
```

### Shape

This is the minimal package with a `prompts/` directory. It names prompt parts
explicitly when a commission has more than one stable prompt-bearing step.

### Strengths

- Comfortable for humans working on prompt quality.
- Keeps long prompt diffs away from Python diffs.
- Makes multi-step prompt ownership visible.
- Gives room for prompt variants without hiding them in code.

### Weaknesses

- Requires packaging discipline for prompt files.
- Makes it easier to proliferate prompt fragments before the commission earns
  them.
- AI assistants may edit prompt text without checking the code path that loads
  it unless the README is explicit.

### Best Fit

Commissions where prompt authoring is the main maintenance surface: research,
review, classification, extraction, or report-writing workflows with long
instructions.

## Alternative C: Step-Centered Package

```text
src/vibrantine/commissions/research_brief/
  __init__.py
  commission.py
  types.py
  steps/
    plan.py
    read.py
    review.py
    assemble.py
  prompts/
    plan.md
    review.md
    assemble.md
  README.md

tests/
  test_research_brief.py
  test_research_brief_steps.py
```

### Shape

The top-level `commission.py` owns the contract and orchestration. Each file in
`steps/` owns a named internal phase only when that phase has enough logic to
stand on its own.

### Strengths

- Very readable for multi-phase custom coordinators.
- Helps humans reason about the process in the same terms as the docs:
  `plan -> fan-out -> review -> assemble`.
- Lets tests target tricky phases directly when they are deterministic helpers.
- Keeps `commission.py` from turning into a long scroll.

### Weaknesses

- Risk of over-modularizing ordinary helper code.
- The boundary between a step helper and a child commission can become blurry.
- More files means more places for an AI assistant to make a plausible but
  wrong edit.
- If steps start holding state or talking sideways, the layout can accidentally
  obscure contract violations.

### Best Fit

Custom coordinator commissions with several real phases, especially when the
phase names are part of the design explanation. Avoid it for basic
LLM-loop commissions.

## Alternative C2: Component-Centered Package

```text
src/vibrantine/commissions/deep_research/
  __init__.py
  commission.py
  types.py
  README.md
  prompts/
    plan.md
    consolidate.md
    adversarial_review.md
  components/
    plan.py
    fan.py
    doc_reader.py
    consolidation.py
    adversarial_review.py

tests/
  test_deep_research.py
```

### Shape

The parent commission is the public object. `components/` holds private child
commissions or substantial implementation pieces owned by that parent. Runtime
nesting does not imply more folder nesting: a component may itself call children
without living under another component's directory.

`components/` does not get an `__init__.py` by default. Add one only when it
serves as a private facade for repeated imports, and do not let it become a
second public surface.

### Strengths

- Handles 5-20 owned pieces without making `commission.py` a long import list.
- Keeps the public package shallow even when the invocation tree is deep.
- Makes ownership clear: these pieces belong to this larger commission.
- Gives humans and AI assistants a predictable place to look for private child
  commissions.

### Weaknesses

- Reusable children can become hidden inside the parent package after they
  deserve first-class packages.
- `components/` can become a drawer for unrelated helpers unless the standard
  distinguishes child commissions from ordinary functions.
- Without discipline, this can drift toward a private framework inside one
  commission.

### Best Fit

A large public commission whose children are meaningful but mostly private to
that workflow. If a child is useful outside the parent, promote it to a sibling
commission package instead of burying it in `components/`.

## Alternative D: Contract-First Package

```text
src/vibrantine/commissions/research_brief/
  __init__.py
  contract.py
  commission.py
  prompt.md
  examples.py
  README.md

tests/
  test_research_brief_contract.py
  test_research_brief.py
```

### Shape

This renames `types.py` to `contract.py` inside the commission package. The
local `contract.py` holds the public input/output plus any private schema that
crosses an internal LLM boundary.

### Strengths

- Reinforces that the typed boundary is the load-bearing artifact.
- Matches the language of the library: the contract is sacred.
- Makes schema review feel like a first-class release step.

### Weaknesses

- Creates a name collision with `src/vibrantine/contract.py`; imports could
  become visually confusing.
- `contract.py` may attract too much: result helpers, constants, and policy
  notes that belong elsewhere.
- `types.py` is more conventional and lower-friction for Python readers.

### Best Fit

External consumer repos or tutorial commissions where teaching the boundary is
more important than minimizing naming ambiguity. Probably not the default inside
the library unless the name proves itself.

## Alternative E: README-First Package

```text
src/vibrantine/commissions/research_brief/
  __init__.py
  README.md
  commission.py
  types.py
  prompt.md
  evals.md

tests/
  test_research_brief.py
```

### Shape

The file layout is close to Alternative A, but the convention says every
folder-sized commission must have a README with the audit template filled in:
purpose, input, output, interior style, toolbox, prompt, failures, budget,
progress, tests, known limitations, and release decision.

### Strengths

- Excellent for human orientation.
- Turns the pre-release audit template into a living commission-level note.
- Gives AI assistants a high-signal context file before code edits.
- Keeps limitations and release status close to the implementation.

### Weaknesses

- Documentation can drift if tests do not enforce the important claims.
- More work for small folder-sized commissions.
- Could become repetitive with `docs/building-a-commission.md` unless scoped to
  commission-specific facts.

### Best Fit

Worked examples, provisional commissions, or anything near the public surface
where maturity and limitations must be unmistakable.

## A Likely First Standard

Start with Alternative A plus one rule from Alternative E:

```text
src/vibrantine/commissions/<name>/
  __init__.py
  commission.py
  types.py
  prompt.md
  README.md

tests/
  test_<name>.py
```

Use these escalation rules:

- Add `prompts/` only when there is more than one stable prompt artifact.
- Add `steps/` only when named phases have real independent logic.
- Add `components/` when a parent owns several private child commissions or
  tool-like implementation pieces.
- Add `evals.md` only when prompt experiments or known failures are important
  enough to preserve.
- Keep one public commission class per folder unless there is a strong reason
  the children are authored and tested as private implementation details.
- Keep `__init__.py` boring: re-export public names, no behavior.
- Do not mirror the invocation tree with nested folders. Keep owned components
  flat until a real second axis of organization appears.
- Do not add `components/__init__.py` by default. Add it only as a private
  facade for repeated imports.

Optional orientation files should stay non-authoritative:

- `components.py` may re-export or list owned private components when imports
  get noisy.
- The constructor and `toolbox` remain the source of truth for behavior.
- Avoid alias tables that hide class names. Short names are useful only when
  they are clearer than the real names.

## Open Decisions

- Should prompt text live in `.md` by default once a commission gets a folder,
  or only after a line-count threshold?
- Should `README.md` be required for every folder-sized commission, or only for
  worked examples and provisional commissions?
- Should private raw LLM response models live in `types.py`, or beside the code
  that consumes them?
- Should tests stay one mirrored file by default, or should folder-sized
  commissions get `tests/commissions/<name>/` once test helpers appear?
- Should the standard bless `types.py` despite the library's contract language,
  or use `contract.py` locally and accept the name collision risk?
- When does a private child commission graduate from `components/` into a
  sibling public commission package?
- Should `components.py` be blessed as an optional orientation index, or left as
  an ad hoc escape hatch once the first large commission earns it?

## Next Exercise

Take one existing commission and sketch it into the likely first standard
without moving code:

- `MorningBriefingCommission`: best test for a custom coordinator with children.
- `DeepResearchCommission`: best test for recursive LLM-loop toolbox behavior.
- `EmailHandlerCommission`: best test for provisional status and stub clarity.

For each sketch, ask:

- What file would a human open first?
- What file would an AI assistant edit for a prompt change?
- Where would a new input field go?
- Where would a new child commission be declared?
- Where would a known prompt failure be recorded?
- Does the folder make the commission easier to understand, or just wider?
