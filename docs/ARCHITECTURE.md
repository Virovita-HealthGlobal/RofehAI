# RofehAI — Architecture

This document maps the running code in this repository to the design
described in the RofehAI concept document and draft patent application.
If you're reviewing this alongside those documents, the section headers
below correspond directly to their component names.

## Components and where they live

| Concept doc / patent component | Code |
|---|---|
| Individualized baseline physiological model ("digital twin") | `backend/app/core/digital_twin.py` |
| Continuous multi-modal data acquisition | `backend/app/models/schemas.py` (`Observation`), `POST /api/observations` |
| Trajectory-aware fusion model | `backend/app/core/trajectory_model.py` |
| Confidence scoring & conservative fallback | `trajectory_model.score()` — see `CONFIDENCE_THRESHOLD` |
| Explainable, ranked risk output | `RiskOutput` schema; `top_contributors` + `narrative` fields |
| Countermeasure recommendation loop | `backend/app/core/countermeasures.py` |
| Asynchronous Earth-in-the-loop feedback | Not yet implemented — see Roadmap |
| Onboard edge deployment (NVIDIA Jetson Orin) | Not yet implemented — see Roadmap |
| Human-in-the-loop / no autonomous action | Enforced by design throughout: the API never executes a countermeasure, only suggests one; a human calls `POST /api/countermeasures/log` to record that they acted |

## Data flow

```
sensors (real, or the synthetic generator for this demo)
   -> Observation
   -> DigitalTwin.ingest()          [backend/app/core/digital_twin.py]
   -> DigitalTwin.baseline()        (recomputed from early-mission data)
   -> trajectory_model.score()      -> RiskOutput
   -> countermeasures.recommend()   -> CountermeasureRecommendation | None
   -> dashboard (frontend/)         renders RiskOutput + recommendation
   -> human decides & logs action   -> POST /api/countermeasures/log
```

## Why the trajectory model is a reference implementation, not a trained model

The concept document and patent draft describe a sequence-aware machine
learning model (RNN / transformer / Bayesian state-space model) trained on
real longitudinal astronaut data. This repository's
`trajectory_model.py` implements the same *behavior* — baseline-relative,
multi-signal, confidence-gated, trend-aware scoring — using an explainable
statistical method (per-signal linear trend + logistic fusion) instead of
a trained neural network, for two honest reasons:

1. This sandbox has no network access to NASA's restricted longitudinal
   OCT/bed-rest datasets, so there is no real data to train a neural model
   on yet. Training on the synthetic generator in `data/synthetic.py`
   would only teach a model to recover the synthetic generator's own
   rules — a meaningless result presented as if it were validated.
2. The rest of the platform (API contracts, dashboard, countermeasure
   loop) is built against `trajectory_model.score()`'s *interface*, not
   its internals. Swapping this module for a trained model later — once
   real data is available — should not require changing any other part
   of the system.

**Do not present this module's output as a validated clinical result.**
It demonstrates the architecture, not clinical accuracy.

## Roadmap to close the gap with the patent/concept documents

1. **Real data integration**: replace `data/synthetic.py` with a loader
   for NASA's public longitudinal OCT and head-down-tilt bed rest
   datasets (see concept document, section 4.2), behind the same
   `DigitalTwin.ingest()` interface.
2. **Trained sequence model**: replace the statistical fusion in
   `trajectory_model.py` with a trained RNN/transformer, validated with a
   real reported accuracy figure, behind the same `score()` interface.
3. **Asynchronous Earth-feedback loop**: add a module that packages
   `RiskOutput` history into a structured case summary, and a mechanism
   to ingest delayed flight-surgeon feedback back into the relevant
   `DigitalTwin`.
4. **Edge deployment**: package the trained model for on-device inference
   on an NVIDIA Jetson Orin (see concept document, section 4.3), with the
   FastAPI service able to run fully offline against local model weights.
5. **Persistence**: replace the in-memory `TwinRegistry` and
   countermeasure log with a real datastore before any non-demo use.

## Regulatory note

Every layer of this codebase is built to the "clinical decision support,
human-in-the-loop" scope described in the concept document — the API has
no code path that autonomously reaches a diagnostic conclusion or
triggers a treatment/countermeasure action. Do not add one without
re-reading that section and consulting qualified regulatory counsel.
