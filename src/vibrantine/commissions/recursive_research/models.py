"""RecursiveResearch needs a provisional model menu before the general API freezes."""

from dataclasses import dataclass

from vibrantine.models import Model


@dataclass(frozen=True)
class RecursiveResearchModelMenu:
    """The tree's LLM seats, filled by the caller at construction.

    The Commission declares its seats; the caller fills them (or none).
    `researcher` is the root's own loop; `subresearcher` is every delegated
    level below the root. An unfilled seat falls back to `default`, then to
    the system default model. Dumb data: resolution happens in `__init__`.
    """

    default: str | Model | None = None
    researcher: str | Model | None = None
    subresearcher: str | Model | None = None
