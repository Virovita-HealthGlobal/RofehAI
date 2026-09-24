# RofehAI

![RofehAI](brand/logo.png)

**RofehAI is one personalized astronaut health-intelligence platform,
not a collection of separate AI medical applications.** It continuously
learns each crew member's own physiological trends across multiple
health domains — SANS (Spaceflight-Associated Neuro-Ocular Syndrome) and
immune dysregulation are fully active today; seven further domains
(cardiovascular, musculoskeletal, radiation, sleep/circadian, cognitive,
behavioral, emergency/general) are registered as honestly-labeled
architecture-ready placeholders — to surface risk before it becomes an
emergency, without needing real-time contact with Earth.

This repository is a working reference implementation of the architecture
described in the RofehAI concept document and draft patent application:
a domain registry, an individualized baseline ("digital twin"), a
continuous trajectory-based risk model shared across every domain,
confidence-gated fallback, and a human-in-the-loop countermeasure
suggestion flow.

> **Read this before anything else:** this repo runs on **synthetic demo
> data** and a **reference statistical model**, not a trained neural
> network validated on real astronaut data. It demonstrates the
> architecture end to end so it can be evaluated, extended, and eventually
> connected to real data and a real trained model. **Do not use any output
> from this repository to inform a real medical decision.** See
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the honest breakdown
> of what's real vs. reference, and the roadmap to close that gap.

## What's in this repo

```
rofehai-platform/
├── backend/            FastAPI service — domain registry, digital twin, trajectory
│                        model, confidence scoring, and countermeasure logic
├── frontend/            Static HTML/CSS/JS mission dashboard
├── docs/
│   ├── ARCHITECTURE.md  Maps this code to the concept doc / patent draft
│   └── DOMAIN.md        rofehai.com vs rofehai.io recommendation
├── docker-compose.yml
└── LICENSE.md           Proprietary — see notice before reusing anything
```

## Quickstart (local, no Docker)

**Backend**
```bash
cd backend
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```
API docs at `http://localhost:8000/docs`.

**Frontend** (in a second terminal)
```bash
cd frontend
python3 -m http.server 8080
```
Dashboard at `http://localhost:8080`. It talks to the backend at
`http://localhost:8000/api` by default — change this in
`frontend/js/config.js` if your backend runs elsewhere.

**Run backend tests**
```bash
cd backend
PYTHONPATH=. pytest tests/ -v
```

## Quickstart (Docker)

```bash
docker compose up --build
```
- Backend: `http://localhost:8000`
- Frontend: `http://localhost:8080`

Note: the containerized frontend still points at `http://localhost:8000`
by default (see `frontend/js/config.js`); adjust for your deployment
target (e.g. a real `api.rofehai.com`) before shipping.

## Briefing agent (`POST /api/agent/ask`)

An LLM (Claude) that reasons across a crew member's active health domains
and produces a single, cited briefing — instead of clicking through
separate risk cards one at a time. It has genuine multi-step tool use
(it decides which domains to check) and persistent memory (a
`conversation_id` lets a follow-up question like "did the countermeasure
help?" reference what was discussed before, without re-stating context).

**The safety design, in one sentence:** the model never writes the final
clinical text itself — it can only select which already-fetched
`RiskOutput`/`Countermeasure` data to include, and the backend assembles
the actual sentences deterministically. See the module docstring in
`backend/app/core/agent.py` for the full reasoning, and
`backend/tests/test_agent.py` for the adversarial tests that verify it
(fabricated domains, invalid tone injection, and a real end-to-end
persistent-memory round trip are all covered).

**Requires `ANTHROPIC_API_KEY`** (see `.env.example`) — without it, this
one endpoint returns 503; everything else in RofehAI runs fine without
it.

**Cost/abuse controls**, since every call spends real money against your
Anthropic account:
- A global daily call cap (`ROFEHAI_AGENT_DAILY_LIMIT`, default 100) —
  once reached, the endpoint returns 429 until the next day. This is a
  blunt instrument (global, not per-user), appropriate for a shared-
  passcode demo — real per-user quotas need real accounts, which don't
  exist yet (see Roadmap).
- Conversation history is capped to the most recent `MAX_HISTORY_MESSAGES`
  (20) turns per call, so a long-running conversation doesn't grow the
  request (and cost) unboundedly.
- Every call is logged (`GET /api/agent/usage`) with crew ID, question
  length, how many tools were called, and whether memory recall was
  used — real observability, not just "hope it's fine." Logs metadata
  only, not conversation content.

**What's still missing**, stated plainly: no per-user auth (the shared
gate passcode is the only access control), no systematic testing of the
model's live behavior beyond the cases in `test_agent.py` and manual
spot-checks, no monitoring/alerting on top of the usage log. See
`docs/ARCHITECTURE.md` for the honest production-readiness assessment.

## What the dashboard shows

- **Crew roster** with a live risk-status indicator per person
- **SANS risk trajectory** and **immune dysregulation risk** — current
  level, 30-day trend, confidence, and which signals are driving it
- **Signal trend chart** — optic nerve sheath diameter and EBV shedding
  over the mission
- **Countermeasure suggestions** — never auto-applied; a person reviews
  and explicitly logs when one is used, which is what closes the
  learning loop back into that person's own model

## Regulatory positioning (read this if you're extending the API)

Per the concept document, RofehAI is scoped as **clinical decision
support**, not autonomous diagnosis or treatment. Concretely, in code:

- The API only ever *suggests* a countermeasure (`countermeasures.recommend`)
  — it never calls an action that "administers" or "schedules" one.
- Logging a countermeasure as used (`POST /api/countermeasures/log`) is a
  distinct, explicit action a human takes — the system doesn't infer it.
- Output language stays in the "surface / flag / prioritize" register
  (see `RiskOutput.narrative`), not "diagnose / confirm / conclude."

If you're adding a feature, keep it on this side of the line, and get
qualified regulatory counsel involved before changing anything here for
a real deployment — see the concept document's regulatory section for
the full reasoning.

## Domain

Short answer: **use `rofehai.com`** as the primary domain; reserve
`rofehai.io` as a defensive/secondary registration rather than putting
anything load-bearing on it. Full reasoning in
[`docs/DOMAIN.md`](docs/DOMAIN.md).

## Deployment (NVIDIA Inception / AWS Activate / Google Cloud for Startups)

If you're standing up real (non-local) infrastructure and have access to
these startup programs, see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
for a concrete, non-overcommitted recommendation on which program to use
for which piece (hosting vs. model training vs. edge deployment), and
why splitting across all three from day one isn't recommended yet.

## Roadmap

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the prioritized
list of what turns this from a working reference implementation into
the real thing: real NASA dataset integration, a trained sequence model
with a reported real accuracy figure, the asynchronous Earth-feedback
loop, and NVIDIA Jetson Orin edge deployment.

## Engineering hardening included so far

Added on top of the initial reference implementation, still well short of
"production ready" (see the honest gap analysis further below and in
`docs/ARCHITECTURE.md`):

- **Persistence**: observations and countermeasure-log entries are stored
  via SQLModel/SQLAlchemy (`backend/app/core/db.py`) and survive a
  restart. Defaults to a local SQLite file for zero-setup local dev.
  **Postgres is a supported, tested target** — set `ROFEHAI_DATABASE_URL`
  to a Postgres URL (e.g. `postgresql://user:pass@host:5432/rofehai`,
  such as an AWS RDS instance per `docs/DEPLOYMENT.md`) and no code
  changes are needed. This was verified directly against a real local
  Postgres 16 instance (table creation, insert via the API, and an
  independent `SELECT` confirming the rows), not just assumed to work
  because the ORM supports it in theory.
  **Breaking schema change (multi-domain refactor):** observations are now
  stored as a JSON `signals` column instead of one hardcoded SQL column
  per signal, matching the generalized `Observation.signals` schema. Any
  existing `rofehai.db` (or Postgres database) from before this change
  needs a clean reset — delete the SQLite file, or drop and let
  `init_db()` recreate the table — since this is synthetic demo data with
  no real users to migrate.
- **Input validation**: `Observation` fields have realistic clinical
  ranges (e.g. ONSD 2–12mm); out-of-range or unknown-crew submissions
  are rejected with a clear 4xx, not silently accepted or a 500.
- **Basic auth**: set `ROFEHAI_API_KEY` in the environment to require an
  `X-API-Key` header on every `/api/*` request. Unset (the local-dev
  default) means auth is **off** — you must set this before deploying
  anywhere reachable outside your own machine.
- **CI**: `.github/workflows/backend-tests.yml` runs the backend test
  suite on every push/PR.
- **Error handling**: unhandled exceptions return a generic 500 without
  leaking stack traces; the real error is logged server-side.
- **Access gate**: `frontend/gate.html` is a combined About + NDA
  acknowledgment + passcode page shown before the dashboard
  (`frontend/index.html` redirects here if the session hasn't passed it).
  This is a **soft, session-scoped gate for a link-shared demo** — it
  keeps casual/unintended visitors out and gives every visitor the
  context a cold landing on the dashboard didn't provide (what this is,
  who it's for, how to use it). It is **not** a real per-user
  authentication system: there's one shared passcode
  (`ROFEHAI_GATE_CODE` env var, default `rofehai-preview-2026` — change
  this before sharing the link), no individual accounts, and the
  check is only as strong as keeping that one passcode private. Real,
  per-user accounts (individual credentials, sessions, roles) are a
  separate, larger piece of work — see Roadmap.

## Disclaimers

- Not a medical device. Not FDA-cleared or reviewed by any regulatory
  body. Not for real clinical use.
- All physiological data in this repository's default configuration is
  synthetically generated (`backend/app/data/synthetic.py`) and does not
  represent real individuals or real measurements.
- This repository and its contents are proprietary — see `LICENSE.md`.
  A patent application covering aspects of this system is in
  preparation; do not distribute outside parties bound by an appropriate
  confidentiality agreement without authorization.
