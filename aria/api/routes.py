"""
aria/api/routes.py
───────────────────
FastAPI router — 6 endpoints for the ARIA REST API.

Endpoints:
  POST /v1/request      — run the ARIA graph on a new user request
  GET  /v1/audit/{id}   — retrieve an audit record by trace_id
  POST /v1/approve      — submit a human approval/denial
  GET  /v1/metrics      — live MetricsSnapshot from the Kafka consumer
  GET  /v1/pending      — list all pending human-approval requests
  GET  /health          — liveness probe
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aria.agents.audit_agent import audit_agent
from aria.api.approval import add_pending, get_pending, process_approval
from aria.graph.graph import aria_graph
from aria.kafka.consumer import aria_consumer
from aria.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    AutonomyMode,
    DecisionOutcome,
    UserRequest,
)
from aria.tools.action_tools import execute_tool

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request / response bodies ─────────────────────────────────────────────────

class RequestBody(BaseModel):
    session_id: str
    user_intent: str
    action_type: str
    context: Optional[dict[str, Any]] = None


class RequestResponse(BaseModel):
    trace_id: str
    autonomy_mode: str
    outcome: str
    result: Optional[str]
    latency_ms: Optional[int]


class ApprovalResult(BaseModel):
    trace_id: str
    status: str
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/v1/request", response_model=RequestResponse, tags=["core"])
async def submit_request(body: RequestBody):
    """
    Submit a new user request to the ARIA decision graph.

    Runs the full pipeline (risk classify → autonomy route → execute/escalate → audit)
    and returns the trace_id, autonomy mode, outcome, action result, and latency.
    """
    user_request = UserRequest(
        session_id=body.session_id,
        user_intent=body.user_intent,
        action_type=body.action_type,
        context=body.context or {},
    )
    logger.info("[%s] /v1/request received: intent=%r", user_request.trace_id, body.user_intent)

    try:
        state = aria_graph.invoke({"request": user_request})
    except Exception as exc:
        logger.error("Graph invocation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Graph execution error: {exc}") from exc

    decision = state.get("autonomy_decision")
    audit    = state.get("audit_record")
    result   = state.get("action_result")

    # Register escalated decisions as pending approvals
    if decision and decision.autonomy_mode == AutonomyMode.ESCALATE:
        ra = state.get("risk_assessment")
        pending_req = ApprovalRequest(
            trace_id=user_request.trace_id,
            explanation=decision.explanation or "Escalated — high risk action.",
            risk_score=ra.risk_score if ra else 100,
            user_intent=body.user_intent,
            requested_action=body.action_type,
        )
        add_pending(pending_req)

    return RequestResponse(
        trace_id=user_request.trace_id,
        autonomy_mode=decision.autonomy_mode.value if decision else "UNKNOWN",
        outcome=audit.outcome.value if audit else "UNKNOWN",
        result=result,
        latency_ms=audit.latency_ms if audit else None,
    )


@router.get("/v1/audit/{trace_id}", tags=["audit"])
async def get_audit(trace_id: str):
    """Retrieve an AuditRecord by trace_id. Returns 404 if not found."""
    record = audit_agent.get(trace_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"trace_id '{trace_id}' not found")
    return record.model_dump()


@router.post("/v1/approve", response_model=ApprovalResult, tags=["approval"])
async def submit_approval(response: ApprovalResponse):
    """
    Submit a human approval or denial for a pending escalation.

    If approved, executes the deferred action and marks outcome APPROVED.
    If denied, marks outcome DENIED without executing the action.
    """
    found = process_approval(response)
    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval found for trace_id='{response.trace_id}'",
        )

    if response.approved:
        # Retrieve the pending request to know which action to execute
        audit_rec = audit_agent.get(response.trace_id)
        if audit_rec:
            try:
                execute_tool(
                    action_type=audit_rec.action_attempted,
                    session_id=audit_rec.session_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Deferred execution failed: %s", response.trace_id, exc)

        message = f"Action approved and executed by {response.reviewed_by}."
        status  = "APPROVED"
    else:
        message = f"Action denied by {response.reviewed_by}."
        status  = "DENIED"

    return ApprovalResult(trace_id=response.trace_id, status=status, message=message)


@router.get("/v1/metrics", tags=["observability"])
async def get_metrics():
    """Return a live MetricsSnapshot computed from the Kafka consumer buffer."""
    return aria_consumer.get_metrics().model_dump()


@router.get("/v1/pending", tags=["approval"])
async def list_pending():
    """Return all currently pending human-approval requests."""
    return [p.model_dump() for p in get_pending()]


@router.get("/health", tags=["infra"])
async def health():
    """Liveness probe."""
    return {
        "status": "ok",
        "version": "1.0.0",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
