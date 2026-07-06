"""The demo CLI: menu, chat loop, spending policy, and trace presentation.

This is the demo's application layer, and deliberately so: everything the
library refuses to own (secret wiring, model choice, budgets, conversation
state, presentation) has to live somewhere, and this file is the worked
example of where. Every run rides the public contract: construct with
config, build a typed input, dispatch with a CallContext, read the result
envelope and the persisted records.
"""

import argparse
import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from vibrantine.contract import (
    CallContext,
    CommissionResult,
    PersistedRecord,
    ProgressEvent,
)
from vibrantine.dispatch import dispatch
from vibrantine.examples.ask import AskOutput
from vibrantine.examples.demo.agent import ChatInput, ChatTurn, demo_agent
from vibrantine.examples.demo.catalog import (
    build_ask,
    build_morning_briefing,
    build_recursive_research,
    build_synthesize,
)
from vibrantine.examples.demo.trace import (
    RecordingBackend,
    record_cost,
    record_status,
    render_trace,
    render_transcript,
    trace_order,
)
from vibrantine.examples.morning_briefing import MorningBriefingOutput
from vibrantine.examples.recursive_research import ResearchOutput
from vibrantine.examples.synthesize import SynthesizeOutput
from vibrantine.models import DEFAULT_MODEL, KNOWN_MODELS, Model, ollama

# Spending policy is the caller's, so it lives here, not on the examples.
# Tiers are backstops sized well above a normal run (a cap that fires should
# mean misbehavior, not an expected stop), small enough that a runaway run
# costs cents.
AGENT_BUDGET_USD = 0.20


@dataclass(frozen=True)
class MenuEntry:
    """One directly runnable example: how to build it, bound it, and show it."""

    key: str
    title: str
    blurb: str
    budget_usd: float
    build: Callable[[Model], tuple[Any, BaseModel]]
    present: Callable[[Any], str]


def _present_ask(output: AskOutput) -> str:
    return output.answer


def _present_synthesize(output: SynthesizeOutput) -> str:
    lines = [output.summary_text]
    if output.claims:
        lines.append("")
        for claim in output.claims:
            sources = ", ".join(p.source for p in claim.sources)
            lines.append(f"- {claim.value} [{sources}] ({claim.confidence})")
    return "\n".join(lines)


def _present_briefing(output: MorningBriefingOutput) -> str:
    lines = [f"Briefing written to {output.markdown_path}"]
    if output.executive_summary:
        lines.extend(["", output.executive_summary])
    if output.failed_sections:
        lines.append(f"(sections unavailable: {', '.join(output.failed_sections)})")
    return "\n".join(lines)


def _present_research(output: ResearchOutput) -> str:
    lines = [output.answer]
    if output.claims:
        lines.append("")
        for claim in output.claims:
            sources = ", ".join(claim.source_urls)
            lines.append(f"- {claim.value} [{sources}] ({claim.confidence})")
    return "\n".join(lines)


MENU: tuple[MenuEntry, ...] = (
    MenuEntry(
        key="1",
        title="ask",
        blurb="answer a canned question about its own source file (ask.py)",
        budget_usd=0.05,
        build=build_ask,
        present=_present_ask,
    ),
    MenuEntry(
        key="2",
        title="synthesize",
        blurb="merge two canned sources into a summary with cited claims",
        budget_usd=0.05,
        build=build_synthesize,
        present=_present_synthesize,
    ),
    MenuEntry(
        key="3",
        title="morning_briefing",
        blurb="weather (wttr.in) + news digests (BBC World, Hacker News) -> markdown file",
        budget_usd=0.10,
        build=build_morning_briefing,
        present=_present_briefing,
    ),
    MenuEntry(
        key="4",
        title="recursive_research",
        blurb="research Python vs Rust governance/licensing (recursive, takes minutes)",
        budget_usd=0.20,
        build=build_recursive_research,
        present=_present_research,
    ),
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m vibrantine.examples",
        description="Interactive demo of the Vibrantine example Commissions.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--model",
        default=None,
        help=f"model id, resolved via KNOWN_MODELS / OpenRouter (default: {DEFAULT_MODEL})",
    )
    target.add_argument(
        "--ollama",
        default=None,
        metavar="NAME",
        help="run against a local Ollama model of this name instead of the cloud",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="override every per-run budget cap with one USD value",
    )
    return parser.parse_args(argv)


def session_model(args: argparse.Namespace) -> Model:
    if args.ollama:
        return ollama(str(args.ollama))
    model_id = str(args.model) if args.model else DEFAULT_MODEL
    known = KNOWN_MODELS.get(model_id)
    return known if known is not None else Model(id=model_id)


def key_missing_message(model: Model) -> str | None:
    """Friendly setup text when the model's API key is absent; None when ready.

    A keyless model (api_key_env=None, e.g. local Ollama) never blocks.
    """
    if model.api_key_env is None or os.environ.get(model.api_key_env):
        return None
    return (
        f"No {model.api_key_env} found in the environment.\n"
        "\n"
        "The demo talks to a real model, so it needs a real key:\n"
        f"  1. Get one (for OpenRouter: https://openrouter.ai/keys).\n"
        f"  2. Put it in a .env file:  {model.api_key_env}=sk-...\n"
        "  3. Run with the file loaded:\n"
        "       uv run --env-file .env python -m vibrantine.examples\n"
        "\n"
        "Or run against a local Ollama model with no key at all:\n"
        "       python -m vibrantine.examples --ollama <model-name>"
    )


def _clip(text: str) -> str:
    return text[:97] + "..." if len(text) > 100 else text


def describe_input(demo_input: BaseModel) -> str:
    """One line per input field, truncated: shows exactly what a canned run sends.

    List fields get one line per item, so multi-source inputs stay readable
    instead of collapsing into one truncated line.
    """
    lines: list[str] = []
    for name, value in demo_input.model_dump().items():
        field_value: object = value
        if isinstance(field_value, list):
            items = cast(list[object], field_value)
            if not items:
                lines.append(f"  {name}: []")
            for index, item in enumerate(items):
                lines.append(f"  {name}[{index}]: {_clip(str(item))}")
        else:
            lines.append(f"  {name}: {_clip(str(field_value))}")
    return "\n".join(lines)


def _print_progress(event: ProgressEvent) -> None:
    detail = f" ({event.detail})" if event.detail else ""
    print(f"  ~ {event.commission_name}: {event.phase}{detail}")


def _print_store(record: PersistedRecord) -> None:
    print(
        f"  * {record.commission_name} finished: "
        f"{record_status(record)}  ${record_cost(record):.4f}"
    )


def _print_result(result: CommissionResult[Any], present: Callable[[Any], str]) -> None:
    print()
    if result.output is not None:
        print(present(result.output))
        if result.status == "partial" and result.error is not None:
            print(f"\n(partial: {result.error.detail})")
    elif result.error is not None:
        print(f"Run failed ({result.error.kind}): {result.error.detail}")
    else:
        print(f"Run ended with status {result.status} and no output.")


def _print_banner(model: Model, budgets_enforceable: bool) -> None:
    print("Vibrantine demo: worked example Commissions behind one contract.")
    print(f"Model: {model.id} @ {model.base_url}")
    if not budgets_enforceable:
        print("Note: this model carries no pricing, so budget caps are off for this session.")
    print()
    print("Type a number to run an example with fixed, canned inputs (shown at")
    print("run start). Anything else is a chat message to the demo agent, which")
    print("can trigger the examples with inputs you describe. After a run,")
    print("t <n> shows the LLM transcript behind trace node [n]. q to quit.")
    print()
    for entry in MENU:
        print(f"  {entry.key}. {entry.title}: {entry.blurb}")


def _transcript_for(line: str, last_trace: list[PersistedRecord]) -> str:
    """Resolve a `t <n>` command against the last run's numbered trace nodes."""
    if not last_trace:
        return "No run to inspect yet; run an example or chat first."
    parts = line.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return (
            f"Usage: t <n>, where n is a node number "
            f"from the last run trace (1..{len(last_trace)})."
        )
    index = int(parts[1])
    if not 1 <= index <= len(last_trace):
        return f"Node {index} is out of range; the last run trace has nodes 1..{len(last_trace)}."
    return render_transcript(last_trace[index - 1])


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    model = session_model(args)
    missing = key_missing_message(model)
    if missing is not None:
        print(missing)
        return 1

    # An unpriced model can't have a USD budget enforced (the loop fails fast
    # rather than running ungoverned), so budgets switch off wholesale.
    budgets_enforceable = model.is_priced

    def budget_for(tier: float) -> float | None:
        if not budgets_enforceable:
            return None
        return float(args.budget) if args.budget is not None else tier

    backend = RecordingBackend(on_store=_print_store)
    agent = demo_agent(model)
    entries = {entry.key: entry for entry in MENU}
    transcript: list[ChatTurn] = []
    # The last run's records in [n]-label order, so `t <n>` resolves against
    # the numbering the user just read in the rendered trace.
    last_trace: list[PersistedRecord] = []

    _print_banner(model, budgets_enforceable)

    while True:
        try:
            line = input("\ndemo> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in {"q", "quit", "exit"}:
            return 0

        first_token = line.split()[0]
        if first_token.lower() == "t":
            print(_transcript_for(line, last_trace))
            continue
        if first_token in entries and line != first_token:
            print(
                f"Menu numbers run fixed canned demos, so the text after '{first_token}' "
                "would be ignored.\nTo use your own input, describe it in plain words "
                "and the agent will trigger the example with it."
            )
            continue

        first_new_record = len(backend.records())

        if line in entries:
            entry = entries[line]
            commission, demo_input = entry.build(model)
            budget = budget_for(entry.budget_usd)
            cap = f"budget ${budget:.2f}" if budget is not None else "no budget cap"
            print(f"Running {entry.title} ({cap}) with its canned input:")
            print(describe_input(demo_input))
            # record="always" is the whole persistence switch: it rides the
            # context to every node in the tree, including any spawned mid-run.
            ctx = CallContext(
                budget_usd=budget,
                on_progress=_print_progress,
                backend=backend,
                record="always",
            )
            result = asyncio.run(dispatch(commission, demo_input, ctx))
            _print_result(result, entry.present)
        else:
            ctx = CallContext(
                budget_usd=budget_for(AGENT_BUDGET_USD),
                on_progress=_print_progress,
                backend=backend,
                record="always",
            )
            chat_input = ChatInput(message=line, transcript=list(transcript))
            result = asyncio.run(dispatch(agent, chat_input, ctx))
            if result.output is not None:
                print(f"\n{result.output.reply}")
                transcript.append(ChatTurn(role="user", text=line))
                transcript.append(ChatTurn(role="assistant", text=result.output.reply))
                if result.status == "partial" and result.error is not None:
                    print(f"\n(partial: {result.error.detail})")
            elif result.error is not None:
                print(f"\nAgent turn failed ({result.error.kind}): {result.error.detail}")

        new_records = backend.records()[first_new_record:]
        last_trace = trace_order(new_records)
        print("\nrun trace:")
        print(render_trace(new_records))
