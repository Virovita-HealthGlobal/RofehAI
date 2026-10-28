# H-MEM in RofehAI

RofehAI uses the **Meterless H-MEM engine** (persistent, tiered, auditable memory) so the briefing
agent remembers earlier sessions.

- `reference/` : Meterless's open-source H-MEM reference implementation, vendored **unmodified**
  (source: github.com/Meterless/Meterless, `engines/hmem/reference`, Apache-2.0, see `LICENSE`).
- `conformance/` : Meterless's conformance suite and vectors. Result against this code: see `CONFORMANCE.txt` (13/13).
- `service/server.ts` : our thin HTTP wrapper (`/add`, `/query`, `/feedback`, `/ledger`, `/health`), state persisted to a Docker volume (`state.json` + append-only `ledger.jsonl`).
- `backend/app/core/hmem_client.py` : Python client used by the agent.
- `backend/app/core/agent.py` (`_apply_hmem`) : the feature.

## The working feature
After each per-crew-member briefing the agent **remembers** that person's current risk values
(code-built text from real fetched data, tagged by crew id and domain). In a **new session**
it **recalls** them through H-MEM's hybrid retrieval and adds a block to the answer, e.g.
`SANS: 82% at 2026-10-28 14:02 UTC -> 90% now (+8 pts)`.
Memories are scoped per crew member (another crew member never sees them) and never contain model-written
text. If the memory service is down, the agent still answers without the block.

## Evidence
`backend/tests/test_agent_hmem_standalone.py` runs the real engine over HTTP: session 1 stores, session 2 (new conversation)
recalls and shows the change, crew isolation holds, and the agent degrades gracefully when the service is down.
The H-MEM ledger records every create/read (`GET /ledger`), providing the trust-ledger audit trail.

## Not used (honest scope)
Mining, dreaming, sleep cycle and conflict resolution exist in the engine but are not wired into the product yet.
