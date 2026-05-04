from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", required=True, help="Control summary JSON")
    parser.add_argument("--b", required=True, help="Candidate summary JSON")
    args = parser.parse_args()

    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))

    keys = [
        "completion_rate",
        "interruption_rate",
        "avg_retry_count",
        "fallback_rate",
        "avg_latency_seconds",
        "avg_cost_usd",
    ]
    print("metric,control,candidate,delta")
    for k in keys:
        av = float(a.get(k, 0.0) or 0.0)
        bv = float(b.get(k, 0.0) or 0.0)
        print(f"{k},{av:.6f},{bv:.6f},{(bv-av):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

