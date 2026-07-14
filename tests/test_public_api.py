"""The package's public boundary, locked exactly.

`vibrantine.__all__` is the SemVer-protected surface: the frozen contract
bones plus the entry points. Example Commissions and tools stay importable
from their submodules but are deliberately *not* part of the frozen surface.

The tests here are LOCKS, not floors. The public surface is minimized
mercilessly (design-decisions.md § The public surface is minimized
mercilessly):
every exported name, constructor kwarg, and context field is permanent
user-facing cognitive load, so each grows only under pressure from a real,
named consumer. If you are here because a lock failed: the failure is the
moment to justify the growth (bring the consumer that demands it and grow
the lock in the same commit), not an obstacle to silence.
"""

import inspect
from collections.abc import Callable
from dataclasses import fields
from typing import cast

import vibrantine
from vibrantine import testing


def test_every_exported_name_resolves() -> None:
    for name in vibrantine.__all__:
        assert getattr(vibrantine, name, None) is not None, name


def test_the_exported_surface_is_locked_exactly() -> None:
    expected = {
        # Contract: the Commission and its result envelope
        "Commission",
        "CommissionResult",
        "CommissionStatus",
        # Runtime conditions
        "CallContext",
        "CapabilitySet",
        "CancelToken",
        "NEVER_CANCELLED",
        "ProgressEvent",
        # Provenance, claims, cost
        "Provenance",
        "ConfidenceLevel",
        "Claim",
        "CostMetrics",
        # Failure model
        "ErrorState",
        "ErrorKind",
        # Policy vocabularies
        "OverflowPolicy",
        "PersistenceMode",
        # Persistence
        "PersistedRecord",
        "PersistenceBackend",
        "FilesystemBackend",
        "SqliteBackend",
        # Model vocabulary: callers are told to register models and build
        # Model targets, so the names they need are part of the surface.
        # KNOWN_MODELS retired 2026-07-12 with the profile split: it had
        # shrunk to a one-entry vestige whose only job was feeding the
        # default; DEFAULT_MODEL is now that profile object itself.
        "Model",
        "DEFAULT_MODEL",
        "openai_compatible",
        "ollama",
        # Entry points
        "run_commission",
        "run_commission_sync",
        "dispatch",
        # Authoring factory: the supported fast path to a basic Commission.
        "create_commission",
        # Authoring edge: the names authoring.md teaches authors to use.
        "ContentPart",
        "TextPart",
        "ImagePart",
        "AudioPart",
        "DEFAULT_MAX_ITERATIONS",
        "estimate_tokens",
        "deposit_llm_trace",
    }
    assert set(vibrantine.__all__) == expected


def test_commission_constructor_surface_is_locked() -> None:
    # Every kwarg here is a decision an author may have to make; the list
    # is part of the surface a user's head must hold.
    init = cast("Callable[..., None]", vibrantine.Commission.__init__)
    params = list(inspect.signature(init).parameters)
    assert params == [
        "self",
        "model",
        "max_iterations",
        "toolbox",
        "max_input_tokens",
        "target_input_fraction",
        "persistence_mode",
        "max_output_tokens",
        "overflow_policy",
    ]


def test_run_commission_keyword_surface_is_locked() -> None:
    # run_commission is the widest single point on the surface and the first
    # function every consumer meets; each keyword is a decision a caller may
    # have to make, so growth here is a deliberate act like everywhere else.
    params = list(inspect.signature(vibrantine.run_commission).parameters)
    assert params == [
        "commission",
        "input",
        "budget_usd",
        "models",
        "default_model",
        "max_llm_calls",
        "time_limit_seconds",
        "concurrency",
        "tool_ceiling",
        "capabilities",
        "cancel",
        "on_progress",
        "on_llm_call",
        "on_dispatch",
        "backend",
        "record",
    ]
    # The sync wrapper is the same door with asyncio.run around it; its
    # keywords may never drift from run_commission's.
    assert list(inspect.signature(vibrantine.run_commission_sync).parameters) == params


def test_call_context_surface_is_locked() -> None:
    # CallContext is the extend-here seam for new orchestration concerns,
    # so it will legitimately grow; the lock makes each growth deliberate.
    names = [f.name for f in fields(vibrantine.CallContext)]
    # Underscore fields are internal plumbing: locked here so their
    # existence stays deliberate, but outside the SemVer promise (the
    # package docstring's rule: any underscore-prefixed name is internal).
    assert names == [
        "budget_usd",
        "capabilities",
        "cancel",
        "on_progress",
        "parent_run_id",
        "backend",
        "record",
        "_gatekeeper",
    ]


def test_testing_surface_is_locked_exactly() -> None:
    # vibrantine.testing is supported surface by declaration (the testing
    # half of the catalog's client-vending seam), so its exports are locked
    # exactly like the top level's.
    expected = {
        "FIXTURE_MODEL",
        "AlwaysCancelled",
        "ScriptedLLM",
        "llm_response",
        "scripted_model",
    }
    assert set(testing.__all__) == expected
    for name in testing.__all__:
        assert getattr(testing, name, None) is not None, name


def test_scripted_model_keyword_surface_is_locked() -> None:
    # scripted_model is the door consumer tests ride; every keyword is a
    # decision a test author may have to make, locked like Commission's.
    params = list(inspect.signature(testing.scripted_model).parameters)
    assert params == [
        "scripted",
        "id",
        "name",
        "params",
        "context_window",
        "input_usd_per_million",
        "output_usd_per_million",
    ]


def test_provisional_commissions_are_not_in_the_frozen_surface() -> None:
    # examples/ is demonstration material: importable from its submodule,
    # not the top-level frozen __all__.
    assert "AskCommission" not in vibrantine.__all__
    assert "RecursiveResearchCommission" not in vibrantine.__all__
    assert "ReadTool" not in vibrantine.__all__


def test_entry_points_are_callable_from_the_top_level() -> None:
    from vibrantine import dispatch, run_commission, run_commission_sync

    assert callable(run_commission)
    assert callable(run_commission_sync)
    assert callable(dispatch)
