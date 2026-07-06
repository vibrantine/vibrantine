"""Vibrantine Commissions: a component model for AI agents.

This module is the package's public boundary. Names listed in `__all__`
are the SemVer-protected surface a third party may import and depend on:

    from vibrantine import Commission, run_one, CommissionResult

The frozen surface is the *bones*: the contract envelope, the closed
`Literal` vocabularies, and the `run_one` / `invoke_sync` / `dispatch`
entry points. The `vibrantine.testing` module is also supported surface:
the test doubles for the `client=` injection seam, kept at its own import
path so test tooling never ships into production namespaces. Everything
else not in `__all__` (including the example Commissions in
`vibrantine.examples`, the tools in `vibrantine.tools`, and any
underscore-prefixed name) is internal and provisional: importable,
but not covered by the stability promise. `commission.invoke` is the
override hook authors implement, not the call API; invoke a Commission
through `run_one` / `invoke_sync` / `dispatch` so run_id stamping, overflow
enforcement, and persistence happen uniformly.
"""

from vibrantine.contract import (
    NEVER_CANCELLED,
    CallContext,
    CancelToken,
    CapabilitySet,
    Claim,
    Commission,
    CommissionResult,
    CommissionStatus,
    ConfidenceLevel,
    CostMetrics,
    ErrorKind,
    ErrorState,
    OverflowPolicy,
    PersistedRecord,
    PersistenceBackend,
    PersistenceMode,
    ProgressEvent,
    Provenance,
)
from vibrantine.dispatch import dispatch
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
]
