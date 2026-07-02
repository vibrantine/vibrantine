# Vibrantine Commissions: Vision

What Vibrantine Commissions is, what it is for, and what shape the library takes. Sister doc: [`composition.md`](composition.md) covers *how the pieces fit together* — the contract jacket, types, patterns, information flow, output discipline, persistence, coordinator templates. Project conventions and AI-assistant guidance live in [`AGENTS.md`](../AGENTS.md); [`README.md`](README.md) indexes the rest of the docs directory.

## Premise

Vibrantine Commissions is the component model for AI agents. Each commission is a typed, contracted, isolated unit of work that involves LLM judgment somewhere in its subtree — a building block from which larger agentic systems are composed. Where LangGraph treats agents as nodes in a shared-state graph and CrewAI treats them as members of a natural-language collaboration, Commissions treats them as *components*: independently authored, independently testable, composable through declared interfaces rather than through emergent coordination.

The architectural commitments follow from the framing. Strict isolation as a feature. Typed contracts at every boundary. Errors as values rather than exceptions. Costs and provenance tracked structurally. A clean split between **commissions** (units with LLM judgment somewhere in their subtree) and **tools** (deterministic primitives the commissions and the LLMs above them call). The intellectual ancestry is the component model from systems literature — the actor model, software components, React-style declarative UI components — applied to LLM-driven work.

### Use cases

A commission is a bounded unit of LLM-judgment work, and nothing about its shape is owed to a particular domain. What grounds the design is a deliberately diverse cast of target workloads — the same spread § The bet returns to — each exercising the contract differently:

- **Document-corpus agents** — managing and organizing directories of documents too large to fit in context (personal knowledge bases, research archives, legal or financial collections, accumulated notes). The agent learns the layout, decides what to read next, builds progressive understanding, and answers with citations, all without loading the whole corpus into any single prompt. Think Claude Code, but for non-developer document work. This is the workload these docs lean on hardest for examples — because it stresses the most of the contract at once, not because the architecture is shaped around it.
- **Coding agents** — comparable in shape to Claude Code, runnable on lesser models. Quality target is "reasonable on basic tasks," not "matches frontier-driven assistants."
- **Inbox / triage assistants, research assistants, and the rest of the design-probe cast** (see § The bet) — same substrate; only the specialised commissions on top differ.

None of these is privileged in the architecture. The contract emphasises typed claims with provenance, structural cost attribution, and recursive composition because *any* serious LLM-judgment work needs them — bounded cost, auditable sources, recoverable errors, composition that survives nesting. A tools layer exists separate from commissions because deterministic primitives (`Glob`, `Read`, `Grep`, `Sample`) are a different kind of thing from judgment-bearing units — in every workload, not just document work. The document-corpus case makes this vivid because it stresses every axis simultaneously; the demands themselves are general.

Sequencing is a separate question from architecture. The document-corpus machinery — patterns like Wiki/accumulator and Hierarchical-summarize, the `Sample` tool — gets built toward first for practical reasons: it is the most demanding exercise of the contract and makes the best worked tutorial, so building it shakes out the most. That is a claim about build order, not about what the library is *for*.

A handful of capabilities belong in the tools layer now rather than a later layer, because the library can't credibly cover its target workloads without them: **browser automation**, **shell and filesystem tools**, and **MCP server integration**.

**Stretch validations of contract generality** — not v1 deliverables, but the architecture should not preclude them:

- **Game-playing and simulated robot control** — combinations of commissions running act-observe-decide-act loops against an environment.
- **Multi-modal inputs and outputs** — image-aware reads and generated visuals where the underlying model supports them. Eventually relevant to several of these workloads (PDFs with figures, scans, slide decks), so the contract is designed to not preclude it; v1 ships text-only commissions.

The audience is people building production agents who have been burned by debugging shared-state graphs, plus people who want a usable agent for their own document or code work without giving the keys to a frontier API. What it trades away is rapid prototyping convenience; what it buys is bounded blast radius, structural cost attribution, multi-author composability, and the ability to let AI assistants edit one commission without defensively reasoning about every other commission.

## Library scope

Vibrantine Commissions is a separately publishable package, not the internal layer of a specific application. It is organized into four layers: the **core primitives** (the Commission contract, with Tools as deterministic Commissions under Shape A: the settled decision that a Tool is just a Commission without an LLM, see [`composition.md`](composition.md)), an **authoring kit** (utilities for building both commissions and tools reliably), a **standard library of tools** (file I/O, shell, HTTP, browser, MCP adapter — the deterministic substrate), and a **standard library of commissions** (pre-built LLM-driven units for common task shapes).

The library deliberately stops at those four layers. It declines to specify the layer above — persistence, scheduling, initiative, user-facing surfaces, decision-making policy — for the reasons § The layered architecture sets out. The point worth making here: even Vibrantine's own future orchestration work consumes the library through the same public contract everyone else does — no privileged access, no back-channels.

This framing is closer to Python's "batteries included" philosophy than to LangGraph, CrewAI, or AutoGen. The competition ships only the framework — they give you the contract for building agents but not the built things. Vibrantine ships the contract plus a curated standard library of both tools and commissions, so users get real work done the first hour they install it. Every shipped piece is also a worked example of its contract done right, which seeds the ecosystem with patterns to match.

> *Scope, not status — the four layers and the inventories below describe what the library is **for**, its target surface, not what is built today. Several items are unbuilt or partial. For the current shipped surface see [`README.md` § Current Status](../README.md#current-status); for the contract as built, [`composition.md`](composition.md); for what's consciously deferred, [`AGENTS.md` § Build phase discipline](../AGENTS.md#build-phase-discipline). This section makes no present-tense existence claims, so it can't drift as phases ship.*

**The core primitives (in scope):**

- The Commission base class and its API contract — see [`composition.md § The contract boundary`](composition.md#the-contract-boundary)
- The Tool primitive (a Commission with no LLM in its subtree, under Shape A — same ABC, different authoring discipline)
- The universal result envelope (`CommissionResult[T]`)
- The CallContext parameter (budget, capabilities, cancellation, observability, concurrency cap, application prompt, envelope sections)
- Payload typing discipline (typed inputs/outputs, errors-as-state, provenance as first-class)
- The `Claim[T]` helper for per-claim provenance inside payloads
- Output discipline slots (`max_output_tokens`, `overflow_policy`)
- Persistence slots (`persistence_mode`, the persistence protocol interface)
- System prompt slots (`system_prompt` ClassVar, application prompt and envelope sections on `CallContext`)
- A thin entry-point function (`run_one`) for invoking a commission from outside the tree

**The authoring kit (in scope):**

- Markdown template loader for declarative commission authoring
- Testing harness for both commissions and tools
- Evaluation utilities — tiered battery, rubric scoring, confidence-tier calibration
- Prompt-cache utilities enforcing system/user message discipline and cache-stable prefix structure
- Cost-attribution and ledger helpers
- An LLM-tool wrapper that turns any commission *or* tool into an LLM-callable tool: commissions wrapped this way are how external orchestrators (LangChain, CrewAI, MCP servers) consume Vibrantine; tools wrapped this way are how commissions' internal LLM loops use the deterministic substrate

**The standard library of tools (in scope):**

- File I/O — `Read`, `Write`, `Edit`, `Delete`, `Move`, `Glob`, `ListDir`, `Grep`, `Sample`. `Sample` is load-bearing for any corpus-structure workload: file metadata, head/tail, line counts — the primitives that let an agent learn corpus structure before committing to read full documents.
- Shell execution and process management
- HTTP — `Fetch` (basic GET; previously `FetchCommission`, migrated to the tools layer)
- Browser automation — Playwright-driven page navigation, interaction, screenshot
- MCP adapter — expose any first-party tool over MCP; consume any MCP server as a tool

**The standard library of commissions (in scope):**

- Information commissions — `Summarize`, `Hierarchical-summarize`, `Synthesize`, `Extract`, `Verify-against-sources`
- Decision commissions — `Triage`, `Plan`, `Choose-from-options` style
- Workflow / coordinator commissions — `Wiki-accumulator` (corpus understanding through progressive integration), `morning briefing`, `plan-fan-review`, `decide-draft-send`

See § Standard library taxonomy for how these slot together and why the morning briefing is the first worked example rather than the centrepiece.

**Out of scope (application-level):**

Everything § The layered architecture enumerates as above-the-line — persistent cross-invocation state, scheduling and initiative, user-facing conversation/notifications/deliverables, the autonomy primitives — is out of scope here too; that list is canonical and not restated. What this section adds, specific to *library scope* rather than the runtime boundary:

- Orchestration topologies beyond the parent-as-hub shape the coordinator commissions demonstrate
- Model tier routing policies (the contract enables them; the policy is the caller's)
- Cost ledger UI
- Curator authoring and distribution
- Runtime extensibility — letting an agent author and register new commissions or tools mid-session belongs in the orchestration layer above
- Anything that names or shapes the layer above the library

This separation is the durable architectural commitment, not a transitional stage. The library being decision-layer-agnostic is what lets it be publishable and lets the layers above it evolve independently. Every later temptation to leak decision concerns into the library "for convenience" should be resisted; new orchestration needs extend the application above, not the library.

## Distribution and layering

A three-tier picture of how the in-scope content above packages and distributes:

1. **`vibrantine` library** (this repo, one pip install). The contract + std-lib primitives (tools, building-block commissions like Synthesize) + a handful of default coordinator commissions. The `pip install vibrantine` user gets everything they need for first-hour utility — no second download required. Internally, `src/vibrantine/` (the library) is the wheel today; the planned `src/vibrantine_apps/` (defaults) will ship as a second top-level import in the same wheel once it earns its place (see below).

2. **Additional commission packages** (separate repos, optional). More complex or domain-specific commission collections, distributed independently on PyPI. The line between "default app" and "extras repo" is a curation call — *what does every install user get?* — not an architectural one. Promote/demote freely as the picture sharpens. Third parties can publish their own commission packages identically; the library only has to keep the public Commission contract stable.

3. **Personal agent / superagent** (separate repo). The application layer with persistence, scheduling, conversation, prompting policy — the thing this doc is explicit about not specifying (see § The layered architecture). Imports `vibrantine` + any number of additional commission packages, exactly the way a third-party consumer would.

Modularity is the load-bearing principle that makes all of this work:

- Every commission and tool takes its dependencies as constructor arguments (DI), never hardwired. `MorningBriefingCommission(fetch=..., synthesize=...)` is the pattern.
- `src/vibrantine_apps/` (once it exists) consumes the library through its public import surface — `from vibrantine import ...` for the SemVer-frozen bones (the `__all__` set), `from vibrantine.commissions / .tools / .models import ...` for the rest — exactly as a third-party consumer would, never reaching past an underscore into `_internals`. The public surface is "anything importable that isn't underscore-prefixed"; `__all__` is its frozen subset; a back-channel is depending on a private name or an import side-effect, *not* importing from a submodule. This is the "no privileged access, no back-channels" rule applied to first-party code.
- New abstractions earn their place by replacing felt swap-ability pain, not by anticipating it.

`src/vibrantine_apps/` does not yet exist. It earns its directory when the first occupant needs a home — most likely `MorningBriefingCommission` migrating out of `src/vibrantine/commissions/`, since it's a worked-example coordinator with a user-specified output path, not a building block consumed by other commissions.

## The layered architecture

The library has a clean boundary at the top edge of its contract. Above that boundary, anything that needs to make decisions about *when* to invoke commissions, *what state survives* across invocations, or *how the user is involved* lives in some application layer. The library doesn't specify what that layer looks like, and the contract works whether the caller is a CLI script, a long-lived process, a runtime owned by another framework, or no caller at all (commissions invoked directly from tests).

The reason for this restraint is not just modularity. Anticipating the shape of the layer above is the fastest way for application concerns to leak back into the library — once the library knows there's a scheduler, you start designing the contract to fit the scheduler; once the contract fits the scheduler, the scheduler has to be the kind of scheduler the contract expects. Refusing to name the layer above keeps both sides free.

What carries across the boundary unambiguously:

- The contract's typed input and output
- Cost and provenance metadata on every result
- Tree-structured invocations within a single commission tree
- Errors-as-state

What is above the line — the application layer's job, whatever that application is. Two clusters:

*Mechanical responsibilities* (present for any caller, even a one-shot test script):

- Deciding when to invoke, and what input to pass
- Persisting any cross-invocation state — the library carries no memory between invocations, so anything that must survive is the caller's to store and re-supply
- Surfacing results to humans, if humans are involved
- Coordinating sibling invocations through their explicit return values (the caller threads one commission's typed output into the next's input; *within* a commission tree this mediation is a coordinator commission's job — see § Composition)

*Autonomy primitives* (what a persistent, self-directed agent adds on top of the mechanics):

- Initiative — deciding what to do without being told
- Time as input, and out-of-band event handling
- Single-inbox discipline

The library provides none of these, by design, and never knows there is an application above it. This is the canonical enumeration of the boundary; other sections (§ Library scope's out-of-scope list, § Bounded agency's agency checklist) point here rather than restating it.

## Bounded agency

A Vibrantine Commission is, fundamentally, a unit of work that involves *LLM judgment somewhere in its subtree*. That's what makes it different in kind from a function, a script, a deterministic pipeline, or a tool — if the work can be done deterministically (truncation, summarisation heuristics, wrapping a primitive), the right home is a tool, not a commission. The contract — typed input, typed output, errors-as-state, structural cost and provenance — exists specifically to make LLM-driven judgment *tractable*: bounded in cost, auditable in source, recoverable when wrong. Without the LLM-judgment dimension you have a function with overhead; without the contract you have an unbounded LLM call. Commissions are the discipline that lets the two be combined safely.

The LLM-judgment test is *anywhere-in-subtree*, not *at-this-level*. A deterministic Python coordinator whose body contains no LLM call but whose children include LLM-bearing commissions is still a commission — its subtree carries LLM judgment, and the cost/provenance/error-state machinery is load-bearing for the call. The Tool category is for units whose entire subtree is deterministic; everything else is a commission.

"Commission" is doing real work as a name. It carries principal-agent semantics: a principal commissions an agent to perform bounded work with delegated authority and discretion. The library inherits that semantic split deliberately — it provides the *executor* side of the principal-agent relationship; persistent initiative lives in the application layer above.

Run a commission against the common cluster of properties people attribute to agentic systems and the split becomes legible:

- **Tool use** — yes; commissions can use any tools inside their invoke body.
- **Multi-step reasoning** — yes; a single invocation can run an arbitrary LLM loop with branching.
- **Decision-making** — yes; LLM-driven commissions make ongoing choices.
- **Autonomy within scope** — yes; once invoked, a commission runs without per-step approval.
- **Self-correction** — yes; plan-fan-review is precisely this pattern, and any commission can implement it internally.
- **Goal pursuit** — yes, but the goal is given by the caller, not chosen by the commission.
- **State during execution** — yes; private state is maintained for the invocation lifecycle.
- **Initiative** — no, by design. A commission doesn't decide to start; it's invoked.
- **Cross-invocation memory** — no, by design. Commissions are stateless across invocations.
- **Peer coordination** — no, by design. No sibling messaging, no shared blackboard within the tree.

The three "no"s are the same boundary § The layered architecture draws, seen as agency properties: initiative and cross-invocation memory are above-the-line application concerns, and peer coordination is what the tree-structure invariant forbids (siblings meet only through the parent that mediates them).

Commissions check most of the boxes for *agency during execution* and deliberately fail on the ones that belong to a different layer. The "agentic" properties of a complete autonomous system emerge from the composition of application and commissions, not from the library alone.

The trade-off worth being explicit about: by forbidding shared state, peer messaging, and back-channels *between siblings* within a single commission tree, the contract rules out emergent-coordination patterns at that scope. This is a constraint on how siblings communicate, not on control flow: iteration, recursion, and re-invocation *inside* an invoke body are explicitly fine — they are how patterns like Hierarchical-summarize and AgentLoop work — and even a runtime cycle a caller deliberately wires up stays contained, bounded by budget and `max_iterations` rather than prevented (loop detection and topology policing are application concerns; see [`composition.md`](composition.md)). What's ruled out is the *sibling coordination* such patterns rely on: some — swarm, blackboard, market-based, gossip — aren't directly expressible as a single commission invocation. They are expressible as applications built on top of the library. On balance the restriction is correct for production systems where emergent coordination is fragile and hard to debug, but it's worth being precise: the library provides *delegated, contracted, tree-structured agency* and leaves emergent-coordination to layers above it that want to opt in.

## Composition

How commissions chain, nest, and coordinate lives in [`composition.md`](composition.md). The shapes the library prescribes:

- One typed input → one typed output at every joint
- Inside an `invoke` body, either the author or an LLM decides what runs next — a Python coordinator or an LLM loop; both wear the same external jacket
- Parent is the only data path between siblings — no back-channels, no shared mutable state
- Sub-commissions wrap as LLM-facing tools through the LLM-tool wrapper; same jacket lets a commission act as a tool for another commission's LLM
- Coordinator templates (`PlanFanReview`, `AgentLoop`, `Pipeline`, `RouteDispatch`, `IterativeRefine`) are planned as `Commission` subclasses with fixed Python skeletons — not yet built (see [`composition.md § Coordinator templates`](composition.md#coordinator-templates-v07-work)). The *shapes* already ship as hand-written coordinators — `MorningBriefingCommission` (pipeline-shaped) and `DeepResearchCommission` (AgentLoop-shaped recursion); a template earns its place by extracting what such coordinators turn out to share, not by being built speculatively

The library deliberately prescribes no topology beyond what these templates provide: pipelining is a caller pattern rather than a library primitive — there is no `Pipeline` object today, only sequential dispatch hand-written inside a coordinator's `invoke` body (the planned `Pipeline` template would be a convenience over that pattern, never a new topology) — and the side effects an invocation produces are the commission's own business so long as its typed output reflects them. The mechanics of both — pipeline-style flow and the acting-vs-drafting rule — live in [`composition.md § Pipeline-style flow`](composition.md#pipeline-style-flow) and [`composition.md § Acting vs drafting`](composition.md#acting-vs-drafting).

## Standard library taxonomy

The standard library is the entry point — for many users it will be the first and only Vibrantine surface they encounter, imported into LangChain or wired into an MCP server. The taxonomy reflects the two-primitive split: tools are the deterministic substrate, commissions are the units with LLM judgment somewhere in their subtree.

### The tools layer

Tools are stateless dispatches with typed I/O and errors-as-state. They have no LLM anywhere in their subtree, no nested LLM invocations, no cost rollup of their own — a tool's cost is its dispatch cost, typically zero or fixed. The standard library's tools layer comprises:

- **File and corpus tools** — `Read`, `Write`, `Edit`, `Delete`, `Move`, `Glob`, `ListDir`, `Grep`, `Sample`. The `Sample` tool is load-bearing for any corpus-structure workload: it returns file metadata (size, modification time, line count) and partial content (head, tail, sniff) so a commission can learn corpus shape *without* loading whole documents into its context. A doc librarian rations reads against a budget; a coding agent reads more aggressively. Same primitives.
- **Shell and process tools** — execute commands, manage long-running processes, capture output.
- **HTTP tools** — basic `Fetch` (GET, POST, headers, timeout). Specifically *not* a commission.
- **Browser tools** — Playwright-driven `Navigate`, `Click`, `Type`, `Screenshot`, `ExtractDOM`. The agent's eyes and hands on the live web.
- **MCP adapter** — `expose_as_mcp` to publish any first-party tool over MCP; `mcp_client_tool` to consume any external MCP server's tools.

If a tool *would* need an internal LLM call to do its job, it's mis-categorised — that's a commission. A composite Tool (a tool that invokes other tools) is still a Tool; the LLM-anywhere rule decides the category, not composition depth.

### The commissions layer

Three loose categories for thinking about contract obligations:

**Information commissions** produce structured understanding from sources. They answer "what is true / what was retrieved / what does this corpus contain." `Summarise` *(shipped)*, `Hierarchical-summarize`, `Synthesize` *(shipped)*, `Extract`, `Verify-against-sources` fit here, as does the shipped `Ask` (read sources and answer, paginating as needed). Their outputs typically include `Claim[T]` entries with per-claim provenance, so downstream consumers can trace assertions to sources. Failure modes are LLM-flavoured (validation, internal, budget_exceeded) plus tool-flavoured failures bubbled up from their tool calls (timeout, rate_limit).

**Decision commissions** produce a typed choice for a parent to act on. `Triage`, `Plan`, `ChooseFromOptions`, `PrioritiseTasks`. Output is a structured decision — option chosen, alternatives considered, rationale — designed for the parent to either execute (by invoking tools or further commissions) or surface to a human. Decision commissions don't act; they hand a typed decision upward. This is what keeps the contract tractable when LLM judgment is wrong — the decision is auditable as a value before any irreversible step is taken. (A unit that *both* decides a route *and* executes it in the same invocation is a coordinator, not a pure decision commission — see the routing note below; `Triage` names the decision shape, and `EmailHandler` — a provisional contract-validator, not shipped std-lib — is the decide-and-execute coordinator built around it.)

**Workflow / coordinator commissions** compose other commissions and tools. Their contract obligation is the same as any commission's — a typed result the parent can act on — regardless of whether the author fixed the topology in code or an LLM decides dispatch at runtime; that interior choice is a mechanic, not a contract distinction. The shipped coordinators bracket the two interior styles: `MorningBriefing` fixes its topology in Python (parallel fetch → synthesise → write), while `DeepResearch` lets an LLM loop decide dispatch and recurses into shallower copies of itself (it's the worked example that drove structural cost rollup). The LLM-routing case (classify, then execute the chosen route in the same loop) is exercised by `EmailHandler`, a provisional contract-validator with stub handlers rather than shipped std-lib. See [`composition.md § Internal composition`](composition.md#internal-composition).

The taxonomy is descriptive, not enforced. A commission doesn't declare its bucket; readers and authors use the buckets to think about contract obligations — what does my output mean, what failures can I produce, what is the parent going to do with my result — rather than as a registry.

The named commissions above describe the *target* surface, not today's shipped set (per § Library scope's scope-not-status caveat). Built today: `Summarise`, `Synthesize`, and `Ask` (information), and the `MorningBriefing` and `DeepResearch` coordinators; `EmailHandler` exists only as a provisional contract-validator with stub handlers, not shipped std-lib. The rest — `Extract`, `Verify-against-sources`, `Plan`, `ChooseFromOptions`, `Wiki-accumulator` — are illustrative of where each bucket is headed. For the current shipped surface see [`README.md` § Current Status](../README.md#current-status).

### Worked examples and sequencing

The document-corpus workload anchors build order — for the practical reasons § Use cases gives (it exercises the most of the contract at once), not any architectural privilege. Two patterns are the centrepieces of that first push:

- **Wiki-accumulator** — a coordinator commission that integrates incoming sources into a persistent structured artifact representing the agent's understanding of a corpus. The integration step uses a `Synthesize`-with-prior-artifact shape; the decisions *when* to integrate and *where* the artifact lives between runs are caller concerns (persistence is application-layer). This is the pattern that proves the contract supports corpus-scale work, because it explicitly handles "I can't read everything; what do I know so far, and what should I look at next."
- **Hierarchical-summarize** — recursive composition that chunks long input, summarises each chunk, then recursively summarises summaries until target compression. Folder → subfolder → file maps naturally onto this; so does any large transcript or codebase. (The shipped `DeepResearch` commission already exercises recursive composition in the LLM-loop direction — recursing into shallower copies of itself — so the recursion-plus-cost-rollup machinery this pattern needs is proven; Hierarchical-summarize is its deterministic-depth counterpart.)

The morning briefing pattern (fan of fetches, synthesise survivors, write a markdown report) is the *first end-to-end loop* — small enough to fit a tutorial, useful enough to ship as a worked example, and exercises information commissions plus a workflow coordinator without yet needing the corpus-management machinery. It is illustrative rather than central.

Beyond those, the next worked examples validate breadth: a triage / dispatcher commission (LLM-mediated routing — the shape `EmailHandler` already validates against the contract; a shippable instance promotes that proof into std-lib), a browser-driven research workflow (Playwright tools plus Synthesize plus filesystem writes — proves the tools-layer integrations compose), and a verification chain (Claim-extraction plus Verify-against-sources — proves the per-claim provenance infrastructure earns its slot).

Game-playing or simulated robot-control patterns are deliberately *not* in the v1 worked-examples set. The contract accommodates the act-observe-decide-act loop they need (it's just an LLM loop inside a commission's invoke body), but shipping a useful instance means picking a target environment and tuning prompts for it — polish work better done after the document-corpus build is solid.

### Conventions for commissions

- **`model` is a constructor argument — a `Model` object.** A `Model` bundles the provider-facing identifier, the endpoint/client it speaks to (any OpenAI-compatible API), and its facts (context window, pricing, modality). The library is **multi-provider by construction**: OpenRouter for cloud models and **local Ollama** for on-device models are both first-class — Ollama exposes an OpenAI-compatible endpoint, so the same client drives it with a different base URL, no separate provider driver. Because each `Model` carries its own endpoint, a caller can assemble a menu of a dozen models across providers and hand each commission whichever one it wants at construction. A bare model string is still accepted as shorthand — resolved through the known-models table to a `Model` on the default OpenRouter endpoint. (This is also what lets the local-first economics of § The economic engine actually run: frontier judgment via cloud, fan workers on the user's own hardware.)
- **A small known-models table ships with the library.** It maps identifiers to the facts a `Model` carries — context window, pricing, and (eventually) modality flags. A bare string resolves through this table; constructing a `Model` directly is how a caller describes a model the library hasn't catalogued — a local Ollama model, a private deployment — without editing the table. The Commission reads its `Model`'s context window at construction to populate `max_input_tokens` automatically (context window minus the commission's own system-prompt footprint); pass an explicit `max_input_tokens` to be more conservative.
- **Commissions are immutable post-construction.** Model, prompt template, `max_input_tokens`, `target_input_fraction`: all fixed when the instance is built. To retarget, build a new instance.
- **System prompts are a first-class, layered slot** — configured at the contract boundary rather than hand-assembled per call. The layer set, composition order, and contract slots are in [`composition.md § System prompts`](composition.md#system-prompts-three-layers).

Tools follow a separate, smaller convention set — typed input/output, errors-as-state, no LLM in subtree, no constructor model argument.

## Depth, breadth, and the costs of nesting

The contract permits arbitrary nesting depth. The contract doesn't fight you; physics does.

Real costs that compound with depth:

- **Budget compounding.** Each level slices from its parent's allocation; conservative slicing can burn most of the original budget within a few levels.
- **Latency compounding.** LLM calls have wall-clock latency; deep trees serialize into minutes of clock time.
- **Error compounding.** Per-level success rates multiply, so many LLM levels deep, even a high per-level rate compounds into a poor end-to-end one.
- **Translation loss.** Each layer turns typed sub-results into prose for the LLM and LLM tool-calls into typed inputs for further delegation; each round-trip loses signal.
- **Goal drift.** The LLM at the top has the original context; the LLM at the bottom sees only what its parent forwarded.

Important distinction: *pipeline length* is not *tree depth*. An eight-stage sequential pattern is a shallow tree (depth two or three) with stages as siblings under one coordinator. Compounding concerns from deep nesting don't apply.

So the rule for large work is **go wide, not deep**: scale by adding siblings under a coordinator (breadth), not by nesting more levels (depth). Breadth is cheap, because siblings don't compound each other's errors, drift each other's goals, or stack each other's latency. Nothing forbids depth: the contract permits arbitrary nesting, and budget, sliced thinner at each level, is what actually bounds a descent. The caution is aimed at stacked *LLM reasoning*, since deterministic levels are cheap. So the instinct that a big job needs many nested LLM levels is usually a category error: it wants many siblings at shallow depth.

The architectural answer: **depth should be determined by pattern choice, not by free-form LLM decisions mid-tree.** A pre-tested coordinator template descends predictably. The danger zone is when an LLM mid-tree decides ad-hoc to delegate further than the pattern was tested for.

A finer distinction worth holding: deep but mostly deterministic is very different from deep and LLM-driven at every level. Twelve levels where the bottom eight are deterministic transformations and only the top four involve LLM reasoning is much more tractable than twelve levels of LLM reasoning all the way down. The probabilistic concentration matters more than the literal depth.

## The economic engine

The architectural payoff isn't just engineering tidiness — it's tiered model routing made reliable.

Consider a plan-fan-review pattern (a design illustration — no coordinator ships in this shape yet; it's planned as a v0.7+ template, see `composition.md § Coordinator templates`). Work splits cleanly:

- **Plan, Review, Report** — high judgment, holistic synthesis, strategic decisions. A handful of frontier-tier calls.
- **Fan workers** — bounded, well-specified, schema-constrained extraction or transformation. Many parallel calls on local 7–14B models with constrained decoding.

That can be dramatically cheaper per substantive task than routing every call to frontier, while staying competitive with the all-frontier version — because the high-judgment work still went to frontier.

This works because the commissions contract provides:

- Schema-constrained output at the leaves (workers reliably produce typed output on small models)
- Errors-as-state (a flaky local worker is a typed failure Review can re-plan around)
- Structural cost attribution (tiering decisions are visible, not vibes-based) — cost rolls up structurally on both the Python-coordinator and LLM-loop paths (see `composition.md § Cost rollup`). Today that attribution is USD-only, which under-counts free local workers (a `$0` rollup for real compute); making it honest for local-first — per-model budgets and token/time accounting — is a settled-but-unbuilt design direction
- Per-commission model selection (`model` is a construction argument, so the caller routes each commission to whatever tier fits — frontier for judgment, local for workers — and the same commission class runs on either). The contract makes the *choice* free and per-instance; it doesn't yet carry a machine-readable tier *expectation* the caller could read off a commission's type (tiering is the author's/caller's policy, not contract-expressed metadata)

Without these contracts, you couldn't safely push work down to cheaper models. With them, you can.

The strategic implication: this is the engine behind the local-first business model. Subscription-funded agent products run everything on frontier because their pricing assumes per-call cost as the dominant variable. A product where workers run on the user's hardware decouples cost from usage — once the user has hardware and a curator's model, the marginal cost per task approaches electricity rather than inference. That makes periodic curator-model releases plausible as monetization.

The curator-model story also gets sharper. A curator specializing in research-style tasks releases a model tuned on Plan and Review prompts. Those high-judgment commissions use the curator's model; workers use whatever generic local model the user has. The curator's value is concentrated where judgment matters; the rest is interchangeable infrastructure.

Second-order effect: most agentic frameworks have a diffuse frontier dependency — every call is some percentage frontier and the user just sees the bill. With tiered commissions, the frontier dependency is bounded and visible. Users gain real control over their cost/quality trade-off rather than turning a global knob.

Net: the commissions library makes it economically rational to use frontier models only where their judgment is structurally required, because cheap models are made reliable enough at everything else.

## Portability

Vibrantine Commissions are portable across LLM frameworks by construction. A commission declares typed Pydantic input and output as class-level attributes, behind a single async call. That shape is exactly what an LLM tool wants: `input_type` yields a JSON schema, `output_type` serialises predictably, and the framework call API (`run_one` from outside a tree, `dispatch` from within one) runs the work and returns a typed result with cost and provenance attached. An adapter wraps that call API — not the author-facing `invoke` override hook, which skips the run-id, overflow, and persistence machinery `dispatch` adds. The adapter is small (on the order of ten lines, since it's mostly schema-passing), though none ship yet — see the implications below.

A well-built commission isn't trapped in Vibrantine. It's usable as a tool by LangGraph and LangChain agents, CrewAI crews, AutoGen conversations, MCP servers (which expose it to any MCP client), and bespoke scripts using the provider SDKs directly. The same commission, in all of those contexts, with no rewrite.

What carries across the boundary:

- The contract (typed input and output)
- Internal behavior (LLM judgment, sub-commission invocation, iteration loops, the lot)
- Cost and provenance metadata in the result — consumers can ignore it, but it's there

What degrades outside a fully Vibrantine-aware stack:

- Cancellation propagation (other frameworks have their own cancellation semantics)
- Capability intersection (other frameworks don't know about CapabilitySet)
- Budget allocation discipline (non-Vibrantine orchestrators don't slice budgets the way the contract expects)
- Tree-structure invariants (a graph-based or message-passing framework isn't tree-shaped, so a commission embedded in it sits inside a non-tree)

The degraded properties are caller-discipline properties. They matter when the whole stack is Vibrantine-aware. They don't matter when a commission is one tool among many — the commission still does its bounded work, still returns its typed result, still produces auditable provenance and cost. The orchestrator above it just doesn't speak the same discipline.

Portability widens the audience. Most agent frameworks are islands — once you've adopted LangGraph you build LangGraph things; CrewAI agents don't drop into AutoGen. Vibrantine Commissions are *components* in the sense that React components are: they live inside the host application's lifecycle but aren't bound to one host. A well-tested commission is a useful drop-in for anyone with an LLM-tool-consuming agent, whether they adopt Vibrantine end-to-end or not — so the proposition is "use Vibrantine Commissions inside LangChain, or as your whole stack," not "use Vibrantine instead of LangChain."

The standard library is the practical entry point: someone uninterested in Vibrantine as an end-to-end stack might still want a Verify commission (per-claim provenance is annoying to build), a Hierarchical-summarize commission (long-context summarization is finicky), or a Plan-fan-review coordinator (they don't want to wire one up by hand). (Those three are illustrative of the draw, not yet shipped — see § Standard library taxonomy's scope-not-status note and `composition.md`'s coordinator templates; the shipped entry points today are `Synthesize`, `Ask`, and the `MorningBriefing` / `DeepResearch` coordinators.)

Three practical implications:

- **Ship adapters as first-class library features.** `vibrantine.commissions.adapters.langchain`, `.crewai`, `.mcp` — small wrappers around the generic LLM-tool form. `pip install vibrantine[langchain]` should make commissions drop into LangChain code immediately. This is what turns portability from "technically possible" into the path of least resistance.
- **The MCP adapter matters most.** MCP is positioning to be the open-standard way tools are exposed across the ecosystem. A first-class MCP server wrapper means every Vibrantine Commission becomes available to every MCP client — which increasingly means every serious agentic system. Better distribution story than asking people to switch frameworks.
- **Vibrantine Commissions, not the larger stack, are the way in.** The deeper value for committed users comes from composition across the whole standard library; for everyone else the commissions are the entry point. Most users will meet the project through a commission they imported into LangChain before they consider running Vibrantine end-to-end.

## The portfolio of patterns

Most of what's useful composes from a small set of structurally distinct coordinator patterns. The reason to catalogue them: each stresses a different architectural property, so building each one validates a different part of the contract. This is the design space, not the build list — build the next pattern when a real use case pulls for it, not to round out the matrix.

- **Morning briefing** (linear pipeline) — sequential composition and the basic typed-input/typed-output contract.
- **Wiki / accumulator** — a persistent artifact that incoming sources integrate into over time; proves the persistence boundary, since the integration is a typed commission but *where the artifact lives between runs* is the caller's job.
- **Plan-fan-review with iteration** — bounded iteration with self-healing; the strongest general template for middle-stakes research and analysis.
- **Triage / dispatcher** — LLM-mediated routing through tool-calls; the canonical first LLM-loop build, with immediate consumer value (inbox triage, note classification).
- **Verification chain** — extract load-bearing claims, verify each, return per-claim confidence; the pattern that proves the `Claim` provenance infrastructure earns its slot, and composable rigor that can post-process any other pattern's output.
- **Hierarchical summarization** — recursive composition with deterministic depth control (chunking bounds depth, not free-form LLM choice).
- **Tree search with pruning** — branching with backtracking; narrower appeal, strong for technical or creative tasks that try multiple approaches.
- **Multi-perspective synthesis** — plan-fan-review with deliberately diverse workers, where Review surfaces disagreement rather than smoothing it.
- **Decide-draft-send** — a decision sub-commission, an information sub-commission, and a final tool dispatch; the simplest example spanning tools and all three commission categories.
- **Browser-driven research** — an LLM loop over browser and filesystem tools plus `Extract`/`Synthesize`; exercises tool-result budgeting, since page DOMs are large.

Sequencing — what to ship first and why — lives in § Standard library taxonomy → Worked examples and sequencing. The coordinator-template forms of these patterns live in [`composition.md § Coordinator templates`](composition.md#coordinator-templates-v07-work).

## What this is not

To be clear about positioning:

- **Not LangGraph.** No shared state, no graph topology, no DSL to build inside. The library is imported into your code; you don't build inside its abstractions.
- **Not CrewAI.** No implicit collaboration, no string-passing between agents. Every boundary is typed.
- **Not a framework in the heavyweight sense.** No DSL to build inside, no required runtime wrapping your code; the library is imported into your code rather than the reverse. "Agent component library" works as a category claim; architecturally it's a library with a small surface area and worked coordinator commissions (`MorningBriefingCommission`, a linear pipeline, and `DeepResearchCommission`, a recursive LLM loop) that demonstrate distinct composition patterns.
- **Not opinionated about topology.** Parent-as-hub is the shape the coordinator commissions demonstrate; every other pattern uses the same contract.
- **Not a UI layer.** The library doesn't render anything. Cost ledgers, conversation surfaces, file deliverables — application concerns.
- **Not an agent runtime.** Whatever orchestrates persistent autonomous behavior — scheduling, initiative, persistent state, user-facing surfaces — lives above the library and uses it. Most of what people call "an agent" lives in that upper layer, which the library doesn't specify.

## The bet

The Commissions library makes a specific bet: that the right primitive for AI work is a bounded, contracted, isolated unit, and that everything else — persistence, autonomy, conversation, scheduling, cost ledgers, user surfaces — composes above that primitive without leaking back into it.

If the bet is right, the library stays small, stable, and durable. The interesting work happens in the applications above it (where strategic reasoning, scheduling, and user surfaces live) and in the curator layer above that (where named coordinator patterns and their prompts get authored, packaged, and distributed). The library's job is to be the substrate those layers can trust — fixed contracts, predictable structure, structural cost and provenance — so that the layers above can change quickly without breaking each other.

The wager that the contract *generalizes* is what a design probe across deliberately diverse consumers — a poker game agent, an inbox assistant, a doc-corpus librarian, a research assistant, a coding agent — set out to test. The finding: the library/application line sits at provably different heights across them while the contract underneath never moves. Three traits vary independently — how fat the above-library shell is, how deep the commission tree runs, and whether the consumer accumulates a persistent artifact above the library. The sharpest data point is the game-agent / research-assistant pair: an identical structural skeleton serving two domains that share nothing else. That one contract absorbs that spread — rather than quietly fitting one shape — is the evidence under the bet.

The contract is the bet; the patterns are how the bet pays off; the layering is what keeps the bet from getting too big to hold.

## See also

- [`composition.md`](composition.md) — how the pieces fit together: contract jacket, types, patterns, info flow, output discipline, persistence, coordinator templates, failure modes
- [`AGENTS.md`](../AGENTS.md) — project conventions and AI-assistant guidance
