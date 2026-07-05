# 🚧 Vibrantine Commission Fundamentals

> Concept draft / build-phase. See [`README.md`](README.md) for the concepts
> folder role.
>
> The **conceptual front door** to Commissions, not a field reference. Its whole
> job is to tell a new developer two things:
>
> > **Here is what must never break. Here is where you are free.**
>
> It is a *map of ownership*, not an exhaustive list of fields. It names each
> surface, says who owns it, and shows the dials that live on it. Field-by-field
> detail lives in [`../../authoring.md`](../../authoring.md) (Part III).
> Written for a novice AI coder: plain language first, type names second.
>
> Standalone for now. It will likely feed the README's "the model" section and
> the reference part of `authoring.md` once it settles, per *promote, don't
> accumulate*.

**Legend.** 🔭 **Planned** = designed and decided, not yet shipped. Everything
unmarked is live on `main` today.

---

## What a Commission is

Vibrantine is an agent-**component** library, not an agent runtime. Its one
primitive is a typed, bounded, isolated unit of AI-bearing work called a
**Commission**.

A Commission is not an agent chatting with other agents. It is closer to a
**sealed work order**. A typed input comes in, a predictable result comes out,
and the internals are free to do whatever they like as long as the boundary
contract holds. Small, tested Commissions nest inside larger ones, and the
larger one stays just as predictable because every joint is the same contract.

Two properties make that promise real rather than aspirational:

- **Errors are values, not explosions.** A Commission that fails returns a
  result that *says* it failed. No exception escapes the boundary. You always
  get the envelope back.
- **The interior is sealed.** How the work happens inside (plain Python steps,
  or an AI model driving tools in a loop) never leaks through the contract.

The firm version of all of this fits in one sentence, and it is the map the
rest of the doc fills in:

> **A Commission separates identity, capacity, permission, task, and result.**

Five concerns. Crucially, five *different owners*. Most confusion about
Commissions is really confusion about which of the five you are touching, and
who is allowed to touch it.

---

## The map: five surfaces, five owners

| # | Surface | Answers | Owner | Fixed when |
|---|---|---|---|---|
| 1 | **Identity** (declaration) | What the Commission *is* | Commission author | Written into the class, forever |
| 2 | **Capacity** (construction) | What this instance *can do*, and its built-in limits | Builder / app / parent | Built into the instance, immutable |
| 3 | **Permission** (call-time context) | What this run is *allowed* to do | Caller | Per run |
| 4 | **Task** (payload) | What this run is *asked* to solve | Caller | Per run |
| 5 | **Result** (envelope) | What came *back*, and how to trust it | Framework + Commission | Returned by the call |

Read it as a sentence of ownership: **the author owns what it is, the builder
owns what it can do, the caller owns both what it may do and what it must
solve, and the framework guarantees the shape of what comes back.**

Two of these never bend, and they are the contract:

- **Identity's boundary** (the declared input and output shapes) is a promise.
- **The result envelope** is a promise.

Everything between those two promises is where you are free: the sealed
interior, and the dials on capacity, permission, and task. So the dials
concentrate in the **middle three surfaces**, bookended by a fixed identity and
a fixed result.

---

## Surface by surface, with its dials

### 1. Identity: what it is · *author-owned*

Set once, in the class, and welded shut. These are not knobs a consumer turns;
they are the Commission's nature. A caller can rely on them never changing.

- **`name` / `description`**: what it is, for provenance and logs.
- **`input_type`**: the shape it promises to accept.
- **`output_type`**: the shape it promises to return. For an AI-loop
  Commission this *is* the only structured way the loop may finish, so the
  model cannot end by merely "saying it's done."
- **`system_prompt`**: the Commission's own instructions to the model, its
  **immutable identity**. A caller can wrap situational context around it but
  can never rewrite it, which is what keeps the Commission's behavior stable no
  matter who calls it.
- **The interior choice**: Python coordinator (you own the control flow) or AI
  loop (the model owns it, within your caps). Changes the inside completely and
  the contract not at all.

### 2. Capacity: what it can do, and its limits · *builder-owned*

What the instance is wired with, supplied at construction and immutable after.
This is dependency injection: the builder hands the Commission its parts and its
ceilings, so children stay swappable and testable. **Capacity is about ability
and safety, not permission for a particular run.**

- **`model`**: the default model this Commission runs on, as a structured
  `Model` object (identity, endpoint, facts like context window and pricing),
  never a bare string. Passing the object is what lets one Commission target a
  paid cloud model and another a free local one with the same code. 🔭
  **Planned:** the object also carrying generation settings (temperature and
  similar), so two profiles on one underlying model (precise vs creative) are
  distinct objects.
- **`toolbox`**: the tools and sub-commissions this instance *can* call. The
  unit of composition.
- **`max_iterations`**: a hard ceiling on AI-loop turns. A backstop against a
  loop that never converges.
- **`max_input_tokens` / `target_input_fraction`**: the input-size capacity,
  resolved by default from the model's context window.
- **`max_output_tokens` / `overflow_policy`**: the output-size capacity and
  what to do when a result is too large.
- **`persistence_mode`**: whether and when each run is saved for inspection.
- 🔭 **Static safety cap**: a built-in spend ceiling this worker must never
  cross, no matter what a caller grants ("this worker may never exceed $0.01").
  See *Capacity vs permission* below.
- **`client`**: an escape hatch to inject a pre-built API client, mainly for
  tests and unusual endpoints.

### 3. Permission: what this run may do · *caller-owned*

The conditions for one specific run, handed in alongside the task so the task
stays clean. **Permission is granted per call and is the caller's to give or
withhold.** A Commission can never grant itself more than the caller allows.

- **`budget_usd`**: the spend grant for this run and everything beneath it
  ("this invocation may spend $0.20"). The run refuses to exceed it.
- **`capabilities`**: an allow-list narrowing which tools the model may
  actually call this run, independent of what the toolbox *can* reach.
- **`cancel`**: a cooperative stop signal the run checks at natural
  breakpoints, so the caller can revoke permission mid-flight.
- **`concurrency`**: how many child calls may run at once.
- **`on_progress`**: an optional observation callback. Reporting only, never
  control.
- **`backend`**: where persisted runs are written, supplied by the caller,
  inherited by children.
- **`parent_run_id`**: set by the framework, not you; links this run to its
  caller for the audit trail.
- 🔭 **`application_prompt`**: context the top-level app sets once that flows
  unchanged through every Commission in the tree.
- 🔭 **Envelope sections**: named, ordered situational prose the immediate
  caller adds, distinct from the task itself.
- 🔭 **Model grant**: the subset of model profiles this run is permitted to
  use. See *Model ownership* below.

### 4. Task: what this run must solve · *caller-owned*

- **`input`**: an instance of the Commission's `input_type`. The one thing that
  is genuinely "the request." Not a dial: it is the substance the run exists to
  process. It is owned by the caller and changes every run, but unlike
  permission it carries *what to do*, not *what is allowed*.

Task and permission are both caller-owned and both per-run, which is exactly why
they are easy to blur. Keeping them apart is the point: one says *solve this*,
the other says *and you may spend this much, touch these tools, and stop when I
say*.

### 5. Result: what came back, and how to trust it · *framework + Commission*

One value comes back from every call: a **`CommissionResult`**. Success or
failure, simple Commission or deep coordinator, the shape is always identical.
This is the **inviolable universal output**, and it is the second of the two
promises. The framework guarantees the envelope; the Commission fills the
typed slots inside it.

- **`status`**: `success`, `failure`, or `partial`. The first thing a caller
  branches on.
- **`output`**: the typed result (an instance of `output_type`). Present on
  success and partial.
- **`error`**: a structured failure value: its category, a readable detail, and
  whether retrying might help. Present on failure and partial. This is
  "errors are values" made concrete.
- **`provenance`**: where this result came from and how grounded it is. The
  trust record.
- **`cost`**: what this call spent, in USD, with children rolling up into
  parents automatically, so a coordinator reports the true total beneath it.
- **`run_id` / `parent_run_id`**: framework-stamped identifiers placing this run
  in the call tree, for inspection and replay.

Two supporting pieces appear inside outputs that need them: **`ConfidenceLevel`**
(the shared vocabulary for how grounded data is) and **`Claim`** (an asserted
value carried with the sources that back it).

---

## Capacity vs permission: the spending example

The cleanest illustration of why capacity and permission are separate surfaces
is money, because the same word ("budget") lives on both, owned by different
people, meaning different things.

| Thing | Means | Surface | Owner |
|---|---|---|---|
| 🔭 Static safety cap | "this worker may never spend over $0.01" | Capacity (construction) | Builder |
| Run grant | "this invocation may spend $0.20" | Permission (call-time) | Caller |

They are not redundant. The static cap is a property of the *worker*: a cheap
local summariser should never run up a large bill even if some caller carelessly
grants it one. The run grant is a property of the *call*: this particular task
is worth up to twenty cents. The run can spend up to the **smaller of the two**.

> **The Commission object owns capacity. The caller owns permission.**

Run-specific spending belongs *outside* the object, in caller-owned context
(this is the live `budget_usd`). A static safety ceiling belongs *on* the object,
set at construction (this is the 🔭 planned half). The effective ceiling is the
minimum of the two.

---

## 🔭 Model ownership across the surfaces

A future shape, placed here because it follows the same five-surface ownership
logic and matters for autonomous *Commission crafting*, a deferred design
direction. The rule is deliberately conservative: a Commission, especially a crafted one,
should choose from a granted menu, not freely discover or invent model access.

> **The app owns model inventory. The object owns its default model and
> capacity. The caller owns model permission.**

A planned vocabulary, one piece per surface:

- **`ModelCatalog`**: the app-owned inventory of every model profile that
  exists. Lives above the library (state belongs outside).
- **`ModelProfile`**: one concrete model configuration (a model plus its
  settings); the structured `Model` object grown up.
- **`ModelGrant`**: the caller-owned subset of profiles a given run may use.
  Permission, not inventory.
- **`CommissionSpec`**: a crafted Commission refers to model profiles by key,
  choosing only from what the grant allows.

So a Commission never holds "all models." The app knows them all, the caller
grants a subset, and a crafter picks within that subset. Same ownership spine,
applied to model access.

---

## The inviolable universals

If you forget everything else, two things never bend, and they are the entire
basis for building on a Commission:

1. **The declared boundary** (`input_type` in, `output_type` out) is a promise
   the author cannot break without it being a new Commission.
2. **The result envelope** (`CommissionResult`) is a promise the framework keeps
   for every call, success or failure.

Everything else (the interior, and every dial on capacity, permission, and task)
is yours to set. That is the trade the contract offers: fix the two edges, and
the freedom in between costs you nothing in predictability.

---

## One Commission across the five surfaces

The same tiny Commission, seen through each surface. (Illustrative shapes, not
exact API.)

```python
# 1. IDENTITY: what it IS (author writes once, into the class)
class Summarise(Commission[Document, Summary]):
    name = "summarise"
    description = "Reduce one document to a short summary."
    input_type = Document          # promised input boundary
    output_type = Summary          # promised output boundary
    system_prompt = "You summarise one document faithfully and briefly."
    # interior: overrides build_user_message -> an AI-loop commission

# 2. CAPACITY: what it CAN DO (builder wires it, immutable)
summarise = Summarise(
    model=PROFILES["A"],           # its default model, a structured object
    toolbox=(),                    # the tools it can reach
    persistence_mode="dev",
    # 🔭 static safety cap would live here too
)

# 3 + 4. PERMISSION and TASK (caller supplies, fresh each run)
result = run_one(
    summarise,
    Document(text="..."),                  # 4. TASK: what to solve
    ctx=CallContext(budget_usd=0.10),      # 3. PERMISSION: what it may do
)

# 5. RESULT: one envelope, always this shape (framework + commission)
result.status     # "success"
result.output     # Summary(...)   <- the typed result
result.cost       # CostMetrics(estimated_usd=0.004)
result.provenance # where it came from, how grounded
```

Read top to bottom, that is the whole model: the author declares what it is, the
builder gives it capacity, the caller grants permission and hands it a task, and
the framework returns one trustworthy envelope.
