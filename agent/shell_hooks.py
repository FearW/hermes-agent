"""Declarative shell hooks loaded from ``config.yaml``.

This module bridges the ``hooks:`` config block into the existing plugin
hook dispatcher so CLI and gateway startup can register shell-script based
observers without depending on Python plugin packages.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home
from hermes_cli.plugins import VALID_HOOKS, get_plugin_manager
from utils import atomic_json_write, env_var_enabled

logger = logging.getLogger(__name__)

_ALLOWLIST_PATH = get_hermes_home() / "shell-hooks-allowlist.json"
_REGISTERED_HOOKS: set[tuple[str, str, str, int]] = set()
_REGISTER_LOCK = threading.RLock()


@dataclass(frozen=True)
class HookSpec:
    event: str
    command: str
    matcher: str = ""
    timeout: int = 30

    def matches_tool(self, tool_name: str | None) -> bool:
        if self.event not in {"pre_tool_call", "post_tool_call"}:
            return True
        if not self.matcher:
            return True
        tool = str(tool_name or "").strip()
        return bool(tool) and fnmatch.fnmatch(tool, self.matcher)


def _normalize_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 30
    return max(1, timeout)


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return []


def _script_path(command: str) -> Optional[Path]:
    parts = _split_command(command)
    if not parts:
        return None
    candidate = Path(parts[0]).expanduser()
    return candidate if candidate.exists() else None


def script_is_executable(command: str) -> bool:
    path = _script_path(command)
    if path is None:
        return False
    if os.name == "nt":
        return path.is_file()
    return path.is_file() and os.access(path, os.X_OK)


def script_mtime_iso(command: str) -> Optional[str]:
    path = _script_path(command)
    if path is None:
        return None
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_allowlist() -> Dict[str, Any]:
    try:
        data = json.loads(_ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"approvals": []}
    if not isinstance(data, dict):
        return {"approvals": []}
    approvals = data.get("approvals")
    data["approvals"] = approvals if isinstance(approvals, list) else []
    return data


def _save_allowlist(data: Dict[str, Any]) -> None:
    _ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(_ALLOWLIST_PATH, data)
    try:
        os.chmod(_ALLOWLIST_PATH, 0o600)
    except OSError:
        pass


def allowlist_entry_for(event: str, command: str) -> Optional[Dict[str, Any]]:
    for entry in load_allowlist().get("approvals", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("event") == event and entry.get("command") == command:
            return entry
    return None


def _approve(spec: HookSpec) -> None:
    data = load_allowlist()
    approvals = [e for e in data.get("approvals", []) if isinstance(e, dict)]
    entry = {
        "event": spec.event,
        "command": spec.command,
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "script_mtime_at_approval": script_mtime_iso(spec.command),
    }
    approvals = [
        e for e in approvals
        if not (e.get("event") == spec.event and e.get("command") == spec.command)
    ]
    approvals.append(entry)
    data["approvals"] = approvals
    _save_allowlist(data)


def revoke(command: str) -> int:
    data = load_allowlist()
    approvals = [e for e in data.get("approvals", []) if isinstance(e, dict)]
    kept = [e for e in approvals if e.get("command") != command]
    removed = len(approvals) - len(kept)
    if removed:
        data["approvals"] = kept
        _save_allowlist(data)
    return removed


def iter_configured_hooks(config: Optional[Dict[str, Any]]) -> List[HookSpec]:
    if not isinstance(config, dict):
        return []
    raw_hooks = config.get("hooks")
    if not isinstance(raw_hooks, list):
        return []

    specs: List[HookSpec] = []
    for entry in raw_hooks:
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("event") or "").strip()
        command = str(entry.get("command") or "").strip()
        matcher = str(entry.get("matcher") or entry.get("tool") or entry.get("tool_name") or "").strip()
        if not event or not command:
            continue
        if event not in VALID_HOOKS:
            logger.warning("Ignoring unknown shell hook event %r for command %s", event, command)
            continue
        specs.append(HookSpec(event=event, command=command, matcher=matcher, timeout=_normalize_timeout(entry.get("timeout"))))
    return specs


def _serialize_payload(spec: HookSpec, payload: Dict[str, Any]) -> str:
    body = {"event": spec.event, **payload}
    return json.dumps(body, ensure_ascii=False)


def run_once(spec: HookSpec, payload: Dict[str, Any]) -> Dict[str, Any]:
    argv = _split_command(spec.command)
    if not argv:
        return {
            "error": f"invalid command: {spec.command}",
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "elapsed_seconds": 0.0,
            "parsed": None,
        }

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=_serialize_payload(spec, payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=spec.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = round(time.monotonic() - started, 3)
        return {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
            "elapsed_seconds": elapsed,
            "parsed": None,
        }
    except OSError as exc:
        elapsed = round(time.monotonic() - started, 3)
        return {
            "error": str(exc),
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "elapsed_seconds": elapsed,
            "parsed": None,
        }

    elapsed = round(time.monotonic() - started, 3)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    parsed: Any = None
    stripped = stdout.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None

    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
        "elapsed_seconds": elapsed,
        "parsed": parsed,
    }


def register_from_config(config: Optional[Dict[str, Any]], *, accept_hooks: bool = False) -> int:
    specs = iter_configured_hooks(config)
    if not specs:
        return 0

    effective_accept = (
        accept_hooks
        or env_var_enabled("HERMES_ACCEPT_HOOKS")
        or bool(isinstance(config, dict) and config.get("hooks_auto_accept"))
    )
    manager = get_plugin_manager()
    registered = 0

    with _REGISTER_LOCK:
        for spec in specs:
            key = (spec.event, spec.command, spec.matcher, spec.timeout)
            if key in _REGISTERED_HOOKS:
                continue

            if allowlist_entry_for(spec.event, spec.command) is None:
                if not effective_accept:
                    logger.info(
                        "Skipping unapproved shell hook event=%s command=%s; rerun with --accept-hooks or set hooks_auto_accept/HERMES_ACCEPT_HOOKS",
                        spec.event,
                        spec.command,
                    )
                    continue
                _approve(spec)

            def _callback(_spec: HookSpec = spec, **kwargs: Any) -> Any:
                if not _spec.matches_tool(kwargs.get("tool_name")):
                    return None
                result = run_once(_spec, dict(kwargs))
                if result.get("error"):
                    logger.warning("Shell hook %s failed: %s", _spec.command, result["error"])
                elif result.get("timed_out"):
                    logger.warning("Shell hook %s timed out after %ss", _spec.command, _spec.timeout)
                return result.get("parsed")

            manager._hooks.setdefault(spec.event, []).append(_callback)
            _REGISTERED_HOOKS.add(key)
            registered += 1

    return registered
