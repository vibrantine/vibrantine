"""Vibrantine Commissions: a component model for AI agents.

This module is the package's public boundary. Names listed in `__all__`
are the SemVer-protected surface a third party may import and depend on:

    from vibrantine import Commission, run_one, CommissionResult

The frozen surface is the *bones*: the contract envelope, the closed
`Literal` vocabularies, the `run_one` / `invoke_sync` / `dispatch`
entry points, and the authoring-edge names a Commission author is taught
to use (`estimate_tokens`, `deposit_llm_trace`, `DEFAULT_MAX_ITERATIONS`,
and the `ContentPart` message vocabulary). Two honesty notes on that
promise. The model vocabulary's *shapes* are protected, but the *contents*
of `KNOWN_MODELS` and the id behind `DEFAULT_MODEL` are catalog data:
they change as models come and go, without a major version. And
`ImagePart`'s exact fields are provisional until the first image-bearing
consumer fixes them (its name and role in `ContentPart` are stable).
The `vibrantine.testing` module is also supported surface:
the test doubles for the `client=` injection seam, kept at its own import
path so test tooling never ships into production namespaces. Everything
else not in `__all__` (including the example Commissions in
`vibrantine.examples`, the tools in `vibrantine.tools`, and any
underscore-prefixed name) is internal and provisional: importable,
but not covered by the stability promise. `commission.invoke` is the
override hook authors implement, not the call API; invoke a Commission
through `run_one` / `invoke_sync` / `dispatch` so run_id stamping, overflow
enforcement, and persistence happen uniformly.

This surface is minimized mercilessly. Every name in `__all__` is a
permanent claim on a user's memory, so the list grows only under pressure
from a real, named consumer, never for convenience; when something can be
solved with interior code instead of a new name, the interior wins. An
exact lock test (`tests/test_public_api.py`) makes any growth a deliberate
act. See `docs/design.md § The public surface is minimized mercilessly`.
"""

from vibrantine.contract import (
    DEFAULT_MAX_ITERATIONS,
    NEVER_CANCELLED,
    CallContext,
    CancelToken,
    CapabilitySet,
    Claim,
    Commission,
    CommissionResult,
    CommissionStatus,
    ConfidenceLevel,
    ContentPart,
    CostMetrics,
    ErrorKind,
    ErrorState,
    ImagePart,
    OverflowPolicy,
    PersistedRecord,
    PersistenceBackend,
    PersistenceMode,
    ProgressEvent,
    Provenance,
    TextPart,
    estimate_tokens,
)
from vibrantine.dispatch import deposit_llm_trace, dispatch
from vibrantine.factory import create_commission
from vibrantine.models import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    Model,
    ollama,
    openai_compatible,
)
from vibrantine.orchestrator import invoke_sync, run_one
from vibrantine.persistence import FilesystemBackend, SqliteBackend

__all__ = [
    # Contract: the Commission and its result envelope
    "Commission",
    "CommissionResult",
    "CommissionStatus",
    # Runtime conditions
    "CallContext",
    "CapabilitySet",
    "CancelToken",
    "NEVER_CANCELLED",
    "ProgressEvent",
    # Provenance, claims, cost
    "Provenance",
    "ConfidenceLevel",
    "Claim",
    "CostMetrics",
    # Failure model
    "ErrorState",
    "ErrorKind",
    # Policy vocabularies
    "OverflowPolicy",
    "PersistenceMode",
    # Persistence
    "PersistedRecord",
    "PersistenceBackend",
    "FilesystemBackend",
    "SqliteBackend",
    # Models: the vocabulary callers use to pick or build a model target
    "Model",
    "KNOWN_MODELS",
    "DEFAULT_MODEL",
    "openai_compatible",
    "ollama",
    # Entry points
    "run_one",
    "invoke_sync",
    "dispatch",
    # Authoring
    "create_commission",
    # Authoring edge: names custom and basic Commissions build against
    "ContentPart",
    "TextPart",
    "ImagePart",
    "DEFAULT_MAX_ITERATIONS",
    "estimate_tokens",
    "deposit_llm_trace",
]
