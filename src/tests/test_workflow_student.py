"""Focused evidence for Nguyen Minh Quan's deterministic workflow contribution."""

from lab8.db import init_store
from lab8.shapes.workflow import run_claim
from tests.conftest import CLAIMS, WORLD


def test_workflow_is_deterministic_and_model_free(tmp_path):
    """A valid claim is resolved by trusted tools without an LLM decision."""

    store = tmp_path / "workflow-student.sqlite"
    init_store(store, WORLD, CLAIMS).close()

    result = run_claim("C-001", seed=11, store_path=str(store))

    assert result.status == "resolved"
    assert result.proposal_amount == 35_000
    assert result.model_calls == 0
    assert result.tool_calls == 3
    assert result.committed_audit_count == 1
    assert len(result.refund_effects) == 1
    assert result.refund_effects[0]["amount_vnd"] == 35_000
