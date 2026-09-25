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

Two request shapes:
- **Single crew member**: `{"crew_id": "crew-01", "question": "..."}` —
  scoped to that person only. The model cannot be tricked into reading a
  different crew member's data via a tool argument; this is a real
  access-control boundary enforced in code, not just prompted for.
- **Fleet-wide triage** (use case 3): omit `crew_id` entirely — e.g.
  `{"question": "who needs review today?"}`. The model calls `list_crew`
  and checks active domains across the real roster, then composes a
  briefing grouped by crew member, prioritized by risk. Every
  `(crew_id, domain_id)` pair in the answer is backed by a real fetch —
  nothing is fabricated just because a name exists in the roster.

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
  once reached, the endpoint returns 429 until the next day. This is
  still a blunt instrument (global, not per-user) even though real
  accounts now exist — per-user quotas would need the rate limiter
  itself to key off the authenticated user, which hasn't been wired up
  yet (see Roadmap).
- Conversation history is capped to the most recent `MAX_HISTORY_MESSAGES`
  (20) turns per call, so a long-running conversation doesn't grow the
  request (and cost) unboundedly.
- Every call is logged (`GET /api/agent/usage`) with crew ID, question
  length, how many tools were called, and whether memory recall was
  used — real observability, not just "hope it's fine." Logs metadata
  only, not conversation content.

**What's still missing**, stated plainly: the agent endpoint itself
doesn't yet require a real login (any gate-passed session can call it),
no per-user rate limits (still global, see above), no systematic testing
of the model's live behavior beyond the cases in `test_agent.py` and
manual spot-checks, no monitoring/alerting on top of the usage log. See
`docs/ARCHITECTURE.md` for the honest production-readiness assessment.

## Clinical relay & response (near-real-time alerting)

`backend/app/core/monitoring.py` — a PUSH-based complement to the PULL-based
dashboard/agent. It continuously (on-demand sweep, `POST /api/monitoring/check`)
compares each crew member's current trajectory against their last known
state, and raises an alert the moment a *new* clinically significant
change is detected — crossing into an elevated severity tier, not
re-alerting on an already-known state every sweep.

**The goal is reducing clinical response latency, not physical
communication latency** — RofehAI can't make Mars closer to Earth, but it
can detect, package, and queue an alert within milliseconds so it's ready
to transmit the instant a link exists, rather than waiting for a human to
notice, interpret, and describe the problem themselves.

- `POST /api/monitoring/check` — run a detection sweep now
- `GET /api/alerts?status=pending|transmitted|acknowledged` — list alerts
- `GET /api/alerts/{id}` — one alert's full evidence package (baseline
  deviation, trend, confidence, severity, top contributors, and a
  pre-authorized protocol reference if the domain has one)
- `POST /api/alerts/{id}/acknowledge` — the human-in-the-loop point; a
  clinician confirms review. Requires the alert to have transmitted first
  — you cannot acknowledge something that was never sent.
- `GET /api/comms/link` / `POST /api/comms/link {"available": bool}` —
  the **simulated** Earth-spacecraft comms link. **Stated plainly: this is
  a manual toggle standing in for real connectivity — there is no actual
  radio link in this reference implementation.** While unavailable, alerts
  queue locally; the moment it's set available, every pending alert
  transmits immediately in priority order (highest severity first).
- `GET /api/alerts/metrics/latency` — real, measured stats from actual
  timestamps, reporting RofehAI's own detection-to-queue time **separately**
  from comms latency, per the feature's own success-metric requirement.

**Content discipline is identical to the rest of the platform**: every
field in an alert is copied verbatim from a real `RiskOutput` — no free
LLM or hand-typed text at alert time. Pre-authorized protocols are a
fixed, reviewed-ahead-of-time catalog (see `domains.py`), explicitly
labeled "not autonomous treatment — clinician confirmation still
required" on every response; RofehAI never executes or auto-administers
anything.

**What's honestly not built**: a real trigger (this requires manually
calling `/api/monitoring/check`; production would run it on a schedule or
on new-observation events), any actual notification mechanism (push,
SMS, radio) beyond the API surfacing "transmitted" status, and the
`authorized_by` field on protocols is an explicit placeholder pending
real clinical sign-off, not a claim that these protocols have been
validated by an actual flight surgeon.

## What the dashboard shows

- **Crew roster** with a live risk-status indicator per person
- **SANS risk trajectory** and **immune dysregulation risk** — current
  level, 30-day trend, confidence, and which signals are driving it
- **Signal trend chart** — optic nerve sheath diameter and EBV shedding
  over the mission
- **Countermeasure suggestions** — never auto-applied; a person reviews
  and explicitly logs when one is used, which is what closes the
  learning loop back into that person's own model
- **"Ask the briefing agent"** — a per-crew-member chat panel calling
  the agent described above, with its own persistent conversation
- **"Fleet-wide triage"** (sidebar) — a separate chat thread for
  crew-id-less questions like "who needs review today?"
- **"Clinical alerts"** (sidebar) — comms link status/toggle, a manual
  sweep trigger, and a live pending-alert count badge; "View alerts"
  opens the full evidence-package list with the acknowledge flow — see
  the Clinical Relay section above

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

  **Real bug, found and fixed**: for most of one day's development, the
  SQLite database had no Docker volume at all — it lived inside the
  backend container's own filesystem, so every `docker compose build`
  silently wiped it completely (every account, alert, and conversation
  created that day was lost on the next rebuild, with no error or
  warning). Fixed by moving the database into its own `data/`
  subdirectory (isolated from application code) and mounting a real
  named Docker volume there — confirmed by directly simulating a
  rebuild (create data in one process, start a completely fresh process
  against the same volume, confirm the data is still there) rather than
  just trusting the YAML looked right. **If you're running an older
  deployment from before this fix, your database was never actually
  persisted** — back up anything you need before pulling this update,
  since the schema/location changed.

- **Chart.js is vendored locally** (`frontend/js/vendor/chart.umd.js`)
  rather than loaded from a CDN — an early deployment had the dashboard's
  trend chart silently fail to render for some visitors because their
  browser/network blocked the external CDN script. Self-hosting removes
  that failure mode entirely for every future visitor.
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
- **Access gate — real per-user accounts**: `frontend/gate.html` is a
  username/password login form (with NDA acknowledgment and a
  show/hide password toggle), backed by real accounts in
  `backend/app/core/user_auth.py`: bcrypt-hashed passwords, server-side
  sessions with opaque (non-JWT) tokens that can be immediately and
  completely revoked on sign-out — confirmed by direct testing that a
  signed-out token no longer resolves to a user at all, not just that
  the browser forgot it.

  **No public signup.** Accounts are created by an admin via
  `POST /api/auth/users`, protected by `ROFEHAI_ADMIN_KEY` — a
  **separate** env var from `ROFEHAI_API_KEY` on purpose: reusing the
  same variable would mean setting a key just to create accounts also
  locks the entire public dashboard behind that key, since
  `ROFEHAI_API_KEY` (if set) requires every `/api/*` request to carry
  it. This exact coupling bug was caught by running the full login flow
  live before shipping it, not assumed away.

  **Real attribution, not spoofable**: countermeasure logs and alert
  acknowledgments use the authenticated user's identity automatically
  (sent via `Authorization: Bearer <token>` on every request) — a
  logged-in user's real name always overrides any name submitted in the
  request body, confirmed by a test that attempts exactly that spoof.

  Every login attempt — NDA acknowledged or not, credentials correct or
  not — is logged server-side (`db.GateAccessLog`, tied to the attempted
  username and, on success, the real user id). The password itself is
  never stored in plaintext or logged anywhere.

  **What's still honestly not built**: no password reset flow (but see
  self-service change below — a logged-in user CAN update their own
  password), no roles/permissions (every account can see everything), no
  rate limiting on login attempts specifically (the agent's separate rate
  limit doesn't cover this), no self-service signup (by design, for
  now). The old shared-passcode endpoint (`POST /api/gate/check`) still
  exists in the code for backward compatibility but is no longer used by
  the frontend.

  **Self-service password change**: once logged in, a "Change password"
  link in the dashboard lets a user set a new password themselves —
  requires the current password (not just a valid session token, so a
  leftover/stolen session alone can't lock the real owner out). Verified
  end-to-end: after a change, the old password genuinely stops working
  and the new one works, not just that the endpoint returned success.

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
