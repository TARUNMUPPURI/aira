"""
aria/schemas.py
───────────────
All Pydantic v2 data models used across every layer of ARIA.

Import convention:

    from aria.schemas import (
        UserRequest, RiskLevel, AutonomyMode, DecisionOutcome,
        RiskAssessment, AutonomyDecision, AuditRecord,
        ApprovalRequest, ApprovalResponse, MetricsSnapshot,
    )
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_trace_id() -> str:
    """Return a trace ID in the format ``aria-<12 hex chars>``."""
    return f"aria-{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
#  Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class RiskLevel(str, Enum):
    """Categorical risk classification derived from a numeric risk score."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AutonomyMode(str, Enum):
    """
    Execution mode chosen by ARIA after evaluating risk.

    * AUTONOMOUS — agent acts without human intervention.
    * SUPERVISED — agent acts but logs for human review.
    * ESCALATE   — action is held and routed to a human approver.
    """
    AUTONOMOUS = "AUTONOMOUS"
    SUPERVISED = "SUPERVISED"
    ESCALATE = "ESCALATE"


class DecisionOutcome(str, Enum):
    """Final outcome recorded in the audit trail."""
    EXECUTED = "EXECUTED"
    FLAGGED = "FLAGGED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


# ─────────────────────────────────────────────────────────────────────────────
#  UserRequest
# ─────────────────────────────────────────────────────────────────────────────


class UserRequest(BaseModel):
    """
    Incoming request entering the ARIA agent graph.

    ``trace_id`` is auto-generated per request and threads through every
    downstream model for end-to-end traceability.
    """

    trace_id: str = Field(
        default_factory=_make_trace_id,
        description="Auto-generated trace ID — format: aria-<12 hex chars>.",
        examples=["aria-3f1a9c72b80e"],
    )
    session_id: str = Field(
        ...,
        description="Persistent identifier for the user's session.",
    )
    user_intent: str = Field(
        ...,
        min_length=1,
        description="Natural-language description of what the user wants to do.",
    )
    action_type: str = Field(
        ...,
        min_length=1,
        description="Machine-readable action category (e.g., READ, WRITE, DELETE, TRANSFER).",
    )
    context: dict = Field(
        default_factory=dict,
        description="Optional key-value metadata forwarded from upstream systems.",
    )
    timestamp: datetime = Field(
        default_factory=_utcnow,
        description="UTC time when the request was created.",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  RiskAssessment
# ─────────────────────────────────────────────────────────────────────────────


class RiskAssessment(BaseModel):
    """Output produced by the risk-classification node."""

    trace_id: str = Field(..., description="Links back to the originating UserRequest.")
    risk_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Numeric risk score in [0, 100].",
    )
    risk_level: RiskLevel = Field(
        ...,
        description="Categorical risk level derived from score + configured thresholds.",
    )
    reasoning: str = Field(
        ...,
        min_length=1,
        description="Human-readable explanation of the risk determination.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model confidence in this assessment (0.0 – 1.0).",
    )
    rag_references: list[str] = Field(
        default_factory=list,
        description="IDs / URIs of ChromaDB chunks that informed this assessment.",
    )

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, v: float) -> float:
        return round(v, 4)


# ─────────────────────────────────────────────────────────────────────────────
#  AutonomyDecision
# ─────────────────────────────────────────────────────────────────────────────


class AutonomyDecision(BaseModel):
    """The agent's final decision on how to proceed with a requested action."""

    trace_id: str = Field(..., description="Trace ID from the originating UserRequest.")
    autonomy_mode: AutonomyMode = Field(
        ...,
        description="Execution mode chosen after evaluating risk.",
    )
    risk_assessment: RiskAssessment = Field(
        ...,
        description="Full risk assessment that drove this decision.",
    )
    action_to_execute: Optional[str] = Field(
        default=None,
        description="The concrete action string to execute (populated on AUTONOMOUS / SUPERVISED).",
    )
    explanation: Optional[str] = Field(
        default=None,
        description="Human-readable rationale — required when autonomy_mode is ESCALATE.",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  AuditRecord
# ─────────────────────────────────────────────────────────────────────────────


class AuditRecord(BaseModel):
    """
    Immutable audit log entry capturing the full lifecycle of one decision.
    Written immediately after any action attempt (success, failure, or escalation).
    """

    trace_id: str = Field(..., description="End-to-end trace identifier.")
    session_id: str = Field(..., description="Session that originated the request.")
    user_intent: str = Field(..., description="The user's stated intent.")
    risk_score: int = Field(..., ge=0, le=100, description="Risk score at decision time.")
    risk_level: RiskLevel = Field(..., description="Risk level at decision time.")
    autonomy_mode: AutonomyMode = Field(..., description="Execution mode that was selected.")
    action_attempted: str = Field(..., description="The concrete action string that was run.")
    outcome: DecisionOutcome = Field(..., description="Result of the action attempt.")
    reasoning: str = Field(..., description="Risk-assessment reasoning string.")
    human_approved: Optional[bool] = Field(
        default=None,
        description="True/False if a human reviewed this in ESCALATE mode; None otherwise.",
    )
    latency_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Optional end-to-end latency in milliseconds.",
    )
    timestamp: datetime = Field(
        default_factory=_utcnow,
        description="UTC timestamp of when this audit record was created.",
    )

    model_config = {"frozen": True}  # Audit records are immutable once written


# ─────────────────────────────────────────────────────────────────────────────
#  Human-in-the-Loop: Approval Request & Response
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalRequest(BaseModel):
    """Payload sent to a human reviewer when the agent escalates a decision."""

    trace_id: str = Field(..., description="Trace ID of the escalated decision.")
    explanation: str = Field(..., description="AI-generated explanation of why this was escalated.")
    risk_score: int = Field(..., ge=0, le=100)
    user_intent: str = Field(..., description="The user's original intent.")
    requested_action: str = Field(..., description="The action that was blocked pending approval.")


class ApprovalResponse(BaseModel):
    """Response from a human reviewer on a pending ApprovalRequest."""

    trace_id: str = Field(..., description="Must match the corresponding ApprovalRequest.trace_id.")
    approved: bool = Field(..., description="True = approved; False = denied.")
    reviewed_by: str = Field(..., description="Identifier of the human reviewer.")
    notes: Optional[str] = Field(default=None, description="Optional reviewer notes.")


# ─────────────────────────────────────────────────────────────────────────────
#  MetricsSnapshot
# ─────────────────────────────────────────────────────────────────────────────


class MetricsSnapshot(BaseModel):
    """Point-in-time metrics summary surfaced in the Streamlit dashboard."""

    escalation_rate_pct: float = Field(
        ..., ge=0.0, le=100.0,
        description="Percentage of decisions that resulted in ESCALATE.",
    )
    false_positive_rate_pct: float = Field(
        ..., ge=0.0, le=100.0,
        description="Percentage of escalations that were subsequently approved by a human.",
    )
    autonomy_drift_7d: float = Field(
        ...,
        description="7-day signed delta between current and baseline autonomy rate.",
    )
    avg_risk_score: float = Field(
        ..., ge=0.0, le=100.0,
        description="Mean risk score over the measurement window.",
    )
    p95_latency_ms: float = Field(
        ..., ge=0.0,
        description="95th-percentile end-to-end latency in milliseconds.",
    )
    total_decisions: int = Field(..., ge=0)
    autonomous_count: int = Field(..., ge=0)
    supervised_count: int = Field(..., ge=0)
    escalate_count: int = Field(..., ge=0)
