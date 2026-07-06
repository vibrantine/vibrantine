"""Canned demo setups: the construction config and inputs for each example run.

The demo is the caller, so everything caller-owned lives here: which weather
source and news feeds the briefing gets (capacity), what question the ask
run poses (task), and the fictional texts synthesize merges. The example
modules themselves stay pure worked examples, free of demo scaffolding.
"""

from datetime import UTC, datetime
from pathlib import Path

from vibrantine.contract import Provenance
from vibrantine.examples import ask as _ask_module
from vibrantine.examples.ask import AskCommission, AskInput
from vibrantine.examples.morning_briefing import (
    MorningBriefingCommission,
    MorningBriefingInput,
    NewsDigestCommission,
    WeatherCommission,
)
from vibrantine.examples.recursive_research import (
    RecursiveResearchCommission,
    ResearchInput,
)
from vibrantine.examples.summarize import SummarizeCommission
from vibrantine.examples.synthesize import (
    SynthesisSource,
    SynthesizeCommission,
    SynthesizeInput,
)
from vibrantine.models import Model

# The one file guaranteed to exist on any install: the ask example's own source.
_ASK_SOURCE = Path(str(_ask_module.__file__))


def build_ask(model: str | Model | None = None) -> tuple[AskCommission, AskInput]:
    """Proof-of-life run: ask the example module about its own source file.

    Self-contained by construction; the file it reads ships with the package,
    so a fresh install can run it with nothing but an API key.
    """
    return (
        AskCommission(model=model),
        AskInput(
            question=(
                "What tools does this Commission hand its LLM, and how does "
                "the loop signal completion?"
            ),
            file_path=_ASK_SOURCE,
        ),
    )


def build_synthesize(
    model: str | Model | None = None,
) -> tuple[SynthesizeCommission, SynthesizeInput]:
    """Merge two overlapping fictional accounts of one event into cited claims.

    The sources are inline text, so the run needs no network beyond the LLM
    calls themselves.
    """
    now = datetime.now(UTC)
    sources = [
        SynthesisSource(
            content=(
                "The city council voted 7-2 on Tuesday to approve the riverside "
                "park expansion. Construction is expected to begin in March, and "
                "the budget was set at $4.2 million."
            ),
            provenance=Provenance(
                source="demo:city-gazette",
                fetched_at=now,
                confidence="grounded",
            ),
        ),
        SynthesisSource(
            content=(
                "Local radio reports the riverside park plan passed this week "
                "with broad support. The station notes the $4.2 million price "
                "tag drew criticism from the two council members who voted "
                "against it."
            ),
            provenance=Provenance(
                source="demo:river-fm",
                fetched_at=now,
                confidence="grounded",
            ),
        ),
    ]
    return (
        SynthesizeCommission(model=model),
        SynthesizeInput(sources=sources, focus="What was decided, and who dissented?"),
    )


def build_morning_briefing(
    model: str | Model | None = None,
) -> tuple[MorningBriefingCommission, MorningBriefingInput]:
    """One weather leaf plus two single-source digests, written to cwd.

    The sources are live public URLs (wttr.in, BBC World RSS, Hacker News
    RSS), so a failed fetch shows up as a partial briefing: that degradation
    path is part of what the example demonstrates.
    """
    briefing = MorningBriefingCommission(
        # j1 = JSON with current conditions plus a 3-day forecast, so the
        # weather LLM has a real outlook to report, not just a one-liner.
        weather=WeatherCommission(source_url="https://wttr.in/?format=j1", model=model),
        news=(
            NewsDigestCommission(
                field="world",
                sources=["https://feeds.bbci.co.uk/news/world/rss.xml"],
                synthesize=SynthesizeCommission(model=model),
            ),
            NewsDigestCommission(
                field="tech",
                sources=["https://hnrss.org/frontpage"],
                synthesize=SynthesizeCommission(model=model),
            ),
        ),
        summarize=SummarizeCommission(model=model),
    )
    return briefing, MorningBriefingInput(output_path=Path("morning-briefing-demo.md"))


def build_recursive_research(
    model: str | Model | None = None,
) -> tuple[RecursiveResearchCommission, ResearchInput]:
    """A cross-organization comparison sized to force decomposition.

    The question spans two projects with separate seed pages, so the root
    researcher can't answer from one fetch; the natural move is to delegate
    one sub-question per project, which makes the recursion visible in the
    trace. Depth and iterations stay low so the run finishes inside budget.
    """
    return (
        RecursiveResearchCommission(max_depth=1, model=model, max_iterations=8),
        ResearchInput(
            question=(
                "How do the Python and Rust projects differ in governance and "
                "licensing? Cover who stewards each language and under what "
                "license each is distributed."
            ),
            # Seeds must be fetch-friendly: foundation.rust-lang.org 403s
            # our fetcher, and a dead seed sends the researcher URL-guessing.
            seed_urls=[
                "https://www.python.org/psf-landing/",
                "https://docs.python.org/3/license.html",
                "https://www.rust-lang.org/governance",
                "https://www.rust-lang.org/policies/licenses",
            ],
        ),
    )
