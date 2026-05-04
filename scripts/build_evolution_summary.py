from __future__ import annotations

import argparse
import json
from pathlib import Path

from hermes_constants import get_hermes_home


def _read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="Output summary path")
    args = parser.parse_args()

    outcomes_path = get_hermes_home() / "evolution" / "outcomes.jsonl"
    rows = [r for r in _read_jsonl(outcomes_path) if r.get("type") == "run_outcome"]
    total = len(rows)
    completed = sum(1 for r in rows if r.get("completed"))
    interrupted = sum(1 for r in rows if r.get("interrupted"))
    retries = sum(int(r.get("retry_count", 0) or 0) for r in rows)
    fallback_hits = sum(1 for r in rows if r.get("fallback_active"))
    total_latency = sum(float(r.get("latency_seconds", 0.0) or 0.0) for r in rows)
    total_cost = sum(float(r.get("estimated_cost_usd", 0.0) or 0.0) for r in rows)

    summary = {
        "source": str(outcomes_path),
        "runs_total": total,
        "completion_rate": (completed / total) if total else 0.0,
        "interruption_rate": (interrupted / total) if total else 0.0,
        "avg_retry_count": (retries / total) if total else 0.0,
        "fallback_rate": (fallback_hits / total) if total else 0.0,
        "avg_latency_seconds": (total_latency / total) if total else 0.0,
        "avg_cost_usd": (total_cost / total) if total else 0.0,
    }

    out_path = Path(args.out) if args.out else get_hermes_home() / "evolution" / "summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

