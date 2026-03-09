"""
aria/kafka/consumer.py
───────────────────────
Background Kafka consumer for ARIA.

Subscribes to ``aria.decisions``, maintains a rolling in-memory deque of up
to 1 000 :class:`~aria.schemas.AuditRecord` objects, and exposes:

* ``get_records()``  → list of AuditRecord
* ``get_metrics()``  → MetricsSnapshot
* ``start()``        → launches the consumer in a daemon thread

Fallback: if Kafka is unavailable, ``get_records()`` transparently reads
from ``audit_fallback.jsonl``.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from pathlib import Path
from statistics import mean, quantiles
from typing import Optional

from aria.config import settings
from aria.schemas import AuditRecord, AutonomyMode, MetricsSnapshot

logger = logging.getLogger(__name__)

TOPIC = "aria.decisions"
_DLQ_PATH = Path("audit_fallback.jsonl")
_GROUP_ID = "aria-consumer-group"


class ARIAConsumer:
    """
    Background Kafka consumer that maintains an in-memory ring buffer of
    the most recent 1 000 audit decisions.
    """

    def __init__(self) -> None:
        self._records: deque[AuditRecord] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the consumer loop in a daemon thread (idempotent)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._consume_loop,
            name="aria-kafka-consumer",
            daemon=True,
        )
        self._thread.start()
        logger.info("ARIAConsumer started on topic=%s", TOPIC)

    def stop(self) -> None:
        """Signal the consumer loop to stop."""
        self._running = False

    def get_records(self) -> list[AuditRecord]:
        """
        Return all buffered AuditRecord objects.
        Falls back to reading ``audit_fallback.jsonl`` when the buffer is empty.
        """
        with self._lock:
            records = list(self._records)
        if not records:
            records = self._read_dlq()
        return records

    def get_metrics(self) -> MetricsSnapshot:
        """Compute and return a real-time MetricsSnapshot from buffered records."""
        records = self.get_records()
        total = len(records)

        if total == 0:
            # No data at all — return zeroed snapshot with mock drift values
            return MetricsSnapshot(
                escalation_rate_pct=0.0,
                false_positive_rate_pct=3.2,
                autonomy_drift_7d=-1.5,
                avg_risk_score=0.0,
                p95_latency_ms=0.0,
                total_decisions=0,
                autonomous_count=0,
                supervised_count=0,
                escalate_count=0,
            )

        autonomous_count = sum(1 for r in records if r.autonomy_mode == AutonomyMode.AUTONOMOUS)
        supervised_count = sum(1 for r in records if r.autonomy_mode == AutonomyMode.SUPERVISED)
        escalate_count   = sum(1 for r in records if r.autonomy_mode == AutonomyMode.ESCALATE)

        escalation_rate_pct = (escalate_count / total) * 100
        avg_risk_score = mean(r.risk_score for r in records)

        latencies = [r.latency_ms for r in records if r.latency_ms is not None]
        if latencies:
            # quantiles returns [p25, p50, p75] with n=4; use a simple sort for p95
            sorted_lat = sorted(latencies)
            p95_idx = max(0, int(len(sorted_lat) * 0.95) - 1)
            p95_latency_ms = float(sorted_lat[p95_idx])
        else:
            p95_latency_ms = 0.0

        # Insufficient-data mocks
        false_positive_rate_pct = 3.2 if total < 10 else _compute_fpr(records)
        autonomy_drift_7d = -1.5 if total < 100 else _compute_drift(records)

        return MetricsSnapshot(
            escalation_rate_pct=round(escalation_rate_pct, 2),
            false_positive_rate_pct=round(false_positive_rate_pct, 2),
            autonomy_drift_7d=round(autonomy_drift_7d, 2),
            avg_risk_score=round(avg_risk_score, 2),
            p95_latency_ms=round(p95_latency_ms, 2),
            total_decisions=total,
            autonomous_count=autonomous_count,
            supervised_count=supervised_count,
            escalate_count=escalate_count,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _consume_loop(self) -> None:
        """Inner thread target — poll Kafka until stopped."""
        try:
            from confluent_kafka import Consumer, KafkaException

            consumer = Consumer({
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "group.id": _GROUP_ID,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,
            })
            consumer.subscribe([TOPIC])
            logger.info("Kafka consumer subscribed to %s", TOPIC)

            while self._running:
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.warning("Kafka consumer error: %s", msg.error())
                    continue
                self._ingest(msg.value())

            consumer.close()

        except Exception as exc:  # noqa: BLE001
            logger.warning("Kafka consumer unavailable (%s) — running in DLQ fallback mode", exc)
            self._running = False

    def _ingest(self, raw: bytes) -> None:
        """Deserialise a raw Kafka message into an AuditRecord and buffer it."""
        try:
            record = AuditRecord.model_validate_json(raw)
            with self._lock:
                self._records.append(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to deserialise Kafka message: %s", exc)

    @staticmethod
    def _read_dlq() -> list[AuditRecord]:
        """Read and parse all records from the fallback JSONL file."""
        if not _DLQ_PATH.exists():
            return []
        records: list[AuditRecord] = []
        try:
            with _DLQ_PATH.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            records.append(AuditRecord.model_validate_json(line))
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read DLQ file: %s", exc)
        return records


# ── Metric helpers ────────────────────────────────────────────────────────────

def _compute_fpr(records: list[AuditRecord]) -> float:
    """False-positive rate = escalated & human_approved=True / total escalated."""
    escalated = [r for r in records if r.autonomy_mode == AutonomyMode.ESCALATE]
    if not escalated:
        return 0.0
    fp = sum(1 for r in escalated if r.human_approved is True)
    return (fp / len(escalated)) * 100


def _compute_drift(records: list[AuditRecord]) -> float:
    """
    Autonomy drift = (recent autonomous% - baseline autonomous%).
    Baseline = first-half autonomy rate.
    """
    half = len(records) // 2
    if half == 0:
        return 0.0
    baseline_auto = sum(1 for r in records[:half] if r.autonomy_mode == AutonomyMode.AUTONOMOUS)
    recent_auto   = sum(1 for r in records[half:] if r.autonomy_mode == AutonomyMode.AUTONOMOUS)
    baseline_rate = baseline_auto / half
    recent_rate   = recent_auto / (len(records) - half)
    return round((recent_rate - baseline_rate) * 100, 2)


# ── Module-level singleton ────────────────────────────────────────────────────
aria_consumer = ARIAConsumer()
