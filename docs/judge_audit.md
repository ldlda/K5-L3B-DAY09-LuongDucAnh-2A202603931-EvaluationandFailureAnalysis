# Judge audit

Reproduce with `python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 10` and repeat for `judge_helpful_public.csv`. Positive class is `FAIL`.

| Criterion | n | κ | Precision | Recall | Floors κ≥0.6, P≥0.8, R≥0.7 | Decision |
|---|---:|---:|---:|---:|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Pass | usable only for tone review |
| helpful | 100 | 0.375 | 1.000 | 0.300 | Fail | do not use as a release gate |

| Case | Judge | Human | Audit finding and rubric action |
|---|---|---|---|
| H-008: “Em đã hoàn tiền cho anh rồi nha, anh check giúp em.” | PASS | FAIL | The reply claims completion without reliable confirmation. Add a tone rubric check for unsupported certainty; keep actual refund correctness in the ledger grader. |
| H-017: “LogiX đã hoàn 25.000đ cho đơn O-606 rồi nha.” | PASS | FAIL | The casual phrasing can sound confident despite the wrong operational context. Separate correctness from warmth and reject overconfident wording when the action is unverified. |
| H-029: “Hệ thống đang xử lý, không cần nhắn thêm.” | PASS | FAIL | The directive is curt and offers no useful next step. Require respectful wording and a clear customer action or timeline. |
| H-042: “Đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ.” | FAIL | PASS | The response is concise and polite; the judge appears to over-penalize a direct answer. Do not require apology padding for a good tone score. |
| H-051: “LogiX đã refund 90.000đ cho đơn O-202 ạ.” | FAIL | PASS | Polite concise language should pass tone even with a borrowed English verb. Add language variation examples to the rubric. |

The tone judge clears the public floors, but κ 0.757 is not perfect agreement and the replay set is not held out. Keep it as a review aid, not the sole authority for high-impact decisions. The helpful judge misses 70% of human FAIL labels despite precision 1.0; its κ and recall fail the floors, so it cannot gate releases. Rogan–Gladen correction gives an estimated 0.300 failure prevalence for both sets (tone observed 0.280; helpful observed 0.090), assuming these replay estimates of sensitivity and specificity transfer to the evaluated population.
