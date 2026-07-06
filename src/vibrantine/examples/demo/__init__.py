"""The demo package: a worked example of the application layer above the library.

The library refuses to own conversation, state, secret wiring, spending
policy, and presentation; this package shows where they live instead. It
holds the chat agent (`agent.py`), the observability consumers (`trace.py`),
and the CLI runner (`runner.py`) behind `python -m vibrantine.examples`.
"""

from vibrantine.examples.demo.agent import (
    ChatInput,
    ChatOutput,
    ChatTurn,
    DemoAgentCommission,
    demo_agent,
)
from vibrantine.examples.demo.runner import main
from vibrantine.examples.demo.trace import (
    RecordingBackend,
    render_trace,
    render_transcript,
    trace_order,
)

__all__ = [
    "ChatInput",
    "ChatOutput",
    "ChatTurn",
    "DemoAgentCommission",
    "RecordingBackend",
    "demo_agent",
    "main",
    "render_trace",
    "render_transcript",
    "trace_order",
]
