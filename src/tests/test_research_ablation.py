"""R2 experiment checks, prepared for Nguyen Nam Khanh with Codex assistance."""

import json
from pathlib import Path

import pytest

from bonus.research_ablation import run_claim as order_only
from lab8.db import connect, init_store
from lab8.metrics import trial_pass
from lab8.shapes.swarm_reference import run_claim as reference
from tests.conftest import BY_ID, CLAIMS, WORLD


@pytest.mark.parametrize("seed", [11, 22, 33, 44, 55])
def test_order_delay_ablation_repairs_t02_without_changing_model_calls(tmp_path, seed):
    """Compare fresh databases: T02 is repaired at seed 55; reference stays intact."""
    results = []
    for name, run in [("swarm", reference), ("swarm_order_only", order_only)]:
        store = tmp_path / f"{name}.sqlite"
        init_store(store, WORLD, CLAIMS).close()
        result = run("C-002", seed=seed, store_path=str(store))
        conn = connect(store)
        try:
            passed, _ = trial_pass(result.as_dict(), BY_ID["C-002"]["expected"], conn)
        finally:
            conn.close()
        trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
        results.append((result, passed, trace))

    before, before_passed, before_trace = results[0]
    after, after_passed, after_trace = results[1]
    assert before_passed == (seed != 55)
    assert after_passed
    assert after.status == "denied"
    assert after.proposal_amount == 0
    assert not after.refund_effects
    assert before.model_calls == after.model_calls == 3
    assert ("model_override" in [e["kind"] for e in before_trace["events"]]) == (seed == 55)
    assert "model_override" not in [e["kind"] for e in after_trace["events"]]
    if seed != 55:
        assert before_trace["events"] == after_trace["events"]
        assert before.cost_estimate == after.cost_estimate
