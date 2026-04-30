"""Restore a Hermes snapshot produced by :mod:`builder`.

Restore strategy:

1. If encrypted, decrypt to a temp ``.tar.zst``.
2. Decompress the outer zstd stream on the fly while scanning the tar.
3. Validate ``manifest.yaml`` and the embedded sha256 against the on-disk
   compressed bytes *before* extracting anything.
4. Extract ``home/<rel>`` entries into HERMES_HOME, skipping paths not
   matched by the ``--only`` filter.

Never overwrites existing non-matching files unless ``--overwrite`` is
set. ``hermes-agent/`` and ``node/`` are always ignored on restore (they
were excluded at snapshot time anyway).
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import yaml
import zstandard as zstd


# Map --only groups → path prefixes (matched against home/<rel>).
ONLY_GROUPS = {
    "config":   ("config.yaml", ".env", "auth.json", "SOUL.md",
                 "channel_directory.json", "todo.txt"),
    "memory":   ("memories", "memory_l4", "state.db"),
    "skills":   ("skills", "evolution"),
    "cron":     ("cron", "workflows", "hooks", "platforms"),
    "sessions": ("sessions", "archive", "notes"),
    "all":      None,   # sentinel — no filter
}


@dataclass
class RestoreResult:
    manifest: dict
    files_extracted: int
    bytes_extracted: int
    skipped: int
    dry_run: bool


def _matches_only(rel: str, only: Optional[Sequence[str]]) -> bool:
    if not only or "all" in only:
        return True
    prefixes: List[str] = []
    for group in only:
        g = ONLY_GROUPS.get(group)
        if g is None:
            continue
        prefixes.extend(g)
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


def _read_and_verify(compressed_path: Path, expected_sha256: Optional[str]) -> None:
    """Hash the compressed tar.zst and check against the manifest's sha256."""
    if not expected_sha256:
        return
    hasher = hashlib.sha256()
    with open(compressed_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            hasher.update(chunk)
    got = hasher.hexdigest()
    if got != expected_sha256:
        raise ValueError(
            f"sha256 mismatch: manifest={expected_sha256}, actual={got}"
        )


def _read_manifest(compressed_path: Path) -> dict:
    """Extract and parse manifest.yaml without extracting the full archive."""
    dctx = zstd.ZstdDecompressor()
    with open(compressed_path, "rb") as raw, dctx.stream_reader(raw) as r:
        with tarfile.open(fileobj=r, mode="r|") as tar:
            for member in tar:
                if member.name == "manifest.yaml":
                    f = tar.extractfile(member)
                    if f is None:
                        break
                    data = f.read()
                    return yaml.safe_load(data) or {}
                # manifest must be first; stop if we missed it
                break
    raise ValueError("manifest.yaml not found (is this a Hermes snapshot?)")


def restore_snapshot(
    src: Path,
    home: Path,
    *,
    only: Optional[Sequence[str]] = None,
    dry_run: bool = True,
    overwrite: bool = False,
    passphrase: Optional[str] = None,
) -> RestoreResult:
    """Restore *src* into *home*. Returns a :class:`RestoreResult`."""
    src = Path(src)
    home = Path(home)

    # 1. Decrypt if needed
    tmpdir = Path(tempfile.mkdtemp(prefix="hermes-restore-"))
    try:
        compressed = src
        from hermes_cli.snapshot.crypto import is_encrypted, decrypt_file
        if is_encrypted(src):
            if not passphrase:
                raise ValueError("snapshot is encrypted but no passphrase provided")
            compressed = tmpdir / "decrypted.tar.zst"
            decrypt_file(src, compressed, passphrase)

        manifest = _read_manifest(compressed)
        _read_and_verify(compressed, manifest.get("sha256"))

        files_extracted = 0
        bytes_extracted = 0
        skipped = 0

        if not dry_run:
            home.mkdir(parents=True, exist_ok=True)

        dctx = zstd.ZstdDecompressor()
        with open(compressed, "rb") as raw, dctx.stream_reader(raw) as r:
            with tarfile.open(fileobj=r, mode="r|") as tar:
                for member in tar:
                    if member.name == "manifest.yaml":
                        continue
                    if not member.name.startswith("home/"):
                        continue
                    rel = member.name[len("home/"):]
                    if not _matches_only(rel, only):
                        skipped += 1
                        tar.extractfile(member)  # advance
                        continue

                    dest = home / rel
                    if dest.exists() and not overwrite and member.isfile():
                        skipped += 1
                        continue

                    if dry_run:
                        if member.isfile():
                            files_extracted += 1
                            bytes_extracted += member.size
                        continue

                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if member.isdir():
                        dest.mkdir(exist_ok=True)
                        continue
                    if member.isfile():
                        f = tar.extractfile(member)
                        if f is None:
                            skipped += 1
                            continue
                        with open(dest, "wb") as out:
                            shutil.copyfileobj(f, out)
                        try:
                            os.chmod(dest, member.mode & 0o777)
                        except OSError:
                            pass
                        files_extracted += 1
                        bytes_extracted += member.size

        return RestoreResult(
            manifest=manifest,
            files_extracted=files_extracted,
            bytes_extracted=bytes_extracted,
            skipped=skipped,
            dry_run=dry_run,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
