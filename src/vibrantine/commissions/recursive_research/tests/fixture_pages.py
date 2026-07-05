"""Pinned fixture sources for the RecursiveResearch eval cases.

A small corpus about the fictional Meridian Vale desalination project.
Fictional on purpose: the model cannot know these facts, so every correct
fact in an eval answer must have come through a fetch. Each page carries
targets (the facts a case grades on) and, where a case needs one, a trap
(a nearby wrong answer a sloppy read would pick up).
"""

_BASE = "https://fixtures.vibrantine.test/meridian-vale"

URL_OUTPUT = f"{_BASE}/output.html"
URL_ENERGY = f"{_BASE}/energy.html"
URL_COST = f"{_BASE}/cost.html"
URL_COMMISSION_GOV = f"{_BASE}/commissioning-gov.html"
URL_COMMISSION_NEWS = f"{_BASE}/commissioning-news.html"

FIXTURE_PAGES: dict[str, str] = {
    # Target: 4,821 ML audited output. Trap: Saltgrass's 7,450 ML.
    URL_OUTPUT: (
        "Meridian Vale Desalination Project: Annual Output Report\n"
        "\n"
        "The independent audit recorded the Meridian Vale plant's 2025 "
        "production at 4,821 megalitres of drinking water. The initial 2023 "
        "projection of 6,200 megalitres was revised downward after the "
        "second intake was deferred.\n"
        "\n"
        "For comparison, the neighbouring Saltgrass plant, an older facility "
        "serving the same coastal region, produced 7,450 megalitres in the "
        "same period.\n"
    ),
    # Target: powered by the Copperline solar array.
    URL_ENERGY: (
        "Meridian Vale Desalination Project: Energy Profile\n"
        "\n"
        "The plant draws its power primarily from the dedicated Copperline "
        "solar array (220 MW nameplate), with grid backup used during "
        "extended low-irradiance periods. In 2025 the array covered 92% of "
        "the plant's electricity demand.\n"
    ),
    # Target: A$1.9 billion final cost.
    URL_COST: (
        "Meridian Vale Desalination Project: Construction Cost\n"
        "\n"
        "Final audited construction cost was A$1.9 billion, against an "
        "original budget of A$1.4 billion. The overrun is attributed mainly "
        "to marine tunnelling delays in 2022.\n"
    ),
    # Conflict pair: these two pages disagree on the operational date.
    URL_COMMISSION_GOV: (
        "State Water Authority: Commissioning Notice\n"
        "\n"
        "The Meridian Vale desalination plant was formally commissioned and "
        "declared operational in March 2024.\n"
    ),
    URL_COMMISSION_NEWS: (
        "Coastal Times: Meridian Vale's Slow Start\n"
        "\n"
        "Despite the official March 2024 commissioning ceremony, plant "
        "records show Meridian Vale did not sustain full-capacity operation "
        "until November 2025, following repeated membrane failures in its "
        "first year.\n"
    ),
}
