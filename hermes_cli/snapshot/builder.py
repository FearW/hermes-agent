"""Build a portable Hermes snapshot.

Output layout inside the tar:
    manifest.yaml         — version, checksum, inclusion list
    home/...              — mirror of included entries under HERMES_HOME

We compress the tar stream with zstd (level 19) and optionally run the
result through AES-256-GCM via ``crypto.encrypt_file``. The final file
name is ``hermes-snapshot-<host>-<timestamp>.tar.zst[.age]``.
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import socket
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

import yaml
import zstandard as zstd


# ---------------------------------------------------------------------------
# Inclusion policy
# ---------------------------------------------------------------------------

# Always copy these relative paths (file or dir) if they exist.
CORE_INCLUDES: List[str] = [
    "config.yaml",
    ".env",
    "SOUL.md",
    "auth.json",
    "channel_directory.json",
    "memories",
    "skills",
    "cron",
    "notes",
    "hooks",
    "platforms",
    "workflows",
    "evolution",
    "todo.txt",
]

# Heavy but optional — included unless ``lite=True``.
HEAVY_INCLUDES: List[str] = [
    "state.db",
    "memory_l4",
    "checkpoints",
    "sessions",
    "archive",
]

# Never include.
ALWAYS_EXCLUDE: List[str] = [
    "hermes-agent",
    "node",
    "cache",
    "image_cache",
    "audio_cache",
    "tmp",
    "tmp_med",
    "logs",
    "sandboxes",
    "gateway.pid",
    "cron.pid",
    "processes.json",
    "__pycache__",
]

# File name suffixes that are never useful to carry forward.
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".sock")


# ---------------------------------------------------------------------------
# Manifest + size tally
# ---------------------------------------------------------------------------

@dataclass
class SnapshotManifest:
    version: int = 1
    created_at: str = ""
    hostname: str = ""
    platform: str = ""
    hermes_version: str = ""
    git_commit: str = ""
    lite: bool = False
    included: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)
    total_bytes: int = 0
    file_count: int = 0
    sha256: str = ""
    encrypted: bool = False

    def as_yaml(self) -> str:
        return yaml.safe_dump(self.__dict__, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_commit(repo: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        )
        return out.decode().strip()
    except Exception:
        return ""


def _hermes_version() -> str:
    try:
        from hermes_constants import __version__ as v  # type: ignore
        return v
    except Exception:
        return ""


def _should_skip(rel: Path) -> bool:
    parts = rel.parts
    for part in parts:
        if part in ALWAYS_EXCLUDE:
            return True
    return rel.name.endswith(EXCLUDE_SUFFIXES)


def _iter_files(home: Path, includes: Iterable[str]) -> Iterable[Path]:
    for rel_str in includes:
        root = home / rel_str
        if not root.exists():
            continue
        if root.is_file():
            rel = root.relative_to(home)
            if not _should_skip(rel):
                yield root
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(home)
            if _should_skip(rel):
                continue
            yield p


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_snapshot(
    home: Path,
    out_path: Path,
    *,
    lite: bool = False,
    include_env: bool = True,
    passphrase: Optional[str] = None,
    zstd_level: int = 19,
    hermes_agent_path: Optional[Path] = None,
) -> SnapshotManifest:
    """Build a snapshot tar.zst (optionally encrypted) at *out_path*.

    Returns the manifest that was embedded. The on-disk file name is
    preserved exactly as *out_path*.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    includes = list(CORE_INCLUDES)
    if not lite:
        includes += HEAVY_INCLUDES
    if not include_env:
        includes = [i for i in includes if i != ".env"]

    manifest = SnapshotManifest(
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        hostname=socket.gethostname(),
        platform=f"{platform.system()} {platform.release()}",
        hermes_version=_hermes_version(),
        git_commit=_git_commit(hermes_agent_path or (home / "hermes-agent")),
        lite=lite,
        included=includes,
        excluded=ALWAYS_EXCLUDE,
    )

    files = list(_iter_files(home, includes))
    manifest.file_count = len(files)
    manifest.total_bytes = sum(f.stat().st_size for f in files)

    # Build the inner tar stream, compress it with zstd, optionally encrypt.
    encrypt = bool(passphrase)
    plain_tar_zst = (
        out_path.with_suffix(out_path.suffix + ".plain")
        if encrypt else out_path
    )

    # Write tar → zstd in one pipeline; compute sha256 over the compressed bytes.
    hasher = hashlib.sha256()
    cctx = zstd.ZstdCompressor(level=zstd_level, threads=-1)
    with open(plain_tar_zst, "wb") as raw_out:
        with cctx.stream_writer(raw_out) as zw:
            with tarfile.open(fileobj=zw, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                # 1. manifest first (without sha256 yet — we patch in after hashing)
                manifest_bytes = manifest.as_yaml().encode("utf-8")
                m_info = tarfile.TarInfo(name="manifest.yaml")
                m_info.size = len(manifest_bytes)
                m_info.mtime = int(time.time())
                tar.addfile(m_info, io.BytesIO(manifest_bytes))
                # 2. file payload under home/
                for f in files:
                    rel = f.relative_to(home)
                    try:
                        tar.add(str(f), arcname=f"home/{rel.as_posix()}")
                    except (FileNotFoundError, PermissionError):
                        continue

    # sha256 of the compressed tar
    with open(plain_tar_zst, "rb") as fin:
        for chunk in iter(lambda: fin.read(1 << 20), b""):
            hasher.update(chunk)
    manifest.sha256 = hasher.hexdigest()

    if encrypt:
        from hermes_cli.snapshot.crypto import encrypt_file
        encrypt_file(plain_tar_zst, out_path, passphrase)
        plain_tar_zst.unlink(missing_ok=True)
        manifest.encrypted = True

    # Drop a sidecar manifest next to the snapshot for easy inspection.
    sidecar = out_path.with_suffix(out_path.suffix + ".manifest.yaml")
    sidecar.write_text(manifest.as_yaml(), encoding="utf-8")

    return manifest


def default_snapshot_name(lite: bool, encrypt: bool) -> str:
    host = socket.gethostname().split(".")[0]
    ts = time.strftime("%Y%m%d-%H%M%S")
    tag = "lite" if lite else "full"
    ext = ".tar.zst.age" if encrypt else ".tar.zst"
    return f"hermes-snapshot-{host}-{tag}-{ts}{ext}"
