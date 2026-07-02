"""Type-level SSOT tests.

Locks in the documented vocabularies for status / kind / confidence so that
adding a value without updating the docs (and downstream dispatch) fails
loudly. AGENTS.md classifies `ErrorState.kind` as the canonical SSOT for
failure categories; the other two are smaller but share the same discipline.

Also exercises the Commission constructor's policy-override surface
(persistence_mode / max_output_tokens / overflow_policy) — the
sentinel-driven kwargs that let one Commission class run with different
policies in different environments.
"""

from datetime import UTC, datetime
from typing import ClassVar, get_args

import pytest
from pydantic import BaseModel

from vibrantine.contract import (
    CallContext,
    CapabilitySet,
    Commission,
    CommissionResult,
    CommissionStatus,
    ConfidenceLevel,
    CostMetrics,
    ErrorKind,
    OverflowPolicy,
    PersistenceMode,
    Provenance,
)


def test_error_kind_literals_match_documented_ssot() -> None:
    assert set(get_args(ErrorKind.__value__)) == {
        "validation",
        "internal",
        "rate_limit",
        "timeout",
        "budget_exceeded",
        "cancelled",
        "output_too_large",
    }


def test_commission_status_literals_match_documented_ssot() -> None:
    assert set(get_args(CommissionStatus.__value__)) == {"success", "failure", "partial"}


def test_confidence_level_literals_match_documented_ssot() -> None:
    assert set(get_args(ConfidenceLevel.__value__)) == {
        "verified",
        "grounded",
        "speculative",
    }


def test_persistence_mode_literals_match_documented_ssot() -> None:
    assert set(get_args(PersistenceMode.__value__)) == {
        "off",
        "on_failure",
        "dev",
        "always",
    }


def test_overflow_policy_literals_match_documented_ssot() -> None:
    assert set(get_args(OverflowPolicy.__value__)) == {
        "reject",
        "truncate_with_reference",
        "partial",
        "flag",
    }


# Policy override surface ------------------------------------------------


class _PolicyProbeInput(BaseModel):
    pass


class _PolicyProbeOutput(BaseModel):
    pass


class _PolicyProbe(Commission[_PolicyProbeInput, _PolicyProbeOutput]):
    """Minimal Commission for exercising the policy-override kwargs."""

    name: ClassVar[str] = "policy_probe"
    description: ClassVar[str] = "Test commission for policy override kwargs."
    input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
    output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput

    async def invoke(
        self,
        input: _PolicyProbeInput,
        ctx: CallContext,
    ) -> CommissionResult[_PolicyProbeOutput]:
        return CommissionResult[_PolicyProbeOutput](
            status="success",
            output=_PolicyProbeOutput(),
            provenance=Provenance(
                source="policy_probe",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        )


class _CappedProbe(_PolicyProbe):
    """Class default with max_output_tokens set, to test meaningful-None override."""

    max_output_tokens: int | None = 1000


def test_omitting_policy_kwargs_falls_back_to_class_defaults() -> None:
    probe = _PolicyProbe()

    assert probe.persistence_mode == "off"
    assert probe.max_output_tokens is None
    assert probe.overflow_policy == "flag"


def test_persistence_mode_kwarg_overrides_class_default() -> None:
    probe = _PolicyProbe(persistence_mode="always")

    assert probe.persistence_mode == "always"
    # Other policies untouched.
    assert probe.max_output_tokens is None
    assert probe.overflow_policy == "flag"


def test_overflow_policy_kwarg_overrides_class_default() -> None:
    probe = _PolicyProbe(overflow_policy="reject")

    assert probe.overflow_policy == "reject"
    assert probe.persistence_mode == "off"


def test_max_output_tokens_kwarg_overrides_class_default() -> None:
    probe = _PolicyProbe(max_output_tokens=500)

    assert probe.max_output_tokens == 500


def test_max_output_tokens_none_is_a_meaningful_override() -> None:
    # _CappedProbe declares max_output_tokens=1000 at class level.
    # Passing None explicitly should disable enforcement on this instance —
    # the case the _UNSET sentinel exists for, since None can't double as
    # "use class default".
    probe = _CappedProbe(max_output_tokens=None)

    assert probe.max_output_tokens is None
    # Confirm omission still picks up the class default.
    assert _CappedProbe().max_output_tokens == 1000


# Toolbox declaration ----------------------------------------------------


def test_toolbox_defaults_to_empty_tuple() -> None:
    # Workers (and coordinators with no deps) inherit this.
    assert _PolicyProbe().toolbox == ()


def test_toolbox_kwarg_is_stored() -> None:
    worker = _PolicyProbe()
    parent = _PolicyProbe(toolbox=(worker,))

    assert parent.toolbox == (worker,)


def test_toolbox_does_not_leak_between_instances() -> None:
    # toolbox is per-instance, not a ClassVar: giving one commission a
    # toolbox must never change another's empty default.
    with_dep = _PolicyProbe(toolbox=(_PolicyProbe(),))
    without = _PolicyProbe()

    assert len(with_dep.toolbox) == 1
    assert without.toolbox == ()


# Base default-invoke surface --------------------------------------------


class _BasicProbe(Commission[_PolicyProbeInput, _PolicyProbeOutput]):
    """A basic commission: declarations + build_user_message, default invoke."""

    name: ClassVar[str] = "basic_probe"
    description: ClassVar[str] = "Test commission that uses the default invoke."
    input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
    output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput

    def build_user_message(self, input: _PolicyProbeInput, ctx: CallContext) -> str:
        return "probe"


class _ToolboxProbe(_BasicProbe):
    """Declares a class-level toolbox, like a real basic commission."""

    toolbox = (_PolicyProbe(),)


def test_build_user_message_default_raises_not_implemented() -> None:
    # A custom commission overrides invoke and never triggers
    # build_user_message; the inherited base default still raises if a caller
    # pokes it directly.
    with pytest.raises(NotImplementedError, match="build_user_message"):
        _PolicyProbe().build_user_message(_PolicyProbeInput(), CallContext())


def test_default_model_resolves_the_size_gate() -> None:
    # No model kwarg → the system default (models.DEFAULT_MODEL); its context
    # window auto-resolves max_input_tokens, the size-gate ceiling. Observing
    # the resolved gate confirms the default model flowed through.
    assert _BasicProbe().max_input_tokens == 1_050_000


def test_explicit_none_disables_the_size_gate() -> None:
    # max_input_tokens=None is Shape A's "no gate" — it must mean no gate,
    # not "auto-resolve from the model" (that's what leaving it unset does).
    from vibrantine.tools.read import ReadTool

    probe = _PolicyProbe(max_input_tokens=None)
    assert probe.max_input_tokens is None
    assert probe.fits(10**9)
    # The std-lib tools construct with None, so Shape A holds literally.
    assert ReadTool().max_input_tokens is None


def test_client_is_not_constructed_eagerly(monkeypatch: pytest.MonkeyPatch) -> None:
    # The base must not build an LLM client at construction. Tools and
    # coordinators never run the default loop, and building a client with no
    # credentials raises — so eager construction would break them. Absence of
    # a raise here is the lazy-client guarantee; the client is built only when
    # the default loop actually runs.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _BasicProbe()
    _PolicyProbe()  # overrides invoke; must likewise stay client-free


def test_class_level_toolbox_is_the_default() -> None:
    # The class-attribute toolbox is used when no kwarg is passed (the path a
    # basic commission like AskCommission relies on); the kwarg still overrides.
    assert len(_ToolboxProbe().toolbox) == 1
    assert _ToolboxProbe(toolbox=()).toolbox == ()
    # And the override does not mutate the class-level default.
    assert len(_ToolboxProbe().toolbox) == 1


# Capability set ---------------------------------------------------------


def test_capability_set_defaults_to_unrestricted() -> None:
    # None = unrestricted (the root default); the empty set is explicit deny-all.
    assert CapabilitySet().tools is None
    assert CapabilitySet(tools=frozenset()).tools == frozenset()


# Definition-time required-ClassVar validation ---------------------------


def test_missing_output_type_fails_at_class_definition() -> None:
    # The standard format is enforced when the class is defined, not deferred
    # to first invoke.
    with pytest.raises(TypeError, match="output_type"):

        class _MissingOutput(  # pyright: ignore[reportUnusedClass]
            Commission[_PolicyProbeInput, _PolicyProbeOutput]
        ):
            name: ClassVar[str] = "missing_output"
            description: ClassVar[str] = "declares everything but output_type"
            input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput


def test_missing_identity_classvars_are_reported() -> None:
    with pytest.raises(TypeError, match="name"):

        class _MissingName(  # pyright: ignore[reportUnusedClass]
            Commission[_PolicyProbeInput, _PolicyProbeOutput]
        ):
            input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
            output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput


def test_subclass_inherits_required_classvars() -> None:
    # _CappedProbe inherits all four from _PolicyProbe and is defined at
    # import without error — inheritance satisfies the definition-time check.
    assert _CappedProbe.name == "policy_probe"
    assert _CappedProbe.output_type is _PolicyProbeOutput


def test_overriding_neither_build_nor_invoke_fails_at_definition() -> None:
    # The standard format requires exactly one of the two extension points;
    # a commission that overrides neither could never run.
    with pytest.raises(TypeError, match="overrides neither"):

        class _Inert(  # pyright: ignore[reportUnusedClass]
            Commission[_PolicyProbeInput, _PolicyProbeOutput]
        ):
            name: ClassVar[str] = "inert"
            description: ClassVar[str] = "overrides neither extension point"
            input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
            output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput
