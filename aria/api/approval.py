"""
aria/api/approval.py
─────────────────────
In-memory pending-approval store for the ARIA human-in-the-loop flow.

Used by:
  • ``node_escalate`` to register a pending approval
  • ``POST /v1/approve`` to resolve it
"""

from __future__ import annotations

import logging
from typing import Optional

from aria.agents.audit_agent import audit_agent
from aria.schemas import ApprovalRequest, ApprovalResponse, DecisionOutcome

logger = logging.getLogger(__name__)

# ── In-memory store keyed by trace_id ─────────────────────────────────────────
pending_approvals: dict[str, ApprovalRequest] = {}


def add_pending(req: ApprovalRequest) -> None:
    """Register an ApprovalRequest as pending human review."""
    pending_approvals[req.trace_id] = req
    logger.info("[%s] Approval request added to pending store", req.trace_id)


def get_pending() -> list[ApprovalRequest]:
    """Return all currently pending ApprovalRequests."""
    return list(pending_approvals.values())


def process_approval(response: ApprovalResponse) -> bool:
    """
    Resolve a pending approval:

    1. Look up the trace_id in the pending store.
    2. Update the AuditRecord in ``audit_agent`` with the new outcome and
       ``human_approved`` flag.
    3. Remove from the pending store.

    Returns ``True`` if the trace_id was found and processed, ``False`` otherwise.
    """
    req = pending_approvals.get(response.trace_id)
    if req is None:
        logger.warning(
            "[%s] process_approval: trace_id not found in pending store",
            response.trace_id,
        )
        return False

    new_outcome = DecisionOutcome.APPROVED if response.approved else DecisionOutcome.DENIED
    audit_agent.update_outcome(
        trace_id=response.trace_id,
        outcome=new_outcome,
        human_approved=response.approved,
    )

    del pending_approvals[response.trace_id]
    logger.info(
        "[%s] Approval processed: approved=%s reviewer=%s outcome=%s",
        response.trace_id, response.approved, response.reviewed_by, new_outcome,
    )
    return True
