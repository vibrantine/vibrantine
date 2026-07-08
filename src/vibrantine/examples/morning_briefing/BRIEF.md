# MorningBriefing

Heterogeneous coordinator tree, shipped as a worked example. It is the
substantive demonstration of the author-decided pole of composition, the
counterpart to RecursiveResearch (LLM-decided, homogeneous, recursive). It
works, but it is not a supported general-use briefing product.

```
MorningBriefingCommission        (Python coordinator; date header is plain code)
|-- WeatherCommission            (basic LLM loop: one fetch + thin judgment)
|-- NewsDigestCommission x N     (Python coordinator: fetch x M + Synthesize)
|-- SummarizeCommission          (executive summary over the sections)
`-- write markdown               (deterministic, attested in the output)
```

Input: `MorningBriefingInput(output_path)`. Output:
`MorningBriefingOutput(markdown_path, executive_summary, sections,
failed_sections)`. What the briefing covers is capacity, fixed at
construction: the weather instance owns its source URL, each news digest
owns its field label and source list, like subscriptions. The per-run work
order is only where to write today's edition.

What it demonstrates that nothing else in the examples does:

- **A three-level tree with heterogeneous children.** A coordinator nesting
  coordinators (news digests), beside a leaf judgment loop (weather), beside
  a pure judgment call (the executive summary), beside a tool (fetch, one
  level down).
- **The three-categories rule in one file.** The date header is plain
  application code inside `_run`: no judgment, no fetch worth wrapping, so
  no contract jacket.
- **Two-level partial semantics as explicit authorial choices.** A failed
  source makes its section partial; a wholly failed section is skipped and
  named in `failed_sections`; a failed executive summary degrades the
  briefing rather than killing it. Only a morning with no sections at all is
  a failure. Every one of those calls is visible Python, which is the point
  of this pole.
- **Budget down, cost up, across real depth.** The coordinator slices
  `budget_usd` into per-section shares (reserving one for the summary), each
  digest slices its share across fetches, and every level's cost rolls back
  up into one number on the result.
- **Configured reuse without subclassing.** The news fields are one class,
  N instances, each built with its own sources. Instances share the
  class-level `name`; the parent labels which section is which. This is the
  first example where that shows.

This is the second folder-standard package and the first to use
`subcommissions/`: children that exist for this coordinator live inside its
folder, not in the shared examples namespace.

## Testing

Deterministic only: fetches run through the real `FetchTool` over injected
`httpx.MockTransport`, LLM children consume scripted fake clients, so every
partial/failure path and the cost arithmetic are exact assertions. No live
eval cases; the composition is the subject here, not model competence.
