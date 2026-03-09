"""
aria/graph/state.py
────────────────────
LangGraph state contract for the ARIA decision pipeline.

Every node receives the full ``ARIAState`` and returns a *partial* dict
containing only the keys it mutates. LangGraph merges them automatically.
"""

from __future__ import annotations

from typing import Optional
from typing_extensions import TypedDict

from aria.schemas import AuditRecord, AutonomyDecision, RiskAssessment, UserRequest


class ARIAState(TypedDict, total=False):
    """
    Shared mutable state threaded through every node in the ARIA graph.

    Fields
    ------
    request:
        The originating :class:`~aria.schemas.UserRequest` — set at graph entry
        and never mutated by downstream nodes.
    risk_assessment:
        Populated by ``node_classify_risk``; ``None`` until that node runs.
    autonomy_decision:
        Populated by ``node_route_autonomy``; ``None`` until routing resolves.
    action_result:
        Raw string output from whichever execution node ran.
    audit_record:
        Immutable :class:`~aria.schemas.AuditRecord` written by ``node_write_audit``.
    error:
        Non-empty string if any node encountered a non-fatal error; ``None`` otherwise.
    start_time_ms:
        Wall-clock timestamp (milliseconds) recorded by ``node_start`` for
        end-to-end latency calculation.
    """

    request: UserRequest
    risk_assessment: Optional[RiskAssessment]
    autonomy_decision: Optional[AutonomyDecision]
    action_result: Optional[str]
    audit_record: Optional[AuditRecord]
    error: Optional[str]
    start_time_ms: Optional[float]
