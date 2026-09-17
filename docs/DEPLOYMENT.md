# Deployment note — leveraging NVIDIA Inception, AWS Activate, and Google Cloud for Startups

**Audience:** RofehAI technical team, planning real (non-demo) infrastructure.

**Caveat up front:** the specific dollar figures below are drawn from
third-party trackers of these programs (NVIDIA, AWS, and Google do not
all publish fixed numbers on their own sites — NVIDIA in particular
confirms "free cloud credits from partners" without a guaranteed fixed
amount). Treat every number here as *what to ask your program contact to
confirm*, not a guaranteed balance. Re-verify directly with each
program's console/account manager before budgeting against it.

## What each program is actually good for in this architecture

### NVIDIA Inception — the model/training and edge story
- Reported benefits include partner cloud credits (commonly cited up to
  ~$100K, routed through AWS Activate rather than issued directly by
  NVIDIA), a 30% discount on DGX Cloud H100/H200 capacity (gated behind a
  minimum spend/node commitment, so more relevant once you're training at
  real scale than today), NVIDIA Deep Learning Institute training
  credits (~$10K reported), preferred hardware pricing, and technical
  office hours with NVIDIA engineers.
- **Where this maps to RofehAI**: this is your path to (a) GPU capacity
  for training the real sequence model once real data is available
  (replacing `trajectory_model.py`'s reference implementation), and (b)
  direct technical guidance on deploying that trained model to an
  **NVIDIA Jetson Orin** for onboard inference, which is the edge
  hardware named in the concept document. Use the Inception badge and
  network primarily for (b) — most funded teams get more real value from
  the technical relationship and DLI training than from the credit
  headline.

### AWS Activate — general hosting and managed services
- Reported tiers range from a self-serve Founders tier (~$1K–$5K, no
  accelerator needed) up to a Portfolio tier (commonly cited ~$25K–$100K,
  requires a qualifying VC/accelerator/incubator sponsor) and a
  specialized Generative AI tier (reported up to ~$300K for startups
  building on Bedrock/SageMaker/Trainium).
- **Where this maps to RofehAI**: general application hosting (backend
  API, database, object storage for imaging data), managed Postgres
  (RDS), and — if useful later — Bedrock for any non-core-model AI
  features (e.g., drafting case-summary text for the asynchronous
  Earth-feedback loop). This is the natural home for the "boring but
  necessary" production infrastructure: database, backups, logging,
  networking.

### Google Cloud for Startups — the AI/ML tooling story
- Reported tiers run from a small self-serve amount up to a
  headline "AI-first" tier (commonly cited up to ~$350K), typically
  gated behind accelerator/VC affiliation for the largest tier.
- **Where this maps to RofehAI**: Vertex AI for managed model training
  and experiment tracking if you end up preferring GCP's ML tooling over
  a self-managed training pipeline, BigQuery if the longitudinal
  biometric dataset grows large enough to want a real warehouse, and
  Firebase if a future patient/crew-facing mobile companion app is ever
  built.

## A practical, non-overcommitted first architecture

Given the actual current state of the project (reference implementation,
synthetic data, no trained model yet, no real users), the honest
recommendation is: **don't split infrastructure across all three
providers yet.** Multi-cloud adds real operational complexity (auth,
networking, monitoring, billing across three consoles) that isn't
justified until there's a concrete reason for each leg. A more
defensible first architecture:

1. **Pick one cloud for the "boring" always-on pieces** (API hosting,
   Postgres, backups, logging) — AWS is a reasonable default given
   Activate's broad general-purpose credit availability and the team's
   likely familiarity with it. Stand up the FastAPI backend here as a
   normal containerized service (e.g., ECS Fargate or a small EC2/RDS
   pair) once persistence and auth are added (see roadmap below).
2. **Use NVIDIA Inception specifically for the model-training and
   edge-deployment leg**, once real data is available: train the actual
   sequence model on NVIDIA GPU capacity (via Inception's partner
   credits or DGX Cloud), then package and validate the trained model on
   an actual Jetson Orin dev kit before ever discussing flight hardware.
   This keeps the "who does what" boundary clean: AWS runs the always-on
   service, NVIDIA is where the model itself is trained and made to run
   on the target edge hardware.
3. **Hold Google Cloud in reserve** for a specific, concrete future need
   (e.g., if the team decides Vertex AI's managed training/experiment
   tracking is worth adopting, or a patient/crew-facing mobile app is
   built and Firebase is a good fit) rather than standing up GCP
   infrastructure with no workload assigned to it yet. Apply and get
   accepted into the program now if there's no cost to doing so, but
   don't split real workloads onto it prematurely.

## Sequencing

1. Finish the engineering hardening already in progress (persistence,
   input validation, CI, basic auth — see `docs/ARCHITECTURE.md`).
2. Stand up a single real hosting environment (AWS) for the API and
   database, behind real auth, before showing this to anyone outside the
   immediate team.
3. Once real longitudinal data access is arranged (see the concept
   document's TRISH path), use NVIDIA Inception's compute and technical
   relationship specifically for the training and edge-deployment work.
4. Revisit Google Cloud once there's a concrete workload (managed
   ML tooling, or a crew/patient-facing app) that specifically wants it.

## Who should own this

This note is a starting recommendation for your technical team to plan
against, not a finalized infrastructure decision — actual account setup,
credit applications, and architecture choices should be made by whoever
is (or becomes) your infrastructure/DevOps lead, with the specific
credit amounts reconfirmed directly with each program at application
time.
