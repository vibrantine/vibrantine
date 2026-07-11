"""Tests for the known-models table and the `Model` value type.

Pins the invariants the budget machinery leans on (every entry well-formed,
the system default registered *with* real pricing; see
`Commission._budget_unenforceable_failure`) plus the multi-provider menu:
a `Model` bundles identity + endpoint + facts, and unpriced (`None`) is
distinct from free (`0.0`). Run-catalog resolution (unknown names fail
fast, the empty catalog auto-registers the default) is exercised in
`test_gatekeeper.py`, where the catalog lives.
"""

from vibrantine.models import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    OPENROUTER_BASE_URL,
    Model,
    ollama,
    openai_compatible,
)


def test_default_model_is_registered() -> None:
    assert DEFAULT_MODEL in KNOWN_MODELS


def test_default_model_has_real_pricing() -> None:
    # Nonzero pricing is what makes budget_usd enforceable for the default loop.
    model = KNOWN_MODELS[DEFAULT_MODEL]
    assert model.is_priced
    assert model.input_usd_per_million is not None
    assert model.input_usd_per_million > 0.0
    assert model.output_usd_per_million is not None
    assert model.output_usd_per_million > 0.0


def test_known_models_entries_are_well_formed() -> None:
    assert KNOWN_MODELS, "the table must not be empty"
    for name, model in KNOWN_MODELS.items():
        assert isinstance(name, str) and name
        assert isinstance(model, Model)
        assert model.context_window is not None
        assert model.context_window > 0
        assert model.input_usd_per_million is not None
        assert model.input_usd_per_million >= 0.0
        assert model.output_usd_per_million is not None
        assert model.output_usd_per_million >= 0.0


def test_known_model_carries_openrouter_endpoint() -> None:
    model = KNOWN_MODELS["google/gemini-3.5-flash"]
    assert model.id == "google/gemini-3.5-flash"
    assert model.base_url == OPENROUTER_BASE_URL
    assert model.api_key_env == "OPENROUTER_API_KEY"


def test_cloud_and_local_models_form_one_menu() -> None:
    # The cross-provider point: endpoint travels with the model, so cloud and
    # local are selectable from one menu over the same OpenAI-compatible path.
    cloud = Model(
        id="some/cloud-model",
        input_usd_per_million=0.50,
        output_usd_per_million=3.00,
    )
    local = ollama("llama3.1")

    assert cloud.base_url == OPENROUTER_BASE_URL
    assert local.base_url == "http://localhost:11434/v1"
    assert cloud.api_key_env == "OPENROUTER_API_KEY"
    assert local.api_key_env is None  # local Ollama needs no key at all

    # Both priced, but local is genuinely free (0.0), not unpriced (None).
    assert cloud.is_priced
    assert local.is_priced
    assert local.input_usd_per_million == 0.0
    assert local.output_usd_per_million == 0.0


def test_unpriced_is_distinct_from_free() -> None:
    unpriced = Model(id="some/unknown-model")
    free = ollama("phi3")
    assert not unpriced.is_priced
    assert free.is_priced


def test_openai_compatible_builds_from_name_and_address() -> None:
    # The generic front door: name + address, everything else optional.
    model = openai_compatible("my-model", "https://gateway.internal/v1")
    assert model.id == "my-model"
    assert model.base_url == "https://gateway.internal/v1"
    # A generic endpoint's cost is unknown, so it defaults to unpriced (None),
    # distinct from ollama()'s genuinely-free 0.0.
    assert not model.is_priced
    assert model.context_window is None


def test_openai_compatible_accepts_optional_facts() -> None:
    model = openai_compatible(
        "my-model",
        "https://gateway.internal/v1",
        api_key_env="MY_GATEWAY_KEY",
        context_window=128_000,
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
    )
    assert model.api_key_env == "MY_GATEWAY_KEY"
    assert model.context_window == 128_000
    assert model.is_priced
