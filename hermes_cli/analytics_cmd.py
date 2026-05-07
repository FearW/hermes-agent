"""``hermes analytics`` — tool-usage analytics dashboard.

Subcommands
-----------
  summary   (default) per-tool call count, success rate, latency stats
  timeline  call volume over time (hourly / daily buckets)
  errors    top failing tools and error types
  purge     delete analytics data older than N days
"""

import json
import sys


def _fmt_pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "—"
    return f"{numerator / denominator * 100:.1f}%"


def _fmt_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def cmd_analytics(args):
    from agent.tool_analytics import (
        query_summary,
        query_timeline,
        query_top_errors,
        purge_old_data,
    )

    sub = getattr(args, "analytics_command", None) or "summary"
    period = getattr(args, "period", "day") or "day"
    fmt = getattr(args, "format", "table") or "table"
    tool_name = getattr(args, "tool_name", None)
    limit = getattr(args, "limit", 20)

    if sub == "summary":
        _show_summary(period, tool_name, limit, fmt)
    elif sub == "timeline":
        bucket = getattr(args, "bucket", "hour") or "hour"
        _show_timeline(period, tool_name, bucket, fmt)
    elif sub == "errors":
        _show_errors(period, limit, fmt)
    elif sub == "purge":
        max_days = getattr(args, "max_days", 90) or 90
        removed = purge_old_data(max_days)
        print(f"Purged {removed} records older than {max_days} days.")
    else:
        print(f"Unknown analytics subcommand: {sub}", file=sys.stderr)
        sys.exit(1)


def _show_summary(period: str, tool_name, limit: int, fmt: str):
    from agent.tool_analytics import query_summary

    rows = query_summary(period=period, tool_name=tool_name, limit=limit)
    if not rows:
        print("No analytics data found for the selected period.")
        return

    if fmt == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    period_label = {"hour": "1 hour", "day": "24 hours", "week": "7 days",
                    "month": "30 days", "all": "all time"}.get(period, period)
    print(f"\n  📊 Tool Usage Summary — last {period_label}\n")
    print(f"  {'Tool':<24} {'Calls':>7} {'Success':>8} {'Fail':>5} {'Rate':>7} {'Avg':>8} {'P50':>8} {'Max':>8}")
    print(f"  {'─' * 24} {'─' * 7} {'─' * 8} {'─' * 5} {'─' * 7} {'─' * 8} {'─' * 8} {'─' * 8}")
    for r in rows:
        total = r["total_calls"]
        succ = r["successes"]
        fail = r["failures"]
        rate = _fmt_pct(succ, total)
        avg = _fmt_ms(r["avg_ms"])
        p50 = _fmt_ms(r.get("p50_ms", 0))
        mx = _fmt_ms(r["max_ms"])
        name = r["tool_name"][:24]
        print(f"  {name:<24} {total:>7} {succ:>8} {fail:>5} {rate:>7} {avg:>8} {p50:>8} {mx:>8}")
    print()


def _show_timeline(period: str, tool_name, bucket: str, fmt: str):
    from agent.tool_analytics import query_timeline

    rows = query_timeline(period=period, tool_name=tool_name, bucket=bucket)
    if not rows:
        print("No analytics data found for the selected period.")
        return

    if fmt == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    bucket_label = {"hour": "Hourly", "day": "Daily", "week": "Weekly"}.get(bucket, bucket)
    print(f"\n  📈 Call Volume Timeline — {bucket_label}\n")
    print(f"  {'Bucket':<20} {'Calls':>7} {'Success':>8} {'Fail':>5} {'Avg ms':>8}")
    print(f"  {'─' * 20} {'─' * 7} {'─' * 8} {'─' * 5} {'─' * 8}")
    for r in rows:
        b = r["bucket"][:20]
        total = r["total_calls"]
        succ = r["successes"]
        fail = r["failures"]
        avg = _fmt_ms(r["avg_ms"])
        print(f"  {b:<20} {total:>7} {succ:>8} {fail:>5} {avg:>8}")
    print()


def _show_errors(period: str, limit: int, fmt: str):
    from agent.tool_analytics import query_top_errors

    rows = query_top_errors(period=period, limit=limit)
    if not rows:
        print("No error data found for the selected period.")
        return

    if fmt == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    period_label = {"hour": "1 hour", "day": "24 hours", "week": "7 days",
                    "month": "30 days", "all": "all time"}.get(period, period)
    print(f"\n  ❌ Top Errors — last {period_label}\n")
    print(f"  {'Tool':<24} {'Error Type':<24} {'Count':>7}")
    print(f"  {'─' * 24} {'─' * 24} {'─' * 7}")
    for r in rows:
        name = r["tool_name"][:24]
        err = r["error_type"][:24]
        count = r["count"]
        print(f"  {name:<24} {err:<24} {count:>7}")
    print()
