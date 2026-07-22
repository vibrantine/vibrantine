# Exposing Commissions Through a Local MCP Server

Status: temporary SDK v1 implementation complete as a development bridge.
Inspector and SDK-client compatibility pass; named-host compatibility remains
incomplete, and the mandatory SDK v2 replacement remains. This document still
does not describe a feature that ships yet. The ruling lives in
[`design-decisions.md`](../design-decisions.md).

## Executive Summary

Build a small optional outward MCP adapter that exposes an explicit set of
Commission objects as ordinary MCP tools. The first named consumer is a
repository-local application that gives coding agents such as Codex and Claude
Code access to selected Commissions over stdio.

The adapter translates the existing Commission contract. It does not create a
registry, choose an exposure topology, carry host conversation state, or add an
MCP concept to the Vibrantine core. Each valid MCP call becomes one ordinary,
governed `run_commission` root invocation and returns the complete
`CommissionResult` envelope.

The application owns the useful nexus. It constructs the actual Commission
objects, chooses which ones to expose, supplies run policy, and starts the
server. It may expose many specialist Commissions to a capable principal agent,
a few service-level Commissions to a constrained agent, or one routing
Commission to a minimal agent. The MCP surface is flat in every case; semantic
structure lives inside the registered Commissions and the application that
selected them.

This specification is outward only:

```text
Commission -> MCP tool -> external agent
```

Feeding external MCP operations into Commission toolboxes is a separate client
adapter with a separate lifecycle and specification. That inward adapter is
parked until the outward adapter is implemented, migrated to stable MCP v2, and
exercised by its named hosts.

## Ruling

The deferred-adapter trigger has been met: a repository-local Codex and Claude
Code server is the first named external consumer. The ruling record therefore
permits this outward adapter while leaving the inward adapter deferred.

The optional submodule `vibrantine.mcp.server`:

- registers only explicitly supplied Commission objects;
- maps each object to one standard MCP tool definition;
- validates call arguments as the Commission's declared Pydantic input;
- invokes the Commission through an application-supplied governed runner;
- maps MCP cancellation into Vibrantine's existing cancellation path; and
- returns the full result envelope in structured MCP content.

The adapter must not change `Commission`, `CallContext`, `dispatch`, the
Gatekeeper, the frozen top-level `vibrantine.__all__` surface, or any closed
contract vocabulary.

## Why This Belongs as an Adapter

The Commission boundary already contains everything an MCP tool needs:

- a stable name;
- LLM-facing selection prose;
- a Pydantic input schema;
- a Pydantic output inside a structured result envelope;
- cost and provenance;
- errors as values; and
- one governed entry point.

MCP adds a protocol and process boundary around that contract. It does not need
to become part of the contract itself.

The adapter also has a real consumer without making that consumer part of the
library. A small repository-local application can configure Codex or Claude
Code to start the server, while the same adapter remains usable by another MCP
host with different tool-loading behavior.

## Goals

- Let a local MCP host discover selected Commissions as ordinary MCP tools.
- Preserve each Commission's exact input contract at the MCP boundary.
- Return the complete `CommissionResult`, including status, error, cost,
  provenance, and run identifiers.
- Route every valid call through `run_commission`, never `_run` or bare
  `dispatch`.
- Let the application own models, budgets, ceilings, persistence, policy,
  secrets, and the exposed set.
- Support direct specialist, service-level, and routing Commission exposure
  without making any one topology an adapter feature.
- Carry typed image, audio, video, and other artifact references in Commission
  inputs and outputs without adding modality-specific adapter behavior.
- Work correctly for clients that load every schema as well as clients that
  defer or search tools.
- Make malformed inputs fail early with bounded, actionable validation detail.
- Keep the core dependency set and public surface unchanged.
- Make the adapter testable without a live model or subprocess.

## Non-Goals

- A registry that discovers every Commission in a Python environment.
- Exposing a Commission the application did not register explicitly.
- A `list_commissions` tool paired with a generic
  `invoke_commission(name, arguments)` escape hatch.
- A hierarchy, category system, capability catalog, or router built into the
  adapter.
- Automatically choosing a tool-menu size for the consuming agent.
- Receiving the host's conversation, system prompt, workspace, or open files
  implicitly.
- Keeping conversational or resumable state between MCP calls.
- Treating several MCP calls as one Vibrantine invocation tree.
- Mirroring every `run_commission` keyword on a server constructor.
- Mutating the registered tool list after startup in the first slice.
- MCP resources, prompts, roots, sampling, Apps, or task extensions.
- Inline large-media transport and native MCP image, audio, or video result
  blocks in the first slice.
- A remote multi-user deployment or its authorization policy.
- Installation into particular MCP hosts as library behavior.
- An MCP protocol implementation written by Vibrantine.
- Consuming external MCP tools inside Commissions; that is the separate inward
  adapter.

## Ownership Boundary

The adapter translates. The application configures. The Gatekeeper governs one
run.

| Concern | Owner |
|---|---|
| Which Commissions are exposed | Application |
| Direct, service-level, or routing exposure | Application |
| Models, budgets, ceilings, persistence | Application-supplied invocation function |
| MCP name, schema, argument, and result translation | Adapter |
| MCP transport implementation | Official MCP SDK |
| One call's fuses, concurrency, and logs | Vibrantine Gatekeeper |
| Credentials used by model providers | Existing model catalog/client vending |
| File and artifact access granted to a Commission | Application and Commission toolbox |
| MCP-host installation and consent | Host/application |

The server must not receive or retain a Gatekeeper. `run_commission` creates one
for each tool call and closes its provider clients when that call finishes.

## Exposure Model

### A flat protocol surface

MCP exposes a flat tool list. The application supplies the exact Commission
objects that populate it:

```text
First repository-local MCP server
└── compose_vibrantine_sonnet
```

The deliberately unmistakable first tool is specified in
[`compose-sonnet-commission-spec.md`](compose-sonnet-commission-spec.md). It is
a smoke test for discovery, argument construction, invocation, and result
return—not a claim that one-tool servers are the intended final topology. The
first launcher uses `vibrantine_commissions` as its host-configuration server
name so the resulting tool identity is also unmistakable when a host qualifies
it with the server name.

A later application may expose a broader menu such as research, verification,
email, and document Commissions. Flat does not mean structureless: each listed
Commission may own a private tree of children and tools that the principal
agent cannot see directly.

```text
handle_email
├── search_email
├── read_email
├── analyze_email
├── create_draft
└── organize_email
```

The adapter never walks or exposes that private tree. Registering
`handle_email` exposes only `handle_email` unless the application separately
registers one of its children.

Deterministic Vibrantine Tools share the `Commission` subclass contract and
are therefore eligible only when the application registers them explicitly.
The adapter does not need a special Tool path, and the first launcher registers
only `ComposeSonnetCommission`.

### Application-selected granularity

The adapter supports three useful application choices without naming them in
its API:

1. **Direct specialists.** A capable principal agent sees several narrow,
   strongly typed Commissions and selects among them itself.
2. **Service Commissions.** A constrained principal sees a smaller set of
   domain-level workflows whose private children perform the specialized work.
3. **Routing Commissions.** A minimal principal sends a natural-language domain
   request to a Commission whose LLM selects and invokes its granted children.

A routing Commission remains an ordinary Commission. Its MCP description must
be precise enough for the principal to select it even when its typed input
contains a broad natural-language request.

The router should normally execute the selected child inside its own invocation
tree and return the service result. Returning a child name for a second generic
MCP call would discard typed tool discovery, split cost and provenance across
roots, and recreate the forbidden name-based registry.

### One server or several

One local server may expose a dozen or more Commissions when they share a host,
deployment lifecycle, and trust boundary. The adapter imposes no architectural
singleton. An application may start separate servers when credentials,
operators, privileges, audiences, or lifecycle differ materially.

The number exposed is an application decision informed by the intended host.
The adapter must not assume that the host provides native deferred tool search.

## Client Compatibility and Tool Discovery

The server publishes a conventional, complete `tools/list` response. Every tool
has its real name, description, input schema, and output schema. Correctness
must not depend on a client-specific search or reveal protocol.

Sophisticated hosts may keep schemas outside model context and retrieve them
through native tool search. Simpler hosts may place every schema in context.
Both receive the same MCP server surface.

Therefore:

- no `ToolSearch` behavior is implemented by this adapter;
- no client-specific deferred-loading metadata is required for correctness;
- no generic call-by-name tool replaces the actual Commission tools;
- names and descriptions remain high-quality LLM-facing selection prose; and
- compatibility is evaluated against both capable and minimal clients.

Decision: the adapter emits short, fixed server-wide MCP instructions in
addition to every Commission's complete name, description, input schema, and
output schema. The instructions describe only invariant shared behavior:

> Each tool is an independent Vibrantine Commission invocation. Calls do not
> share run or conversation state. Results are complete CommissionResult
> envelopes; inspect status, output, error, cost, and provenance. Correct
> invalid arguments and retry only when safe.

Individual descriptions remain the LLM-facing selection prose for their exact
Commissions, and field descriptions remain part of the complete input schema.
The first slice adds no application-configurable instructions parameter.

## Context Transmission

### Only explicit arguments cross the boundary

The host's model may use its whole active context to decide what to call, but
the MCP server receives only the JSON object in `tools/call.arguments`.

The adapter does not implicitly receive or reconstruct:

- conversation history;
- system or developer instructions;
- files the host has inspected;
- the host's current plan or scratchpad;
- open editor state;
- a workspace root; or
- credentials and connection state.

If a Commission needs any of that information, its declared input type must
represent the required domain context explicitly.

### Complex inputs are ordinary typed inputs

A Commission may accept a short query or a richer request containing an
objective, constraints, file references, artifact handles, relevant context,
and a deliverable specification. The existing Vibrantine schema discipline
still applies: described fields, bounded depth, bounded field count, no
recursion, and `Literal` for enum-shaped values.

The adapter neither adds nor strips a universal context envelope. It maps the
Commission's exact `input_type.model_json_schema()` and validates the received
arguments with that same type.

### Prefer references to bulk duplication

Large source material should usually cross as stable typed references rather
than repeated content:

- repository-relative paths interpreted against an application-approved root;
- application-owned artifact handles;
- message, document, or record identifiers;
- bounded excerpts; or
- a transcript artifact plus an explicit relevant range.

Supplying a path or handle does not grant access. The receiving Commission must
already own an appropriate deterministic tool or application capability, and
the application remains responsible for path containment and authorization.
The adapter does not read files on the Commission's behalf.

Whole conversation histories may be explicit domain input when genuinely
required, but they are never automatic. Regenerating a long history inside a
tool call costs model output tokens, adds latency, and risks omission or
distortion. A typed summary plus stable references is the default application
pattern.

### Media uses the same reference pattern

Image, audio, video, and later media types cross the MCP boundary as ordinary
fields in a Commission's declared Pydantic input or output. Useful references
include HTTPS URLs, paths contained by an application-approved local root, and
application-owned artifact handles.

The adapter does not define a universal media-reference type, inspect or
dereference media, transcode files, copy host attachments implicitly, or map a
Commission result into native MCP media content blocks. The application and
Commission own the reference shape and the deterministic capability that
resolves it.

A reference may appear in either direction:

```text
host -> typed image/audio/video reference -> Commission
Commission -> typed image/audio/video reference -> host
```

An output reference remains part of the complete `CommissionResult` envelope.
For the first local stdio consumer, an approved repository-relative path is a
useful result; a remote application would normally use an HTTPS URL or durable
artifact handle instead.

Current native provider support remains unchanged. Vibrantine can build opening
messages with image URLs/data URIs and base64 audio, while video has no native
content part today. A typed video reference is still valid domain data when a
Commission or one of its tools knows how to process it; the MCP adapter does not
claim that an LLM provider can consume it directly.

Small inline data URIs or base64 fields remain ordinary schema-valid strings,
but they are subject to the fixed request limit. Large media should use a
reference rather than increasing the adapter's transport ceiling.

## Developer Shape

The first optional-module surface contains one adapter factory,
`create_commission_mcp_server`, plus one application-owned invocation function.
The factory is not re-exported from `vibrantine`; callers opt into the adapter
through `vibrantine.mcp.server`. Cancellation is explicit because the adapter
must connect MCP request cancellation to the run it starts.

```python
from vibrantine import CancelToken, Commission, run_commission
from vibrantine.mcp.server import create_commission_mcp_server


async def invoke(
    commission: Commission,
    input: object,
    *,
    cancel: CancelToken,
):
    return await run_commission(
        commission,
        input,
        models=models,
        budget_usd=0.10,
        tool_ceiling=allowed_tools,
        backend=backend,
        cancel=cancel,
    )


server = create_commission_mcp_server(
    commissions=(compose_sonnet,),
    invoke=invoke,
)

server.run(transport="stdio")
```

The application-owned function prevents a second run-configuration surface
from forming. It may choose settings by Commission or deployment without
teaching the adapter those policies.

Decision: the invocation function is a required asynchronous callable with the
conceptual signature
`invoke(commission, input, *, cancel) -> CommissionResult`. The adapter calls it
exactly once and requires a `CommissionResult` return. The application contract
requires it to enter through `run_commission` and pass the supplied cancellation
token. The adapter checks that `invoke` is callable but does not introspect its
exact Python signature; decorators, partials, and callable objects make strict
signature inspection brittle. A call-shape failure or invalid return becomes a
bounded adapter error.

The repository-local consumer is a small application module or script that:

1. constructs the selected Commission objects;
2. defines models and run policy;
3. creates the server through the adapter;
4. starts stdio; and
5. is named in the Codex or Claude Code MCP configuration.

That launcher is application code. It is not a new Vibrantine orchestration
layer or a universal nexus abstraction.

Decision: stdio startup remains application code in the first slice. The
factory returns the configured official-SDK server; the application starts its
stdio transport. The adapter adds no generic command-line entry point. A
generic CLI would still need a registry, dynamic imports, or a new configuration
format to locate application-owned Commission objects and policy. The tiny
launcher is the explicit composition root and avoids all three.

## Construction-Time Validation

Before accepting requests, the adapter validates that:

- at least one Commission was supplied;
- every tool name is valid for the supported MCP protocol version;
- names are unique within the server;
- input and specialized result schemas can be serialized;
- each supplied object satisfies the Commission contract; and
- the supplied invocation object is callable.

There is no environment scan, dotted-path import, or name-based Commission
lookup after construction. The handler retains the exact registered object
behind each MCP tool name.

## MCP Tool Mapping

| Commission property | MCP field |
|---|---|
| `name` | tool `name` |
| `description` | tool `description` |
| `input_type.model_json_schema()` | `inputSchema` |
| specialized `CommissionResult[OutputT]` schema | `outputSchema` |
| serialized `CommissionResult` | `structuredContent` |

The output schema describes the complete envelope, not only `OutputT`.
Removing the envelope would discard status, partial-result semantics, errors,
provenance, cost, and run identity.

For clients that do not consume `structuredContent`, the adapter also returns
one JSON text content block representing the same envelope. It must never
silently truncate that JSON into a misleading partial document.

Decision: the first slice uses private, fixed UTF-8 JSON limits rather than new
constructor parameters:

| Payload | Limit | Behavior when exceeded |
|---|---:|---|
| Canonically encoded `tools/call.arguments` | 1 MiB | Reject before Pydantic validation and start no run |
| Complete serialized `CommissionResult` envelope | 1 MiB | Return a bounded adapter tool error that includes the run ID |
| Duplicated compatibility text | 64 KiB | Keep full `structuredContent`; return a small valid JSON notice in text |

The compatibility notice states that the complete envelope is available only
in `structuredContent` and includes the run ID. It is not a truncated prefix of
the envelope. A client that cannot consume structured content therefore fails
explicitly rather than receiving plausible but incomplete data.

These are transport-safety ceilings, not Commission policy. Existing
`max_input_tokens`, `max_output_tokens`, and `overflow_policy` remain
authoritative inside the run. Media larger than the request ceiling uses a
typed URL, approved path, or artifact handle.

## Call Lifecycle

For one MCP `tools/call` request:

1. Resolve the exact registered Commission object by the MCP tool name.
2. Validate `arguments` with `commission.input_type.model_validate`.
3. On validation failure, return a bounded MCP tool error without starting a
   Vibrantine run.
4. Create a request-scoped cancellation token connected to MCP cancellation.
5. Call the application-supplied invocation function exactly once.
6. Require a `CommissionResult` return value.
7. Serialize the complete envelope into structured and compatibility content.
8. Release request-scoped resources.

The handler never calls `_run`. It does not construct `CallContext`, invoke
`dispatch` outside a run, or reuse a Gatekeeper between requests.

Every MCP call is a new root invocation with `parent_run_id=None`. If an
external host wants continuity, it owns that state and supplies the necessary
domain information again through a later Commission's typed input.

## Validation and Agent Repair

The typed boundary deliberately supports a repair loop:

```text
host generates arguments
  -> Pydantic validation fails at files.3.path
  -> adapter returns expected type and field location
  -> capable host corrects the arguments and retries
```

The adapter does not assume that every host retries. It makes repair possible
by returning errors that are:

- precise about the invalid field;
- bounded before entering model context;
- explicit about the declared expectation;
- free of tracebacks, secrets, and internal implementation details; and
- present in text as well as structured form where the MCP result permits.

Schema validity does not prove semantic completeness. A valid request may omit
an important file, cite an inaccessible handle, or contain contradictory goals.
Those are domain outcomes returned by the Commission as ordinary structured
`validation`, `partial`, or other existing failures.

Automatic correction is safe for many read-only tasks. Consequential writes
must not depend on blind retries. Confirmation, idempotency, and duplicate
suppression remain application/provider responsibilities, not adapter policy.

## Result and Error Mapping

An MCP request can fail at two levels:

1. **Protocol or adapter failure:** unknown tool, malformed request, invalid
   arguments, unsafe result encoding, or a broken invocation function.
2. **Commission outcome:** a valid invocation returning `success`, `partial`,
   or `failure` in its ordinary result envelope.

Recommended mapping:

| Condition | MCP result |
|---|---|
| Commission `success` | full envelope, `isError=false` |
| Commission `partial` | full envelope, `isError=false` |
| Commission `failure` | full envelope, `isError=true` |
| Invalid input arguments | tool error with bounded validation detail |
| Invocation function raises | tool error with bounded internal detail |
| Invocation returns a non-result value | tool error with bounded internal detail |

An invocation function raising or returning the wrong type is an
adapter/application defect, not a new Vibrantine error kind. The server writes
a bounded diagnostic containing the tool identity and exception type to
standard error. It does not echo arguments, exception text, or a traceback to
the MCP caller.

`partial` is not an MCP error: the envelope contains usable output plus the
reason it is incomplete.

No MCP-specific `ErrorKind` is introduced.

Decision: Commission `success` and `partial` set MCP `isError=false`;
Commission `failure` sets `isError=true`. The complete envelope remains present
in structured content and, while within the compatibility-text ceiling, text
content. Above that ceiling the text block contains the specified valid JSON
notice instead. The error signal does not turn a Commission failure into an
exception or discard its value. Compatibility tests must verify that supported
hosts preserve the envelope when `isError=true`.

## Cancellation, Progress, and Concurrency

Cancellation belongs in the first slice because MCP request cancellation maps
directly to Vibrantine's existing cooperative `CancelToken`. The
application-supplied invocation function receives that token and passes it to
`run_commission(cancel=...)`.

Progress forwarding is deferred. `ProgressEvent` can later map to MCP progress
notifications only after a named host demonstrates the expected token and
notification lifecycle. Progress must not grow the Commission contract.

Concurrent MCP requests are independent root runs. Each receives its own
Gatekeeper; each run's configured `concurrency` governs provider calls within
that tree. The MCP server may accept requests concurrently, but the adapter
adds no global scheduler, shared spend ledger, or cross-request cancellation
state.

## Transport and Lifecycle

The first server supports local stdio only:

- the MCP host starts the process;
- protocol messages use standard input and output;
- diagnostics go to standard error, never standard output;
- the registered tool list is fixed when the process starts;
- process shutdown cooperatively cancels in-flight invocations; and
- every completed request releases its run-scoped clients and resources.

Streamable HTTP is a later adapter host, not another Commission feature. It
requires a named consumer plus explicit decisions about authentication,
origin/host validation, request limits, tenancy, and configuration isolation.

## Security Requirements

- Register only explicitly supplied Commission objects.
- Make the exact exposed tool list visible to the operator before startup.
- Never import a Commission from an MCP request or arbitrary dotted path.
- Never accept Python code, a shell command, or `run_commission` keywords from
  MCP arguments unless they are the Commission's own declared domain input.
- Never treat a host-supplied path as authorized merely because it validated as
  a string.
- Keep model-provider secrets in the application environment and existing
  client-vending path.
- Bound request size, validation work, error detail, and compatibility text.
- Do not expose tracebacks, environment variables, prompts, or persisted traces
  to the MCP caller.
- Treat host-supplied text, file contents, and references as untrusted input.
- Keep diagnostics off standard output.
- Treat local-host installation as application-managed code execution; the
  adapter does not claim to sandbox itself.

The adapter cannot make a powerful Commission safe merely by transporting it
over stdio. The application remains responsible for toolbox construction,
`tool_ceiling`, capability grants, confirmation gates, idempotency, and the
operating-system privileges of the server process.

## Packaging and Dependency

The implementation belongs in the optional submodule
`vibrantine.mcp.server`, not the package root. The official MCP Python SDK is an
optional dependency so ordinary Vibrantine installs retain the current minimal
dependency set.

The installation extra is:

```text
vibrantine[mcp]
```

Core imports must continue working when that extra is absent. MCP SDK and
protocol types remain behind the adapter boundary and do not enter
`contract.py`.

### Temporary v1 implementation decision

Initial development targets the stable official MCP Python SDK v1 line and the
stable `2025-11-25` protocol so implementation can proceed against released
software. On 2026-07-23 the resolved stable v1 release is 1.28.1, so the
temporary dependency range is `mcp>=1.28,<2`, with the exact resolved version
recorded in `uv.lock`.

This range exists only to unblock initial development against released
software. It is a development bridge, not a lasting compatibility promise or
a supported-version strategy. The official Python SDK v2 stable release is an
explicit replacement trigger: migration starts as soon as that release is
available, without waiting for another feature request or a later cleanup
cycle:

- migrate the adapter in place to `mcp>=2,<3` and the `2026-07-28` protocol;
- use the official SDK's protocol behavior rather than adding Vibrantine-owned
  negotiation;
- remove the v1 dependency and v1-specific tests in the same change; and
- do not build a dual-version adapter, compatibility shim, or runtime SDK
  switch.

The outward adapter is not released until the v2 migration passes the complete
test plan, including real Codex and Claude Code compatibility checks. If either
named host cannot use the stable v2 server, that is a stop-and-rerule point,
not permission to let temporary v1 support become permanent by drift.

The parked inward adapter may later live under `vibrantine.mcp.client` and
share the optional SDK dependency. Sharing a package area does not combine the
two adapters, their lifecycles, or their public APIs.

## Implementation Sequence

1. Implement `ComposeSonnetCommission` and its ordinary Commission tests.
2. Add the optional `mcp` extra using MCP Python SDK v1 and protocol
   `2025-11-25`.
3. Define the private invocation seam, including cancellation.
4. Implement in-memory registration, `tools/list`, and construction checks.
5. Implement argument validation and `tools/call` handling.
6. Add specialized full-envelope schemas and result serialization.
7. Add bounded validation, adapter-error, and compatibility-text handling.
8. Add MCP-to-Vibrantine cancellation translation.
9. Add the stdio entry path with clean standard-output discipline.
10. Build one repository-local launcher exposing only
    `compose_vibrantine_sonnet`.
11. Verify the v1 implementation with the MCP Inspector, Codex, Claude Code,
    and one minimal client.
12. Migrate in place to stable MCP Python SDK v2 and protocol `2026-07-28`,
    removing v1 support.
13. Repeat the complete test and host-compatibility pass on v2.
14. Document the external application pattern without promoting its Commission
    selection into library policy.

## Test Plan

### Contract and unit tests

Use the official SDK's in-memory transport where possible and scripted
Vibrantine models.

- `tools/list` exposes exactly the registered Commissions.
- Names, descriptions, and input schemas match Commission definitions.
- Result schemas describe the specialized complete envelope.
- Duplicate or invalid names fail before startup.
- An empty registration fails before startup.
- Valid arguments become the exact declared Pydantic input type.
- Invalid nested arguments report a precise field location and start no run.
- A successful result round-trips as the complete envelope.
- Partial output and its error both survive serialization.
- Commission failure sets the chosen MCP error signal without losing the
  envelope.
- Oversized compatibility content fails explicitly without silent truncation.
- The invocation function receives request cancellation and is called exactly
  once.
- A broken invocation function returns a bounded adapter error.
- Two simultaneous calls receive different root run IDs and Gatekeepers.
- The adapter never calls `_run` directly.
- Standard output contains protocol traffic only.
- Core Vibrantine imports work without the MCP extra installed.
- The same suite passes after the in-place v2 migration with no v1
  compatibility path remaining.

### Follow-up context-shape evaluations

After the single-tool smoke test passes, use host-facing cases that exercise
semantic argument construction, not only JSON validity:

- one short natural-language request;
- a request with a dozen file references and distinct roles;
- a request with objective, constraints, context, and deliverable fields;
- a request where relevant conversation must be summarized into explicit
  context;
- an omitted required reference;
- an invalid or inaccessible path;
- contradictory but structurally valid instructions; and
- a consequential action where retry must not duplicate the effect.

Record first-call validity, corrected-call success, omitted information,
latency, and unnecessary retries. These are compatibility observations, not
promises added to the adapter contract.

### Host compatibility

One stdio integration test exercises discovery, a successful call, validation
failure, cancellation, and clean shutdown through an SDK client.

The first manual or opt-in compatibility evaluation exposes only
`compose_vibrantine_sonnet` and covers:

- Codex with a project-scoped local MCP configuration;
- Claude Code with default tool search;
- Claude Code with the server always loaded when practical;
- a minimal MCP client that injects the complete flat tool list;
- an explicit request to invoke the Vibrantine Sonnet Commission;
- a natural request to compose a Vibrantine sonnet about a supplied subject;
  and
- receipt of a successful full envelope containing a title and exactly 14
  lines.

The server must work correctly without native tool search. Tool-search quality
is measured because it affects application menu design, not because the server
depends on it.

Crowded direct menus and routing Commissions are later application evaluations,
not first-implementation gates. The adapter treats every registered Commission
identically, so no routing-specific server behavior is waiting to be designed.

#### Temporary v1 checkpoint: 2026-07-23

The temporary v1 implementation has the following compatibility evidence:

- MCP Inspector 0.21.2 discovers exactly
  `compose_vibrantine_sonnet`, including its complete input and specialized
  result schemas.
- An official Python SDK stdio client discovers the same tool, invokes it
  through the repository launcher and OpenRouter, receives a successful full
  envelope with a title and exactly 14 lines, and shuts down cleanly. The live
  invocation took roughly 40 seconds.
- Codex CLI 0.144.6 discovers and selects the exact server, tool, and valid
  subject argument, but reports `user cancelled MCP tool call` after roughly
  five seconds. Repeating with host approval policy set to `never` produces the
  same result. Because the identical launcher completes through the official
  client, this is currently a host-specific cancellation observation, not a
  reason to change the adapter contract.
- Claude Code 2.1.214 cannot reach MCP discovery in the available environment:
  its organization rejects Claude Code subscription access with HTTP 403
  before an API turn begins. The attempt uses zero tokens and makes no MCP
  request, so it is an environment/authentication blocker rather than a pass or
  failure of the adapter.

These observations do not satisfy the named-host release gate. Retry both
hosts after the mandatory SDK v2 migration; retry Claude Code sooner only when
working host credentials are available. If the Codex cancellation persists on
stable v2, stop and re-rule as specified above instead of preserving v1 or
adding a host-specific compatibility path.

## Acceptance Criteria

- An unmodified Commission can be explicitly registered as a local MCP tool.
- A compatible client can discover and invoke every registered Commission.
- The server exposes no unregistered Commission or private child implicitly.
- Every valid call passes through `run_commission`.
- Every invalid argument set fails before a run starts.
- The caller receives the full typed result envelope.
- MCP cancellation reaches `CallContext.cancel` through the existing run path.
- A capable host can repair a validation failure from bounded error detail.
- Correctness does not require client-native deferred tool search.
- An application can choose specialist, service-level, or routing exposure
  without an adapter API change.
- No host conversation or workspace context crosses the boundary implicitly.
- No MCP concept appears in `Commission`, `CallContext`, the Gatekeeper,
  `contract.py`, or the frozen top-level public surface.
- No Commission registry or automatic discovery is introduced.
- Installing without the MCP extra continues to support all core features.
- The repository-local server works with Codex and Claude Code as named real
  consumers.
- The first repository-local launcher exposes exactly
  `compose_vibrantine_sonnet` and passes its dedicated Commission specification.
- The outward adapter is not released on the temporary MCP v1 implementation.

## Implementation Readiness

No open review question blocks completion of the temporary v1 implementation.
Named-host compatibility still blocks release as recorded above. Broader
menus, routing-Commission comparisons, progress forwarding, Streamable HTTP,
and the inward client adapter each require their own named pressure after this
local stdio path is proven; none belongs in the first slice by anticipation.
