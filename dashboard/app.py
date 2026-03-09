"""
dashboard/app.py
─────────────────
ARIA Streamlit Monitoring Dashboard

Run:
    streamlit run dashboard/app.py

Expects the ARIA FastAPI server running at http://localhost:8000
(override by setting ARIA_API_URL env var).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
import pandas as pd
import streamlit as st

# ── Allow imports from project root ──────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from aria.config import settings

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ARIA — Risk Intelligence Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Constants ─────────────────────────────────────────────────────────────────
API_BASE = os.getenv("ARIA_API_URL", f"http://localhost:{settings.api_port}")
REFRESH_INTERVAL = 10  # seconds
LOW_T  = settings.risk_low_threshold
HIGH_T = settings.risk_high_threshold

# ── Colour map ────────────────────────────────────────────────────────────────
MODE_COLORS = {
    "AUTONOMOUS": "#22c55e",   # green
    "SUPERVISED": "#f97316",   # orange
    "ESCALATE":   "#ef4444",   # red
}

# ── HTTP helpers (never crash the dashboard) ──────────────────────────────────

def _get(path: str, timeout: float = 4.0) -> Any | None:
    try:
        r = httpx.get(f"{API_BASE}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return None


def _post(path: str, payload: dict, timeout: float = 6.0) -> tuple[bool, dict]:
    try:
        r = httpx.post(f"{API_BASE}{path}", json=payload, timeout=timeout)
        r.raise_for_status()
        return True, r.json()
    except Exception as exc:
        return False, {"error": str(exc)}


# ── Mock data helpers ─────────────────────────────────────────────────────────

def _mock_metrics() -> dict:
    return {
        "total_decisions": 0, "autonomous_count": 0,
        "supervised_count": 0, "escalate_count": 0,
        "escalation_rate_pct": 0.0, "avg_risk_score": 0.0,
        "p95_latency_ms": 0.0, "false_positive_rate_pct": 3.2,
        "autonomy_drift_7d": -1.5,
    }


def _mock_drift_df() -> pd.DataFrame:
    """7 days of synthetic hourly avg risk data when fewer than 10 real records exist."""
    now = datetime.now(tz=timezone.utc)
    hours = [(now - timedelta(hours=i)).strftime("%Y-%m-%d %H:00") for i in range(167, -1, -1)]
    scores = []
    import math, random
    random.seed(42)
    for i in range(168):
        base = 45 + 20 * math.sin(i / 24 * 3.14)
        scores.append(round(base + random.uniform(-5, 5), 1))
    return pd.DataFrame({"hour": hours, "avg_risk_score": scores})


# ═════════════════════════════════════════════════════════════════════════════
#  Dashboard header
# ═════════════════════════════════════════════════════════════════════════════

st.markdown(
    """
    <h1 style="color:#6366f1;margin-bottom:0">🛡️ ARIA Risk Intelligence Dashboard</h1>
    <p style="color:#94a3b8;margin-top:4px">Autonomous Risk-Aware Intelligence Architecture — Live Monitor</p>
    <hr style="border-color:#1e293b;margin:8px 0 20px 0"/>
    """,
    unsafe_allow_html=True,
)

# Auto-refresh counter displayed in sidebar
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    st.write(f"**API:** `{API_BASE}`")
    st.write(f"**Low threshold:** {LOW_T}")
    st.write(f"**High threshold:** {HIGH_T}")
    st.write(f"**Auto-refresh:** every {REFRESH_INTERVAL}s")
    if st.button("🔄 Refresh now"):
        st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
#  Section 1 — Live Metrics
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("## 📊 Section 1 — Live Metrics")

metrics_data = _get("/v1/metrics") or _mock_metrics()

col1, col2, col3, col4 = st.columns(4)
col1.metric("🔢 Total Decisions",    metrics_data.get("total_decisions", 0))
col2.metric("🚨 Escalation Rate",    f"{metrics_data.get('escalation_rate_pct', 0.0):.1f}%")
col3.metric("⚖️ Avg Risk Score",     f"{metrics_data.get('avg_risk_score', 0.0):.1f}")
col4.metric("⚡ P95 Latency",        f"{metrics_data.get('p95_latency_ms', 0.0):.0f} ms")

# Bar chart: mode distribution
bar_df = pd.DataFrame({
    "Mode":  ["AUTONOMOUS", "SUPERVISED", "ESCALATE"],
    "Count": [
        metrics_data.get("autonomous_count", 0),
        metrics_data.get("supervised_count", 0),
        metrics_data.get("escalate_count",   0),
    ],
})
st.bar_chart(bar_df.set_index("Mode"), color="#6366f1", height=220)

st.caption(f"Last refreshed: {datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')}")


# ═════════════════════════════════════════════════════════════════════════════
#  Section 2 — Recent Decisions Table
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown("## 📋 Section 2 — Recent Decisions")

# Fetch recent audit records via the Kafka consumer's in-memory buffer
# We call get_metrics to confirm freshness, but load records differently
# Since REST has no /v1/decisions list endpoint yet, we read from the consumer directly
try:
    from aria.kafka.consumer import aria_consumer
    raw_records = aria_consumer.get_records()[-20:]
except Exception:
    raw_records = []

if raw_records:
    rows = []
    for r in reversed(raw_records):  # most recent first
        ts = r.timestamp.strftime("%H:%M:%S") if hasattr(r.timestamp, "strftime") else str(r.timestamp)
        rows.append({
            "trace_id":     r.trace_id[:16],
            "user_intent":  r.user_intent[:40],
            "risk_score":   r.risk_score,
            "autonomy_mode": r.autonomy_mode.value if hasattr(r.autonomy_mode, "value") else str(r.autonomy_mode),
            "outcome":      r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome),
            "timestamp":    ts,
        })
    df = pd.DataFrame(rows)

    def _row_style(row):
        color_map = {"AUTONOMOUS": "#14532d", "SUPERVISED": "#431407", "ESCALATE": "#450a0a"}
        bg = color_map.get(row.get("autonomy_mode", ""), "#1e293b")
        return [f"background-color:{bg}"] * len(row)

    st.dataframe(
        df.style.apply(_row_style, axis=1),
        use_container_width=True,
        height=300,
    )
else:
    st.info("No audit records yet. Submit a request via **POST /v1/request** to see decisions here.")


# ═════════════════════════════════════════════════════════════════════════════
#  Section 3 — Autonomy Drift Chart
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown("## 📈 Section 3 — Autonomy Drift & Risk Trend")

# Build hourly avg risk from real records, or fall back to mock
if len(raw_records) >= 10:
    records_df = pd.DataFrame([
        {
            "hour": r.timestamp.strftime("%Y-%m-%d %H:00"),
            "risk_score": r.risk_score,
        }
        for r in raw_records
    ])
    drift_df = records_df.groupby("hour")["risk_score"].mean().reset_index()
    drift_df.columns = ["hour", "avg_risk_score"]
    drift_df = drift_df.sort_values("hour")
    using_mock = False
else:
    drift_df = _mock_drift_df()
    using_mock = True

if using_mock:
    st.warning("⚠️ Fewer than 10 real records — showing 7-day synthetic data for reference.")

# 7-day drift warning
drift_7d = metrics_data.get("autonomy_drift_7d", 0.0)
if abs(drift_7d) > 5.0:
    st.error(
        f"🚨 **Autonomy drift alert:** 7-day drift is **{drift_7d:+.1f}%** — "
        "agent behaviour has deviated significantly from baseline."
    )

# Line chart
st.line_chart(drift_df.set_index("hour")["avg_risk_score"], height=240, color="#6366f1")

# Threshold reference lines via caption (Streamlit doesn't support hlines on line_chart natively)
st.caption(
    f"Horizontal reference lines — 🟢 LOW threshold: {LOW_T}   🔴 HIGH threshold: {HIGH_T}   "
    f"| 7-day drift: **{drift_7d:+.1f}%**"
)


# ═════════════════════════════════════════════════════════════════════════════
#  Section 4 — Pending Approvals
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown("## ✋ Section 4 — Pending Approvals")

pending = _get("/v1/pending") or []

if not pending:
    st.success("✅ No actions pending human review.")
else:
    st.warning(f"⏳ **{len(pending)} action(s)** require your review:")

    for item in pending:
        tid      = item.get("trace_id", "")
        intent   = item.get("user_intent", "—")
        score    = item.get("risk_score", "?")
        explain  = item.get("explanation", "—")

        with st.expander(f"🔴 `{tid}` — {intent}  (risk score: {score})", expanded=True):
            col_info, col_actions = st.columns([3, 1])

            with col_info:
                st.markdown(f"**Intent:** {intent}")
                st.markdown(f"**Risk Score:** `{score}` / 100")
                st.markdown(f"**Explanation:** {explain}")

            with col_actions:
                key_approve = f"approve_{tid}"
                key_deny    = f"deny_{tid}"

                if st.button("✅ Approve", key=key_approve, type="primary"):
                    ok, resp = _post("/v1/approve", {
                        "trace_id":    tid,
                        "approved":    True,
                        "reviewed_by": "dashboard_operator",
                        "notes":       "Approved via ARIA dashboard",
                    })
                    if ok:
                        st.success(f"✅ Approved — {resp.get('message','')}")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"❌ Approve failed: {resp.get('error', resp)}")

                if st.button("❌ Deny", key=key_deny):
                    ok, resp = _post("/v1/approve", {
                        "trace_id":    tid,
                        "approved":    False,
                        "reviewed_by": "dashboard_operator",
                        "notes":       "Denied via ARIA dashboard",
                    })
                    if ok:
                        st.warning(f"🚫 Denied — {resp.get('message','')}")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"❌ Deny failed: {resp.get('error', resp)}")


# ═════════════════════════════════════════════════════════════════════════════
#  Auto-refresh footer
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.caption(
    f"ARIA v1.0.0 · Auto-refreshing every {REFRESH_INTERVAL}s · "
    f"API: `{API_BASE}` · "
    f"Thresholds: LOW≤{LOW_T} · HIGH≥{HIGH_T}"
)

# Auto-refresh via meta refresh via st.html (Streamlit ≥ 1.31 supports st.html)
try:
    st.html(f'<meta http-equiv="refresh" content="{REFRESH_INTERVAL}">')
except AttributeError:
    # Older Streamlit — use components or just skip auto-refresh
    pass
