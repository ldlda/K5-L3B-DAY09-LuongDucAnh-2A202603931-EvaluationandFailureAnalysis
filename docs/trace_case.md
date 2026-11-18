# Trace case: ticket delay overrides database (FM-03)

Reproduce from `src/` with:

```bash
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11
```

| Field | Finding |
|---|---|
| Trial | `seeded_regression`, C-002@11; the reproducible run ID is `R-seeded_regression-C-002-s11`. |
| Symptom | Lab 8 reports `status resolved != denied`; the ledger contains a 20,000 VND refund while the label expects no refund. |
| First wrong step | Event 3, `model_override`: the agent replaces the database delay with the ticket's `5` days. |
| Why first | Events 1–2 establish the authorized handoff and retrieve the order from the database. The quote and ledger write happen after the override. |
| Failure mode | FM-03, `ticket_trust`. |
| Control | `fixed` C-002@11 uses the database delay, quotes zero, and leaves the refund ledger at zero. |
| Regression test | `test_c002_ticket_override_is_first_failure_and_fixed_is_clean` asserts the override appears only in the regression trace and that the fixed trace matches the zero-refund label. |
| Limits | This controlled MockLLM trace establishes the event order for this claim and seed. It does not estimate how often real users phrase conflicting ticket facts. |

The regression assertion is in `src/tests/test_lab9_trace_regression.py`; it exercises both the failing variant and its fixed control through the trace harness.
