"""Shared helpers across the std-lib tools layer.

Every tool needs to build the same shape of `Provenance` and the same
shape of failure `CommissionResult`; rather than reimplementing those
in each tool's module, the helpers live here. Module-private (`_`
prefix on the filename) so the public surface stays the tool classes
themselves.
"""

from datetime import UTC, datetime
from typing import Final, cast

from vibrantine.contract import (
    CommissionResult,
    CostMetrics,
    ErrorKind,
    ErrorState,
    Provenance,
)

ZERO_COST: Final[CostMetrics] = CostMetrics(estimated_usd=0.0)


def provenance(source: str) -> Provenance:
    """Build a tool-call Provenance for `source` at the current instant."""
    return Provenance(
        source=source,
        fetched_at=datetime.now(UTC),
        confidence="grounded",
    )


def failure[OutputT](
    kind: ErrorKind,
    detail: str,
    *,
    retryable: bool,
    provenance: Provenance,
) -> CommissionResult[OutputT]:
    """Build a failure `CommissionResult[OutputT]` with zero cost.

    Tools never cross the invoke boundary with an exception; this is the
    one-liner every tool reaches for when something goes wrong.
    """
    return cast(
        "CommissionResult[OutputT]",
        CommissionResult(
            status="failure",
            error=ErrorState(kind=kind, detail=detail, retryable=retryable),
            provenance=provenance,
            cost=ZERO_COST,
        ),
    )
