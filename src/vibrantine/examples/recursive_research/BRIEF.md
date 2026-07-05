# RecursiveResearch

Recursive LLM-loop research Commission, shipped as a worked example. It exists
to demonstrate the composition pattern (recursion through the toolbox,
structural termination, cost rollup); it works, but it is not a supported
general-use research surface. It answers one research question by letting the
root researcher delegate narrower sub-questions to shallower copies of itself,
while leaves ground claims through `FetchTool`.

Input: `ResearchInput(question, seed_urls)`. Output:
`ResearchOutput(answer, claims)`, where each claim carries supporting
provenance.

This is the first real folder-sized Commission package. It proves the standard
layout's prompt slot, colocated tests, and provisional model-menu slot without
adding private tools or private subcommission directories.

Do not break the structural termination rule: `max_depth=0` must offer only
`fetch`, and every larger instance must construct a strictly shallower
`RecursiveResearchCommission`.

## Efficacy Bar

Success criteria:

- Answers the user's question directly and concisely.
- Every load-bearing factual claim has supporting provenance from a fetched
  source or delegated sub-answer.
- Broad questions are decomposed into at most three useful sub-questions when
  decomposition improves the answer.
- Leaf researchers answer from `fetch` results instead of attempting to
  delegate.

Failure criteria:

- Asserts uncited facts that depend on external knowledge.
- Delegates when a simple fetch would answer the question.
- Produces claims whose provenance does not support the claim text.
- Recurses or delegates beyond the offered toolbox.

Eval cases (`tests/test_eval.py`; run `uv run --env-file .env pytest -m eval -s`):

- `direct_source_question`: one fixture page carries the target figure and a
  neighbouring-plant trap figure. Deterministic checks: target present and
  cited; the trap figure, wherever it appears, is attributed to its own plant
  (mentioning the neighbour for comparison is fine; misattributing it is the
  trap).
- `broad_decomposable_question`: three fixture pages, one aspect each, so no
  single fetch answers the whole question. Deterministic checks: all three
  facts present, at least two distinct sources cited.
- `source_conflict`: two fixture pages disagree on the operational date.
  Watchlist status, crude heuristic: both dates must survive into the answer;
  whether the disagreement is framed well is judged from the transcript.

All cases pin the model and serve fictional fixture sources through the real
`FetchTool` over an injected `httpx.MockTransport`, so the only free variable
is the Commission's competence. Delegation behavior (leaves answer from
fetches; at most three sub-questions) is not output-observable today and is
reviewed from transcripts.
