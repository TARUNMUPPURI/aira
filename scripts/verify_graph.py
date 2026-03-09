import ast
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

# Structural checks
for f in [
    "aria/graph/state.py",
    "aria/graph/nodes.py",
    "aria/graph/graph.py",
    "aria/agents/audit_agent.py",
]:
    ast.parse(open(f, encoding="utf-8").read())
    print(f"AST OK: {f}")

# Graph compiles cleanly?
from aria.graph.graph import aria_graph  # noqa: E402
print(f"Graph compiled: nodes={list(aria_graph.nodes.keys())}")

# Live test — only if a real key is present
from dotenv import load_dotenv  # noqa: E402
load_dotenv()
key = os.getenv("GEMINI_API_KEY", "")
if not key or key == "your-gemini-api-key-here":
    print("SKIP_LIVE: No real GEMINI_API_KEY found in .env")
    raise SystemExit(0)

from aria.schemas import UserRequest, AutonomyMode  # noqa: E402

# Test 1: get account balance → AUTONOMOUS
r1 = aria_graph.invoke({
    "request": UserRequest(
        session_id="verify",
        user_intent="get account balance",
        action_type="read",
    )
})
mode1 = r1["autonomy_decision"].autonomy_mode
score1 = r1["risk_assessment"].risk_score
print(f"[LOW]  mode={mode1} score={score1}")
assert mode1 == AutonomyMode.AUTONOMOUS, f"Expected AUTONOMOUS, got {mode1}"
assert r1["audit_record"] is not None, "audit_record should not be None"
assert r1["audit_record"].latency_ms is not None

# Test 2: transfer externally → ESCALATE
r2 = aria_graph.invoke({
    "request": UserRequest(
        session_id="verify",
        user_intent="transfer funds externally",
        action_type="transfer",
    )
})
mode2 = r2["autonomy_decision"].autonomy_mode
score2 = r2["risk_assessment"].risk_score
print(f"[HIGH] mode={mode2} score={score2}")
assert mode2 == AutonomyMode.ESCALATE, f"Expected ESCALATE, got {mode2}"
assert r2["audit_record"] is not None, "audit_record should not be None"
assert r2["action_result"] is not None  # escalation explanation

print("ALL LIVE CHECKS PASSED")
