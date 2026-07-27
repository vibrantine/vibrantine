"""Runner policy helpers: model resolution, key gating, and menu construction."""

import argparse
from pathlib import Path

import pytest

from vibrantine import DEFAULT_MODEL, Model, ollama
from vibrantine.examples.ask import AskInput
from vibrantine.examples.demo.catalog import build_ask, build_synthesize
from vibrantine.examples.demo.runner import (
    MENU,
    describe_input,
    key_missing_message,
    session_model,
)


def _args(model: str | None = None, ollama_name: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(model=model, ollama=ollama_name, budget=None)


def test_session_model_defaults_to_the_known_default() -> None:
    model = session_model(_args())
    assert model is DEFAULT_MODEL
    assert model.is_priced


def test_session_model_unknown_id_falls_back_unpriced() -> None:
    model = session_model(_args(model="acme/mystery"))
    assert model.id == "acme/mystery"
    assert not model.is_priced


def test_session_model_ollama_is_local_and_free() -> None:
    model = session_model(_args(ollama_name="llama3"))
    assert "localhost" in model.base_url
    assert model.is_priced and model.input_usd_per_million == 0.0


def test_key_message_names_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    message = key_missing_message(Model(id="x"))
    assert message is not None and "OPENROUTER_API_KEY" in message


def test_key_message_absent_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    assert key_missing_message(Model(id="x")) is None


def test_key_message_never_blocks_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert key_missing_message(ollama("llama3")) is None


def test_menu_entries_build_commission_and_matching_input() -> None:
    # Builders take a model name (None = the run default), as the runner
    # calls them; the session's Model object goes to run_commission(models=[...]).
    for entry in MENU:
        commission, demo_input = entry.build(None)
        assert isinstance(demo_input, commission.input_type)
        assert entry.budget_usd > 0


def test_describe_input_shows_the_canned_ask_run() -> None:
    _, demo_input = build_ask()
    text = describe_input(demo_input)
    assert "question:" in text
    assert "ask.py" in text


def test_describe_input_gives_each_list_item_its_own_line() -> None:
    _, demo_input = build_synthesize()
    text = describe_input(demo_input)
    assert "sources[0]:" in text
    assert "sources[1]:" in text
    assert "city council voted 7-2" in text
    assert "Local radio reports" in text
    assert "focus: What was decided, and who dissented?" in text


def test_describe_input_truncates_long_fields() -> None:
    long_question = "why? " * 100
    text = describe_input(AskInput(question=long_question, file_path=Path(__file__)))
    question_line = text.splitlines()[0]
    assert len(question_line) <= 112
    assert question_line.endswith("...")
