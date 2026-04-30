"""argparse glue for ``hermes lifecycle``."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

from hermes_cli.lifecycle.core import (
    DEFAULT_RULES,
    Action,
    Rule,
    RunSummary,
    run_rules,
    status,
)


def _hermes_home() -> Path:
    # hermes_constants.get_default_hermes_root returns a Path.
    try:
        from hermes_constants import get_default_hermes_root
        return Path(get_default_hermes_root())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _filter_rules(rules: List[Rule], include: List[str] | None,
                  exclude: List[str] | None) -> List[Rule]:
    if include:
        inc = set(include)
        rules = [r for r in rules if r.name in inc]
    if exclude:
        exc = set(exclude)
        rules = [r for r in rules if r.name not in exc]
    return rules


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _cmd_status(args) -> int:
    home = _hermes_home()
    snap = status(home, DEFAULT_RULES)

    print(f"Hermes home: {home}")
    print(f"{'RULE':<14} {'KIND':<12} {'EXISTS':<7} {'SIZE':>10}  PATH")
    total = 0
    for row in snap:
        total += row["size"]
        print(f"{row['name']:<14} {row['kind']:<12} "
              f"{'yes' if row['exists'] else 'no':<7} "
              f"{_fmt_size(row['size']):>10}  {row['path']}")
    print(f"\nTotal tracked: {_fmt_size(total)}")
    return 0


def _cmd_run(args) -> int:
    home = _hermes_home()
    rules = _filter_rules(DEFAULT_RULES, args.only, args.skip)

    if not rules:
        print("No rules selected.", file=sys.stderr)
        return 1

    summary = run_rules(home, rules, dry_run=args.dry_run)

    mode = "DRY-RUN" if args.dry_run else "EXECUTED"
    print(f"[{mode}] {len(summary.actions)} action(s) across {len(rules)} rule(s):\n")

    if not summary.actions:
        print("  Nothing to do — everything already within retention.")
        return 0

    print(f"{'RULE':<14} {'OP':<9} {'BYTES IN':>10} {'SAVED':>10}  TARGET")
    for a in summary.actions:
        print(f"{a.rule:<14} {a.op:<9} "
              f"{_fmt_size(a.bytes_in):>10} {_fmt_size(a.saved()):>10}  {a.source}")

    print()
    print(f"  compressed: {summary.count('compress'):>4}   "
          f"delete: {summary.count('delete'):>4}   "
          f"archive: {summary.count('archive'):>4}   "
          f"vacuum: {summary.count('vacuum'):>4}   "
          f"skip: {summary.count('skip'):>4}")
    print(f"  Estimated space freed: {_fmt_size(summary.total_saved())}")
    if args.dry_run:
        print("\n  Re-run without --dry-run to execute.")
    return 0


_CRON_JOB_NAME = "hermes-lifecycle-daily"


def _cmd_schedule(args) -> int:
    """Emit systemd unit + timer recipe to run lifecycle daily.

    We don't install anything automatically — unit files under
    /etc/systemd/system require root and touch shared state. Instead we
    print ready-to-paste content and the three enable commands. Users
    who want a one-liner can pipe via ``sudo tee``.
    """
    command = f"{sys.executable} -m hermes_cli.main lifecycle run --execute"

    if args.disable:
        print("To disable the timer:")
        print("  sudo systemctl disable --now hermes-lifecycle.timer")
        print("  sudo rm /etc/systemd/system/hermes-lifecycle.{service,timer}")
        print("  sudo systemctl daemon-reload")
        return 0

    hermes_home = _hermes_home()
    service = f"""[Unit]
Description=Hermes data lifecycle sweep
After=hermes-gateway.service

[Service]
Type=oneshot
Environment=HOME={hermes_home.parent if hermes_home.name == '.hermes' else '/root'}
Environment=HERMES_HOME={hermes_home}
ExecStart={command}
"""

    timer = f"""[Unit]
Description=Daily Hermes data lifecycle sweep

[Timer]
OnCalendar={args.on_calendar}
Persistent=true
RandomizedDelaySec=15min

[Install]
WantedBy=timers.target
"""

    print("# --- /etc/systemd/system/hermes-lifecycle.service ---")
    print(service)
    print("# --- /etc/systemd/system/hermes-lifecycle.timer ---")
    print(timer)
    print("# --- enable ---")
    print("# sudo tee /etc/systemd/system/hermes-lifecycle.service >/dev/null <<'EOF'")
    print("# (paste service content above, then EOF)")
    print("# sudo tee /etc/systemd/system/hermes-lifecycle.timer >/dev/null <<'EOF'")
    print("# (paste timer content above, then EOF)")
    print("sudo systemctl daemon-reload")
    print("sudo systemctl enable --now hermes-lifecycle.timer")
    print("sudo systemctl list-timers hermes-lifecycle.timer")
    return 0


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------

def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``lifecycle`` subcommand to *subparsers*."""
    p = subparsers.add_parser(
        "lifecycle",
        help="Manage long-term data retention — compress/archive/prune ~/.hermes",
        description=(
            "Tier data under ~/.hermes by age: compress warm entries with zstd, "
            "delete cold ones past retention, rotate bak files, and VACUUM state.db. "
            "Default rules are conservative; use `--dry-run` first."
        ),
    )
    sub = p.add_subparsers(dest="lifecycle_command", required=True)

    st = sub.add_parser("status", help="Show size & path for each tracked target")
    st.set_defaults(func=_cmd_status)

    rn = sub.add_parser("run", help="Execute lifecycle rules (dry-run by default)")
    rn.add_argument("--execute", dest="dry_run", action="store_false",
                    help="Actually perform actions (default is dry-run)")
    rn.add_argument("--only", nargs="+", metavar="RULE",
                    help="Only run these rule names (by --list)")
    rn.add_argument("--skip", nargs="+", metavar="RULE",
                    help="Skip these rule names")
    rn.set_defaults(func=_cmd_run, dry_run=True)

    sc = sub.add_parser(
        "schedule",
        help="Print ready-to-install systemd service+timer for daily sweeps",
    )
    sc.add_argument("--on-calendar", default="03:30",
                    help="systemd OnCalendar= expression (default: 03:30 daily)")
    sc.add_argument("--disable", action="store_true",
                    help="Print the uninstall commands instead")
    sc.set_defaults(func=_cmd_schedule)

    p.set_defaults(func=lambda a: (_cmd_status(a)
                                   if getattr(a, "lifecycle_command", None) is None
                                   else a.func(a)))
