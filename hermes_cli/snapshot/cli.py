"""argparse glue for ``hermes snapshot``."""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from hermes_cli.snapshot.builder import (
    create_snapshot,
    default_snapshot_name,
)
from hermes_cli.snapshot.restore import (
    ONLY_GROUPS,
    restore_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hermes_home() -> Path:
    try:
        from hermes_constants import get_default_hermes_root
        return Path(get_default_hermes_root())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))


def _snapshot_dir(home: Path) -> Path:
    d = home / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _resolve_passphrase(args) -> Optional[str]:
    if not getattr(args, "encrypt", False):
        return None
    if args.passphrase:
        return args.passphrase
    env = os.environ.get("HERMES_SNAPSHOT_KEY")
    if env:
        return env
    return getpass.getpass("Snapshot passphrase: ")


_SNAP_RE = re.compile(
    r"^hermes-snapshot-[^-]+-(?P<tag>lite|full)-(?P<ts>\d{8}-\d{6})\.tar\.zst(\.age)?$"
)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _cmd_create(args) -> int:
    home = _hermes_home()
    passphrase = _resolve_passphrase(args)
    encrypt = bool(passphrase)

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = _snapshot_dir(home) / default_snapshot_name(args.lite, encrypt)

    print(f"Creating snapshot: {out_path}")
    print(f"  mode: {'lite' if args.lite else 'full'}"
          f"{' + encrypted' if encrypt else ''}")
    t0 = time.time()
    manifest = create_snapshot(
        home,
        out_path,
        lite=args.lite,
        include_env=not args.no_env,
        passphrase=passphrase,
    )
    dt = time.time() - t0

    size = out_path.stat().st_size if out_path.exists() else 0
    ratio = (size / manifest.total_bytes * 100) if manifest.total_bytes else 0
    print(f"  files included : {manifest.file_count}")
    print(f"  raw size       : {_fmt_size(manifest.total_bytes)}")
    print(f"  archive size   : {_fmt_size(size)} ({ratio:.1f}%)")
    print(f"  sha256         : {manifest.sha256[:16]}…")
    print(f"  elapsed        : {dt:.1f}s")
    print(f"\nManifest: {out_path.with_suffix(out_path.suffix + '.manifest.yaml')}")
    return 0


def _cmd_list(args) -> int:
    home = _hermes_home()
    sdir = _snapshot_dir(home)
    entries = sorted(
        p for p in sdir.glob("hermes-snapshot-*.tar.zst*")
        if not p.name.endswith(".manifest.yaml")
    )
    if not entries:
        print(f"No snapshots found under {sdir}")
        return 0
    print(f"{'SIZE':>10}  {'AGE':>8}   NAME")
    now = time.time()
    for p in entries:
        age_h = (now - p.stat().st_mtime) / 3600
        age_str = f"{age_h:.1f}h" if age_h < 48 else f"{age_h / 24:.1f}d"
        print(f"{_fmt_size(p.stat().st_size):>10}  {age_str:>8}   {p.name}")
    return 0


def _cmd_restore(args) -> int:
    home = _hermes_home()
    src = Path(args.path)
    if not src.exists():
        print(f"Not found: {src}", file=sys.stderr)
        return 1

    # Encrypted snapshots need a passphrase; probe first.
    passphrase: Optional[str] = None
    try:
        from hermes_cli.snapshot.crypto import is_encrypted
        if is_encrypted(src):
            passphrase = (args.passphrase
                          or os.environ.get("HERMES_SNAPSHOT_KEY")
                          or getpass.getpass("Snapshot passphrase: "))
    except Exception:
        pass

    result = restore_snapshot(
        src, home,
        only=args.only,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        passphrase=passphrase,
    )

    mode = "DRY-RUN" if result.dry_run else "RESTORED"
    print(f"[{mode}] from {src}")
    print(f"  manifest: host={result.manifest.get('hostname')} "
          f"created={result.manifest.get('created_at')} "
          f"lite={result.manifest.get('lite')}")
    print(f"  files  : {result.files_extracted}   bytes : "
          f"{_fmt_size(result.bytes_extracted)}   skipped : {result.skipped}")
    if result.dry_run:
        print("\n  Re-run with --execute to actually restore.")
    return 0


def _cmd_prune(args) -> int:
    """Grandfather-father-son retention over snapshots in ~/.hermes/snapshots/."""
    home = _hermes_home()
    sdir = _snapshot_dir(home)
    now = time.time()

    items: List[tuple[float, Path]] = []
    for p in sdir.glob("hermes-snapshot-*.tar.zst*"):
        m = _SNAP_RE.match(p.name)
        if not m:
            continue
        try:
            t = time.mktime(time.strptime(m.group("ts"), "%Y%m%d-%H%M%S"))
        except ValueError:
            continue
        items.append((t, p))

    items.sort(reverse=True)
    keep: set[Path] = set()

    # Daily: keep the newest snapshot per calendar day (local).
    seen_day: set[str] = set()
    for t, p in items:
        day = time.strftime("%Y-%m-%d", time.localtime(t))
        if day in seen_day:
            continue
        seen_day.add(day)
        if len(seen_day) <= args.keep_daily:
            keep.add(p)

    # Weekly (ISO week).
    seen_week: set[str] = set()
    for t, p in items:
        week = time.strftime("%G-W%V", time.localtime(t))
        if week in seen_week:
            continue
        seen_week.add(week)
        if len(seen_week) <= args.keep_weekly:
            keep.add(p)

    # Monthly.
    seen_month: set[str] = set()
    for t, p in items:
        month = time.strftime("%Y-%m", time.localtime(t))
        if month in seen_month:
            continue
        seen_month.add(month)
        if len(seen_month) <= args.keep_monthly:
            keep.add(p)

    deleted = 0
    freed = 0
    for _, p in items:
        if p in keep:
            continue
        freed += p.stat().st_size
        deleted += 1
        if args.dry_run:
            print(f"  would delete: {p.name}")
        else:
            p.unlink(missing_ok=True)
            sidecar = p.with_suffix(p.suffix + ".manifest.yaml")
            sidecar.unlink(missing_ok=True)

    mode = "DRY-RUN" if args.dry_run else "PRUNED"
    print(f"[{mode}] keep {len(keep)} snapshot(s), "
          f"delete {deleted}, free {_fmt_size(freed)}")
    return 0


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------

def register_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "snapshot",
        help="Portable, optionally-encrypted snapshots of ~/.hermes",
        description=(
            "Create/restore/list/prune Hermes snapshots. Snapshots are "
            "tar+zstd (optionally AES-256-GCM) with an embedded manifest "
            "and sha256 checksum. Supersedes `hermes backup`."
        ),
    )
    sub = p.add_subparsers(dest="snapshot_command", required=True)

    c = sub.add_parser("create", help="Create a new snapshot")
    c.add_argument("--out", help="Output path "
                                 "(default: ~/.hermes/snapshots/<auto-name>)")
    c.add_argument("--lite", action="store_true",
                   help="Exclude state.db, caches, and sessions for a slim archive")
    c.add_argument("--no-env", action="store_true",
                   help="Exclude .env secrets from the snapshot")
    c.add_argument("--encrypt", action="store_true",
                   help="AES-256-GCM encrypt with a passphrase")
    c.add_argument("--passphrase",
                   help="Encryption passphrase (prefer $HERMES_SNAPSHOT_KEY)")
    c.set_defaults(func=_cmd_create)

    r = sub.add_parser("restore", help="Restore a snapshot into ~/.hermes")
    r.add_argument("path", help="Path to a snapshot file")
    r.add_argument("--execute", dest="dry_run", action="store_false",
                   help="Actually write files (default is dry-run)")
    r.add_argument("--overwrite", action="store_true",
                   help="Overwrite existing files on conflict")
    r.add_argument("--only", nargs="+",
                   choices=list(ONLY_GROUPS.keys()),
                   help="Restore only selected groups: "
                        + " / ".join(ONLY_GROUPS.keys()))
    r.add_argument("--passphrase",
                   help="Passphrase for encrypted snapshots")
    r.set_defaults(func=_cmd_restore, dry_run=True)

    l = sub.add_parser("list", help="List local snapshots")
    l.set_defaults(func=_cmd_list)

    pr = sub.add_parser("prune", help="Apply GFS retention under ~/.hermes/snapshots")
    pr.add_argument("--keep-daily", type=int, default=7)
    pr.add_argument("--keep-weekly", type=int, default=4)
    pr.add_argument("--keep-monthly", type=int, default=12)
    pr.add_argument("--execute", dest="dry_run", action="store_false")
    pr.set_defaults(func=_cmd_prune, dry_run=True)

    p.set_defaults(func=lambda a: a.func(a))
