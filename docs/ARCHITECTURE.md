# RofehAI — Architecture

RofehAI is not a collection of separate AI medical applications. It is
**one underlying personalized astronaut health-intelligence platform**
with multiple health domains registered against a shared engine:

```
Personal astronaut model
   -> Continuous multimodal data
   -> Longitudinal health trajectory (per domain)
   -> Deviation / risk signal
   -> Confidence gating
   -> Human-in-the-loop decision support
   -> Observed outcome (logged; closed-loop learning is architecture-ready, not implemented)
```

SANS (Spaceflight-Associated Neuro-Ocular Syndrome) is the flagship,
most-complete domain. Immune dysregulation is the second fully-active
domain. Seven further domains (cardiovascular, musculoskeletal,
radiation, sleep/circadian, cognitive, behavioral, emergency/general)
are registered as **architecture-ready placeholders** -- visible in the
platform's domain list and dashboard, honestly labeled as not yet
implemented, never presented as if they produce real output.

## The domain registry (`backend/app/core/domains.py`)

This is the single place a health domain is declared: its id, display
name, status (`"active"` or `"architecture_ready"`), its list of
`SignalSpec`s (which observation fields it reads, their weight, scale,
and validation bounds), its narrative templates, and its countermeasure
catalog. Adding a new domain means adding one entry here -- it does not
require touching the trajectory model, the API routes, the database
schema, or the frontend's rendering loop, all of which read the registry
generically.

## Data flow

```
sensors (real, or the synthetic generator for this demo)
   -> Observation (crew_id, mission_day, signals: dict[str, float])
   -> DigitalTwin.ingest()          [backend/app/core/digital_twin.py]
   -> DigitalTwin.baseline()        (per-signal average from early-mission data)
   -> trajectory_model.score(twin, domain_id, mission_day)
        -> looks up the domain in the registry
        -> "architecture_ready" or no signals -> explicit not-implemented RiskOutput
        -> "active" -> real (reference-statistical) trajectory scoring -> RiskOutput
   -> countermeasures.recommend()   -> CountermeasureRecommendation | None
   -> dashboard (frontend/)         loops over GET /api/domains: active domains get
                                     full risk cards; architecture-ready domains get
                                     a single honestly-labeled badge row
   -> human decides & logs action   -> POST /api/countermeasures/log
```

## Component map

| Concept doc / patent component | Code |
|---|---|
| Health domain registry (new) | `backend/app/core/domains.py` |
| Individualized baseline physiological model ("digital twin") | `backend/app/core/digital_twin.py` |
| Continuous multi-modal data acquisition | `backend/app/models/schemas.py` (`Observation.signals`), `POST /api/observations` |
| Trajectory-aware fusion model | `backend/app/core/trajectory_model.py` |
| Confidence scoring & conservative fallback | `trajectory_model.score()` -- see `CONFIDENCE_THRESHOLD` |
| Explainable, ranked risk output | `RiskOutput` schema; `top_contributors` + `narrative` fields |
| Countermeasure recommendation loop | `backend/app/core/countermeasures.py` |
| Domain listing for the dashboard | `GET /api/domains` |
| Asynchronous Earth-in-the-loop feedback | Not yet implemented -- see Roadmap |
| Onboard edge deployment (NVIDIA Jetson Orin) | Not yet implemented -- see Roadmap |
| Human-in-the-loop / no autonomous action | Enforced by design throughout: the API never executes a countermeasure, only suggests one; a human calls `POST /api/countermeasures/log` to record that they acted |
| Mission context, crew-level intelligence, outcome/closed-loop learning | Architecture-ready only -- inert data models, not yet built. Deliberately scoped this way to avoid overbuilding ahead of real data; see Roadmap. |

## Why the trajectory model is a reference implementation, not a trained model

The concept document and patent draft describe a sequence-aware machine
learning model (RNN / transformer / Bayesian state-space model) trained
on real longitudinal astronaut data. This repository's
`trajectory_model.py` implements the same *behavior* -- baseline-relative,
multi-signal, confidence-gated, trend-aware scoring, read generically
from the domain registry -- using an explainable statistical method
(per-signal linear trend + logistic fusion) instead of a trained neural
network, for two honest reasons:

1. This sandbox has no network access to NASA's restricted longitudinal
   OCT/bed-rest datasets, so there is no real data to train a neural
   model on yet. Training on the synthetic generator in `data/synthetic.py`
   would only teach a model to recover the synthetic generator's own
   rules -- a meaningless result presented as if it were validated.
2. The rest of the platform (API contracts, dashboard, countermeasure
   loop) is built against `trajectory_model.score()`'s *interface*, not
   its internals. Swapping this module for a trained model later --
   once real data is available -- should not require changing any other
   part of the system, and does not require touching the domain
   registry either.

**Do not present this module's output as a validated clinical result.**
It demonstrates the architecture, not clinical accuracy. Architecture-
ready domains produce no output at all beyond an explicit "not yet
implemented" narrative -- never a fabricated number.

## Roadmap to close the gap with the patent/concept documents

1. **Real data integration**: replace `data/synthetic.py` with a loader
   for NASA's public longitudinal OCT and head-down-tilt bed rest
   datasets (see concept document, section 4.2), and for the
   Inspiration4 open dataset on NASA's Open Science Data Repository for
   the immune-dysregulation domain, behind the same
   `DigitalTwin.ingest()` interface.
2. **Trained sequence model**: replace the statistical fusion in
   `trajectory_model.py` with a trained RNN/transformer, validated with a
   real reported accuracy figure, behind the same `score()` interface --
   the domain registry does not need to change for this.
3. **Wire up an architecture-ready domain**: pick one placeholder domain
   (e.g. cardiovascular), define its real `SignalSpec` list and
   narratives in the registry, and flip its status to `"active"` -- this
   is the intended way to demonstrate the registry pattern actually
   working, not just declared.
4. **Asynchronous Earth-feedback loop**: add a module that packages
   `RiskOutput` history into a structured case summary, and a mechanism
   to ingest delayed flight-surgeon feedback back into the relevant
   `DigitalTwin`.
5. **Outcome / closed-loop learning**: build real data models and APIs
   for predicted trajectory -> human action -> observed outcome ->
   prediction-vs-actual, as scoped in the multi-system architecture
   assessment. Inert schemas only until there's real outcome data to
   learn from.
6. **Mission context & crew-level intelligence**: wire up the
   architecture-ready data models for mission phase, comms delay,
   available resources, and cross-crew pattern detection -- once there's
   a real reason to (see concept document sections 8-9).
7. **Edge deployment**: package the trained model for on-device inference
   on an NVIDIA Jetson Orin (see concept document, section 4.3), with the
   FastAPI service able to run fully offline against local model weights.
8. **Persistence**: replace the in-memory `TwinRegistry` cache's
   underlying store with a production-grade datastore before any
   non-demo use; see docs/DEPLOYMENT.md for the Postgres path already
   verified to work.

## Regulatory note

Every layer of this codebase is built to the "clinical decision support,
human-in-the-loop" scope described in the concept document -- the API has
no code path that autonomously reaches a diagnostic conclusion or
triggers a treatment/countermeasure action, for any domain, active or
architecture-ready. Output language stays in the "surface / flag /
prioritize" register (see narrative templates in `domains.py`), never
"diagnose / confirm / conclude". Do not add a code path that breaks this
without re-reading the concept document's regulatory section and
consulting qualified regulatory counsel.
