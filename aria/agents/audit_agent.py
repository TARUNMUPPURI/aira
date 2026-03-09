"""
aria/agents/audit_agent.py
───────────────────────────
Audit trail writer and in-memory registry for the ARIA decision pipeline.

Responsibilities:
  - Write AuditRecord to Kafka via the producer (with DLQ fallback)
  - Maintain an in-memory store keyed by trace_id
  - Allow post-hoc outcome updates (e.g., human approval)

Usage::

    from aria.agents.audit_agent import audit_agent
    from aria.schemas import AuditRecord

    audit_agent.write(record)
    stored = audit_agent.get(record.trace_id)
    audit_agent.update_outcome(trace_id, DecisionOutcome.APPROVED, human_approved=True)
"""

from __future__ import annotations

import logging
from typing import Optional

from aria.schemas import AuditRecord, DecisionOutcome

logger = logging.getLogger(__name__)


class AuditAgent:
    """
    Central audit writer with:

    * Kafka persistence via :func:`~aria.kafka.producer.send_audit`
    * In-memory store (``_store``) keyed by ``trace_id``
    * ``get(trace_id)`` — retrieve any previously written record
    * ``update_outcome(trace_id, outcome, human_approved)`` — post-hoc mutation
      for the human-approval flow (creates a replacement frozen record)
    """

    def __init__(self) -> None:
        self._store: dict[str, AuditRecord] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def write(self, record: AuditRecord) -> None:
        """
        Persist *record*:
          1. Cache in ``_store`` keyed by ``trace_id``
          2. Produce to Kafka topic ``aria.decisions`` (DLQ fallback on failure)

        Never raises.
        """
        # Cache first — always available even if Kafka fails
        self._store[record.trace_id] = record

        # Kafka produce (falls back to DLQ internally)
        try:
            from aria.kafka.producer import send_audit
            send_audit(record)
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] AuditAgent.write failed to send: %s", record.trace_id, exc)

    def get(self, trace_id: str) -> Optional[AuditRecord]:
        """
        Return the cached :class:`AuditRecord` for *trace_id*, or ``None``
        if the trace has not been written to this agent instance.
        """
        return self._store.get(trace_id)

    def update_outcome(
        self,
        trace_id: str,
        outcome: DecisionOutcome,
        human_approved: bool,
    ) -> Optional[AuditRecord]:
        """
        Replace the stored record for *trace_id* with an updated copy
        reflecting the new *outcome* and *human_approved* flag.

        AuditRecord is frozen, so this creates a new instance via
        ``model_copy(update=...)``.

        Returns the updated record, or ``None`` if *trace_id* is unknown.
        """
        existing = self._store.get(trace_id)
        if existing is None:
            logger.warning("update_outcome: trace_id=%r not found in store", trace_id)
            return None

        updated = existing.model_copy(
            update={"outcome": outcome, "human_approved": human_approved}
        )
        self._store[trace_id] = updated
        logger.info(
            "[%s] Outcome updated: %s → %s (human_approved=%s)",
            trace_id, existing.outcome, outcome, human_approved,
        )
        return updated

    @property
    def record_count(self) -> int:
        """Number of records currently held in the in-memory store."""
        return len(self._store)


# ── Module-level singleton ────────────────────────────────────────────────────
audit_agent = AuditAgent()
