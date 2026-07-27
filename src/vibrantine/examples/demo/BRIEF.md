# Demo

The runnable front door of the examples package, and the worked example of
the *application layer*: everything the library refuses to own has to live
somewhere, and this package shows where.

Run it:

```
uv run --env-file .env python -m vibrantine.examples
```

Type a menu number to run one example directly with canned inputs, or type
anything else to chat with the demo agent, an LLM-loop Commission whose
toolbox holds the four examples, so a chat message can trigger real
Commission runs. Every run streams progress events and completion lines
while it works, then prints a cost trace tree built from the persisted
records. `--model <id>` targets any OpenRouter model; `--ollama <name>`
targets a local Ollama model with no key or cost at all.

## What each file demonstrates

- `runner.py`: the application layer's obligations, concretely: the secret
  check, model choice, spending policy (tiered per-run budget caps),
  conversation state (the session transcript), and presentation. All of it
  rides the public contract: construct with config, build a typed input,
  enter through `run_commission`, then read the envelope and records.
- `catalog.py`: the canned setups. Construction config and inputs are
  caller concerns, so they live here with the caller; the example modules
  stay pure worked examples with no demo scaffolding in them.
- `agent.py`: LLM-decided control flow at the top of a tree. The agent's
  toolbox holds the example Commissions, so their LLM-facing `description`
  prose gets a real LLM caller, the first genuine test of the description
  discipline as a routing surface.
- `trace.py`: observability as a pure consumer. A `PersistenceBackend` that
  narrates each stored record gives live completion events; the finished
  records render as a per-node cost tree. If the trace can't be built from
  `PersistedRecord`s alone, the record shape is what should improve.

## Boundaries held

- Public contract only. No private helper, no framework hook.
- The catalog's canned runs are sized to finish at a fraction of their
  budget caps; a cap that fires means misbehavior, not a planned stop.
- Conversation memory belongs to the runner, not the agent: the transcript
  is threaded through the typed input each turn (state stays above the
  library).
