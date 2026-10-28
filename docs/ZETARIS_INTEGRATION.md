# Zetaris Integration — RofehAI

RofehAI uses Zetaris as its Unified Semantic Layer (USL) to federate spaceflight health data into a single queryable surface.

## Architecture

Frontend (app.rofehai.com) → Cloudflare Tunnel → FastAPI Backend → ngrok → Zetaris USL → mission_observations.csv

## Table: ROFEHAI.mission_observations

Columns: crew_id, mission_day, metric, value, recorded_at

## Key Queries

```sql
-- 1. Crew observations up to mission day 210
SELECT * FROM ROFEHAI.mission_observations
WHERE crew_id = 'chen_wei' AND mission_day <= 210
ORDER BY mission_day ASC;

-- 2. Latest readings per metric
SELECT metric, value FROM ROFEHAI.mission_observations
WHERE crew_id = 'chen_wei'
AND mission_day = (SELECT MAX(mission_day) FROM ROFEHAI.mission_observations WHERE crew_id = 'chen_wei');

-- 3. Fleet-wide ONSD trend
SELECT crew_id, mission_day, value AS onsd_mm
FROM ROFEHAI.mission_observations WHERE metric = 'onsd_mm'
ORDER BY crew_id, mission_day;
```

## Data Quality Rule

```sql
-- No negative physiological values
SELECT * FROM ROFEHAI.mission_observations WHERE value < 0;
-- Expected: 0 rows
```

See backend/app/data/zetaris_client.py for API implementation.
