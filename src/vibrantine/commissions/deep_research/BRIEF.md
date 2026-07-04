# DeepResearch

Recursive LLM-loop research commission. It answers one research question by
letting the root researcher delegate narrower sub-questions to shallower copies
of itself, while leaves ground claims through `FetchTool`.

Input: `ResearchInput(question, seed_urls)`. Output:
`ResearchOutput(answer, claims)`, where each claim carries supporting
provenance.

This is the first real folder-sized commission package. It proves the standard
layout's prompt slot, colocated tests, and provisional model-menu slot without
adding private tools or private subcommission directories.

Do not break the structural termination rule: `max_depth=0` must offer only
`fetch`, and every larger instance must construct a strictly shallower
`DeepResearchCommission`.
