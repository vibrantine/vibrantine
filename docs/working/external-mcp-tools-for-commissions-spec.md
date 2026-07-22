# Feeding External MCP Tools Into Commissions

Status: parked working proposal. It does not authorize implementation. Review
resumes only after the outward adapter ships on stable MCP v2 and a real
Commission needs an external MCP operation.

## Parked Proposal

If its trigger is later met, reconsider an optional MCP client adapter that
lets an application bind selected tools from an external MCP server into
ordinary Vibrantine Tool objects, then place those objects explicitly into
chosen Commission toolboxes.

Connection does not imply exposure. Discovery does not imply binding. Binding
does not imply placement. Placement does not override the run's capability
grant or tool ceiling.

No MCP connection, catalog, credential, or integration registry enters
`CallContext` or the Gatekeeper.

## Summary

One installed MCP server is represented above Vibrantine by an
application-owned integration object. It owns the connection and discovered
catalog and can produce approved, typed Tool proxies. Each proxy has a stable
local name and contract while remembering the remote MCP tool name internally.

```text
Application-owned XYZMAIL integration
├── MCP connection
├── discovered catalog
├── approved binding manifest
└── Vibrantine Tool proxies
    ├── email_read  -> remote "peek"
    ├── email_send  -> remote "go"
    └── email_trash -> remote "bin_it"

AttendToEmail
├── ReadEmail.toolbox              = (email_read,)
├── AnalyzeEmail.toolbox           = ()
├── DetermineResponse.toolbox      = ()
├── DraftResponse.toolbox          = ()
├── ReviewDraft.toolbox            = ()
└── SendEmail.toolbox              = (email_send,)
```

The model sees the stable local contract. It never needs to know that XYZMAIL
called its send operation `go`.

## Relation to Existing Rulings

The current deferred adapter ruling speaks only about exposing Commissions to
external agent systems. Before this client adapter is implemented,
[`design-decisions.md`](../design-decisions.md#not-built-yet) must be re-ruled to
cover both directions.

This proposal preserves the existing decisions:

- Tools and Commissions share one toolbox menu.
- Objects, not names or a registry, link in-process composition.
- Commissions retain nothing between invocations.
- The application owns state, policy, and confirmation gates.
- `CapabilitySet` only narrows the toolbox offered to an LLM.
- The Gatekeeper is an in-process guardrail, not a sandbox.
- No new `ErrorKind` is introduced for MCP.

Arbitrary image, audio, embedded-resource, and resource-link results intersect
the deferred multimodal-output decision. The first slice therefore supports
typed JSON structured content and an explicit text fallback only.

## Goals

- Connect to a local or remote MCP server through the official SDK.
- Discover its tools without exposing them to any Commission automatically.
- Bind a selected remote operation to a stable local Tool name and Pydantic
  input/output contract.
- Keep awkward remote names and shapes behind deterministic mappings.
- Let the application place exact Tool objects into exact Commission toolboxes.
- Preserve dispatch logging, cancellation, provenance, and errors-as-values.
- Support a later no-code onboarding flow without executing generated code.
- Prevent a large connected catalog from becoming one universal LLM menu.

## Non-Goals

- Adding an MCP server registry to Vibrantine.
- Passing an integration catalog through `CallContext`.
- Storing an MCP client or session in the Gatekeeper.
- Automatically adding all discovered tools to every Commission.
- Mutating a shared Commission's toolbox after construction.
- Faithfully converting every possible JSON Schema into a Pydantic model at
  runtime.
- Letting an LLM generate and execute Python adapter code.
- Making MCP annotations an authorization mechanism.
- Sandboxing local MCP processes inside Vibrantine.
- MCP resources, prompts, sampling, Apps, or task extensions in the first
  slice.

## Four Separate Objects

### 1. MCP connection

An application-owned client/session or process connection. It knows transport,
authentication, server lifecycle, and protocol details. It may be shared by
several Tool proxies if its implementation is concurrency-safe.

The application opens and closes it explicitly, preferably with an async
context manager. It is never serialized, placed in Commission input, or stored
in run context.

### 2. MCP integration

An application object representing one installed server. It groups:

- connection configuration;
- trust and approval state;
- discovered tool descriptors;
- schema fingerprints;
- an approved binding manifest; and
- the Tool proxies constructed from those bindings.

This is application code, not a Commission and not a Gatekeeper extension.

### 3. MCP Tool proxy

A deterministic `Commission[InputT, OutputT]` with `deterministic=True`. One
proxy wraps one approved remote MCP operation. Its `_run` validates and maps the
typed input, performs `tools/call`, validates and maps the result, and returns a
normal `CommissionResult`.

### 4. Optional onboarding Commission

An application-level Commission that examines bounded MCP metadata and proposes
a typed binding manifest. It runs during installation or reconfiguration, not
on every operational call. Its output is data for validation and review; it
does not edit source files, import modules, mutate live toolboxes, or execute
generated code.

## Proposed Developer Shape

Names are provisional. The boundary is more important than the spelling:

```python
from vibrantine.mcp import connect_mcp, bind_mcp_tool


async with connect_mcp(xyzmail_config) as xyzmail:
    email_read = bind_mcp_tool(
        connection=xyzmail,
        remote_name="peek",
        name="email_read",
        description="Read one email by its stable message identifier.",
        input_type=ReadEmailInput,
        output_type=ReadEmailOutput,
        mapping=read_mapping,
    )

    email_send = bind_mcp_tool(
        connection=xyzmail,
        remote_name="go",
        name="email_send",
        description="Send an already reviewed email message.",
        input_type=SendEmailInput,
        output_type=SendEmailOutput,
        mapping=send_mapping,
    )

    attend = AttendToEmail(
        reader=ReadEmail(toolbox=(email_read,)),
        analyzer=AnalyzeEmail(toolbox=()),
        determiner=DetermineResponse(toolbox=()),
        drafter=DraftResponse(toolbox=()),
        reviewer=ReviewDraft(toolbox=()),
        sender=SendEmail(toolbox=(email_send,)),
    )

    result = await run_commission(
        attend,
        input,
        tool_ceiling=("email_read", "email_send"),
        capabilities=CapabilitySet(
            tools=frozenset({"email_read", "email_send"})
        ),
    )
```

The proxy objects are constructor dependencies. They are not part of the typed
domain input, and the model cannot inspect their connection or credentials.

## Visibility and Authority

For an LLM-loop Commission, the effective tool menu remains:

```text
Commission toolbox
    intersect branch capabilities
    intersect run-wide tool ceiling
```

The three controls have different jobs:

| Control | Job |
|---|---|
| Toolbox membership | Places a real dependency at a specific node |
| `CapabilitySet` | Narrows what that branch's LLM may call |
| `tool_ceiling` | Applies an immutable maximum to every LLM menu in the run |

Toolbox membership is the primary structural boundary. A Commission that must
not send email receives no send Tool object.

Capabilities and the ceiling govern LLM exposure, not arbitrary Python. A
custom coordinator's hardcoded `dispatch` remains ungated by design. Therefore
the application must not hand a high-risk proxy to code that should not invoke
it. Strong isolation against untrusted Python requires an application sandbox,
not another Gatekeeper field.

## Stable Local Contract, Awkward Remote Contract

A binding gives the remote operation a stable local identity:

| Local contract | Remote MCP operation |
|---|---|
| `email_read` | `peek` |
| `email_send` | `go` |
| `email_trash` | `bin_it` |

The local description is application-controlled LLM-facing prose. Raw remote
descriptions are discovery evidence, not trusted instructions copied verbatim
into every operational prompt.

The local Pydantic input/output types remain the contract functional
Commissions were authored against. A binding may translate field names and
shapes, but it may not weaken or silently change the local contract.

## Mapping Model

Two binding paths serve different consumers.

### Explicit developer binding

A developer may write ordinary deterministic Python around an irregular
provider. That adapter is reviewed application code and wears the same Tool
contract as any other deterministic capability.

### Declarative no-code binding

An onboarding Commission may propose a manifest using a deliberately small
transformation vocabulary:

- rename a field;
- select a nested field;
- supply a constant default;
- map an enum through an explicit table;
- convert a bounded date/time representation;
- wrap or unwrap one list/object layer; and
- select structured content or a single text result.

The deterministic manifest interpreter must reject everything else. It must not
support Python expressions, `eval`, `exec`, dynamic imports, shell expansion,
arbitrary templates, callbacks, or external schema-reference fetching.

An onboarding model proposes; deterministic validation and application/user
approval install.

## Schema Policy

The first slice requires explicit local Pydantic input and output types. It
does not attempt complete runtime conversion from arbitrary MCP JSON Schema to
Pydantic.

At binding time, the adapter checks the remote schema against the declared
mapping and local contract. Unsupported composition, recursion, excessive
depth, external references, or unbounded schemas fail closed.

Supported result modes:

1. Validate MCP `structuredContent` as the mapped local output type.
2. Parse one JSON text content block, then validate it as the local output.
3. Put one plain text content block into an explicitly declared local text
   field.

Image, audio, resource links, embedded resources, multiple heterogeneous
content blocks, and server-to-client elicitation are unsupported in the first
slice. A binding that receives them returns an ordinary structured failure
rather than silently discarding content.

## Proxy Call Lifecycle

For one proxy invocation:

1. Check `ctx.cancel` before starting external work.
2. Serialize the typed local input through the approved mapping.
3. Call the fixed remote MCP tool name on the application-owned connection.
4. Honor request cancellation and a binding/client timeout.
5. Convert MCP tool errors and transport failures into `ErrorState` values.
6. Extract the configured result form.
7. Apply the approved output mapping.
8. Validate the exact local Pydantic output type.
9. Return a `CommissionResult` with cost and provenance.

The proxy's provenance identifies the server identity, remote tool name, and
call time without exposing credentials. Standard MCP has no portable external
API price field; a generic proxy reports no separately observed USD cost. A
provider with per-call pricing needs an explicit application adapter that can
report it honestly.

## Error Mapping

No MCP-specific `ErrorKind` is added.

| Condition | Vibrantine kind |
|---|---|
| Invalid local input or manifest | `validation` |
| Unsupported or incompatible remote schema | `validation` |
| Remote result violates the approved output contract | `internal` |
| MCP transport/tool timeout | `timeout` |
| Request cancellation | `cancelled` |
| Recognized remote rate limit | `rate_limit` |
| Other MCP tool or transport failure | `internal` |

Retryability is based on the actual condition, not on untrusted MCP annotations.
MCP error text is bounded before it enters an LLM transcript; full diagnostics
belong in application logs.

## Catalog and Connection Lifecycle

Discovery produces an application-side catalog. It does not produce a live
toolbox.

```text
connect
  -> discover
  -> inspect/select
  -> approve/bind
  -> construct proxies
  -> construct Commission tree
  -> run
```

Tool-list changes invalidate the integration's schema fingerprint. The
application revalidates bindings and reconstructs affected Commission objects
between runs. It never mutates a shared toolbox during a run or after an object
has been shared.

The application chooses connection lifetime. A stdio server may remain open
across several runs, but that transport lifetime is not Commission memory. Any
semantic state needed by a later invocation must still be represented by an
explicit typed handle or application input.

## Preventing Tool-Menu Explosion

Connected, bound, and visible are separate sets:

```text
all connected MCP tools
  -> explicitly approved bindings
  -> exact Commission toolboxes
  -> capabilities and ceiling
  -> one LLM's visible menu
```

A general personal agent should see service or task Commissions rather than
hundreds of raw operations:

```text
PersonalAgent
└── email_operator
    ├── inbox_reader
    │   ├── email_search
    │   └── email_read
    ├── mail_writer
    │   ├── email_create_draft
    │   └── email_send
    └── mailbox_organizer
        ├── email_move
        └── email_trash
```

No model sees every level simultaneously. If one external server still has an
unmanageably large relevant catalog, the application may run a bounded selector
before constructing a task-specific Commission. Dynamic mid-loop tool mutation
and a generic `call_any_tool(server, name, arguments)` escape hatch are deferred.

## No-Code Onboarding Outcome

An onboarding flow may produce one of three explicit outcomes:

1. Canonical integration: remote operations satisfy known application
   capabilities through validated mappings.
2. Bounded generic integration: selected native operations are available
   behind one service Commission but do not claim canonical compatibility.
3. Adapter required: semantics, authentication, schema, output content, or
   safety cannot be represented by the supported binding model.

The flow must report confidence and omissions. It must not silently map an
ambiguous destructive operation such as `remove` to `trash` or `delete`.

## Security Requirements

- Installing a local stdio server is application-managed code execution and
  requires explicit consent plus operating-system sandboxing where possible.
- Do not inherit the application's whole environment into a child server;
  provide an explicit environment allow-list.
- Credentials remain in the connection/broker and never enter model context.
- Treat remote names, descriptions, schemas, annotations, errors, and results
  as untrusted data.
- Never execute code generated from MCP metadata or onboarding output.
- Bound schema depth, descriptor length, tool counts, result size, and
  validation work.
- Use application-controlled local names and descriptions for canonical
  bindings.
- Require deterministic application approval for consequential actions such as
  send, delete, purchase, or publish.
- Keep shell/code-execution tools out of branches that process untrusted MCP
  or email content.
- Revalidate and require approval when a bound remote schema changes.

MCP annotations can inform review but cannot grant authority. A server may lie
about being read-only or non-destructive.

## Packaging and Dependency

The client adapter belongs in the same optional `vibrantine.mcp` integration
area as the server adapter, behind the official MCP SDK dependency. Core imports
must continue working when that optional dependency is absent.

Application-level integration catalogs, domain capability contracts, onboarding
Commissions, approval records, and credential stores do not belong in the
Vibrantine package.

## Implementation Sequence

1. Re-rule adapters to cover both MCP directions and name the consumer.
2. Define the private connection seam and test it with an in-memory MCP server.
3. Implement one explicitly typed structured-content Tool proxy.
4. Add cancellation, timeout, provenance, and error mapping.
5. Add local-name/remote-name mapping and collision checks.
6. Add the bounded declarative mapping interpreter.
7. Add schema fingerprints and stale-binding refusal.
8. Prove nested toolbox exposure with the XYZMAIL email workflow.
9. Only then prototype onboarding-Commission proposals and generic fallback.

## Test Plan

- Discovery alone creates no Tool proxy and changes no toolbox.
- Binding `email_send` to remote `go` exposes only `email_send` to the LLM.
- `ReadEmail` cannot see or call `email_send` when it was not placed there.
- `SendEmail` can invoke remote `go` through the canonical proxy.
- Capability and ceiling intersection still removes an otherwise wired proxy.
- Input and output mappings round-trip the declared local Pydantic types.
- Incompatible schemas fail at binding time.
- Unexpected content kinds fail without data loss or arbitrary parsing.
- MCP errors, timeouts, cancellation, and rate limits become existing error
  values.
- Error details are bounded before entering an LLM transcript.
- Concurrent proxies sharing one connection behave according to the client's
  documented concurrency contract.
- A schema-list change marks bindings stale and does not mutate a live tree.
- A manifest containing an executable expression is rejected as data.
- No MCP object appears in `CallContext`, the Gatekeeper, or the frozen top-level
  public surface.

Integration tests cover one local stdio fake server and one Streamable HTTP fake
server. Live third-party servers belong in opt-in integration tests and require
their own credentials.

## Acceptance Criteria

- An application can wrap one external MCP operation as an ordinary typed Tool.
- The application can place that Tool in one selected Commission and nowhere
  else.
- The remote operation may have a different name and shape behind a validated
  mapping.
- A twenty-node Commission tree exposes only the tools wired at each node.
- Connecting ten MCP servers does not automatically add one tool to an LLM
  menu.
- No generated Python or shell code is needed for supported declarative
  mappings.
- Unsupported integrations fail closed with a specific explanation.
- The Gatekeeper remains unchanged and contains no integration state.

## Questions If Resumed

1. Is the first supported output subset—structured JSON plus explicit text
   fallback—large enough for the first real consumer?
2. Should the optional package ship the declarative mapping interpreter in the
   first slice, or first prove only explicit typed developer bindings?
3. Which provider-neutral domain contract, if any, is the first no-code proof:
   email, calendar, or a smaller service?
4. Does the application need a long-lived connection manager immediately, or
   can the first proof own one connection in one application context?
5. What exact schema change invalidates a binding: any descriptor hash change,
   input/output schema change only, or a reviewed compatibility relation?
6. Where should confirmation for `email_send` live in the first consumer: a
   user-facing application gate, a deterministic policy gate, or both?
