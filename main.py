"""
main.py
────────
ARIA FastAPI application entry point.

Start the server:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Or directly:
    python main.py
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aria.api.routes import router
from aria.config import settings
from aria.kafka.consumer import aria_consumer

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("aria.main")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Startup:
      - Launch Kafka consumer background thread (DLQ fallback if unavailable)
    Shutdown:
      - Signal consumer to stop
    """
    logger.info("ARIA starting up on port=%d ...", settings.api_port)
    aria_consumer.start()
    logger.info("Kafka consumer started (or in DLQ-fallback mode)")
    yield
    logger.info("ARIA shutting down ...")
    aria_consumer.stop()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ARIA — Autonomous Risk-Aware Intelligence Architecture",
    description=(
        "Production-grade agentic AI system for financial services. "
        "Risk-classifies user actions, routes them through autonomous / supervised / escalation flows, "
        "and provides a full audit trail."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routes at root level (no prefix)
app.include_router(router)


# ── Dev runner ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
