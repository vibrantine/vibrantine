# 🚧 Composing Commissions

> Concept draft / build-phase. See [`README.md`](README.md) for the concepts
> folder role.
>
> A companion to [`commission-fundamentals.md`](commission-fundamentals.md) and
> [`boundary-types.md`](boundary-types.md). Those explain *what a commission is*
> and *how to design its boundary*. This one explains the next question:
> **how do small commissions become larger behavior?**
>
> Field-by-field detail belongs in a future reference doc; today's live contract
> reference is [`../../authoring-from-an-external-repo.md`](../../authoring-from-an-external-repo.md).
> Written for a novice AI coder: plain language first, type names second.

**Legend.** 🔭 **Planned** = designed and decided, not yet shipped. Everything
unmarked is live on `main` today.

---

## The short version

Composition in Vibrantine is delegated work with receipts.

A parent commission calls a child commission, gets back one
`CommissionResult`, inspects it, and decides what to do next. The child does
not know who its siblings are. It does not write to shared state. It does not
send messages sideways. Everything flows through the parent.

That sounds restrictive until you notice what it buys:

- The data path is easy to read.
- Failures arrive as values the parent can handle.
- Cost and provenance roll upward through the structure.
- A small commission can be tested alone, then reused inside a larger one.

The whole model is:

```text
caller
  -> parent commission
       -> child A -> CommissionResult
       -> child B -> CommissionResult
       -> child C -> CommissionResult
     parent combines those results
  <- one parent CommissionResult
```

The parent is the hub. The result envelope is the joint.

**The whole model reduces to two sentences.** A commission is one typed function
with an LLM somewhere inside it: one input in, one result envelope out. The
parent is the only path between children: no sibling channel. If a design keeps
growing machinery that no longer obviously reduces to those two sentences, doubt
the machinery, not the core. The working code is the check: a concept the code
does not actually need was conversational sediment, not architecture.

---

## The two ways a parent decides what runs next

Every commission has the same outside: typed input in, `CommissionResult` out.
What changes is the inside. For composition, the key question is simple:

> Who decides which child gets called next: your Python code, or the model?

### Python coordinator: you decide

A Python coordinator is a commission whose `invoke` method calls children in an
order you wrote. Use this when the workflow is known:

- fetch these URLs, then synthesize the successful ones
- plan, fan out workers, review their answers
- run a verifier after an editor finishes

The shape is plain Python:

```python
class BriefingCommission(Commission[BriefingInput, BriefingOutput]):
    async def invoke(
        self,
        input: BriefingInput,
        ctx: CallContext,
    ) -> CommissionResult[BriefingOutput]:
        fetch_results = await asyncio.gather(*[
            dispatch(self._fetch, FetchInput(url=url), ctx)
            for url in input.urls
        ])

        sources = [
            SynthesisSource(content=r.output.content, provenance=r.provenance)
            for r in fetch_results
            if r.status == "success" and r.output is not None
        ]

        synth = await dispatch(
            self._synthesize,
            SynthesizeInput(sources=sources),
            ctx,
        )

        # Build and return one CommissionResult for the parent call.
```

The model may still be involved inside the children. The coordinator simply
owns the traffic pattern.

### AI-loop commission: the model decides

An AI-loop commission gives the model a toolbox. On each turn, the model chooses
which tool or sub-commission to call, with what input, and when it has enough to
finish. It finishes by calling the framework-supplied `conclude` tool, whose
schema is the commission's `output_type`.

Use this when the routing itself needs judgment:

- choose which file-reading tool to use next
- decide whether to ask a sub-researcher or fetch a source directly
- inspect partial results and choose another step

The author supplies the toolbox; the model chooses from it:

```python
class AskCommission(Commission[AskInput, AskOutput]):
    name: ClassVar[str] = "ask"
    description: ClassVar[str] = "Answer a question about a single file."
    input_type: ClassVar[type] = AskInput
    output_type: ClassVar[type] = AskOutput
    system_prompt: ClassVar[str | None] = "Use read, then conclude."
    toolbox = (ReadTool(),)

    def build_user_message(self, input: AskInput, ctx: CallContext) -> str:
        return f"File path: {input.file_path}\nQuestion: {input.question}"
```

You did not write the loop. The base commission owns it.

Rule of thumb: keep control flow in Python when you already know the steps.
Reach for an AI loop when choosing the steps is itself the work.

---

## Toolbox is capacity; capabilities are permission

Composition has two separate questions that are easy to blur:

- **What can this commission reach if fully trusted?**
- **What is this run allowed to reach right now?**

The first is **capacity**. It lives on the commission instance, usually through
its `toolbox`. The builder wires it at construction:

```python
class ResearchCommission(Commission[ResearchInput, ResearchOutput]):
    def __init__(self) -> None:
        super().__init__(
            toolbox=(FetchTool(), SummariseCommission()),
        )
```

The second is **permission**. It lives in `CallContext`, per run:

```python
ctx = CallContext(
    capabilities=CapabilitySet(tools=frozenset({"fetch"})),
)
```

If a tool is in the toolbox but not in `capabilities`, an AI-loop commission's
model is not offered it. The commission may be able to fetch and write in
general, while this particular run is allowed only to fetch.

That split is the safety lever:

- Build powerful commissions.
- Grant narrow permissions per run.
- Expand the grant only when the caller means to.

Python coordinators are different: their child calls are written directly in
code, so `capabilities` does not gate them. The author already chose those
calls. If a coordinator needs a narrower child grant, it passes a narrower
context to that child.

---

## Children return values, not vibes

When a parent calls a child, it receives a `CommissionResult`. That result has
the same shape whether the child is a simple tool, an AI-loop commission, or a
coordinator with its own subtree.

The first parent decision is always:

```python
if result.status == "success" and result.output is not None:
    use(result.output)
elif result.status == "partial" and result.output is not None:
    use_what_is_safe(result.output)
    record_or_surface(result.error)
else:
    handle_failure(result.error)
```

This is where errors-as-values pays off. A failed child does not tear down the
program unless the parent chooses to stop. The parent can retry, skip that child,
continue with survivors, narrow the task, or return its own structured failure.

For example, a briefing coordinator can fetch ten sources, drop the three that
failed, and still synthesize the seven that succeeded. It returns `partial`
because the job mostly worked and the caller deserves both the usable result and
the failure list.

---

## Cost and provenance roll upward

Each child reports what it spent and where its result came from. A parent does
not need to guess. It adds the child costs into its own result and carries
provenance forward where the output depends on child data.

For a Python coordinator, this is explicit:

```python
total_cost = sum(r.cost.estimated_usd for r in child_results)

return CommissionResult(
    status="success",
    output=MyOutput(...),
    provenance=Provenance(...),
    cost=CostMetrics(estimated_usd=total_cost),
)
```

For an AI-loop commission, the framework does the child-cost rollup for calls
made through the loop. A sub-commission used as a tool spends money; the parent
result includes it.

That is the difference between a call tree and a pile of API calls. The tree
remembers what happened underneath it.

---

## State stays above the tree

A commission can keep local variables while one invocation is running. A parent
can collect child results, build a list of claims, or carry a question from one
round to the next.

But when the invocation ends, that private state ends with it.

If something must survive across invocations, it belongs to the caller and comes
back through typed input on the next run:

```python
class ResearchInput(BaseModel):
    question: str = Field(description="The question to answer.")
    prior_claims: list[Claim[str]] = Field(
        default_factory=list,
        description="Claims carried in by the caller from earlier runs.",
    )
```

Not all state is threaded by value. Heavy read-only state (a whole codebase, a
large corpus) is referenced by **handle**, not copied into a field: the caller
hands the run a path, and the run reads what it needs from the world. Re-reading
the world on the next run is not hidden memory, it is re-reading, so the run
stays a pure function of the world it read. The rule of thumb is **reads look,
writes carry**: many runs can read the same files in place, since shared reads do
not race, while *writes to that shared state* serialize through a single owner
rather than many workers editing it at once. This is not a ban on side effects: a
commission may act and write on its own (the acting-vs-drafting choice, gated by
capabilities). The funnel applies specifically when a wide fan shares one
underlying state, because concurrent independent writes reintroduce the race the
tree exists to prevent. The clean shape there is workers that *draft* the change
as a typed value and one owner that applies it.

Persistence records are for inspection and replay. They are not hidden memory.
This keeps a commission testable: same input, same permissions, same declared
capacity, one result envelope.

---

## Three composition shapes to reach for first

Most useful compositions start from one of three simple shapes.

### Pipeline

One child feeds the next:

```text
fetch -> summarize -> write report
```

Use it when each step clearly depends on the previous step's output.

### Fan out, then gather

Many children do similar work, then the parent combines them:

```text
          -> worker A
plan/task -> worker B -> review/synthesize
          -> worker C
```

Use it when the task naturally splits into independent parts.

This is also how large work scales: **go wide, not deep.** A big job wants many
siblings under one coordinator (breadth), not many nested levels (depth). Breadth
is cheap, because siblings don't compound each other's errors, drift each other's
goals, or stack each other's latency. Nothing forbids depth: the contract permits
arbitrary nesting, and budget, sliced thinner at each level, is what actually
bounds a descent. The caution is aimed at stacked *LLM reasoning*, since
deterministic levels are cheap. So the instinct that a big job needs many nested
LLM levels is usually a category error: it wants many siblings at shallow depth.

### Loop until done

The parent repeats a small cycle until a condition is met:

```text
plan -> act -> review -> maybe continue
```

Use it when the work may need refinement, but keep the stop condition explicit:
round cap, budget, deadline, or a typed "done" signal from a child.

These are not framework types. They are authoring patterns. Write the plain
Python shape first; extract a reusable template only after the second or third
real commission asks for it.

---

## What composition rules out

Vibrantine composition is intentionally not a shared-state graph.

No sibling messages:

```text
child A -/-> child B
```

No shared blackboard:

```text
child A -> shared mutable state <- child B
```

No hidden peer discovery:

```text
child A finds child B at runtime
```

If two children need to influence each other, the parent mediates:

```text
child A -> parent -> child B
```

That is less expressive than a graph runtime. It is also much easier to inspect,
test, and trust. When the work really does need graph-shaped state, that is a
sign you may be building an application layer above Vibrantine, not a single
commission tree inside it.

---

## A good composition

A good composition has a few visible properties:

- The parent owns the data path.
- Children are injected at construction, not discovered at runtime.
- Every child call goes through `dispatch`, never directly through `invoke`.
- The parent checks child `status` before reading child `output`.
- Costs from children appear in the parent's `cost`.
- Partial success is represented honestly, not flattened into success.
- State that must survive the run is threaded through input/output, not hidden
  inside the commission.

If a boundary type turns an AI instruction into a typed order of work,
composition lets you delegate larger work through smaller orders without losing
the receipts.
