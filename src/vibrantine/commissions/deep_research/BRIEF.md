# DeepResearch

Recursive LLM-loop research commission, shipped as a worked example. It exists
to demonstrate the composition pattern (recursion through the toolbox,
structural termination, cost rollup); it works, but it is not a supported
general-use research surface. It answers one research question by letting the
root researcher delegate narrower sub-questions to shallower copies of itself,
while leaves ground claims through `FetchTool`.

Input: `ResearchInput(question, seed_urls)`. Output:
`ResearchOutput(answer, claims)`, where each claim carries supporting
provenance.

This is the first real folder-sized commission package. It proves the standard
layout's prompt slot, colocated tests, and provisional model-menu slot without
adding private tools or private subcommission directories.

Do not break the structural termination rule: `max_depth=0` must offer only
`fetch`, and every larger instance must construct a strictly shallower
`DeepResearchCommission`.

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

Eval cases:

- None yet. First cases should cover one direct source question, one broad
  decomposable question, and one source-conflict question where the answer must
  preserve uncertainty.
