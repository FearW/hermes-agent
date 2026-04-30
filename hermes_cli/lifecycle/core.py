"""Lifecycle core — rules, dispatch, summary.

Each Rule describes how one target under HERMES_HOME should be treated.
Handlers are dispatched by ``Rule.kind``:

- ``age_based``: entries with ``mtime >= warm_days`` → zstd-compress into
  ``archive/<subdir>/<YYYY-MM>/``; entries with ``mtime >= cold_days`` → delete.
- ``keep_latest``: keep newest ``keep`` immediate entries; archive the rest.
- ``bak_prefix``: group files by prefix before ``.bak``, keep newest ``keep``
  per group, archive the rest.
- ``vacuum``: run SQLite ``VACUUM`` and optionally delete rows older than
  ``vacuum_days`` from each configured table.

The runner never touches the ``archive/`` root itself, so lifecycle output
is stable across runs.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

try:
    import zstandard as zstd
    _HAVE_ZSTD = True
except ImportError:  # pragma: no cover
    zstd = None  # type: ignore[assignment]
    _HAVE_ZSTD = False


# ---------------------------------------------------------------------------
# Rule + Action dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    name: str
    kind: str                           # age_based | keep_latest | bak_prefix | vacuum
    path: str                           # relative to HERMES_HOME (file or dir)
    warm_days: Optional[int] = None
    cold_days: Optional[int] = None
    keep: Optional[int] = None
    glob: Optional[str] = None
    archive_subdir: Optional[str] = None
    vacuum_days: Optional[int] = None
    # [(table, timestamp_column, keep_days), ...] — used only for kind=vacuum
    vacuum_tables: Sequence[Tuple[str, str, int]] = ()
    # File-name globs (matched against ``Path.name``) to skip — used to keep
    # ``bak_prefix`` rules non-overlapping when a narrower rule already
    # covers a subset (e.g. ``config.yaml.bak_*`` handled separately).
    exclude_name_globs: Sequence[str] = ()


@dataclass
class Action:
    rule: str
    op: str                             # compress | delete | archive | vacuum | skip
    source: Path
    dest: Optional[Path]
    bytes_in: int
    bytes_out: int                      # 0 on pure delete
    reason: str

    def saved(self) -> int:
        return max(self.bytes_in - self.bytes_out, 0)


@dataclass
class RunSummary:
    actions: List[Action] = field(default_factory=list)

    def total_saved(self) -> int:
        return sum(a.saved() for a in self.actions)

    def count(self, op: str) -> int:
        return sum(1 for a in self.actions if a.op == op)

    def bytes_by_op(self, op: str) -> int:
        return sum(a.saved() if op in ("delete", "vacuum") else a.bytes_out
                   for a in self.actions if a.op == op)


# ---------------------------------------------------------------------------
# Defaults — conservative; users can override via ~/.hermes/lifecycle.yaml
# ---------------------------------------------------------------------------

DEFAULT_RULES: List[Rule] = [
    Rule("sessions",    "age_based", "sessions",
         warm_days=30, cold_days=180, archive_subdir="sessions"),
    Rule("logs",        "age_based", "logs",
         warm_days=7, cold_days=30, archive_subdir="logs"),
    Rule("cache",       "age_based", "cache",        cold_days=7),
    Rule("image_cache", "age_based", "image_cache",  cold_days=7),
    Rule("audio_cache", "age_based", "audio_cache",  cold_days=7),
    Rule("tmp_med",     "age_based", "tmp_med",      cold_days=7),
    Rule("checkpoints", "keep_latest", "checkpoints",
         keep=10, archive_subdir="checkpoints"),
    # Narrower rule first: keep 5 newest config.yaml backups.
    Rule("config_bak",  "bak_prefix", ".",
         glob="config.yaml.bak_*", keep=5, archive_subdir="bak/config"),
    # General bak-file sweep; excludes what `config_bak` already owns.
    Rule("bak_files",   "bak_prefix", ".",
         glob="**/*.bak_*", keep=3, archive_subdir="bak",
         exclude_name_globs=("config.yaml.bak_*",)),
    Rule("state_db",    "vacuum", "state.db",
         vacuum_days=365),
]


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

_SKIP_COMPONENTS = {"hermes-agent", "node", "archive", "__pycache__", ".git", "node_modules"}


def _under_skip(rel: Path) -> bool:
    return any(part in _SKIP_COMPONENTS for part in rel.parts)


def _age_days(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 86400.0
    except OSError:
        return 0.0


def _entry_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _iter_entries(path: Path) -> List[Path]:
    try:
        return list(path.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []


# ---------------------------------------------------------------------------
# Compression + delete primitives
# ---------------------------------------------------------------------------

def _compress(src: Path, archive_root: Path, subdir: str,
              dry_run: bool) -> Tuple[Path, int, int]:
    """Compress *src* (file or dir) with zstd; return (dest, bytes_in, bytes_out).

    Output goes to ``archive_root / subdir / <YYYY-MM> / <name>{.tar}.zst``,
    partitioned by the source's own mtime so re-runs stay stable.
    """
    if not _HAVE_ZSTD:
        raise RuntimeError("zstandard not installed")

    stat = src.stat()
    ym = time.strftime("%Y-%m", time.localtime(stat.st_mtime))
    dest_dir = archive_root / subdir / ym
    suffix = ".tar.zst" if src.is_dir() else ".zst"
    dest = dest_dir / (src.name + suffix)

    bytes_in = _entry_size(src)
    if dry_run:
        # Conservative estimate (40% of input) — actual varies widely.
        return dest, bytes_in, int(bytes_in * 0.4)

    dest_dir.mkdir(parents=True, exist_ok=True)
    cctx = zstd.ZstdCompressor(level=15, threads=-1)
    if src.is_file():
        with open(src, "rb") as fin, open(dest, "wb") as fout:
            cctx.copy_stream(fin, fout)
    else:
        with open(dest, "wb") as fout, cctx.stream_writer(fout) as zw:
            with tarfile.open(fileobj=zw, mode="w|") as tar:
                tar.add(src, arcname=src.name)
    return dest, bytes_in, dest.stat().st_size


def _delete(src: Path, dry_run: bool) -> int:
    size = _entry_size(src)
    if dry_run:
        return size
    if src.is_dir():
        shutil.rmtree(src, ignore_errors=True)
    else:
        try:
            src.unlink()
        except FileNotFoundError:
            pass
    return size


# ---------------------------------------------------------------------------
# Per-kind handlers
# ---------------------------------------------------------------------------

def _handle_age_based(rule: Rule, home: Path, archive: Path,
                      summary: RunSummary, dry_run: bool) -> None:
    target = home / rule.path
    if not target.exists() or not target.is_dir():
        return

    for entry in _iter_entries(target):
        rel = entry.relative_to(home)
        if _under_skip(rel):
            continue

        age = _age_days(entry)

        if rule.cold_days is not None and age >= rule.cold_days:
            freed = _delete(entry, dry_run)
            summary.actions.append(Action(
                rule.name, "delete", entry, None, freed, 0,
                f"age {age:.0f}d ≥ cold {rule.cold_days}d",
            ))
            continue

        if rule.warm_days is not None and age >= rule.warm_days:
            subdir = rule.archive_subdir or rule.name
            dest, bin_, bout = _compress(entry, archive, subdir, dry_run)
            if not dry_run:
                _delete(entry, False)
            summary.actions.append(Action(
                rule.name, "compress", entry, dest, bin_, bout,
                f"age {age:.0f}d ≥ warm {rule.warm_days}d",
            ))


def _handle_keep_latest(rule: Rule, home: Path, archive: Path,
                        summary: RunSummary, dry_run: bool) -> None:
    target = home / rule.path
    if not target.exists() or not target.is_dir():
        return

    entries = _iter_entries(target)
    entries.sort(
        key=lambda p: (p.stat().st_mtime if p.exists() else 0.0),
        reverse=True,
    )
    keep = rule.keep or 10
    for entry in entries[keep:]:
        subdir = rule.archive_subdir or rule.name
        dest, bin_, bout = _compress(entry, archive, subdir, dry_run)
        if not dry_run:
            _delete(entry, False)
        summary.actions.append(Action(
            rule.name, "archive", entry, dest, bin_, bout,
            f"keep newest {keep}",
        ))


_BAK_RE = re.compile(r"^(?P<prefix>.+?)\.bak[_.].*$")


def _handle_bak_prefix(rule: Rule, home: Path, archive: Path,
                       summary: RunSummary, dry_run: bool) -> None:
    target = home / rule.path
    if not target.exists():
        return

    import fnmatch
    pattern = rule.glob or "*.bak_*"
    keep = rule.keep or 3
    groups: dict[str, List[Path]] = {}

    for p in target.glob(pattern):
        if not p.is_file():
            continue
        rel = p.relative_to(home)
        if _under_skip(rel):
            continue
        if any(fnmatch.fnmatchcase(p.name, g) for g in rule.exclude_name_globs):
            continue
        m = _BAK_RE.match(p.name)
        prefix_key = str(p.parent / (m.group("prefix") if m else p.stem))
        groups.setdefault(prefix_key, []).append(p)

    for prefix_key, files in groups.items():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            subdir = rule.archive_subdir or "bak"
            dest, bin_, bout = _compress(old, archive, subdir, dry_run)
            if not dry_run:
                _delete(old, False)
            summary.actions.append(Action(
                rule.name, "archive", old, dest, bin_, bout,
                f"bak group '{Path(prefix_key).name}' keep {keep}",
            ))


def _handle_vacuum(rule: Rule, home: Path, archive: Path,
                   summary: RunSummary, dry_run: bool) -> None:
    db = home / rule.path
    if not db.exists():
        return

    before = db.stat().st_size

    if dry_run:
        summary.actions.append(Action(
            rule.name, "vacuum", db, None, before, before,
            "dry-run (VACUUM skipped)",
        ))
        return

    try:
        conn = sqlite3.connect(str(db), timeout=30.0)
        conn.isolation_level = None           # VACUUM requires autocommit
        cur = conn.cursor()

        if rule.vacuum_tables and rule.vacuum_days:
            now = int(time.time())
            for table, ts_col, keep_days in rule.vacuum_tables:
                cutoff = now - keep_days * 86400
                try:
                    cur.execute(
                        f"DELETE FROM {table} WHERE {ts_col} < ?", (cutoff,)
                    )
                except sqlite3.Error as e:
                    logger.warning("row prune failed for %s: %s", table, e)

        cur.execute("VACUUM")
        conn.close()
    except sqlite3.OperationalError as e:
        summary.actions.append(Action(
            rule.name, "skip", db, None, before, before,
            f"locked or unavailable: {e}",
        ))
        return

    after = db.stat().st_size
    summary.actions.append(Action(
        rule.name, "vacuum", db, None, before, after, "VACUUM",
    ))


_DISPATCH = {
    "age_based":   _handle_age_based,
    "keep_latest": _handle_keep_latest,
    "bak_prefix":  _handle_bak_prefix,
    "vacuum":      _handle_vacuum,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_rules(home: Path, rules: Iterable[Rule] = DEFAULT_RULES,
              dry_run: bool = True) -> RunSummary:
    """Execute *rules* against HERMES_HOME and return a summary."""
    archive_root = home / "archive"
    if not dry_run:
        archive_root.mkdir(parents=True, exist_ok=True)

    summary = RunSummary()
    for rule in rules:
        handler = _DISPATCH.get(rule.kind)
        if handler is None:
            logger.warning("unknown rule kind %r on %s", rule.kind, rule.name)
            continue
        try:
            handler(rule, home, archive_root, summary, dry_run)
        except Exception as e:   # pragma: no cover — per-rule isolation
            logger.exception("rule %s failed: %s", rule.name, e)
            summary.actions.append(Action(
                rule.name, "skip", home / rule.path, None, 0, 0, f"error: {e}",
            ))
    return summary


def status(home: Path, rules: Iterable[Rule] = DEFAULT_RULES) -> List[dict]:
    """Return a per-rule current-state snapshot (size + exists).

    For ``bak_prefix`` rules the size is summed only over ``glob`` matches,
    so sweeping rules rooted at HERMES_HOME don't report the entire tree.
    """
    import fnmatch
    snap: List[dict] = []
    for rule in rules:
        target = home / rule.path
        exists = target.exists()

        if exists and rule.kind == "bak_prefix":
            pattern = rule.glob or "*.bak_*"
            total = 0
            count = 0
            for p in target.glob(pattern):
                if not p.is_file():
                    continue
                if _under_skip(p.relative_to(home)):
                    continue
                if any(fnmatch.fnmatchcase(p.name, g)
                       for g in rule.exclude_name_globs):
                    continue
                try:
                    total += p.stat().st_size
                    count += 1
                except OSError:
                    pass
            snap.append({
                "name":   rule.name,
                "kind":   rule.kind,
                "path":   f"{target}  ({pattern}, {count} file(s))",
                "exists": exists,
                "size":   total,
            })
            continue

        snap.append({
            "name":   rule.name,
            "kind":   rule.kind,
            "path":   str(target),
            "exists": exists,
            "size":   _entry_size(target) if exists else 0,
        })
    return snap
