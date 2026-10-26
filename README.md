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

Note: the frontend is configured to talk to `https://api.rofehai.com/api` by default (see `frontend/js/config.js`).

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

## Voice access & conversation audit trail

The briefing agent is reachable as chat **and voice**, from any device
with a modern browser and an internet connection (including from
Earth — it's a hosted web page, not a local app; no VPN, pairing, or
special client required).

- **Voice input**: browser-native `SpeechRecognition`
  (`webkitSpeechRecognition` on Chrome/Safari) — a mic button appears
  next to both chat inputs (per-crew and fleet-wide) when the browser
  supports it, and is silently omitted when it doesn't. Speech is
  transcribed client-side into the existing text input; nothing new is
  sent to the backend, no third-party speech API is involved.
- **Voice output**: browser-native `speechSynthesis`, opt-in via a
  "Speak answers aloud" toggle in the header. Off by default.
- **Deliberately out of scope for now**: no telephony/SMS access, no
  offline mode, no dedicated mobile app — all scoped out to keep the
  voice feature client-side and zero-infrastructure. See Roadmap.
- **Conversation audit trail** (`GET /api/agent/audit-log`, admin-key
  protected): every `/api/agent/ask` call now stores the full question,
  full answer, and the logged-in user's display name (`actor_name`)
  alongside the existing usage metadata (crew ID, tool calls, memory
  recall), in the same `AgentCallLog` table. Viewable from the Agent &
  System Metrics panel.
- **On the "HIPAA" question, stated plainly**: this is a real audit
  trail (who asked what, when, and what came back) — it is **not** a
  HIPAA-compliant system. All data in this repo's default configuration
  is synthetic, there's no BAA, no encryption-at-rest attestation, no
  per-admin identity (the audit log is gated by one shared admin key,
  not individual accounts), and no retention/deletion policy on
  transcripts. Treat this as the foundation a real compliance program
  would build on, not a finished one.

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

## Marketing landing page vs. the dashboard

`rofehai.com` and `app.rofehai.com` now serve genuinely different content
from the same frontend container, via host-based routing in
`frontend/nginx.conf`:

- **`rofehai.com` / `www.rofehai.com`** → `frontend/marketing/index.html`,
  a standalone landing page (own hero, own copy, own trajectory-chart
  SVG) with "Request demo access" and "Sign in" both linking to
  `app.rofehai.com`. Self-contained — its own copy of the logo assets,
  not sharing paths with the dashboard, since nginx scopes its root
  separately.
- **`app.rofehai.com`, and any other/unrecognized hostname** → the
  existing dashboard (gate/login → crew roster, unchanged).

This was verified directly, not just assumed from reading the config: a
real nginx instance was started locally with the exact file layout
Docker builds, and `curl` with different `Host` headers confirmed each
hostname actually returns its own distinct page.

The marketing page also has: a "Recognized by" section with the real
NVIDIA Inception and CAI (Center for Advancing Innovation) badge assets
(the official kit, not a screenshot), plus an AWS Startups text chip
pending a real badge asset; `privacy.html` and `terms.html` — genuine,
specific drafts (not filler) describing exactly what this site collects
and does, explicitly labeled as pending real attorney review, matching
the same honesty pattern as the patent draft elsewhere in this project;
and `contact.html`, a general-inquiry form (`POST /api/contact`) that
emails the same admin recipients as signup approvals — verified live
that a real routing test confirms every new page and asset actually
loads under `rofehai.com`, and `app.rofehai.com` remains unaffected.

## Mission-day slider and crew renaming

- **Mission-day slider** (dashboard header): scrubs the currently-viewed
  crew member's risk cards, chart, and countermeasures to "as of" any
  day from 30 to 210, using the `mission_day` parameter the scoring
  endpoints already supported. Live-updates the label while dragging,
  only re-fetches on release (not per-tick), verified end-to-end with a
  real session token and real API calls intercepted to confirm the
  correct `mission_day` value on each release. The roster's own
  status dots always reflect the full mission length, unaffected by the
  slider, to avoid confusing side effects on the sidebar.

- **Crew renaming** (`PUT`/`DELETE /api/crew/{id}/name`, admin-key
  protected): relabels one of the four synthetic crew slots — e.g. for
  a live pilot session with real reviewers — WITHOUT touching the
  underlying synthetic health data, which stays keyed by the original
  `crew_id`. **Deliberately not full crew CRUD**: adding or removing
  crew members was scoped out on purpose, since each existing member has
  a purpose-built synthetic history and a new one would either show
  "insufficient data" for weeks of simulated time or require an
  on-demand history generator — real work for a feature that doesn't
  strengthen what the demo is actually trying to prove. A real gap was
  found and fixed while building this: the fleet-wide agent built its
  own crew-name lookup directly from the static roster, bypassing any
  rename — a renamed crew member would show correctly in the sidebar but
  the *old* name in an agent answer. Fixed, with a regression test.

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
- **Agent & System Metrics** (sidebar) — agent call volume today,
  clinical alert relay latency (detection to queued to transmitted to
  clinician acknowledged, all measured not simulated), a recent-calls
  table, and the admin-gated full conversation audit log described
  above
- **About / how to use** (`about.html`) — standalone page covering what
  the demo is, who it's for, what it's not, and how to use it; kept
  separate from the minimal gate/login screen so the sign-in page stays
  a sign-in page

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

## Infrastructure (DNS / TLS)

DNS for `rofehai.com` is managed directly through Hostinger's own
nameservers (not Cloudflare) — `A` records for the bare domain, `app.`,
and `api.` all point at the VPS. TLS is issued and auto-renewed by
Certbot/Let's Encrypt against the host's own nginx
(`/etc/nginx/sites-available/rofehai.com`), the same pattern used for
the other products on this VPS. `cloudflared` is still installed but no
longer load-bearing for this domain — traffic no longer depends on a
Cloudflare Tunnel. This setup was chosen after a Cloudflare account
lockout made DNS changes impossible mid-incident; moving to a DNS
provider tied to the domain registrar itself avoids that single point
of failure going forward.

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

**Near-term, not yet done**: per-admin identity for the audit log and
admin-key-gated actions generally, instead of one shared key; a
native/qualified Russian speaker's review of the templated Russian
narratives before use beyond internal testing (see `i18n.py`); more
languages beyond Russian.

**Authentication is single-factor** (password + server-issued session
token) -- there is no second factor (email code, authenticator app, or
similar) required on login itself. The existing single-use 6-digit
email code is used only for initial password setup and password reset,
not as an MFA step on every login. This is a deliberate scope decision
for the concept-stage/synthetic-data demo, not an oversight -- a real
deployment handling real health data would need MFA as a baseline
requirement before go-live.

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

  **Two ways to get an account.** An admin can create one directly via
  `POST /api/auth/users`, protected by `ROFEHAI_ADMIN_KEY` — a
  **separate** env var from `ROFEHAI_API_KEY` on purpose: reusing the
  same variable would mean setting a key just to create accounts also
  locks the entire public dashboard behind that key, since
  `ROFEHAI_API_KEY` (if set) requires every `/api/*` request to carry
  it. This exact coupling bug was caught by running the full login flow
  live before shipping it, not assumed away.

  **Self-service signup, email-only, gated by real admin approval**
  (`gate.html`'s "Request access" link, or `POST /api/auth/signup`):
  collects ONLY a professional email address, display name, and NDA
  acknowledgment — **no password at signup time**. The account is
  created immediately but starts **inactive with no password hash at
  all**, and genuinely cannot log in under any password until two
  separate things happen: an admin approves, AND the requester enters a
  6-digit code (emailed to them) alongside their email to set their own
  password.

  The admin is notified by email (via SMTP through your existing
  mailbox) containing the
  requester's email, display name, and NDA-acknowledgment timestamp —
  never a password, since none exists yet. Approve/Reject are one-click
  links straight from that email (each a single-use token — clicking
  Approve twice does nothing the second time, confirmed by a test).
  **Approving does not activate the account directly** — it issues a
  single-use, expiring 6-digit code and emails it to the *requester's
  own inbox*, never shown to or handled by the admin. Because a 6-digit
  code has far less entropy than a long random token (1 million
  possibilities, not astronomically more), `set-password` requires the
  matching **email together with** the code — confirmed by a test that
  the right code with the wrong email is rejected, so knowing someone
  else's code alone isn't enough. The account only becomes active once
  the requester actually submits the correct email+code pair through
  `set-password.html`, confirmed end-to-end by a live test: real signup
  form → real approve call → real 6-digit code → real set-password page
  (including the digit-only input sanitization actually stripping junk
  characters as typed) → real login, all through the actual browser
  forms, not just backend unit tests.

  **Forgot password reuses the exact same mechanism** — same code type,
  same `set-password.html` page, same 48-hour single-use guarantee —
  rather than a separate system. `POST /api/auth/forgot-password`
  always returns an identical response whether or not the email has an
  account, confirmed by a test, so it can't be used to enumerate real
  accounts. **Honest, unresolved gap**: there is no rate limiting on
  `set-password` attempts specifically, so a determined attacker who
  already knows a pending requester's email could attempt to brute-force
  the 6-digit code within its 48-hour window — a real tradeoff of
  choosing a short code over a long token, accepted here for the
  simpler, more mobile-friendly experience, not something to treat as
  fully closed.

  If SMTP isn't configured or a send fails, the pending account still
  exists (nothing is silently lost) and the requester/admin sees a
  clear error rather than a false "success." **Switched from Resend to
  plain SMTP** after Resend's account-side permissions rejected every
  send attempt, confirmed via a raw API call that bypassed all of this
  app's own code entirely (genuinely their side, not a config mistake
  here) — with real time pressure, SMTP through an already-working
  mailbox was the faster path. Every public function in `notify.py`
  kept its exact name and signature through the swap, confirmed by the
  full existing test suite passing unchanged against the
  new implementation, plus 7 new tests for the SMTP transport itself
  (correct SSL-vs-STARTTLS port handling, clear errors on missing
  config or a failed send).

  **Real attribution, not spoofable**: countermeasure logs and alert
  acknowledgments use the authenticated user's identity automatically
  (sent via `Authorization: Bearer <token>` on every request) — a
  logged-in user's real name always overrides any name submitted in the
  request body, confirmed by a test that attempts exactly that spoof.

  Every login attempt — NDA acknowledged or not, credentials correct or
  not — is logged server-side and visible via `GET /api/gate/audit-log`:
  the attempted username, and on success the real display name and user
  id, so you can actually answer "who checked the NDA box and when," not
  just "how many attempts happened." (A real gap existed here briefly:
  the database recorded who attempted, but the endpoint didn't return
  it — fixed, with a test confirming a successful attempt shows the real
  name and a failed one correctly shows nothing, never a guess.) The
  password itself is never stored in plaintext or logged anywhere.

  **What's still honestly not built**: no roles/permissions (every
  account can see everything), no rate limiting on login, signup, OR
  forgot-password attempts specifically (the agent's separate rate limit
  doesn't cover this — someone could spam signup requests and flood the
  admin's inbox, or spam forgot-password to flood a real user's inbox),
  no expiry on pending signup requests that are never approved or
  rejected (they sit forever). The old shared-passcode endpoint
  (`POST /api/gate/check`) still exists
  in the code for backward compatibility but is no longer used by the
  frontend.

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

## Open Agent Hackathon 2026 — Sponsor Technology

RofehAI was extended for the Open Agent Hackathon 2026 using three sponsor technologies:

**DATA — Zetaris**
Zetaris serves as the federated data layer. Longitudinal astronaut health records are queried across distributed sources without moving sensitive data. The AI queries the data where it lives, preserving privacy across every source.

**BUILD — H-MEM (Meterless Engine)**
H-MEM is implemented as the briefing memory engine. It retains clinical context across sessions, giving flight surgeons a briefing agent that remembers every patient interaction. Context persistence is validated across sessions with no data loss between interactions.

**POWER — NVIDIA Nemotron via Meterless Nightshift**
Meterless Nightshift is the primary AI provider. NVIDIA Nemotron powers the grounded briefing agent, answering only from retrieved data. Every response is traceable to its source. The human makes every call.

