"""Type-level SSOT tests.

Locks in the documented vocabularies for status / kind / confidence so that
adding a value without updating the docs (and downstream dispatch) fails
loudly. AGENTS.md classifies `ErrorState.kind` as the canonical SSOT for
failure categories; the other two are smaller but share the same discipline.

Also exercises the Commission constructor's policy-override surface
(persistence_mode / max_output_tokens / overflow_policy): the
sentinel-driven kwargs that let one Commission class run with different
policies in different environments.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar, get_args

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
from vibrantine.models import ollama
from vibrantine.orchestrator import run_one
from vibrantine.testing import ScriptedLLM, scripted_model


def test_error_kind_literals_match_documented_ssot() -> None:
    assert set(get_args(ErrorKind.__value__)) == {
        "validation",
        "internal",
        "rate_limit",
        "timeout",
        "budget_exceeded",
        "cancelled",
        "output_too_large",
        "run_halted",
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
    description: ClassVar[str] = "Test Commission for policy override kwargs."
    input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
    output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput

    async def _run(
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

    # None = no recording opinion: the node follows the caller's ctx.record.
    assert probe.persistence_mode is None
    assert probe.max_output_tokens is None
    assert probe.overflow_policy == "partial"


def test_persistence_mode_kwarg_overrides_class_default() -> None:
    probe = _PolicyProbe(persistence_mode="always")

    assert probe.persistence_mode == "always"
    # Other policies untouched.
    assert probe.max_output_tokens is None
    assert probe.overflow_policy == "partial"


def test_overflow_policy_kwarg_overrides_class_default() -> None:
    probe = _PolicyProbe(overflow_policy="reject")

    assert probe.overflow_policy == "reject"
    assert probe.persistence_mode is None


def test_max_output_tokens_kwarg_overrides_class_default() -> None:
    probe = _PolicyProbe(max_output_tokens=500)

    assert probe.max_output_tokens == 500


def test_max_output_tokens_none_is_a_meaningful_override() -> None:
    # _CappedProbe declares max_output_tokens=1000 at class level.
    # Passing None explicitly should disable enforcement on this instance:
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
    # toolbox is per-instance, not a ClassVar: giving one Commission a
    # toolbox must never change another's empty default.
    with_dep = _PolicyProbe(toolbox=(_PolicyProbe(),))
    without = _PolicyProbe()

    assert len(with_dep.toolbox) == 1
    assert without.toolbox == ()


# Base default-_run surface ----------------------------------------------


class _BasicProbe(Commission[_PolicyProbeInput, _PolicyProbeOutput]):
    """A basic Commission: declarations + build_user_message, default _run."""

    name: ClassVar[str] = "basic_probe"
    description: ClassVar[str] = "Test Commission that uses the default _run."
    input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
    output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput

    def build_user_message(self, input: _PolicyProbeInput, ctx: CallContext) -> str:
        return "probe"


class _ToolboxProbe(_BasicProbe):
    """Declares a class-level toolbox, like a real basic Commission."""

    toolbox = (_PolicyProbe(),)


def test_succeed_builds_the_success_envelope() -> None:
    # _succeed is _fail's counterpart: the protected helper for the most
    # common return a custom _run writes. Status, output, and the absence
    # of an error are the envelope invariants it must uphold.
    probe = _PolicyProbe()
    result = probe._succeed(  # pyright: ignore[reportPrivateUsage]
        _PolicyProbeOutput(),
        provenance=Provenance(
            source="policy_probe",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        ),
        cost=CostMetrics(estimated_usd=0.0),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.error is None
    assert result.cost.estimated_usd == 0.0


def test_build_user_message_default_raises_not_implemented() -> None:
    # A custom Commission overrides _run and never triggers
    # build_user_message; the inherited base default still raises if a caller
    # pokes it directly.
    with pytest.raises(NotImplementedError, match="build_user_message"):
        _PolicyProbe().build_user_message(_PolicyProbeInput(), CallContext())


class _LongWindedProbe(_BasicProbe):
    """Opening message far above a tiny context window's size gate."""

    def build_user_message(self, input: _PolicyProbeInput, ctx: CallContext) -> str:
        return "x" * 400  # estimates to ~100 tokens (len // 4)


async def test_run_catalog_entry_resolves_the_size_gate() -> None:
    # max_input_tokens left unset resolves at run time from the run catalog
    # entry's context window (it is no longer fixed at construction). A tiny
    # window (40 tokens * 0.75 target fraction = 30) must reject the
    # ~100-token opening message before any LLM call.
    fake = ScriptedLLM([])

    result = await run_one(
        _LongWindedProbe(),
        _PolicyProbeInput(),
        models=[scripted_model(fake, context_window=40)],
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "size gate" in result.error.detail
    assert len(fake.calls) == 0


def test_explicit_none_disables_the_size_gate() -> None:
    # max_input_tokens=None is the tools' "no gate": it must mean no gate,
    # not "auto-resolve from the model" (that's what leaving it unset does).
    from vibrantine.tools.read import ReadTool

    probe = _PolicyProbe(max_input_tokens=None)
    assert probe.max_input_tokens is None
    assert probe.fits(10**9)
    # The std-lib tools construct with None, so the no-gate shape holds.
    assert ReadTool().max_input_tokens is None


def test_client_is_not_constructed_eagerly(monkeypatch: pytest.MonkeyPatch) -> None:
    # The base must not build an LLM client at construction. Tools and
    # coordinators never run the default loop, and building a client with no
    # credentials raises, so eager construction would break them. Absence of
    # a raise here is the lazy-client guarantee; the client is built only when
    # the default loop actually runs.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _BasicProbe()
    _PolicyProbe()  # overrides _run; must likewise stay client-free


async def test_missing_key_fails_fast_with_the_env_var_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A keyed endpoint whose key is absent must fail before any network call,
    # naming the env var, instead of surfacing as a raw provider 401 mid-run.
    # The raise happens in the run's client vending at the first provider
    # call; it crosses _run, so dispatch's backstop delivers it as a failure
    # value through the front door.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = await run_one(_BasicProbe(), _PolicyProbeInput())

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "OPENROUTER_API_KEY" in result.error.detail
    assert result.cost.estimated_usd == 0.0  # failed before spending


def test_keyless_model_builds_a_client_without_any_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # api_key_env=None means the endpoint needs no key (local Ollama), so the
    # missing-key check must never block it. The subject here is the run's
    # client-vending seam itself, hence the direct Gatekeeper access.
    from vibrantine._gatekeeper import Gatekeeper, build_catalog

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    catalog, default = build_catalog([ollama("llama3")], None)
    gk = Gatekeeper(
        catalog=catalog,
        default_model=default,
        max_llm_calls=None,
        time_limit_seconds=None,
        spend_limit_usd=None,
        concurrency=16,
    )

    assert gk.client_for(gk.resolve_model("llama3")) is not None


def test_class_level_toolbox_is_the_default() -> None:
    # The class-attribute toolbox is used when no kwarg is passed (the path a
    # basic Commission like AskCommission relies on); the kwarg still overrides.
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
    # to first run.
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
    # import without error; inheritance satisfies the definition-time check.
    assert _CappedProbe.name == "policy_probe"
    assert _CappedProbe.output_type is _PolicyProbeOutput


def test_overriding_neither_build_nor_run_fails_at_definition() -> None:
    # The standard format requires exactly one of the two extension points;
    # a Commission that overrides neither could never run.
    with pytest.raises(TypeError, match="overrides neither"):

        class _Inert(  # pyright: ignore[reportUnusedClass]
            Commission[_PolicyProbeInput, _PolicyProbeOutput]
        ):
            name: ClassVar[str] = "inert"
            description: ClassVar[str] = "overrides neither extension point"
            input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
            output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput


def test_output_classvar_disagreeing_with_generics_fails_at_definition() -> None:
    # The generics and the ClassVars state the same contract twice: type
    # checkers read the former, the runtime the latter. A mismatch must fail
    # at definition, not run with the two audiences seeing different types.
    with pytest.raises(TypeError, match="output_type"):

        class _Mismatched(  # pyright: ignore[reportUnusedClass]
            Commission[_PolicyProbeInput, _PolicyProbeOutput]
        ):
            name: ClassVar[str] = "mismatched"
            description: ClassVar[str] = "generic and ClassVar disagree on output"
            input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
            output_type: ClassVar[type[BaseModel]] = _PolicyProbeInput  # wrong on purpose

            def build_user_message(self, input: _PolicyProbeInput, ctx: CallContext) -> str:
                return "probe"


def test_input_classvar_disagreeing_with_generics_fails_at_definition() -> None:
    with pytest.raises(TypeError, match="input_type"):

        class _Mismatched(  # pyright: ignore[reportUnusedClass]
            Commission[_PolicyProbeInput, _PolicyProbeOutput]
        ):
            name: ClassVar[str] = "mismatched"
            description: ClassVar[str] = "generic and ClassVar disagree on input"
            input_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput  # wrong on purpose
            output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput

            def build_user_message(self, input: _PolicyProbeInput, ctx: CallContext) -> str:
                return "probe"


def test_any_generic_parameters_skip_the_agreement_check() -> None:
    # `Any` is a class on 3.12+, so the check must exclude it explicitly or
    # an Any-parameterized subclass would false-positive against its concrete
    # ClassVars. Defining cleanly is the assertion.
    class _AnyParams(Commission[Any, Any]):
        name: ClassVar[str] = "any_params"
        description: ClassVar[str] = "Any-parameterized probe"
        input_type: ClassVar[type[BaseModel]] = _PolicyProbeInput
        output_type: ClassVar[type[BaseModel]] = _PolicyProbeOutput

        def build_user_message(self, input: Any, ctx: CallContext) -> str:
            return "probe"

    assert _AnyParams.input_type is _PolicyProbeInput
