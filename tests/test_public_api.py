"""The package's public boundary.

`vibrantine.__all__` is the SemVer-protected surface: the frozen contract
bones plus the entry points. Example Commissions and tools stay importable
from their submodules but are deliberately *not* part of the frozen surface.
"""

import vibrantine


def test_every_exported_name_resolves() -> None:
    for name in vibrantine.__all__:
        assert getattr(vibrantine, name, None) is not None, name


def test_core_contract_and_entry_points_are_exported() -> None:
    expected = {
        "Commission",
        "CommissionResult",
        "CallContext",
        "Provenance",
        "ErrorState",
        "CostMetrics",
        "Claim",
        "PersistenceBackend",
        "run_one",
        "invoke_sync",
        "dispatch",
        # Model vocabulary: callers are told to register models and build
        # Model targets, so the names they need are part of the surface.
        "Model",
        "KNOWN_MODELS",
        "DEFAULT_MODEL",
        "openai_compatible",
        "ollama",
        # Authoring factory: the supported fast path to a basic Commission.
        "create_commission",
        # Authoring edge: the names authoring.md teaches authors to use.
        "ContentPart",
        "TextPart",
        "ImagePart",
        "DEFAULT_MAX_ITERATIONS",
        "estimate_tokens",
        "deposit_llm_trace",
    }
    assert expected <= set(vibrantine.__all__)


def test_provisional_commissions_are_not_in_the_frozen_surface() -> None:
    # examples/ is demonstration material: importable from its submodule,
    # not the top-level frozen __all__.
    assert "AskCommission" not in vibrantine.__all__
    assert "RecursiveResearchCommission" not in vibrantine.__all__
    assert "ReadTool" not in vibrantine.__all__


def test_entry_points_are_callable_from_the_top_level() -> None:
    from vibrantine import dispatch, invoke_sync, run_one

    assert callable(run_one)
    assert callable(invoke_sync)
    assert callable(dispatch)
