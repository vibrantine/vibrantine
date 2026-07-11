"""RecursiveResearch needs a provisional model menu before the general API freezes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RecursiveResearchModelMenu:
    """The tree's LLM seats, filled by the caller at construction.

    The Commission declares its seats; the caller fills them (or none).
    `researcher` is the root's own loop; `subresearcher` is every delegated
    level below the root. An unfilled seat falls back to `default`, then to
    the run's default model. Seats are pure names resolved against the run
    catalog (the distributed model *choice*; the Model objects live in
    `run_one(models=[...])`). Dumb data: seat assignment happens in
    `__init__`.
    """

    default: str | None = None
    researcher: str | None = None
    subresearcher: str | None = None
