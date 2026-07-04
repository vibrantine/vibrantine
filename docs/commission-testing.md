# Commission Testing

How to prove a commission still satisfies its contract and is effective at the
LLM-shaped work it claims to do.

## Two Test Lanes

Every shipped or worked-example commission needs two kinds of confidence:

1. **Contract tests** prove the component boundary works. They use fake clients,
   scripted model responses, deterministic tools, and no API key.
2. **Heuristic evaluation tests** prove the commission is actually good enough
   at its stated LLM task. They run representative cases with explicit success
   and failure criteria.

Contract tests answer "does this commission obey the framework contract?"
Evaluation tests answer "does this commission do useful work?"

## Contract Tests

Unit tests must require no credentials. LLM-backed commissions inject a fake
`AsyncOpenAI`-shaped client through `client=` and script provider responses.
The model's intelligence is not under test; the commission's behavior around
the scripted response is.

Cover the contract surface the commission exercises:

- Public import works from the intended module or package path.
- Constructor injection works for `model`, `client`, child commissions/tools,
  and test doubles.
- Typed input is rendered correctly by `build_user_message`, or a custom
  `invoke` preserves the typed input semantics.
- Success returns `CommissionResult(status="success")` with validated output.
- Failures and partials return `CommissionResult(status="failure"|"partial")`;
  no exception crosses the `invoke` boundary.
- Cancellation is checked before expensive or irreversible work.
- Budget behavior is covered where the commission spends model money.
- Cost is reported, and child cost rolls up when children are dispatched.
- Progress events are covered if the commission emits them.
- LLM-loop commissions cover tool menu shape, malformed/no-tool provider
  responses, and conclusion through the `conclude` tool.
- Coordinators cover child dispatch order/parallelism policy, partial child
  failures, and output assembly.
- Folder-sized commissions cover prompt/resource loading if they use
  `prompts/`.

Integration tests are optional, marked `@pytest.mark.integration`, and skip
when credentials are absent. They are smoke tests, not the backbone.

## Evaluation Tests

Every LLM-driven commission should carry active heuristic evaluation once it is
more than a mechanical contract probe. A commission's `BRIEF.md` states the
behavioral promise in plain language; its evaluation cases should turn that
promise into pass/fail criteria.

An evaluation case records:

- **Input**: the typed input, plus any relevant fixtures or seed sources.
- **Success criteria**: observable requirements the output must satisfy.
- **Failure criteria**: mistakes that make the result unacceptable.
- **Scoring method**: deterministic checks, heuristic assertions, human review,
  or judge-model rubric.
- **Status**: pass, fail, expected-fail, or watchlist.

Prefer deterministic or cheap heuristic checks first:

- Required fields are populated with meaningful content.
- Claims cite allowed sources and avoid uncited load-bearing assertions.
- The answer contains required facts and omits known-bad facts.
- The route/decision matches a labeled fixture.
- The output stays within requested scope, tone, or safety constraints.
- The commission uses or avoids tools as the case requires.

Use judge-model evaluation only when deterministic checks cannot express the
quality bar. Judge prompts are themselves test assets: keep them stable, version
them with the cases, and treat changed judge prompts as evaluation changes.

## Where Tests Live

Module-sized commission:

```text
tests/test_my_commission.py
```

Folder-sized commission:

```text
src/vibrantine/commissions/my_commission/
  tests/
    test_commission.py
    test_eval.py       # when heuristic cases exist
```

Evaluation fixtures may live under the commission package when they are owned
by that commission:

```text
src/vibrantine/commissions/my_commission/
  tests/
    fixtures/
      cases.jsonl
```

Keep large or sensitive evaluation corpora out of the package. Store only small
fixtures that are safe to publish, and document external/private corpora in
`BRIEF.md`.

## BRIEF.md Notes

Each folder-sized commission's `BRIEF.md` should name its efficacy bar:

```text
success criteria:
failure criteria:
known failures:
eval cases:
prompt changes tried:
```

The goal is not to pretend LLM behavior is perfectly unit-testable. The goal is
to make quality claims falsifiable, keep prompt changes from being vibes-only,
and preserve a trail of what the commission is known to handle.
