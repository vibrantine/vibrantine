"""Shared helpers across the std-lib tools layer.

Every tool needs to build the same shape of `Provenance` and the same
shape of failure `CommissionResult`; rather than reimplementing those
in each tool's module, the helpers live here. Module-private (`_`
prefix on the filename) so the public surface stays the tool classes
themselves.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from vibrantine.contract import (
    CommissionResult,
    CostMetrics,
    ErrorKind,
    ErrorState,
    Provenance,
)

ZERO_COST: Final[CostMetrics] = CostMetrics(estimated_usd=0.0)


class ReadFailure(Exception):
    """A classified UTF-8 read failure, carrying the standard (kind, detail).

    Raised by `read_text_utf8` and caught at each tool's own boundary, where
    it becomes a failure value; it never crosses the call boundary.
    """

    def __init__(self, kind: ErrorKind, detail: str) -> None:
        super().__init__(detail)
        self.kind: ErrorKind = kind
        self.detail = detail


def read_text_utf8(path: Path) -> str:
    """Read a file as UTF-8, raising `ReadFailure` with the standard classification.

    One shared classification (a missing file is the caller's mistake, so
    `validation`; permission and decode trouble is environment, so `internal`)
    so the file-reading tools can't silently diverge on equivalent failures.

    Decodes raw bytes rather than using `read_text`, whose universal-newline
    mode would silently rewrite CRLF/CR to LF; the returned text must stay
    faithful to the file's actual line endings.
    """
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise ReadFailure("validation", f"File not found: {path!s}.") from None
    except PermissionError as exc:
        raise ReadFailure("internal", f"Permission denied reading {path!s}: {exc}.") from exc
    except UnicodeDecodeError as exc:
        raise ReadFailure("internal", f"Could not decode {path!s} as UTF-8: {exc}.") from exc
    except OSError as exc:
        # Catch-all for I/O trouble (device errors, cloud-placeholder
        # reads): still a classified failure, so grep's directory walk
        # skips the bad entry instead of aborting the whole search.
        raise ReadFailure("internal", f"Filesystem error reading {path!s}: {exc}.") from exc


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

    Tools never cross the call boundary with an exception; this is the
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
