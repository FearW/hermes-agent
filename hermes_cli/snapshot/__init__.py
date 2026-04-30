"""Encrypted, portable snapshots of ~/.hermes — successor to `hermes backup`.

`hermes snapshot create` produces a tar.zst (optionally AES-256-GCM
encrypted) with a manifest describing what's inside, git commit, and a
sha256 checksum. `hermes snapshot restore` validates the manifest and
unpacks the tar into the current HERMES_HOME.
"""
from hermes_cli.snapshot.builder import create_snapshot
from hermes_cli.snapshot.restore import restore_snapshot

__all__ = ["create_snapshot", "restore_snapshot"]
