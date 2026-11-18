"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from numbers import Real
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows = pr.get("rows") or []
    base_rows = base.get("rows") or []
    manifest = pr.get("manifest") or {}
    expected = int(manifest.get("items", 0)) * len(manifest.get("seeds", []))
    if len(rows) != expected:
        problems.append(f"row count {len(rows)} != manifest items×seeds {expected}")
    pairs = [(r.get("item_id"), r.get("seed")) for r in rows]
    if len(set(pairs)) != len(pairs):
        problems.append("duplicate (item_id, seed) rows")
    base_pairs = {(r.get("item_id"), r.get("seed")) for r in base_rows}
    if set(pairs) != base_pairs:
        problems.append("(item_id, seed) set differs from baseline")
    if pr.get("evaluator_failures"):
        problems.append("evaluator_failures is non-empty")
    for m in spec.get("metrics", []):
        mid = m["id"]
        val = (pr.get("metrics") or {}).get(mid)
        if not isinstance(val, Real) or not math.isfinite(float(val)):
            problems.append(f"metric {mid} is missing or non-finite")
        if mid not in GRADERS:
            continue
        for i, row in enumerate(rows):
            if mid not in row or not isinstance(row[mid], bool):
                problems.append(f"row {i} metric {mid} is missing or not boolean")
                break
    base_by_pair = {(r.get("item_id"), r.get("seed")): r for r in base_rows}
    for row in rows:
        old = base_by_pair.get((row.get("item_id"), row.get("seed")))
        if old is not None and sorted(row.get("slices") or []) != sorted(old.get("slices") or []):
            problems.append(f"slice tags changed for {row.get('item_id')}@{row.get('seed')}")
            break

    if base_spec and spec.get("eval_set_version") == base_spec.get("eval_set_version"):
        current = {m["id"]: m for m in spec.get("metrics", [])}
        previous = {m["id"]: m for m in base_spec.get("metrics", [])}
        for mid, old in previous.items():
            new = current.get(mid)
            if new is None:
                problems.append(f"ratchet: metric {mid} was removed")
                continue
            old_th, new_th = old.get("threshold") or {}, new.get("threshold") or {}
            if old.get("blocking") and not new.get("blocking"):
                problems.append(f"ratchet: metric {mid} is no longer blocking")
            if "min" in old_th and ("min" not in new_th or new_th["min"] < old_th["min"]):
                problems.append(f"ratchet: metric {mid} minimum was loosened")
            if "max" in old_th and ("max" not in new_th or new_th["max"] > old_th["max"]):
                problems.append(f"ratchet: metric {mid} maximum was loosened")
            if old_th.get("no_significant_drop") and not new_th.get("no_significant_drop"):
                problems.append(f"ratchet: metric {mid} removed no_significant_drop")
        old_slices = {(s.get("name"), s.get("metric")): s for s in base_spec.get("slices", [])}
        new_slices = {(s.get("name"), s.get("metric")): s for s in spec.get("slices", [])}
        for key, old in old_slices.items():
            new = new_slices.get(key)
            if new is None:
                problems.append(f"ratchet: slice {key[0]}/{key[1]} was removed")
                continue
            if old.get("blocking") and not new.get("blocking"):
                problems.append(f"ratchet: slice {key[0]}/{key[1]} is no longer blocking")
            if "min" in old and ("min" not in new or new["min"] < old["min"]):
                problems.append(f"ratchet: slice {key[0]}/{key[1]} minimum was loosened")
            if "max" in old and ("max" not in new or new["max"] > old["max"]):
                problems.append(f"ratchet: slice {key[0]}/{key[1]} maximum was loosened")
            if old.get("no_significant_drop") and not new.get("no_significant_drop"):
                problems.append(f"ratchet: slice {key[0]}/{key[1]} removed no_significant_drop")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices", []):
        name, metric = s["name"], s["metric"]
        values = [float(r[metric]) for r in pr.get("rows", [])
                  if name in (r.get("slices") or []) and r.get(metric) is not None]
        value = sum(values) / len(values) if values else None
        verdicts = []
        if value is None:
            verdicts.append("no rows")
        elif "min" in s and value < s["min"]:
            verdicts.append(f"{value:.3f} < min {s['min']}")
        note = ""
        if s.get("no_significant_drop"):
            a, b = per_item(pr, metric, name), per_item(base, metric, name)
            keys = sorted(set(a) & set(b))
            if not keys:
                verdicts.append("no paired items")
            else:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000),
                                                   rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdicts.append("CI below 0")
        ident = f"slice:{name}/{metric}"
        failed = bool(verdicts)
        lines.append(f"  {ident:<38} {value!s:<8} {note:<28} " +
                     ("BLOCK " + "; ".join(verdicts) if failed and s.get("blocking") else
                      "warn " + "; ".join(verdicts) if failed else "ok"))
        if failed and s.get("blocking"):
            blocked.append(ident)
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
