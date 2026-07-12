# Model Profiles: do it once, do it right, call when needed

## Status

**BUILT, 2026-07-12.** Specced and shipped the same day after a
one-at-a-time walkthrough ruled all four open slots (recorded below).
Closed a confirmed drift: the Gatekeeper spec promised "one registry of
*profiles* per run" and the build delivered a registry of
endpoint-and-pricing facts keyed by the wire id. Two profiles of one
underlying model (Gemini Flash at low and high temperature) were
impossible: they collided as duplicate catalog entries, and there was
no settings slot for them to differ by.

## Thesis

Do it once, do it right, call when needed. A profile is the one place a
model configuration is done right: the wire id, the endpoint, the
prices, and the provider-specific call settings (a thinking toggle,
sampling knobs, whatever that provider takes). A Commission references
a profile by role name ("the fast cheap one", "the deep thinker") and
knows nothing else, which is the correct amount of ignorance: the run
decides what each role concretely means, and the same tree runs against
different providers by rewiring the catalog, never the nodes.

This also dissolves the "isn't a name lossy?" objection to name-based
model references (weighed and kept 2026-07-12): the node's string is
not a lossy stand-in for an object it could have held. It names a role
the run owns. The loss is the decoupling doing its job.

## Settled by prior rulings (not up for re-decision here)

- **Node references stay pure names** resolved against the run catalog
  (ratified with the Gatekeeper build). Profiles strengthen the reason;
  they do not reopen it.
- **No capability catalog** (ruled 2026-07-10, multimodal plan): a
  profile carries call settings, never a schema of what the model "can
  do". A profile with Anthropic-only knobs is *de facto* locked to
  Anthropic endpoints because only those endpoints accept them; the
  lock is run wiring plus the provider's own validation, not enforced
  metadata. Behavioral differences between providers (guardrail
  strength, refusal posture) are exactly why an author wires a role to
  a provider deliberately, and exactly the kind of fact the framework
  does not pretend to know.
- **Definitions central, choice distributed**: which profiles exist is
  a run fact on the Gatekeeper; which profile a node uses stays a value
  the node carries.

## The shape

1. **Split the catalog key from the wire string.** `Model` gains a
   `name` field, defaulting to `id`. `name` is what `build_catalog`
   keys by, what `Commission(model=)` and `default_model=` reference,
   and what `resolve_model` looks up. `id` stays exactly what goes on
   the wire. Two entries sharing an `id` under different names is the
   point; two entries sharing a `name` stays the duplicate error.
   Every existing one-line catalog keeps working because the default
   makes `name == id`.

2. **A raw params slot, not typed knobs.** `Model` gains
   `params: dict` (empty by default), passed through to the provider
   call verbatim (merged into `chat.completions.create`). Raw over
   typed-early, per the standing rule: temperature, top_p, reasoning
   settings, and provider-specific toggles all ride the same dict, and
   the knobs that recur across real profiles graduate to typed fields
   later, when profiles in the wild show which ones earn it. The
   framework does not validate the pairing; the provider is the
   validator, and a wrong knob fails loudly at the door with the
   provider's own error.

3. **Applied at every governed call.** The default loop and the
   library-owned custom flows (Synthesize's passes) merge the entry's
   params into their provider calls. One definition point, every
   caller.

4. **Everything else is untouched.** Client vending stays keyed by
   endpoint (two profiles on one provider share a connection pool
   automatically). Pricing, fuses, the size gate's context window, and
   the `scripted_model` testing seam all read the same entry fields
   they read today.

## Rulings (walkthrough of 2026-07-12, one at a time)

1. **The field is `name`.** Plainest word for "the string you look this
   up by"; the concept "profile" lives in prose. No prefix
   (`model_name` would stutter as `model.model_name`); the prefix earns
   its keep only in the flat call-row namespace (below).
2. **Call rows gain `model_name` now**, beside `model` (the wire id).
   Nothing deferred to the register work: the register's rows carry no
   model at all, so there was nothing to co-decide. The `calls` table
   is an unreleased delta, so the column was still free.
3. **Both helpers pass `name=` / `params=` through**, defaults matching
   `Model`. Build note: `openai_compatible()`'s first parameter renamed
   `name` → `id` (it always meant the wire id) so one vocabulary holds
   across `Model`, `ollama()`, and the helper; positional callers
   unaffected.
4. **KNOWN_MODELS retired; DEFAULT_MODEL is the profile object.**
   Walked as "confirm no special casing" and ruled further: the dict
   had shrunk to a one-entry vestige feeding the empty-catalog default,
   and a curated list would rot within weeks of any provider reshuffle.
   The default is now one owned definition (`DEFAULT_MODEL: Model`),
   the seam the future config-loaded default routes through. Breaking:
   both names were in `__all__`; the lock test grew the justification
   comment.

Build extras settled at the diff: params keys the framework owns
(`model`, `messages`, `tools`, `tool_choice`, `response_format`) are
refused at catalog build, loudly, rather than colliding at call time;
`params` is excluded from the dataclass hash (dicts aren't hashable)
and treated as frozen by convention; `testing.scripted_model()` passes
both fields through so tests can script two roles on one wire id.

## Relationship to the rest of the map

Profiles are the model-shaped instance of the do-it-once principle,
alongside shared Commission instances (one object, many toolboxes) and
the catalog's one-client-per-endpoint vending. On the five-surface map
they sharpen the planned model-ownership split: the catalog (run)
owns what a role *is*; the node owns only which role it plays.
