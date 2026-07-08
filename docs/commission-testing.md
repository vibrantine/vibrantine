# Commission Testing

How to prove a Commission still satisfies its contract and is effective at the
LLM-shaped work it claims to do.

## Principles

Five principles govern this document. The first four are about truth; the
fifth is about your time. Everything below is an application of them.

1. **Test the promise, not the interior.** Tests attach to the contract: what
   goes in, what comes out, what the output promises to be. Prompts,
   decomposition style, and control flow are interior and free to change. The
   eval suite is what makes that freedom safe: it catches what a prompt
   rewrite silently broke.
2. **Two lanes, never mixed.** A contract-test failure means the code broke. An
   efficacy-test failure means behavior drifted. A suite that blends the lanes
   produces failures you cannot interpret.
3. **Let exactly one thing vary.** In an efficacy run the thing under test is
   the Commission's competence, so pin everything else: fixture sources
   instead of the live web, a named model instead of the current default. A
   pinned eval that fails means the Commission changed. Swapping the pinned
   model is a different experiment; run one at a time.
4. **Write pass/fail rules before seeing output, and give every case a way to
   fail.** Criteria written after looking at output always pass. Fixtures
   carry targets and traps, both planted at authoring time. Human judgment is
   spent once, when the case is written, so the check repeats for free.
5. **Match the cost of a check to how often it must run.** Deterministic
   checks run every time. Human review runs when a person has attention to
   spend, so it reviews the residue that resists pre-written criteria. A
   judge model is a recurring human review made automatic, adopted only once
   it earns its maintenance cost.

## Two Test Lanes

Every shipped or worked-example Commission needs two kinds of confidence:

1. **Contract tests** prove the component boundary works. They use scripted
   model responses (`vibrantine.testing.ScriptedLLM`), deterministic tools,
   and no API key.
2. **Heuristic evaluation tests** prove the Commission is actually good enough
   at its stated LLM task. They run representative cases with explicit success
   and failure criteria.

Contract tests answer "does this Commission obey the framework contract?"
Evaluation tests answer "does this Commission do useful work?"

## Contract Tests

Unit tests must require no credentials. LLM-backed Commissions inject a
scripted client through `client=`; the supported double is
`vibrantine.testing.ScriptedLLM`, with `llm_response` building each scripted
reply. The model's intelligence is not under test; the Commission's behavior
around the scripted response is.

Tests are callers, so they use the caller's API: launch the Commission under
test through the public entry points (`run_one`, or `dispatch` when the test
supplies its own `CallContext`), never by calling the `_run` hook directly.
`_run` is the hook authors implement, not the call surface, and a test that
calls it raw skips the framework wrapping (run_id stamping, overflow enforcement,
exception-to-failure conversion, persistence) that every real caller gets.
The one exemption is a test whose *subject* is that interior machinery
itself, such as the dispatch wrapper's own tests or direct `run_llm_loop`
probes; those necessarily sit inside the boundary.

Cover the contract surface the Commission exercises:

- Public import works from the intended module or package path.
- Constructor injection works for `model`, `client`, child Commissions/tools,
  and test doubles.
- Typed input is rendered correctly by `build_user_message`, or a custom
  `_run` preserves the typed input semantics.
- Success returns `CommissionResult(status="success")` with validated output.
- Failures and partials return `CommissionResult(status="failure"|"partial")`;
  no exception crosses the call boundary.
- Cancellation is checked before expensive or irreversible work.
- Budget behavior is covered where the Commission spends model money.
- Cost is reported, and child cost rolls up when children are dispatched.
- Progress events are covered if the Commission emits them.
- LLM-loop Commissions cover tool menu shape, malformed/no-tool provider
  responses, and conclusion through the `conclude` tool.
- Coordinators cover child dispatch order/parallelism policy, partial child
  failures, and output assembly.
- Folder-sized Commissions cover prompt/resource loading if they use
  `prompts/`.

Integration tests are optional, marked `@pytest.mark.integration`, and skip
when credentials are absent. They are smoke tests, not the backbone.

## Evaluation Tests

Every LLM-driven Commission should carry active heuristic evaluation once it is
more than a mechanical contract probe. A Commission's `BRIEF.md` states the
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
- The Commission uses or avoids tools as the case requires.

Use judge-model evaluation only when deterministic checks cannot express the
quality bar. Judge prompts are themselves test assets: keep them stable, version
them with the cases, and treat changed judge prompts as evaluation changes.

## Where Tests Live

Module-sized Commission:

```text
tests/test_my_commission.py
```

Folder-sized Commission:

```text
src/vibrantine/examples/my_commission/
  tests/
    test_commission.py
    test_eval.py       # when heuristic cases exist
```

Evaluation fixtures may live under the Commission package when they are owned
by that Commission:

```text
src/vibrantine/examples/my_commission/
  tests/
    fixtures/
      cases.jsonl
```

Keep large or sensitive evaluation corpora out of the package. Store only small
fixtures that are safe to publish, and document external/private corpora in
`BRIEF.md`.

## BRIEF.md Notes

Each folder-sized Commission's `BRIEF.md` should name its efficacy bar:

```text
success criteria:
failure criteria:
known failures:
eval cases:
prompt changes tried:
```

The goal is not to pretend LLM behavior is perfectly unit-testable. The goal is
to make quality claims falsifiable, keep prompt changes from being vibes-only,
and preserve a trail of what the Commission is known to handle.
