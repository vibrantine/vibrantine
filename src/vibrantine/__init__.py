"""Vibrantine Commissions: a component model for AI agents.

This module is the package's public boundary. Names listed in `__all__`
are the SemVer-protected surface a third party may import and depend on:

    from vibrantine import Commission, run_commission, CommissionResult

The frozen surface is the *bones*: the contract envelope, the closed
`Literal` vocabularies, the `run_commission` / `run_commission_sync` / `dispatch`
entry points, and the authoring-edge names a Commission author is taught
to use (`estimate_tokens`, `deposit_llm_trace`, `DEFAULT_MAX_ITERATIONS`,
and the `ContentPart` message vocabulary). Two honesty notes on that
promise. The model vocabulary's *shapes* are protected, but the *contents*
of `DEFAULT_MODEL` (its id, pricing, context window) are catalog data:
they change as models come and go, without a major version. And the
non-text `ContentPart` members are provisional in their fields: `ImagePart`
and `AudioPart` today, and any later modality part (a video or document part)
when its consumer arrives, keeps its exact fields open until a real consumer
fixes them. What is stable is the union itself: its name, its role, and that
new modality parts join it additively. `TextPart`'s fields are settled.
The `vibrantine.testing` module is also supported surface:
the test doubles for the run catalog's client-vending seam
(`scripted_model` and friends), kept at its own import
path so test tooling never ships into production namespaces. Everything
else not in `__all__` (including the example Commissions in
`vibrantine.examples`, the tools in `vibrantine.tools`, and any
underscore-prefixed name) is internal and provisional: importable,
but not covered by the stability promise. `Commission._run` is the
override hook a custom Commission implements; the leading underscore marks
it as the framework's to call. Invoke a Commission through `run_commission` /
`run_commission_sync` / `dispatch` so run_id stamping, overflow enforcement, and
persistence happen uniformly.

This surface is minimized mercilessly. Every name in `__all__` is a
permanent claim on a user's memory, so the list grows only under pressure
from a real, named consumer, never for convenience; when something can be
solved with interior code instead of a new name, the interior wins. An
exact lock test (`tests/test_public_api.py`) makes any growth a deliberate
act. See `docs/design-decisions.md § The public surface is minimized
mercilessly`.
"""

from vibrantine.contract import (
    DEFAULT_MAX_ITERATIONS,
    NEVER_CANCELLED,
    AudioPart,
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
    Model,
    ollama,
    openai_compatible,
)
from vibrantine.orchestrator import run_commission, run_commission_sync
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
    "DEFAULT_MODEL",
    "openai_compatible",
    "ollama",
    # Entry points
    "run_commission",
    "run_commission_sync",
    "dispatch",
    # Authoring
    "create_commission",
    # Authoring edge: names custom and basic Commissions build against
    "ContentPart",
    "TextPart",
    "ImagePart",
    "AudioPart",
    "DEFAULT_MAX_ITERATIONS",
    "estimate_tokens",
    "deposit_llm_trace",
]
