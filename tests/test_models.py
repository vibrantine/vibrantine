"""Tests for the `Model` profile type and the system default.

Pins the invariants the budget machinery leans on (the system default is a
well-formed profile *with* real pricing; see
`Commission._budget_unenforceable_failure`) plus the multi-provider menu:
a `Model` bundles name + identity + endpoint + facts + params, and unpriced
(`None`) is distinct from free (`0.0`). Run-catalog resolution (unknown
names fail fast, the empty catalog auto-registers the default, profiles
sharing an id) is exercised in `test_gatekeeper.py`, where the catalog
lives.
"""

from vibrantine.models import (
    DEFAULT_MODEL,
    OPENROUTER_BASE_URL,
    Model,
    ollama,
    openai_compatible,
)


def test_default_model_is_a_well_formed_profile() -> None:
    assert DEFAULT_MODEL.id == "google/gemini-3.5-flash"
    assert DEFAULT_MODEL.name == DEFAULT_MODEL.id
    assert DEFAULT_MODEL.base_url == OPENROUTER_BASE_URL
    assert DEFAULT_MODEL.api_key_env == "OPENROUTER_API_KEY"
    assert DEFAULT_MODEL.context_window is not None
    assert DEFAULT_MODEL.context_window > 0
    assert DEFAULT_MODEL.params == {}


def test_default_model_has_real_pricing() -> None:
    # Nonzero pricing is what makes budget_usd enforceable for the default loop.
    assert DEFAULT_MODEL.is_priced
    assert DEFAULT_MODEL.input_usd_per_million is not None
    assert DEFAULT_MODEL.input_usd_per_million > 0.0
    assert DEFAULT_MODEL.output_usd_per_million is not None
    assert DEFAULT_MODEL.output_usd_per_million > 0.0


def test_name_defaults_to_id() -> None:
    model = Model(id="some/model")
    assert model.name == "some/model"


def test_two_profiles_may_share_one_wire_id() -> None:
    # The point of the name/id split: one underlying model, two profiles
    # differing only in call settings.
    cool = Model(id="some/model", name="fast-cool", params={"temperature": 0.1})
    hot = Model(id="some/model", name="fast-hot", params={"temperature": 1.0})
    assert cool.id == hot.id
    assert cool.name != hot.name
    assert cool.params != hot.params


def test_model_stays_hashable_with_params() -> None:
    # params is a dict (unhashable), excluded from the generated hash so a
    # Model can still live in sets and dict keys.
    assert isinstance(hash(Model(id="some/model", params={"temperature": 0.5})), int)


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


def test_openai_compatible_builds_from_id_and_address() -> None:
    # The generic front door: id + address, everything else optional.
    model = openai_compatible("my-model", "https://gateway.internal/v1")
    assert model.id == "my-model"
    assert model.name == "my-model"
    assert model.base_url == "https://gateway.internal/v1"
    assert model.params == {}
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


def test_helpers_pass_profile_fields_through() -> None:
    gateway = openai_compatible(
        "my-model",
        "https://gateway.internal/v1",
        name="deep-thinker",
        params={"temperature": 0.2},
    )
    local = ollama("llama3.1", name="local-fast", params={"top_p": 0.9})
    assert gateway.name == "deep-thinker"
    assert gateway.params == {"temperature": 0.2}
    assert local.name == "local-fast"
    assert local.params == {"top_p": 0.9}
