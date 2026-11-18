"""Statistics students implement (M2 judge calibration, M4 paired bootstrap).

Rules: a judge is a classifier -- measure it like one. A higher mean is not
"better" until the paired CI of the difference excludes 0.
Reference values to self-check are in tests/test_lab9_stats.py.
"""

from __future__ import annotations

from collections.abc import Sequence
import math
import inspect
from math import comb

import numpy as np
from scipy.stats import bootstrap

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    """tp/fp/fn/tn with FAIL as positive."""
    if len(human) != len(judge):
        raise ValueError("human and judge labels must have equal length")
    h = [str(x).strip().upper() for x in human]
    j = [str(x).strip().upper() for x in judge]
    if any(x not in {"PASS", "FAIL"} for x in (*h, *j)):
        raise ValueError("labels must be PASS or FAIL")
    return {"tp": sum(a == POS and b == POS for a, b in zip(h, j)),
            "fp": sum(a != POS and b == POS for a, b in zip(h, j)),
            "fn": sum(a == POS and b != POS for a, b in zip(h, j)),
            "tn": sum(a != POS and b != POS for a, b in zip(h, j))}


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    cm = confusion(human, judge)
    pden, rden = cm["tp"] + cm["fp"], cm["tp"] + cm["fn"]
    return (cm["tp"] / pden if pden else 0.0, cm["tp"] / rden if rden else 0.0)


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e). Raise ValueError when undefined (p_e == 1):
    a constant judge has no signal and must FAIL calibration, not pass on a technicality."""
    if len(human) != len(judge) or not human:
        raise ValueError("kappa requires equally sized, non-empty labels")
    cm = confusion(human, judge)
    n = len(human)
    po = (cm["tp"] + cm["tn"]) / n
    h_pos = (cm["tp"] + cm["fn"]) / n
    j_pos = (cm["tp"] + cm["fp"]) / n
    pe = h_pos * j_pos + (1 - h_pos) * (1 - j_pos)
    if math.isclose(pe, 1.0):
        raise ValueError("kappa undefined when expected agreement is 1")
    return (po - pe) / (1 - pe)


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed failure rate for judge error; clip to [0, 1]."""
    if not all(math.isfinite(x) for x in (observed_rate, sensitivity, specificity)):
        raise ValueError("rates must be finite")
    if not all(0 <= x <= 1 for x in (observed_rate, sensitivity, specificity)):
        raise ValueError("rates must be in [0, 1]")
    denom = sensitivity + specificity - 1
    if denom <= 0:
        raise ValueError("sensitivity + specificity must exceed 1")
    return min(1.0, max(0.0, (observed_rate + specificity - 1) / denom))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    """a[i], b[i] = per-item mean over seeds, SAME items, SAME order.
    Return (mean(a)-mean(b), lo, hi) with scipy.stats.bootstrap(paired=True, method="percentile",
    rng=numpy.random.default_rng(rng)). If a == b everywhere return (0.0, 0.0, 0.0)."""
    if len(a) != len(b) or not a:
        raise ValueError("paired bootstrap requires equally sized, non-empty samples")
    aa, bb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if not np.isfinite(aa).all() or not np.isfinite(bb).all():
        raise ValueError("bootstrap samples must be finite")
    if np.array_equal(aa, bb):
        return 0.0, 0.0, 0.0
    if n_resamples < 1 or not 0 < confidence < 1:
        raise ValueError("n_resamples must be positive and confidence in (0, 1)")
    kwargs = {"paired": True, "vectorized": True, "n_resamples": n_resamples,
              "confidence_level": confidence, "method": "percentile"}
    # SciPy renamed random_state to rng; support both while the lab pins the newer API.
    kwargs["rng" if "rng" in inspect.signature(bootstrap).parameters else "random_state"] = np.random.default_rng(rng)
    res = bootstrap((aa, bb), lambda x, y, axis: np.mean(x - y, axis=axis), **kwargs)
    return float(np.mean(aa - bb)), float(res.confidence_interval.low), float(res.confidence_interval.high)


def _validate_pass_args(n: int, c: int, k: int) -> None:
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (n, c, k)):
        raise ValueError("n, c, and k must be integers")
    if n < 1 or not 0 <= c <= n or not 1 <= k <= n:
        raise ValueError("require n >= 1, 0 <= c <= n, and 1 <= k <= n")


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError if k > n."""
    _validate_pass_args(n, c, k)
    return 1.0 - (comb(n - c, k) / comb(n, k) if n - c >= k else 0.0)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError if k > n.
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    _validate_pass_args(n, c, k)
    return comb(c, k) / comb(n, k) if c >= k else 0.0
