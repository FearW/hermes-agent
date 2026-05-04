from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--max-completion-drop", type=float, default=0.01)
    parser.add_argument("--max-latency-increase-ratio", type=float, default=0.15)
    parser.add_argument("--max-cost-increase-ratio", type=float, default=0.20)
    args = parser.parse_args()

    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))

    ok = True
    reasons: list[str] = []

    completion_delta = summary.get("completion_rate", 0.0) - baseline.get("completion_rate", 0.0)
    if completion_delta < -args.max_completion_drop:
        ok = False
        reasons.append(f"completion_rate drop too large: {completion_delta:.4f}")

    base_latency = float(baseline.get("avg_latency_seconds", 0.0) or 0.0)
    new_latency = float(summary.get("avg_latency_seconds", 0.0) or 0.0)
    if base_latency > 0 and (new_latency - base_latency) / base_latency > args.max_latency_increase_ratio:
        ok = False
        reasons.append("avg_latency_seconds increased beyond threshold")

    base_cost = float(baseline.get("avg_cost_usd", 0.0) or 0.0)
    new_cost = float(summary.get("avg_cost_usd", 0.0) or 0.0)
    if base_cost > 0 and (new_cost - base_cost) / base_cost > args.max_cost_increase_ratio:
        ok = False
        reasons.append("avg_cost_usd increased beyond threshold")

    if ok:
        print("PASS")
        return 0
    print("FAIL")
    for r in reasons:
        print(f"- {r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

