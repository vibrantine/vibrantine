# 🚧 Designing a Commission's Input and Output Types

> Concept draft / build-phase. See [`README.md`](README.md) for the concepts
> folder role.
>
> A how-to companion to [`commission-fundamentals.md`](commission-fundamentals.md).
> That doc maps *who owns what* across a commission. This one is narrower: **how
> do you write the two types a commission declares, and why do they matter?**
> Field-by-field detail belongs in a future reference doc; today's live contract
> reference is [`../../authoring.md`](../../authoring.md) (Part III).
> Written for a novice AI coder: plain language first, type names second.

**Legend.** 🔭 **Planned** = designed and decided, not yet shipped. Everything
unmarked is live on `main` today.

---

## Why these two types exist

You do not chat with a commission. You issue a bounded **order of work**: hand it
the task-shaped value it needs, get a result-shaped value back. The input and
output types are what make that possible, and that is the whole reason to care
about them.

Compare the two ways of getting work out of an AI model:

| Talking to a model directly | Commissioning work |
|---|---|
| Write a prompt string by hand | Fill a typed **input** |
| Send it, read back a blob of text | Get back a typed **output** |
| Parse the text, hope it is shaped right | The value is already the right shape, validated |

The input type and the output type are the two halves of that transformation:

- **The input type is the work order's form.** A caller fills in a structured
  value, not a hand-written prompt.
- **The output type is the promised deliverable.** A caller gets back a typed
  result, not free text to pick apart.

Everything else in this doc, validation, `Claim[T]`, the design rules, exists to
keep that commissioning promise solid. Hold onto that shape and the rest
follows.

> But a model only understands words, not types. So where does the instruction
> go? The author writes it **once**, inside the commission, so no caller ever has
> to. That is the job of `build_user_message`, and it is where the typed input
> becomes the concrete request the model sees.

This guide builds both types for a real commission, `Summarise` (shorten one
piece of content), and shows where `Synthesize` (merge several sources) needs
more.

---

## Part 1: The input type, the parameters

The input type is the structured form a caller fills in when issuing the order.
Here is the whole thing for Summarise, which we will build up to:

```python
class SummariseInput(BaseModel):
    content: str = Field(min_length=1, description="The content to summarise; must be non-empty.")
    length: SummaryLength = Field(default="short", description="Target length of the summary.")
    focus: str | None = Field(default=None, description="Optional aspect to steer toward.")
```

It is a Pydantic `BaseModel`, which is what gives the parameters their powers:
they validate themselves and they serialize cleanly so a run can be saved.
Three moves get you from "what does this work need?" to a good input type.

### Move 1: Name the parameters, substance first

Split the request into two kinds of field:

- **The substance** is the thing the run exists to process. For Summarise that
  is `content`. There is usually exactly one.
- **The steering** is everything that shapes *how* the work happens without
  being the work itself: `length`, `focus`. These are optional knobs, and they
  almost always carry sensible defaults.

This is the same instinct as designing a good request form: one clear subject, a
few well-named options. It keeps the substance from getting buried.

### Move 2: Put the preconditions in the type

A work order has preconditions. An input type carries them, using Pydantic
`Field` constraints, so a bad request fails at construction time:

```python
content: str = Field(min_length=1, ...)
```

If the caller constructs the input model, `SummariseInput(content="")` raises a
`ValidationError` in the caller's code before the commission ever runs. The work
never starts on that bad request, and the interior does not have to defend
against it. Use `min_length`, numeric bounds, and a `Literal` for closed choices
(`length` is a `Literal` of four sizes). Give every field a `description`: it is
documentation, and it is what the model is shown when this type is used as a
tool.

### Move 3: Write the prompt once, in `build_user_message`

This is the move that makes the metaphor real. The model cannot read
`SummariseInput`; it only reads words. So you write one method that turns the
typed parameters into the words the model sees:

```python
def build_user_message(self, input: SummariseInput, ctx: CallContext) -> str:
    parts = [f"Target length: {input.length}"]
    if input.focus:
        parts.append(f"Focus: {input.focus}")
    parts.append("")
    parts.append("Content to summarise:")
    parts.append(input.content)
    return "\n".join(parts)
```

This is where the instruction becomes concrete, written once by the author.
Every caller after that just fills in the shape. That single method is what
turns "craft a prompt each time" into "commission this work with these inputs",
which is the entire point of the input type.

> A good input type: one clear subject, a few well-defaulted knobs, every field
> constrained and described, and a `build_user_message` that turns it into a
> clear request to the model.

The boundary is doing its job when it names the work being commissioned, not the
prompt the author happens to build from it:

```python
# Too prompt-shaped: every caller has to know how to ask.
class SummariseInput(BaseModel):
    prompt: str = Field(description="The prompt to send to the model.")
    instructions: str = Field(description="Extra instructions for the prompt.")


# Better: the caller fills the work order; the commission owns the wording.
class SummariseInput(BaseModel):
    content: str = Field(
        min_length=1,
        description="The content to summarise; must be non-empty.",
    )
    length: SummaryLength = Field(
        default="short",
        description="Target length of the summary.",
    )
    focus: str | None = Field(
        default=None,
        description="Optional aspect to steer toward.",
    )
```

---

## Part 2: The output type, the return value

The output type is the value the caller gets back, and it carries a guarantee an
ordinary AI reply cannot.

**A successful AI-loop commission can only finish by producing the promised
deliverable.** For a commission whose interior is an AI loop, the `output_type`
becomes the schema of a framework-supplied `conclude` tool. The model cannot
successfully finish by writing "done" in prose. It must call `conclude` with a
value that fits the type, and that value is validated on the way out. If it
never calls `conclude`, calls it with invalid arguments, runs out of budget, or
hits the iteration cap, the commission returns a structured failure instead.
That is why a successful return is a typed object you can use directly, not text
you have to parse and hope about. Two moves design a good one.

### Move 1: Return the smallest shape the caller needs

Return exactly what the caller will consume, nothing more. For Summarise that is
one field:

```python
class SummariseOutput(BaseModel):
    summary: str = Field(description="The summary, written to the target length.")
```

One field is not under-design. It is an honest return type. Add a field only
when a caller will read it.

### Move 2: If the result asserts facts, return the trail too

Some return values are plain answers. Others make claims a caller will act on,
and those should say *where each claim came from*. Three reusable pieces exist
for this:

- **`Provenance`** — where a piece of data came from and how grounded it is.
- **`ConfidenceLevel`** — the shared vocabulary: `verified`, `grounded`,
  `speculative`.
- **`Claim[T]`** — an asserted value carried with the provenances that back it.

This is the one upgrade from Summarise to Synthesize, which merges many sources,
so the trail has to survive the merge:

```python
class SynthesizeOutput(BaseModel):
    summary_text: str = Field(description="Neutral prose summary of the sources.")
    claims: list[Claim[str]] = Field(description="Assertions with their supporting provenances.")
```

Rule of thumb: reach for `Claim[T]` when the return value asserts something a
caller should be able to audit, cite, verify, deduplicate, or act on. Source
count is not the rule. A one-document legal summary may need claims because each
assertion must be traceable; a five-source creative brief may not. `Synthesize`
needs claims because it merges sources and the trail has to survive the merge.

> A good output type: the smallest shape the caller consumes, plus a provenance
> trail (`Claim[T]`) only when the result makes assertions worth tracing.

---

## Keeping the order honest

Three habits keep the two types worth calling. They matter more than for
ordinary code, because the types *are* the commissioning contract: changing one
changes what every caller relies on.

- **Reusable primitives stay domain-neutral.** Name fields for the work, not for
  one caller. `content` and `focus` serve everyone; `email_thread` belongs on an
  inbox-specific commission. General parameters are what let unrelated callers
  reuse a primitive. Domain-specific commissions are still valid; their boundary
  should simply be honest about the domain they serve.
- **Simplest shape that carries the weight.** Fewer fields, each earning its
  place. A short signature is easier to call and harder to break.
- **Shape via the consumer.** When you do not yet have a real use for a field,
  do not invent its exact form. (`ImagePart`, the multimodal input piece, ships
  carrying only a URL string until the first image-bearing commission reveals
  what it needs.) Let the consumer pin the shape.

---

## 🔭 One gotcha: the return type can't vary per call

`output_type` is part of the commission's identity. It is welded to the class
and immutable, so the promised deliverable is fixed for every call. That is
right almost always, but it bites classification: a `Classify` whose caller
passes the labels at call time would want its return constrained to *those*
labels, which differ every call, and that collides with a fixed `output_type`.
Summarise dodges the same pressure by putting its closed vocabulary
(`SummaryLength`) on the *input*, where per-call values are expected. How to
handle a return shape that genuinely wants to vary is an open question, recorded
here so it is not a surprise.
