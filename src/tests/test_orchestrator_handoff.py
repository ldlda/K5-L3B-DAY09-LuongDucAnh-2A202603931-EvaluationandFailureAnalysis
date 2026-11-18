"""Shape C keeps the investigator handoff and trace within their allowlists."""

import json
from pathlib import Path

from lab8.shapes.orchestrator import run_claim


def test_orchestrator_trace_contains_only_sanitized_handoff(public_db):
    result = run_claim("C-001", seed=11, store_path=str(public_db))

    assert result.status == "resolved"
    trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert trace["run_id"] == result.run_id

    handoff_event = next(event for event in trace["events"] if event["kind"] == "handoff")
    handoff = handoff_event["payload"]
    assert handoff["contract_version"] == "handoff.v1"
    assert handoff["allowed_tools"] == [
        "lookup_order",
        "lookup_policy",
        "lookup_refund_history",
        "compute_compensation",
    ]

    trace_text = json.dumps(trace, ensure_ascii=False)
    assert "CANARY_SECRET_" not in trace_text
    assert "parent_secret" not in trace_text
    assert "raw_headers" not in trace_text
