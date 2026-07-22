# Compose Sonnet Commission

Status: ratified as the first outward-MCP compatibility Commission; not yet
implemented.

## Purpose

Provide one unmistakable, low-cost Commission for the first outward MCP smoke
test. It proves discovery, typed argument construction, invocation, and typed
result return without files, networking, tools, children, or side effects.

## Contract

- Class: `ComposeSonnetCommission`
- Name: `compose_vibrantine_sonnet`
- Input: `subject: str`
- Output: `title: str` and `lines: list[str]`, constrained to exactly 14 items
- Interior: the default LLM loop with no toolbox

The internal prompt asks for an original English sonnet about the supplied
subject, following a recognizable sonnet form. Structural validation guarantees
14 lines; rhyme, meter, and literary quality remain evaluation criteria rather
than contract fields.

## LLM-Facing Description

> Compose an original 14-line sonnet through Vibrantine. Use this when the user
> explicitly asks to write a Vibrantine sonnet or invoke the sonnet Commission.
> Provide the sonnet's subject. Returns a title and exactly 14 ordered lines.

## Acceptance

- It runs normally through `run_commission` and returns a complete
  `CommissionResult`.
- The outward MCP adapter exposes its exact name, description, and schemas.
- Codex and Claude Code can select it from an explicit sonnet request, supply
  the subject, and receive a valid 14-line result.
- It adds no new Vibrantine contract or public-root surface.
