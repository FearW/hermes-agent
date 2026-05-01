"""
Gateway runtime status helpers.

Provides PID-file based detection of whether the gateway daemon is running,
used by send_message's check_fn to gate availability in the CLI.

The PID file lives at ``{HERMES_HOME}/gateway.pid``.  HERMES_HOME defaults to
``~/.hermes`` but can be overridden via the environment variable.  This means
separate HERMES_HOME directories naturally get separate PID files — a property
that will be useful when we add named profiles (multiple agents running
concurrently under distinct configurations).
"""

import hashlib
import ctypes
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Any, Optional

_GATEWAY_KIND = "hermes-gateway"
_RUNTIME_STATUS_FILE = "gateway_state.json"
_LOCKS_DIRNAME = "gateway-locks"
_RUNTIME_LOCK_FILE = "gateway.lock"
_TAKEOVER_MARKER_FILE = ".gateway-takeover.json"
_TAKEOVER_MARKER_TTL = timedelta(seconds=60)
_IS_WINDOWS = sys.platform == "win32"
_UNSET = object()


def _get_pid_path() -> Path:
    """Return the path to the gateway PID file, respecting HERMES_HOME."""
    home = get_hermes_home()
    return home / "gateway.pid"


def _get_runtime_status_path() -> Path:
    """Return the persisted runtime health/status file path."""
    return _get_pid_path().with_name(_RUNTIME_STATUS_FILE)


def _get_runtime_lock_path() -> Path:
    """Return the gateway runtime lock path, respecting HERMES_HOME."""
    return _get_pid_path().with_name(_RUNTIME_LOCK_FILE)


def _get_takeover_marker_path() -> Path:
    """Return the planned --replace takeover marker path."""
    return _get_pid_path().with_name(_TAKEOVER_MARKER_FILE)


def _get_lock_dir() -> Path:
    """Return the machine-local directory for token-scoped gateway locks."""
    override = os.getenv("HERMES_GATEWAY_LOCK_DIR")
    if override:
        return Path(override)
    state_home = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "hermes" / _LOCKS_DIRNAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def terminate_pid(pid: int, *, force: bool = False) -> None:
    """Terminate a PID with platform-appropriate force semantics.

    POSIX uses SIGTERM/SIGKILL. Windows uses taskkill /T /F for true force-kill
    because os.kill(..., SIGTERM) is not equivalent to a tree-killing hard stop.
    """
    if force and _IS_WINDOWS:
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            os.kill(pid, signal.SIGTERM)
            return

        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise OSError(details or f"taskkill failed for PID {pid}")
        return

    sig = signal.SIGTERM if not force else getattr(signal, "SIGKILL", signal.SIGTERM)
    os.kill(pid, sig)


def _scope_hash(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _get_scope_lock_path(scope: str, identity: str) -> Path:
    return _get_lock_dir() / f"{scope}-{_scope_hash(identity)}.lock"


def _get_process_start_time(pid: int) -> Optional[int]:
    """Return the kernel start time for a process when available."""
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        # Field 22 in /proc/<pid>/stat is process start time (clock ticks).
        return int(stat_path.read_text().split()[21])
    except (FileNotFoundError, IndexError, PermissionError, ValueError, OSError):
        return None


def _pid_is_alive(pid: int) -> bool:
    if _IS_WINDOWS:
        if pid == os.getpid():
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            process_query_limited_information = 0x1000
            handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def get_process_start_time(pid: int) -> Optional[int]:
    """Return a process start time suitable for PID-reuse checks."""
    return _get_process_start_time(pid)


def _record_matches_live_process(record: dict[str, Any]) -> bool:
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return False

    if not _pid_is_alive(pid):
        return False

    recorded_start = record.get("start_time")
    current_start = _get_process_start_time(pid)
    return not (
        recorded_start is not None
        and current_start is not None
        and current_start != recorded_start
    )


def _running_pid_from_runtime_lock() -> Optional[int]:
    lock_path = _get_runtime_lock_path()
    if not is_gateway_runtime_lock_active(lock_path):
        return None
    record = _read_json_file(lock_path)
    if not record:
        return None
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _looks_like_gateway_process(pid) and not _record_looks_like_gateway(record):
        return None
    return pid


def _cleanup_stale_pid_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _read_process_cmdline(pid: int) -> Optional[str]:
    """Return the process command line as a space-separated string."""
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = cmdline_path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return None

    if not raw:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _looks_like_gateway_process(pid: int) -> bool:
    """Return True when the live PID still looks like the Hermes gateway."""
    cmdline = _read_process_cmdline(pid)
    if not cmdline:
        return False

    patterns = (
        "hermes_cli.main gateway",
        "hermes_cli/main.py gateway",
        "hermes gateway",
        "gateway/run.py",
    )
    return any(pattern in cmdline for pattern in patterns)


def _record_looks_like_gateway(record: dict[str, Any]) -> bool:
    """Validate gateway identity from PID-file metadata when cmdline is unavailable."""
    if record.get("kind") != _GATEWAY_KIND:
        return False

    argv = record.get("argv")
    if not isinstance(argv, list) or not argv:
        return False

    cmdline = " ".join(str(part) for part in argv)
    patterns = (
        "hermes_cli.main gateway",
        "hermes_cli/main.py gateway",
        "hermes gateway",
        "gateway/run.py",
    )
    return any(pattern in cmdline for pattern in patterns)


def _build_pid_record() -> dict:
    return {
        "pid": os.getpid(),
        "kind": _GATEWAY_KIND,
        "argv": list(sys.argv),
        "start_time": _get_process_start_time(os.getpid()),
    }


def _build_runtime_status_record() -> dict[str, Any]:
    payload = _build_pid_record()
    payload.update({
        "gateway_state": "starting",
        "exit_reason": None,
        "restart_requested": False,
        "active_agents": 0,
        "platforms": {},
        "updated_at": _utc_now_iso(),
    })
    return payload


def _read_json_file(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _read_pid_record() -> Optional[dict]:
    pid_path = _get_pid_path()
    if not pid_path.exists():
        return None

    raw = pid_path.read_text().strip()
    if not raw:
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            return {"pid": int(raw)}
        except ValueError:
            return None

    if isinstance(payload, int):
        return {"pid": payload}
    if isinstance(payload, dict):
        return payload
    return None


def write_pid_file() -> None:
    """Write the current process PID and metadata to the gateway PID file."""
    pid_path = _get_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pid_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_build_pid_record(), handle)
    except Exception:
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def is_gateway_runtime_lock_active(lock_path: Optional[Path] = None) -> bool:
    """Return True when the runtime lock is held by a live process."""
    path = lock_path or _get_runtime_lock_path()
    record = _read_json_file(path)
    if not record:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    if _record_matches_live_process(record):
        return True

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def acquire_gateway_runtime_lock() -> bool:
    """Acquire the per-HERMES_HOME gateway runtime lock."""
    lock_path = _get_runtime_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    record = _build_pid_record()

    existing = _read_json_file(lock_path)
    if existing is None and lock_path.exists():
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
    if existing:
        if (
            existing.get("pid") == os.getpid()
            and existing.get("start_time") == record.get("start_time")
        ):
            _write_json_file(lock_path, record)
            return True
        if _record_matches_live_process(existing):
            return False
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            return False

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
    except Exception:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return True


def release_gateway_runtime_lock() -> None:
    """Release the runtime lock when owned by this process."""
    lock_path = _get_runtime_lock_path()
    existing = _read_json_file(lock_path)
    if not existing:
        return
    if existing.get("pid") != os.getpid():
        return
    if existing.get("start_time") != _get_process_start_time(os.getpid()):
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def write_takeover_marker(target_pid: int) -> bool:
    """Write a best-effort planned --replace takeover marker."""
    try:
        _write_json_file(_get_takeover_marker_path(), {
            "target_pid": int(target_pid),
            "target_start_time": _get_process_start_time(int(target_pid)),
            "replacer_pid": os.getpid(),
            "written_at": _utc_now_iso(),
        })
    except (OSError, TypeError, ValueError):
        return False
    return True


def clear_takeover_marker() -> None:
    """Remove the planned takeover marker if present."""
    try:
        _get_takeover_marker_path().unlink(missing_ok=True)
    except OSError:
        pass


def consume_takeover_marker_for_self() -> bool:
    """Consume and validate a planned takeover marker for this process."""
    marker_path = _get_takeover_marker_path()
    marker = _read_json_file(marker_path)
    if not marker:
        clear_takeover_marker()
        return False

    try:
        written_at = datetime.fromisoformat(str(marker["written_at"]))
        target_pid = int(marker["target_pid"])
        target_start_time = marker["target_start_time"]
    except (KeyError, TypeError, ValueError):
        clear_takeover_marker()
        return False

    if written_at.tzinfo is None:
        written_at = written_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - written_at > _TAKEOVER_MARKER_TTL:
        clear_takeover_marker()
        return False

    is_self = target_pid == os.getpid()
    same_start = target_start_time == _get_process_start_time(os.getpid())
    clear_takeover_marker()
    return bool(is_self and same_start)


def write_runtime_status(
    *,
    gateway_state: Any = _UNSET,
    exit_reason: Any = _UNSET,
    restart_requested: Any = _UNSET,
    active_agents: Any = _UNSET,
    platform: Any = _UNSET,
    platform_state: Any = _UNSET,
    error_code: Any = _UNSET,
    error_message: Any = _UNSET,
) -> None:
    """Persist gateway runtime health information for diagnostics/status."""
    path = _get_runtime_status_path()
    payload = _read_json_file(path) or _build_runtime_status_record()
    payload.setdefault("platforms", {})
    payload.setdefault("kind", _GATEWAY_KIND)
    payload["pid"] = os.getpid()
    payload["start_time"] = _get_process_start_time(os.getpid())
    payload["updated_at"] = _utc_now_iso()

    if gateway_state is not _UNSET:
        payload["gateway_state"] = gateway_state
    if exit_reason is not _UNSET:
        payload["exit_reason"] = exit_reason
    if restart_requested is not _UNSET:
        payload["restart_requested"] = bool(restart_requested)
    if active_agents is not _UNSET:
        payload["active_agents"] = max(0, int(active_agents))

    if platform is not _UNSET:
        platform_payload = payload["platforms"].get(platform, {})
        if platform_state is not _UNSET:
            platform_payload["state"] = platform_state
        if error_code is not _UNSET:
            platform_payload["error_code"] = error_code
        if error_message is not _UNSET:
            platform_payload["error_message"] = error_message
        platform_payload["updated_at"] = _utc_now_iso()
        payload["platforms"][platform] = platform_payload

    _write_json_file(path, payload)


def read_runtime_status() -> Optional[dict[str, Any]]:
    """Read the persisted gateway runtime health/status information."""
    return _read_json_file(_get_runtime_status_path())


def remove_pid_file() -> None:
    """Remove the gateway PID file if it exists."""
    try:
        _get_pid_path().unlink(missing_ok=True)
    except Exception:
        pass


def acquire_scoped_lock(scope: str, identity: str, metadata: Optional[dict[str, Any]] = None) -> tuple[bool, Optional[dict[str, Any]]]:
    """Acquire a machine-local lock keyed by scope + identity.

    Used to prevent multiple local gateways from using the same external identity
    at once (e.g. the same Telegram bot token across different HERMES_HOME dirs).
    """
    lock_path = _get_scope_lock_path(scope, identity)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        **_build_pid_record(),
        "scope": scope,
        "identity_hash": _scope_hash(identity),
        "metadata": metadata or {},
        "updated_at": _utc_now_iso(),
    }

    existing = _read_json_file(lock_path)
    if existing is None and lock_path.exists():
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
    if existing:
        try:
            existing_pid = int(existing["pid"])
        except (KeyError, TypeError, ValueError):
            existing_pid = None

        if existing_pid == os.getpid() and existing.get("start_time") == record.get("start_time"):
            _write_json_file(lock_path, record)
            return True, existing

        stale = existing_pid is None
        if not stale:
            try:
                os.kill(existing_pid, 0)
            except (ProcessLookupError, PermissionError):
                stale = True
            else:
                current_start = _get_process_start_time(existing_pid)
                if (
                    existing.get("start_time") is not None
                    and current_start is not None
                    and current_start != existing.get("start_time")
                ):
                    stale = True
                # Check if process is stopped (Ctrl+Z / SIGTSTP) — stopped
                # processes still respond to os.kill(pid, 0) but are not
                # actually running. Treat them as stale so --replace works.
                if not stale:
                    try:
                        _proc_status = Path(f"/proc/{existing_pid}/status")
                        if _proc_status.exists():
                            for _line in _proc_status.read_text().splitlines():
                                if _line.startswith("State:"):
                                    _state = _line.split()[1]
                                    if _state in ("T", "t"):  # stopped or tracing stop
                                        stale = True
                                    break
                    except (OSError, PermissionError):
                        pass
        if stale:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            return False, existing

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False, _read_json_file(lock_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
    except Exception:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return True, None


def release_scoped_lock(scope: str, identity: str) -> None:
    """Release a previously-acquired scope lock when owned by this process."""
    lock_path = _get_scope_lock_path(scope, identity)
    existing = _read_json_file(lock_path)
    if not existing:
        return
    if existing.get("pid") != os.getpid():
        return
    if existing.get("start_time") != _get_process_start_time(os.getpid()):
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def release_all_scoped_locks(
    owner_pid: Optional[int] = None,
    owner_start_time: Optional[int] = None,
) -> int:
    """Remove all scoped lock files in the lock directory.

    Called during --replace to clean up stale locks left by stopped/killed
    gateway processes that did not release their locks gracefully.
    When owner metadata is provided, only locks owned by that process are
    removed; malformed lock files are treated as stale and removed.
    Returns the number of lock files removed.
    """
    lock_dir = _get_lock_dir()
    removed = 0
    if lock_dir.exists():
        for lock_file in lock_dir.glob("*.lock"):
            try:
                if owner_pid is not None or owner_start_time is not None:
                    record = _read_json_file(lock_file)
                    if not isinstance(record, dict):
                        pass
                    elif owner_pid is not None and record.get("pid") != owner_pid:
                        continue
                    elif owner_start_time is not None and record.get("start_time") != owner_start_time:
                        continue
                lock_file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


def get_running_pid(
    pid_path: Optional[Path] = None,
    *,
    cleanup_stale: bool = True,
) -> Optional[int]:
    """Return the PID of a running gateway instance, or ``None``.

    Checks the PID file and verifies the process is actually alive.
    Cleans up stale PID files automatically.
    """
    path = pid_path or _get_pid_path()
    if pid_path is None:
        record = _read_pid_record()
    else:
        payload = _read_json_file(path)
        record = payload if payload is not None else None
    if not record:
        if cleanup_stale and pid_path is None:
            remove_pid_file()
        return None

    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        if cleanup_stale:
            _cleanup_stale_pid_path(path)
        return None

    if not _pid_is_alive(pid):
        if cleanup_stale:
            _cleanup_stale_pid_path(path)
        return _running_pid_from_runtime_lock() if pid_path is None else None

    recorded_start = record.get("start_time")
    current_start = _get_process_start_time(pid)
    if recorded_start is not None and current_start is not None and current_start != recorded_start:
        if cleanup_stale:
            _cleanup_stale_pid_path(path)
        return _running_pid_from_runtime_lock() if pid_path is None else None

    if not _looks_like_gateway_process(pid):
        if not _record_looks_like_gateway(record):
            if cleanup_stale:
                _cleanup_stale_pid_path(path)
            return None

    if pid_path is None and not is_gateway_runtime_lock_active():
        if cleanup_stale:
            _cleanup_stale_pid_path(path)
        return None

    return pid


def is_gateway_running() -> bool:
    """Check if the gateway daemon is currently running."""
    return get_running_pid() is not None
