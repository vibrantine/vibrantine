"""Known-models table: model identity + endpoint + per-token pricing facts.

Lives at the library level so every LLM-using Commission consults one table
rather than carrying its own copy. Add a model here once; consumers resolve it by identifier at
construction. Unknown identifiers are permitted; they fall back to a bare
`Model` pointed at the OpenRouter endpoint with no context window and no
pricing, so a caller can target any OpenRouter model without first registering
it. Pass an explicit `max_input_tokens` to be conservative under that fallback.

A model is an *object* (`Model`), not a bare id string: it bundles identity
(`id`), endpoint (`base_url`, `api_key_env`) and facts (context window,
pricing). Because the endpoint travels with the model, cloud (OpenRouter) and
local (Ollama, also OpenAI-compatible) providers are selectable from one menu;
the library is multi-provider, local-first by construction. Bare id strings
still resolve through `KNOWN_MODELS` / `resolve()` for backward compatibility.

Pricing is `float | None`. `None` means *unpriced/unknown*: the model simply
carries no price here. `$0` (`0.0`) means *genuinely free*, e.g. a local Ollama
model. The distinction matters: a call's cost rolls up structurally into its
parents (`design.md § Cost and provenance are structural`), so an *unpriced*
model anywhere in a
tree silently under-reports the whole tree's cost; register it for accurate
accounting. A Commission invoked with a `budget_usd` but an *unpriced* model
can't have that budget enforced, so it fails fast at invoke; a *free* model
(`0.0`) enforces fine and runs.
"""

from dataclasses import dataclass

# The OpenRouter OpenAI-compatible API base. The default endpoint for models
# resolved from a bare id string or an unknown identifier.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Model:
    """A model: identity + endpoint + the facts a Commission needs.

    Defaults target OpenRouter; override `base_url` / `api_key_env` for any
    other OpenAI-compatible provider (e.g. a local Ollama server; see the
    `ollama()` helper).
    """

    id: str
    base_url: str = OPENROUTER_BASE_URL
    api_key_env: str = "OPENROUTER_API_KEY"
    context_window: int | None = None
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None

    @property
    def is_priced(self) -> bool:
        """True when both per-token prices are known (a free `0.0` counts)."""
        return self.input_usd_per_million is not None and self.output_usd_per_million is not None


KNOWN_MODELS: dict[str, Model] = {
    "google/gemini-3-flash-preview": Model(
        id="google/gemini-3-flash-preview",
        context_window=1_050_000,
        input_usd_per_million=0.50,
        output_usd_per_million=3.00,
    ),
}

# The system-wide default model. Every Commission uses this unless its caller
# passes an explicit `model=`. The single seam a future "loaded default model"
# (config-driven) routes through; keep model selection here, never hardcoded
# inside a Commission body.
DEFAULT_MODEL = "google/gemini-3-flash-preview"


def resolve(model: "str | Model | None") -> Model:
    """Resolve a model spec to a `Model`.

    A `Model` is returned as-is (identity). A string (or `None`, meaning
    `DEFAULT_MODEL`) is looked up in `KNOWN_MODELS`; an unknown id falls back
    to a bare `Model` on the OpenRouter endpoint: unpriced, no context window.
    """
    if isinstance(model, Model):
        return model
    model_id = model or DEFAULT_MODEL
    known = KNOWN_MODELS.get(model_id)
    if known is not None:
        return known
    return Model(id=model_id)


def openai_compatible(
    name: str,
    address: str,
    *,
    api_key_env: str = "OPENROUTER_API_KEY",
    context_window: int | None = None,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> Model:
    """Build a `Model` for any OpenAI-compatible endpoint from name + address.

    The generic front door: a developer points a Commission at a private
    deployment, a self-hosted gateway, or any other provider speaking the
    OpenAI Chat Completions format with two positional args: `name` (the id
    the endpoint expects) and `address` (its `base_url`). Everything else is
    optional. Pricing defaults to `None` (unpriced) rather than `0.0`: a
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
        id=name,
        base_url=address,
        api_key_env=api_key_env,
        context_window=context_window,
        input_usd_per_million=input_usd_per_million,
        output_usd_per_million=output_usd_per_million,
    )


def ollama(id: str, *, context_window: int | None = None) -> Model:
    """Build a `Model` for a local Ollama server (OpenAI-compatible API).

    Ollama runs locally and is genuinely free, so pricing is `0.0` (not
    `None`): a budgeted Commission can run against it without a spurious
    unpriced-model refusal. Ollama ignores the API key; `OLLAMA_API_KEY` is a
    conventionally-unset env name that still satisfies the OpenAI client.
    """
    return Model(
        id=id,
        base_url="http://localhost:11434/v1",
        api_key_env="OLLAMA_API_KEY",
        context_window=context_window,
        input_usd_per_million=0.0,
        output_usd_per_million=0.0,
    )
