"""The model vocabulary: a profile is identity + endpoint + facts + settings.

A model is an *object* (`Model`), not a bare id string: it bundles a catalog
name (`name`), wire identity (`id`), endpoint (`base_url`, `api_key_env`),
facts (context window, pricing), and provider call settings (`params`).
Because the endpoint travels with the model, cloud (OpenRouter) and
local (Ollama, also OpenAI-compatible) providers are selectable from one menu;
the library is multi-provider, local-first by construction.

An entry is a *profile*: the one place a model configuration is done right.
`name` is the role a Commission asks for ("fast-cheap", "deep-thinker");
`id` is what goes on the wire. Two profiles may share an `id` and differ
only in `params` (the same model at two temperatures); `name` defaults to
`id`, so a catalog that never uses roles looks exactly as before.

A run's models are defined once, at the front door: `run_one(models=[...])`
is the run's catalog, and every Commission references an entry by name
(`Commission(model="...")`) or takes the run default. A name not in the
catalog fails fast (`UnknownModelError`, surfaced as a validation failure);
there is no silent fallback. An empty catalog auto-registers the system
default below, so hello world configures nothing.

Pricing is `float | None`. `None` means *unpriced/unknown*: the model simply
carries no price here. `$0` (`0.0`) means *genuinely free*, e.g. a local Ollama
model. The distinction matters: a call's cost rolls up structurally into its
parents (`design.md § Cost and provenance are structural`), so an *unpriced*
model anywhere in a
tree silently under-reports the whole tree's cost; price it for accurate
accounting. A Commission invoked with a `budget_usd` but an *unpriced* model
can't have that budget enforced, so it fails fast before running; a *free* model
(`0.0`) enforces fine and runs.

Catalog construction accepts only finite, non-negative prices. Zero is the
explicit free value; negative and NaN prices would make spend accounting lie.
"""

from dataclasses import dataclass, field
from typing import Any

# The OpenRouter OpenAI-compatible API base. The default endpoint for models
# resolved from a bare id string or an unknown identifier.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Model:
    """A model profile: catalog name + wire identity + endpoint + facts.

    Defaults target OpenRouter; override `base_url` / `api_key_env` for any
    other OpenAI-compatible provider (e.g. a local Ollama server; see the
    `ollama()` helper).
    """

    id: str
    # The catalog key and what a Commission references; empty means "same
    # as id". Distinct names may share an id: that is two profiles of one
    # underlying model.
    name: str = ""
    base_url: str = OPENROUTER_BASE_URL
    # None means the endpoint needs no API key at all (e.g. local Ollama);
    # a missing key for a keyed endpoint fails fast at first client build.
    api_key_env: str | None = "OPENROUTER_API_KEY"
    context_window: int | None = None
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    # Provider call settings, merged verbatim into every
    # `chat.completions.create` this profile serves (temperature, top_p,
    # provider-specific toggles). Raw by design: the provider is the
    # validator, and a wrong knob fails loudly with the provider's own
    # error. Keys the framework owns (model, messages, tools, tool_choice,
    # response_format) are refused at catalog build. Excluded from the
    # hash (dicts aren't hashable); treat it as frozen like the rest.
    params: dict[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if not self.name:
            object.__setattr__(self, "name", self.id)

    @property
    def is_priced(self) -> bool:
        """True when both per-token prices are known (a free `0.0` counts)."""
        return self.input_usd_per_million is not None and self.output_usd_per_million is not None

    def cost_usd(self, in_tokens: int, out_tokens: int) -> float:
        """The USD cost of a call at this model's per-token rates.

        The one pricing formula in the library: node cost rollups, the
        run's observed spend, and the budget gates all price through here,
        so their numbers can never drift apart. An unpriced side rates as
        $0; a caller that needs to *trust* the number for budget
        enforcement gates on `is_priced` first.
        """
        in_price = self.input_usd_per_million or 0.0
        out_price = self.output_usd_per_million or 0.0
        return (in_tokens * in_price + out_tokens * out_price) / 1_000_000


# The system-wide default model, a full profile and the one owned definition
# of it. An empty run catalog auto-registers this entry, and a multi-model
# catalog with no explicit default falls back to it (by `.name`). The single
# seam a future "loaded default model" (config-driven) routes through; keep
# model selection here, never hardcoded inside a Commission body.
DEFAULT_MODEL = Model(
    id="google/gemini-3.5-flash",
    context_window=1_048_576,
    input_usd_per_million=1.50,
    output_usd_per_million=9.00,
)


class UnknownModelError(Exception):
    """A Commission named a model that is not in the run's catalog.

    Immediate and loud, converted to a `validation` failure at the Commission
    boundary. The pre-catalog silent fallback (an unknown id becoming a bare
    OpenRouter model) retired with the catalog: a caller who wants an
    arbitrary model registers it explicitly in `run_one(models=[...])`.
    """


def openai_compatible(
    id: str,
    address: str,
    *,
    name: str = "",
    params: dict[str, Any] | None = None,
    api_key_env: str = "OPENROUTER_API_KEY",
    context_window: int | None = None,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> Model:
    """Build a `Model` for any OpenAI-compatible endpoint from id + address.

    The generic front door: a developer points a Commission at a private
    deployment, a self-hosted gateway, or any other provider speaking the
    OpenAI Chat Completions format with two positional args: `id` (the wire
    id the endpoint expects) and `address` (its `base_url`). Everything else
    is optional, including the profile fields (`name` for a role key,
    `params` for provider call settings). Pricing defaults to `None`
    (unpriced) rather than `0.0`: a
    generic endpoint's cost is unknown, so leave it unpriced unless the caller
    supplies real rates (an unpriced model can't have a `budget_usd` enforced;
    see the module docstring). For the local-Ollama case use `ollama()`, which
    is genuinely free.

    Constructing models (and any user-facing UX for it) is a caller concern;
    this helper and `ollama()` are conveniences over the `Model` constructor,
    not the only way in. An orchestration layer is free to build its own
    factories on top of `Model`.
    """
    return Model(
        id=id,
        name=name,
        params=params if params is not None else {},
        base_url=address,
        api_key_env=api_key_env,
        context_window=context_window,
        input_usd_per_million=input_usd_per_million,
        output_usd_per_million=output_usd_per_million,
    )


def ollama(
    id: str,
    *,
    name: str = "",
    params: dict[str, Any] | None = None,
    context_window: int | None = None,
) -> Model:
    """Build a `Model` for a local Ollama server (OpenAI-compatible API).

    Ollama runs locally and is genuinely free, so pricing is `0.0` (not
    `None`): a budgeted Commission can run against it without a spurious
    unpriced-model refusal. Ollama needs no API key, so `api_key_env` is
    `None` and the missing-key check never blocks a local run.
    """
    return Model(
        id=id,
        name=name,
        params=params if params is not None else {},
        base_url="http://localhost:11434/v1",
        api_key_env=None,
        context_window=context_window,
        input_usd_per_million=0.0,
        output_usd_per_million=0.0,
    )
