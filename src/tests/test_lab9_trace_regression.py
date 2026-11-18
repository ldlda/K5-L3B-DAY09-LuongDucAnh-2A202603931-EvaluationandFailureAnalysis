import pytest

pytestmark = pytest.mark.student


def test_c002_ticket_override_is_first_failure_and_fixed_is_clean():
    from lab9.trace_case import investigate

    bad = investigate("seeded_regression", "C-002", 11)["trial"]
    good = investigate("fixed", "C-002", 11)["trial"]
    bad_kinds = [event["kind"] for event in bad["events"]]
    good_kinds = [event["kind"] for event in good["events"]]
    assert bad_kinds.index("model_override") < bad_kinds.index("quote")
    assert bad["snapshot"]["refund_vnd"] == 20_000
    assert good["snapshot"]["refund_vnd"] == 0
    assert "model_override" not in good_kinds
