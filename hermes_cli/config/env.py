"""Env file management extracted from hermes_cli.config."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

# These are resolved from hermes_cli.config at import time (import-at-end pattern).
from hermes_cli.config import (
    get_env_path,
    _ENV_VAR_NAME_RE,
    _EXTRA_ENV_KEYS,
    _IS_WINDOWS,
    OPTIONAL_ENV_VARS,
    is_managed,
    managed_error,
    _secure_file,
    ensure_hermes_home,
)


def load_env() -> Dict[str, str]:
    """Load environment variables from ~/.hermes/.env."""
    env_path = get_env_path()
    env_vars = {}

    if env_path.exists():
        # On Windows, open() defaults to the system locale (cp1252) which can
        # fail on UTF-8 .env files. Use explicit UTF-8 only on Windows.
        open_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
        with open(env_path, **open_kw) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip().strip("\"'")

    return env_vars


def _sanitize_env_lines(lines: list) -> list:
    """Fix corrupted .env lines before writing.

    Handles two known corruption patterns:
    1. Concatenated KEY=VALUE pairs on a single line (missing newline between
       entries, e.g. ``ANTHROPIC_API_KEY: REDACTED
    2. Stale ``KEY=***`` placeholder entries left by incomplete setup runs.

    Uses a known-keys set (OPTIONAL_ENV_VARS + _EXTRA_ENV_KEYS) so we only
    split on real Hermes env var names, avoiding false positives from values
    that happen to contain uppercase text with ``=``.
    """
    # Build the known keys set lazily from OPTIONAL_ENV_VARS + extras.
    # Done inside the function so OPTIONAL_ENV_VARS is guaranteed to be defined.
    known_keys = set(OPTIONAL_ENV_VARS.keys()) | _EXTRA_ENV_KEYS

    sanitized: list[str] = []
    for line in lines:
        raw = line.rstrip("\r\n")
        stripped = raw.strip()

        # Preserve blank lines and comments
        if not stripped or stripped.startswith("#"):
            sanitized.append(raw + "\n")
            continue

        # Detect concatenated KEY=VALUE pairs on one line.
        # Search for known KEY= patterns at any position in the line.
        split_positions = []
        for key_name in known_keys:
            needle = key_name + "="
            idx = stripped.find(needle)
            while idx >= 0:
                split_positions.append(idx)
                idx = stripped.find(needle, idx + len(needle))

        if len(split_positions) > 1:
            split_positions.sort()
            # Deduplicate (shouldn't happen, but be safe)
            split_positions = sorted(set(split_positions))
            for i, pos in enumerate(split_positions):
                end = (
                    split_positions[i + 1]
                    if i + 1 < len(split_positions)
                    else len(stripped)
                )
                part = stripped[pos:end].strip()
                if part:
                    sanitized.append(part + "\n")
        else:
            sanitized.append(stripped + "\n")

    return sanitized


def sanitize_env_file() -> int:
    """Read, sanitize, and rewrite ~/.hermes/.env in place.

    Returns the number of lines that were fixed (concatenation splits +
    placeholder removals).  Returns 0 when no changes are needed.
    """
    env_path = get_env_path()
    if not env_path.exists():
        return 0

    read_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
    write_kw = {"encoding": "utf-8"} if _IS_WINDOWS else {}

    with open(env_path, **read_kw) as f:
        original_lines = f.readlines()

    sanitized = _sanitize_env_lines(original_lines)

    if sanitized == original_lines:
        return 0

    # Count fixes: difference in line count (from splits) + removed lines
    fixes = abs(len(sanitized) - len(original_lines))
    if fixes == 0:
        # Lines changed content (e.g. *** removal) even if count is same
        fixes = sum(1 for a, b in zip(original_lines, sanitized) if a != b)
        fixes += abs(len(sanitized) - len(original_lines))

    fd, tmp_path = tempfile.mkstemp(
        dir=str(env_path.parent), suffix=".tmp", prefix=".env_"
    )
    try:
        with os.fdopen(fd, "w", **write_kw) as f:
            f.writelines(sanitized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, env_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _secure_file(env_path)
    return fixes


def save_env_value(key: str, value: str):
    """Save or update a value in ~/.hermes/.env."""
    if is_managed():
        managed_error(f"set {key}")
        return
    if not _ENV_VAR_NAME_RE.match(key):
        raise ValueError(f"Invalid environment variable name: {key!r}")
    value = value.replace("\n", "").replace("\r", "")
    ensure_hermes_home()
    env_path = get_env_path()

    # On Windows, open() defaults to the system locale (cp1252) which can
    # cause OSError errno 22 on UTF-8 .env files.
    read_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
    write_kw = {"encoding": "utf-8"} if _IS_WINDOWS else {}

    lines = []
    if env_path.exists():
        with open(env_path, **read_kw) as f:
            lines = f.readlines()
        # Sanitize on every read: split concatenated keys, drop stale placeholders
        lines = _sanitize_env_lines(lines)

    # Find and update or append
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        # Ensure there's a newline at the end of the file before appending
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")

    fd, tmp_path = tempfile.mkstemp(
        dir=str(env_path.parent), suffix=".tmp", prefix=".env_"
    )
    try:
        with os.fdopen(fd, "w", **write_kw) as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, env_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _secure_file(env_path)

    os.environ[key] = value

    # Restrict .env permissions to owner-only (contains API keys)
    if not _IS_WINDOWS:
        try:
            os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def remove_env_value(key: str) -> bool:
    """Remove a key from ~/.hermes/.env and os.environ.

    Returns True if the key was found and removed, False otherwise.
    """
    if is_managed():
        managed_error(f"remove {key}")
        return False
    if not _ENV_VAR_NAME_RE.match(key):
        raise ValueError(f"Invalid environment variable name: {key!r}")
    env_path = get_env_path()
    if not env_path.exists():
        os.environ.pop(key, None)
        return False

    read_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
    write_kw = {"encoding": "utf-8"} if _IS_WINDOWS else {}

    with open(env_path, **read_kw) as f:
        lines = f.readlines()
    lines = _sanitize_env_lines(lines)

    new_lines = [line for line in lines if not line.strip().startswith(f"{key}=")]
    found = len(new_lines) < len(lines)

    if found:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(env_path.parent), suffix=".tmp", prefix=".env_"
        )
        try:
            with os.fdopen(fd, "w", **write_kw) as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, env_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _secure_file(env_path)

    os.environ.pop(key, None)
    return found


def save_anthropic_oauth_token(value: str, save_fn=None):
    """Persist an Anthropic OAuth/setup token and clear the API-key slot."""
    writer = save_fn or save_env_value
    writer("ANTHROPIC_TOKEN", value)
    writer("ANTHROPIC_API_KEY", "")


def use_anthropic_claude_code_credentials(save_fn=None):
    """Use Claude Code's own credential files instead of persisting env tokens."""
    writer = save_fn or save_env_value
    writer("ANTHROPIC_TOKEN", "")
    writer("ANTHROPIC_API_KEY", "")


def save_anthropic_api_key(value: str, save_fn=None):
    """Persist an Anthropic API key and clear the OAuth/setup-token slot."""
    writer = save_fn or save_env_value
    writer("ANTHROPIC_API_KEY", value)
    writer("ANTHROPIC_TOKEN", "")


def save_env_value_secure(key: str, value: str) -> Dict[str, Any]:
    save_env_value(key, value)
    return {
        "success": True,
        "stored_as": key,
        "validated": False,
    }


def reload_env() -> int:
    """Re-read ~/.hermes/.env into os.environ. Returns count of vars updated.

    Adds/updates vars that changed and removes vars that were deleted from
    the .env file (but only vars known to Hermes — OPTIONAL_ENV_VARS and
    _EXTRA_ENV_KEYS — to avoid clobbering unrelated environment).
    """
    env_vars = load_env()
    known_keys = set(OPTIONAL_ENV_VARS.keys()) | _EXTRA_ENV_KEYS
    count = 0
    for key, value in env_vars.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
            count += 1
    # Remove known Hermes vars that are no longer in .env
    for key in known_keys:
        if key not in env_vars and key in os.environ:
            del os.environ[key]
            count += 1
    return count


def get_env_value(key: str) -> Optional[str]:
    """Get a value from ~/.hermes/.env or environment."""
    # Check environment first
    if key in os.environ:
        return os.environ[key]

    # Then check .env file
    env_vars = load_env()
    return env_vars.get(key)
