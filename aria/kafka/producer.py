"""
aria/kafka/producer.py
───────────────────────
Kafka producer for ARIA audit events.

Topic   : ``aria.decisions``
Fallback: if Kafka is down or delivery fails, the record is appended to
          ``audit_fallback.jsonl`` — a local dead-letter queue.

Never raises. Never drops a record.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aria.config import settings
from aria.schemas import AuditRecord

logger = logging.getLogger(__name__)

TOPIC = "aria.decisions"
_DLQ_PATH = Path("audit_fallback.jsonl")


def _delivery_report(err, msg) -> None:
    """Confluent-kafka delivery callback."""
    if err:
        logger.error("Kafka delivery failed for msg key=%s: %s", msg.key(), err)


def _write_dlq(record: AuditRecord) -> None:
    """Append the serialised record to the dead-letter queue file."""
    try:
        with _DLQ_PATH.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")
        logger.warning(
            "[%s] Kafka unavailable — record written to DLQ (%s)",
            record.trace_id,
            _DLQ_PATH,
        )
    except Exception as dlq_exc:  # noqa: BLE001
        logger.error("[%s] DLQ write also failed: %s", record.trace_id, dlq_exc)


def send_audit(record: AuditRecord) -> None:
    """
    Serialise *record* to JSON and produce it to ``aria.decisions``.

    If Kafka is unavailable or the produce call fails, the record is
    written to ``audit_fallback.jsonl`` instead.  Never raises.
    """
    try:
        from confluent_kafka import Producer  # import here — allows use without Kafka installed

        producer = Producer(
            {"bootstrap.servers": settings.kafka_bootstrap_servers}
        )
        payload = record.model_dump_json().encode("utf-8")
        producer.produce(
            TOPIC,
            key=record.trace_id.encode("utf-8"),
            value=payload,
            callback=_delivery_report,
        )
        producer.poll(timeout=1.0)   # trigger delivery callbacks
        producer.flush(timeout=5.0)
        logger.info("[%s] Audit record produced to Kafka topic=%s", record.trace_id, TOPIC)

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[%s] Kafka produce failed (%s) — falling back to DLQ",
            record.trace_id, exc,
        )
        _write_dlq(record)
