"""Machine-checks for the claims in `docs/authoring.md`.

That doc is written for a third party who builds against the public surface
with nothing but the doc in hand. Each runnable claim it makes is pinned here
so the doc fails loudly in CI rather than rotting silently between refreshes.
"""

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

from vibrantine import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    Provenance,
    run_commission_sync,
)
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model
from vibrantine.tools import (
    DeleteInput,
    EditInput,
    FetchInput,
    GlobInput,
    GrepInput,
    ListDirInput,
    MoveInput,
    ReadInput,
    ReadTool,
    SampleInput,
    ShellInput,
    WriteInput,
)


# --- §1: py.typed ships so a consumer's type-checker sees real types ---
def test_py_typed_marker_ships() -> None:
    import vibrantine

    root = Path(vibrantine.__file__).parent
    assert (root / "py.typed").is_file()


# --- §1 / §6: a pure tool runs through an entry point with NO key ---
def test_pure_tool_runs_without_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    f = tmp_path / "sample.txt"
    f.write_text("line one\nline two\n", encoding="utf-8")

    res = run_commission_sync(ReadTool(), ReadInput(path=f))

    assert res.status == "success"
    assert res.run_id is not None  # stamped by the entry point
    assert res.cost.estimated_usd == 0.0  # a tool spends nothing


# --- §2: the std-lib tool input field names the doc tabulates ---
def test_tool_input_fields_match_the_doc_table() -> None:
    expected_required = {
        ReadInput: {"path"},
        WriteInput: {"path", "content"},
        EditInput: {"path", "old_string", "new_string"},
        DeleteInput: {"path"},
        MoveInput: {"source", "target"},
        GlobInput: {"pattern", "base"},
        GrepInput: {"pattern", "path"},
        ListDirInput: {"path"},
        SampleInput: {"path"},
        ShellInput: {"command"},
        FetchInput: {"url"},
    }
    for model, required in expected_required.items():
        actual = {n for n, fld in model.model_fields.items() if fld.is_required()}
        assert actual == required, f"{model.__name__} required fields drifted"
    # The worked example uses `file_path`; the tools deliberately use `path`.
    assert "file_path" not in ReadInput.model_fields


# --- §1 / §3.4 / §8: a scripted catalog entry runs the LLM loop with NO key ---
class _AnswerInput(BaseModel):
    prompt: str = Field(description="What to answer.")


class _AnswerOutput(BaseModel):
    answer: str = Field(description="The answer.")


class _BasicCommission(Commission[_AnswerInput, _AnswerOutput]):
    name: ClassVar[str] = "basic_probe"
    description: ClassVar[str] = "A basic Commission used to probe the default LLM loop."
    input_type: ClassVar[type] = _AnswerInput
    output_type: ClassVar[type] = _AnswerOutput
    system_prompt: ClassVar[str | None] = "Answer, then call conclude."

    def build_user_message(self, input: _AnswerInput, ctx: CallContext) -> str:
        return input.prompt


def test_scripted_catalog_entry_runs_the_loop_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "42"})])])
    commission = _BasicCommission()

    res = run_commission_sync(
        commission,
        _AnswerInput(prompt="what is the answer?"),
        models=[scripted_model(fake)],
        budget_usd=0.10,
    )

    assert res.status == "success"
    assert res.output is not None and res.output.answer == "42"
    # The supported double satisfies the seam: no real provider involved.
    assert isinstance(fake, ScriptedLLM)


# --- §3.3: the override rule is "at least one", not "exactly one" ---
def test_override_neither_fails_at_definition_time() -> None:
    with pytest.raises(TypeError):

        class _Neither(  # pyright: ignore[reportUnusedClass]
            Commission[_AnswerInput, _AnswerOutput]
        ):
            name: ClassVar[str] = "neither"
            description: ClassVar[str] = "d"
            input_type: ClassVar[type] = _AnswerInput
            output_type: ClassVar[type] = _AnswerOutput


def test_override_both_is_allowed() -> None:
    # Overriding both must NOT raise: _run wins, build_user_message is unused.
    class _Both(Commission[_AnswerInput, _AnswerOutput]):
        name: ClassVar[str] = "both"
        description: ClassVar[str] = "d"
        input_type: ClassVar[type] = _AnswerInput
        output_type: ClassVar[type] = _AnswerOutput

        def build_user_message(self, input: _AnswerInput, ctx: CallContext) -> str:
            return "unused"

        async def _run(
            self, input: _AnswerInput, ctx: CallContext
        ) -> CommissionResult[_AnswerOutput]:
            return CommissionResult(
                status="success",
                output=_AnswerOutput(answer="from _run"),
                provenance=Provenance(
                    source=self.name, fetched_at=datetime.now(UTC), confidence="grounded"
                ),
                cost=CostMetrics(estimated_usd=0.0),
            )

    res = run_commission_sync(_Both(), _AnswerInput(prompt="x"))
    assert res.status == "success"
    assert res.output is not None and res.output.answer == "from _run"


# --- §4: errors are values; an exception in _run never crosses the boundary ---
class _Raising(Commission[_AnswerInput, _AnswerOutput]):
    name: ClassVar[str] = "raising"
    description: ClassVar[str] = "d"
    input_type: ClassVar[type] = _AnswerInput
    output_type: ClassVar[type] = _AnswerOutput

    async def _run(self, input: _AnswerInput, ctx: CallContext) -> CommissionResult[_AnswerOutput]:
        raise RuntimeError("boom")


def test_exception_in_run_becomes_a_failure_value() -> None:
    res = run_commission_sync(_Raising(), _AnswerInput(prompt="x"))
    assert res.status == "failure"
    assert res.error is not None and res.error.kind == "internal"


# --- §12: the custom-Commission SUCCESS path (hand-built Provenance/cost) ---
class _CustomSuccess(Commission[_AnswerInput, _AnswerOutput]):
    name: ClassVar[str] = "custom_success"
    description: ClassVar[str] = "d"
    input_type: ClassVar[type] = _AnswerInput
    output_type: ClassVar[type] = _AnswerOutput

    async def _run(self, input: _AnswerInput, ctx: CallContext) -> CommissionResult[_AnswerOutput]:
        return CommissionResult(
            status="success",
            output=_AnswerOutput(answer=input.prompt.upper()),
            provenance=Provenance(
                source=self.name, fetched_at=datetime.now(UTC), confidence="grounded"
            ),
            cost=CostMetrics(estimated_usd=0.0),
        )


def test_custom_success_path_runs() -> None:
    res = run_commission_sync(_CustomSuccess(), _AnswerInput(prompt="hi"))
    assert res.status == "success"
    assert res.output is not None and res.output.answer == "HI"


# --- §4: Provenance has no defaults (the wall a custom author hits first) ---
def test_provenance_requires_all_three_fields() -> None:
    required = {n for n, fld in Provenance.model_fields.items() if fld.is_required()}
    assert required == {"source", "fetched_at", "confidence"}


# --- §3.5: protected helper signatures the doc tabulates ---
def test_protected_helper_signatures() -> None:
    c = _CustomSuccess()

    def params(name: str) -> list[str]:
        return list(inspect.signature(getattr(c, name)).parameters)

    assert params("_fail") == ["kind", "detail", "retryable", "provenance", "cost"]
    assert params("_emit") == ["ctx", "phase", "detail"]
    # _cost now prices against the run catalog entry the caller resolved.
    assert params("_cost") == ["in_tokens", "out_tokens", "entry"]
    assert params("fits") == ["estimated_tokens"]


# --- Part III: the one-import-line block IS the public surface ---
def test_reference_import_block_matches_public_all() -> None:
    # The doc claims everything you may depend on fits in one import line.
    # This parses that block out of "The public surface" and asserts it
    # names exactly `vibrantine.__all__`, so the claim can never drift.
    import re

    import vibrantine

    doc = (Path(__file__).parent.parent / "docs" / "authoring.md").read_text(encoding="utf-8")
    section_start = doc.index("## The public surface")
    section_end = doc.find("\n## ", section_start)
    section = doc[section_start:] if section_end == -1 else doc[section_start:section_end]
    # Exactly one, so a second illustrative import block added to the section
    # can never silently become the one the lock reads.
    blocks = re.findall(r"from vibrantine import \(\n(.*?)\n\)", section, re.DOTALL)
    assert len(blocks) == 1, "expected exactly one import block in 'The public surface'"
    names: set[str] = set()
    for line in blocks[0].splitlines():
        code = line.split("#", 1)[0]
        names.update(name.strip() for name in code.split(",") if name.strip())
    assert names == set(vibrantine.__all__)


# --- §3.5 / §4: the authoring-edge helpers are top-level exports ---
def test_authoring_edge_helpers_are_exported() -> None:
    import vibrantine

    assert callable(vibrantine.estimate_tokens)
    assert callable(vibrantine.deposit_llm_trace)
    assert "estimate_tokens" in vibrantine.__all__
    assert "deposit_llm_trace" in vibrantine.__all__
    # The submodule path keeps working; the top level is the taught one.
    from vibrantine.contract import estimate_tokens

    assert estimate_tokens is vibrantine.estimate_tokens
