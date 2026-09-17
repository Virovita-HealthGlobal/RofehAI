from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core import db
from app.core.auth import require_api_key

logger = logging.getLogger("rofehai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="RofehAI API",
    description=(
        "Reference implementation of the RofehAI closed-loop, personalized "
        "health-tracking architecture: individualized baseline modeling, "
        "continuous trajectory risk scoring, confidence-gated fallback, and "
        "countermeasure suggestion. Runs on SYNTHETIC demo data -- see "
        "app/data/synthetic.py for the honesty note before using any output "
        "for real conclusions."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo only -- restrict this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api", dependencies=[Depends(require_api_key)])


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak internals (stack traces, file paths, query text) to the
    client -- log the real error server-side and return a generic 500."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. This has been logged for review."},
    )


@app.get("/")
def root():
    return {
        "service": "rofehai-api",
        "status": "ok",
        "docs": "/docs",
        "note": "Demo data is synthetic. See README before drawing conclusions from output.",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
