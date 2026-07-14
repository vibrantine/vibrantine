# Vibrantine

[![CI](https://github.com/vibrantine/vibrantine/actions/workflows/ci.yml/badge.svg)](https://github.com/vibrantine/vibrantine/actions/workflows/ci.yml)
[![Latest tag](https://img.shields.io/github/v/tag/vibrantine/vibrantine?label=latest)](CHANGELOG.md)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Vibrantine compresses complex AI behavior into typed tools an AI can
call with a minimal footprint. When every small part can be tested and
fixed by itself, the whole becomes something a caller, human or model,
can trust.**

Each part is a **Commission**: a work order for an AI, with an agreed
job, an agreed shape for the answer, a spending limit, and a receipt
showing what happened. In software terms, Vibrantine is a Python
component model for building reliable AI agents, and a Commission is a
bounded work order with typed input, typed output, structured failure,
provenance, and cost.

A Commission can be one small act, like "summarize these notes." It can be a codebase migration: a coordinator that maps the
dependency graph, fans out one analyzer per module, recurses into each
package to rewrite and test file by file, and passes every change
through reviewer Commissions before concluding. It can be a contract
review that works a thousand-document data room: one recursive
Commission per folder, one per document, one per flagged clause, each
returning typed findings the level above can inspect before rolling
them up. Two Commissions or twenty, the boundary at every node is the
same.

## At a Glance

- **Language:** Python 3.12+, async-first with a sync wrapper.
- **Dependencies:** three (`pydantic` v2, `httpx`, `openai` as the
  client for any OpenAI-compatible endpoint; OpenRouter by default).
- **Status:** early-stage, pre-1.0. Releases are git tags; latest is
  `v0.5.0`. Not on PyPI yet, so `pip install vibrantine` will not
  install this project.
- **License:** MIT.
- **Requires:** an `OPENROUTER_API_KEY` for LLM-backed runs. Tests and
  deterministic tools need no key.

## Why

Agent systems become hard to trust when every part can read shared
state, mutate shared context, or hand vague prose to the next agent:
errors and costs compound with no clear place to inspect or recover. A
prompt string is not a software component. Vibrantine makes the unit of
AI work a real component instead: typed input in, bounded work inside,
one structured result envelope out, with failure, cost, and provenance
on the envelope. Larger behavior is built by nesting these units, and
the boundary holds at every size.

The payoff compounds when the caller is itself a model. When a parent
Commission's model coordinates children, each child appears to it as
one typed tool: a name, a description, an input schema, and a
structured receipt coming back. However much work happened inside the
child, only that surface enters the parent's context. Choosing among
trustworthy work orders and inspecting typed results is a far easier
decision problem than steering prose through a pipeline, so even a
modest model can orchestrate a large tree well.

That reliance is earned, because each Commission can be isolated,
tested, and improved on its own. A failure surfaces at the node that
produced it, as a typed error with its own cost receipt, instead of
dissolving into system-wide misbehavior. When quality drifts, you can
pin the weakness to one Commission, strengthen or swap that component,
and prove the fix at its boundary. Debugging works the way it does in
ordinary software: one component at a time. The same boundary that
makes each part testable by a human makes the whole tractable for an
AI.

## Example

The factory,
[`create_commission`](docs/authoring.md#the-shortcut-create_commission),
builds a working Commission from the decisions no one can make for you:
what goes in, what comes out, what it is called.

```python
from pydantic import BaseModel, Field

from vibrantine import create_commission, run_commission_sync


class BriefInput(BaseModel):
    question: str = Field(description="The question the brief should answer.")
    source_notes: list[str] = Field(description="Notes to ground the brief.")


class BriefOutput(BaseModel):
    answer: str = Field(description="The direct answer to the question.")
    key_claims: list[str] = Field(description="Important claims in the answer.")


research_brief = create_commission(
    name="research_brief",
    description="Create a grounded research brief from supplied notes.",
    input=BriefInput,
    output=BriefOutput,
)

result = run_commission_sync(
    research_brief,
    BriefInput(
        question="What are the main risks in this proposal?",
        source_notes=[
            "The project depends on an unstable third-party API.",
            "The team has not yet validated demand with target users.",
        ],
    ),
    budget_usd=0.10,
)

if result.status == "success" and result.output is not None:
    print(result.output.answer)
else:
    print(result.error)
```

The result is a
[`CommissionResult`](docs/authoring.md#the-result-envelope): `status` is
`success`, `partial`, or `failure`; `output` is the declared type;
`error` is a value, never a raised exception; `cost` and `provenance`
ride along. That envelope, and the `budget_usd` cap on the call, are the
contract every Commission honors, hand-written or factory-made.
Everything on the boundary is documented in
[docs/authoring.md](docs/authoring.md); the reasoning behind it is in
[docs/design.md](docs/design.md).

## What Vibrantine Is Not

A Commission is an act, not an application. It begins, does bounded
work, returns a receipt, and is gone. Anything that must live between
runs (state, memory, schedules, user interfaces, the decision of what
to run next) is application code you own, built above the library.
Vibrantine gives you the trustworthy work units; the application is
still yours to write.

So Vibrantine is not a graph runtime, a chat framework, or an
orchestration DSL, and it is not trying to become your scheduler,
memory layer, or assistant platform. It is the component boundary those
systems lack.

## Installation

Not on PyPI yet. Pin a release tag (see `CHANGELOG.md`) rather than
`main`:

```bash
uv add "vibrantine @ git+https://github.com/vibrantine/vibrantine.git@v0.5.0"
```

Set `OPENROUTER_API_KEY` in the environment before running LLM-backed
Commissions.

## Documentation

- [docs/commission-model.md](docs/commission-model.md): how the Commission
  model works, conceptually. The boundary, the five surfaces, the
  result envelope, composition. Start here to understand.
- [docs/authoring.md](docs/authoring.md): how to build a Commission.
  Tutorial plus full contract reference; every code block verified
  against a live model, machine-checked in CI.
- [docs/running.md](docs/running.md): what you control and what you can see
  when you run a tree. Budgets, fuses, observability, and the short
  list an operator holds in their head.
- [docs/commission-testing.md](docs/commission-testing.md): how to prove a
  Commission obeys the contract (scripted models, no API key) and is
  good at its stated work.
- [docs/design.md](docs/design.md): why the library is shaped this way,
  what that costs, and what is refused on purpose.
- [docs/design-decisions.md](docs/design-decisions.md): the ruling
  record. Every settled design decision, its reason, and what it rules
  out, plus what is planned but not built.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run basedpyright
```

Unit tests script the model and need no API key. Contributions should
preserve the central contract: typed input and output, errors as
values, parent-mediated composition, cost and provenance on every
result, stateful product concerns kept above the library.

## License

MIT. See [LICENSE](LICENSE).

*The aim is not to remove judgment from AI systems. The aim is to make
judgment composable.*
