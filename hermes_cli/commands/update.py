"""Update and self-upgrade commands for Hermes CLI."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, List


def _clear_bytecode_cache(root: Path) -> int:
    """Remove all __pycache__ directories under *root*.

    Stale .pyc files can cause ImportError after code updates when Python
    loads a cached bytecode file that references names that no longer exist
    (or don't yet exist) in the updated source.  Clearing them forces Python
    to recompile from the .py source on next import.

    Returns the number of directories removed.
    """
    removed = 0
    for dirpath, dirnames, _ in os.walk(root):
        # Skip venv / node_modules / .git entirely
        dirnames[:] = [
            d for d in dirnames
            if d not in ("venv", ".venv", "node_modules", ".git", ".worktrees")
        ]
        if os.path.basename(dirpath) == "__pycache__":
            try:
                import shutil as _shutil
                _shutil.rmtree(dirpath)
                removed += 1
            except OSError:
                pass
            dirnames.clear()  # nothing left to recurse into
    return removed


def _gateway_prompt(prompt_text: str, default: str = "", timeout: float = 300.0) -> str:
    """File-based IPC prompt for gateway mode.

    Writes a prompt marker file so the gateway can forward the question to the
    user, then polls for a response file.  Falls back to *default* on timeout.

    Used by ``hermes update --gateway`` so interactive prompts (stash restore,
    config migration) are forwarded to the messenger instead of being silently
    skipped.
    """
    import json as _json
    import uuid as _uuid
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    prompt_path = home / ".update_prompt.json"
    response_path = home / ".update_response"

    # Clean any stale response file
    response_path.unlink(missing_ok=True)

    payload = {
        "prompt": prompt_text,
        "default": default,
        "id": str(_uuid.uuid4()),
    }
    tmp = prompt_path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(payload))
    tmp.replace(prompt_path)

    # Poll for response
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if response_path.exists():
            try:
                answer = response_path.read_text().strip()
                response_path.unlink(missing_ok=True)
                prompt_path.unlink(missing_ok=True)
                return answer if answer else default
            except (OSError, ValueError):
                pass
        _time.sleep(0.5)

    # Timeout — clean up and use default
    prompt_path.unlink(missing_ok=True)
    response_path.unlink(missing_ok=True)
    print(f"  (no response after {int(timeout)}s, using default: {default!r})")
    return default


def _build_web_ui(web_dir: Path, *, fatal: bool = False) -> bool:
    """Build the web UI frontend if npm is available.

    Args:
        web_dir: Path to the ``web/`` source directory.
        fatal: If True, print error guidance and return False on failure
               instead of a soft warning (used by ``hermes web``).

    Returns True if the build succeeded or was skipped (no package.json).
    """
    if not (web_dir / "package.json").exists():
        return True
    import shutil
    npm = shutil.which("npm")
    if not npm:
        if fatal:
            print("Web UI frontend not built and npm is not available.")
            print("Install Node.js, then run:  cd web && npm install && npm run build")
        return not fatal
    print("→ Building web UI...")
    r1 = subprocess.run([npm, "install", "--silent"], cwd=web_dir, capture_output=True)
    if r1.returncode != 0:
        print(f"  {'✗' if fatal else '⚠'} Web UI npm install failed"
              + ("" if fatal else " (hermes web will not be available)"))
        if fatal:
            print("  Run manually:  cd web && npm install && npm run build")
        return False
    r2 = subprocess.run([npm, "run", "build"], cwd=web_dir, capture_output=True)
    if r2.returncode != 0:
        print(f"  {'✗' if fatal else '⚠'} Web UI build failed"
              + ("" if fatal else " (hermes web will not be available)"))
        if fatal:
            print("  Run manually:  cd web && npm install && npm run build")
        return False
    print("  ✓ Web UI built")
    return True


def _update_via_zip(args):
    """Update Hermes Agent by downloading a ZIP archive.
    
    Used on Windows when git file I/O is broken (antivirus, NTFS filter 
    drivers causing 'Invalid argument' errors on file creation).
    """
    import shutil
    import tempfile
    import zipfile
    from urllib.request import urlretrieve
    
    branch = "main"
    zip_url = f"https://github.com/FearW/hermes-agent/archive/refs/heads/{branch}.zip"
    
    print("→ Downloading latest version...")
    try:
        tmp_dir = tempfile.mkdtemp(prefix="hermes-update-")
        zip_path = os.path.join(tmp_dir, f"hermes-agent-{branch}.zip")
        urlretrieve(zip_url, zip_path)
        
        print("→ Extracting...")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Validate paths to prevent zip-slip (path traversal)
            tmp_dir_real = os.path.realpath(tmp_dir)
            for member in zf.infolist():
                member_path = os.path.realpath(os.path.join(tmp_dir, member.filename))
                if not member_path.startswith(tmp_dir_real + os.sep) and member_path != tmp_dir_real:
                    raise ValueError(f"Zip-slip detected: {member.filename} escapes extraction directory")
            zf.extractall(tmp_dir)
        
        # GitHub ZIPs extract to hermes-agent-<branch>/
        extracted = os.path.join(tmp_dir, f"hermes-agent-{branch}")
        if not os.path.isdir(extracted):
            # Try to find it
            for d in os.listdir(tmp_dir):
                candidate = os.path.join(tmp_dir, d)
                if os.path.isdir(candidate) and d != "__MACOSX":
                    extracted = candidate
                    break
        
        # Copy updated files over existing installation, preserving venv/node_modules/.git
        preserve = {'venv', 'node_modules', '.git', '.env'}
        update_count = 0
        for item in os.listdir(extracted):
            if item in preserve:
                continue
            src = os.path.join(extracted, item)
            dst = os.path.join(str(PROJECT_ROOT), item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            update_count += 1
        
        print(f"✓ Updated {update_count} items from ZIP")
        
        # Cleanup
        shutil.rmtree(tmp_dir, ignore_errors=True)
        
    except Exception as e:
        print(f"✗ ZIP update failed: {e}")
        sys.exit(1)

    # Clear stale bytecode after ZIP extraction
    removed = _clear_bytecode_cache(PROJECT_ROOT)
    if removed:
        print(f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}")
    
    # Reinstall Python dependencies. Prefer .[all], but if one optional extra
    # breaks on this machine, keep base deps and reinstall the remaining extras
    # individually so update does not silently strip working capabilities.
    print("→ Updating Python dependencies...")
    import subprocess
    uv_bin = shutil.which("uv")
    if uv_bin:
        uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
        _install_python_dependencies_with_optional_fallback([uv_bin, "pip"], env=uv_env)
    else:
        # Use sys.executable to explicitly call the venv's pip module,
        # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
        # Some environments lose pip inside the venv; bootstrap it back with
        # ensurepip before trying the editable install.
        pip_cmd = [sys.executable, "-m", "pip"]
        try:
            subprocess.run(pip_cmd + ["--version"], cwd=PROJECT_ROOT, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            subprocess.run(
                [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                cwd=PROJECT_ROOT,
                check=True,
            )
        _install_python_dependencies_with_optional_fallback(pip_cmd)

    # Build web UI frontend (optional — requires npm)
    _build_web_ui(PROJECT_ROOT / "web")

    # Sync skills
    try:
        from tools.skills_sync import sync_skills
        print("→ Syncing bundled skills...")
        result = sync_skills(quiet=True)
        if result["copied"]:
            print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
        if result.get("updated"):
            print(f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}")
        if result.get("user_modified"):
            print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
        if result.get("cleaned"):
            print(f"  − {len(result['cleaned'])} removed from manifest")
        if not result["copied"] and not result.get("updated"):
            print("  ✓ Skills are up to date")
    except Exception:
        pass
    
    print()
    print("✓ Update complete!")


def _stash_local_changes_if_needed(git_cmd: list[str], cwd: Path) -> Optional[str]:
    status = subprocess.run(
        git_cmd + ["status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        return None

    # If the index has unmerged entries (e.g. from an interrupted merge/rebase),
    # git stash will fail with "needs merge / could not write index".  Clear the
    # conflict state with `git reset` so the stash can proceed.  Working-tree
    # changes are preserved; only the index conflict markers are dropped.
    unmerged = subprocess.run(
        git_cmd + ["ls-files", "--unmerged"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if unmerged.stdout.strip():
        print("→ Clearing unmerged index entries from a previous conflict...")
        subprocess.run(git_cmd + ["reset"], cwd=cwd, capture_output=True)

    from datetime import datetime, timezone

    stash_name = datetime.now(timezone.utc).strftime("hermes-update-autostash-%Y%m%d-%H%M%S")
    print("→ Local changes detected — stashing before update...")
    subprocess.run(
        git_cmd + ["stash", "push", "--include-untracked", "-m", stash_name],
        cwd=cwd,
        check=True,
    )
    stash_ref = subprocess.run(
        git_cmd + ["rev-parse", "--verify", "refs/stash"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return stash_ref



def _resolve_stash_selector(git_cmd: list[str], cwd: Path, stash_ref: str) -> Optional[str]:
    stash_list = subprocess.run(
        git_cmd + ["stash", "list", "--format=%gd %H"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in stash_list.stdout.splitlines():
        selector, _, commit = line.partition(" ")
        if commit.strip() == stash_ref:
            return selector.strip()
    return None



def _print_stash_cleanup_guidance(stash_ref: str, stash_selector: Optional[str] = None) -> None:
    print("  Check `git status` first so you don't accidentally reapply the same change twice.")
    print("  Find the saved entry with: git stash list --format='%gd %H %s'")
    if stash_selector:
        print(f"  Remove it with: git stash drop {stash_selector}")
    else:
        print(f"  Look for commit {stash_ref}, then drop its selector with: git stash drop stash@{{N}}")



def _restore_stashed_changes(
    git_cmd: list[str],
    cwd: Path,
    stash_ref: str,
    prompt_user: bool = False,
    input_fn=None,
) -> bool:
    if prompt_user:
        print()
        print("⚠ Local changes were stashed before updating.")
        print("  Restoring them may reapply local customizations onto the updated codebase.")
        print("  Review the result afterward if Hermes behaves unexpectedly.")
        print("Restore local changes now? [Y/n]")
        if input_fn is not None:
            response = input_fn("Restore local changes now? [Y/n]", "y")
        else:
            response = input().strip().lower()
        if response not in ("", "y", "yes"):
            print("Skipped restoring local changes.")
            print("Your changes are still preserved in git stash.")
            print(f"Restore manually with: git stash apply {stash_ref}")
            return False

    print("→ Restoring local changes...")
    restore = subprocess.run(
        git_cmd + ["stash", "apply", stash_ref],
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    # Check for unmerged (conflicted) files — can happen even when returncode is 0
    unmerged = subprocess.run(
        git_cmd + ["diff", "--name-only", "--diff-filter=U"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    has_conflicts = bool(unmerged.stdout.strip())

    if restore.returncode != 0 or has_conflicts:
        print("✗ Update pulled new code, but restoring local changes hit conflicts.")
        if restore.stdout.strip():
            print(restore.stdout.strip())
        if restore.stderr.strip():
            print(restore.stderr.strip())

        # Show which files conflicted
        conflicted_files = unmerged.stdout.strip()
        if conflicted_files:
            print("\nConflicted files:")
            for f in conflicted_files.splitlines():
                print(f"  • {f}")

        print("\nYour stashed changes are preserved — nothing is lost.")
        print(f"  Stash ref: {stash_ref}")

        # Always reset to clean state — leaving conflict markers in source
        # files makes hermes completely unrunnable (SyntaxError on import).
        # The user's changes are safe in the stash for manual recovery.
        subprocess.run(
            git_cmd + ["reset", "--hard", "HEAD"],
            cwd=cwd,
            capture_output=True,
        )
        print("Working tree reset to clean state.")
        print(f"Restore your changes later with: git stash apply {stash_ref}")
        # Don't sys.exit — the code update itself succeeded, only the stash
        # restore had conflicts.  Let cmd_update continue with pip install,
        # skill sync, and gateway restart.
        return False

    stash_selector = _resolve_stash_selector(git_cmd, cwd, stash_ref)
    if stash_selector is None:
        print("⚠ Local changes were restored, but Hermes couldn't find the stash entry to drop.")
        print("  The stash was left in place. You can remove it manually after checking the result.")
        _print_stash_cleanup_guidance(stash_ref)
    else:
        drop = subprocess.run(
            git_cmd + ["stash", "drop", stash_selector],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if drop.returncode != 0:
            print("⚠ Local changes were restored, but Hermes couldn't drop the saved stash entry.")
            if drop.stdout.strip():
                print(drop.stdout.strip())
            if drop.stderr.strip():
                print(drop.stderr.strip())
            print("  The stash was left in place. You can remove it manually after checking the result.")
            _print_stash_cleanup_guidance(stash_ref, stash_selector)

    print("⚠ Local changes were restored on top of the updated codebase.")
    print("  Review `git diff` / `git status` if Hermes behaves unexpectedly.")
    return True

# =========================================================================
# Fork detection and upstream management for `hermes update`
# =========================================================================

OFFICIAL_REPO_URLS = {
    "https://github.com/FearW/hermes-agent.git",
    "git@github.com:FearW/hermes-agent.git",
    "https://github.com/FearW/hermes-agent",
    "git@github.com:FearW/hermes-agent",
}
OFFICIAL_REPO_URL = "https://github.com/FearW/hermes-agent.git"
SKIP_UPSTREAM_PROMPT_FILE = ".skip_upstream_prompt"


def _get_origin_url(git_cmd: list[str], cwd: Path) -> Optional[str]:
    """Get the URL of the origin remote, or None if not set."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _is_fork(origin_url: Optional[str]) -> bool:
    """Check if the origin remote points to a fork (not the official repo)."""
    if not origin_url:
        return False
    # Normalize URL for comparison (strip trailing .git if present)
    normalized = origin_url.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    for official in OFFICIAL_REPO_URLS:
        official_normalized = official.rstrip("/")
        if official_normalized.endswith(".git"):
            official_normalized = official_normalized[:-4]
        if normalized == official_normalized:
            return False
    return True


def _has_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Check if an 'upstream' remote already exists."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", "upstream"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _add_upstream_remote(git_cmd: list[str], cwd: Path) -> bool:
    """Add the official repo as the 'upstream' remote. Returns True on success."""
    try:
        result = subprocess.run(
            git_cmd + ["remote", "add", "upstream", OFFICIAL_REPO_URL],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _count_commits_between(git_cmd: list[str], cwd: Path, base: str, head: str) -> int:
    """Count commits on `head` that are not on `base`. Returns -1 on error."""
    try:
        result = subprocess.run(
            git_cmd + ["rev-list", "--count", f"{base}..{head}"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return -1


def _should_skip_upstream_prompt() -> bool:
    """Check if user previously declined to add upstream."""
    from hermes_constants import get_hermes_home
    return (get_hermes_home() / SKIP_UPSTREAM_PROMPT_FILE).exists()


def _mark_skip_upstream_prompt():
    """Create marker file to skip future upstream prompts."""
    try:
        from hermes_constants import get_hermes_home
        (get_hermes_home() / SKIP_UPSTREAM_PROMPT_FILE).touch()
    except Exception:
        pass


def _sync_fork_with_upstream(git_cmd: list[str], cwd: Path) -> bool:
    """Attempt to push updated main to origin (sync fork).

    Returns True if push succeeded, False otherwise.
    """
    try:
        result = subprocess.run(
            git_cmd + ["push", "origin", "main", "--force-with-lease"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _sync_with_upstream_if_needed(git_cmd: list[str], cwd: Path) -> None:
    """Check if fork is behind upstream and sync if safe.

    This implements the fork upstream sync logic:
    - If upstream remote doesn't exist, ask user if they want to add it
    - Compare origin/main with upstream/main
    - If origin/main is strictly behind upstream/main, pull from upstream
    - Try to sync fork back to origin if possible
    """
    has_upstream = _has_upstream_remote(git_cmd, cwd)

    if not has_upstream:
        # Check if user previously declined
        if _should_skip_upstream_prompt():
            return

        # Ask user if they want to add upstream
        print()
        print("ℹ Your fork is not tracking the official Hermes repository.")
        print("  This means you may miss updates from FearW/hermes-agent.")
        print()
        try:
            response = input("Add official repo as 'upstream' remote? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            response = "n"

        if response in ("", "y", "yes"):
            print("→ Adding upstream remote...")
            if _add_upstream_remote(git_cmd, cwd):
                print("  ✓ Added upstream: https://github.com/FearW/hermes-agent.git")
                has_upstream = True
            else:
                print("  ✗ Failed to add upstream remote. Skipping upstream sync.")
                return
        else:
            print("  Skipped. Run 'git remote add upstream https://github.com/FearW/hermes-agent.git' to add later.")
            _mark_skip_upstream_prompt()
            return

    # Fetch upstream
    print()
    print("→ Fetching upstream...")
    try:
        subprocess.run(
            git_cmd + ["fetch", "upstream", "--quiet"],
            cwd=cwd,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        print("  ✗ Failed to fetch upstream. Skipping upstream sync.")
        return

    # Compare origin/main with upstream/main
    origin_ahead = _count_commits_between(git_cmd, cwd, "upstream/main", "origin/main")
    upstream_ahead = _count_commits_between(git_cmd, cwd, "origin/main", "upstream/main")

    if origin_ahead < 0 or upstream_ahead < 0:
        print("  ✗ Could not compare branches. Skipping upstream sync.")
        return

    # If origin/main has commits not on upstream, don't trample
    if origin_ahead > 0:
        print()
        print(f"ℹ Your fork has {origin_ahead} commit(s) not on upstream.")
        print("  Skipping upstream sync to preserve your changes.")
        print("  If you want to merge upstream changes, run:")
        print("    git pull upstream main")
        return

    # If upstream is not ahead, fork is up to date
    if upstream_ahead == 0:
        print("  ✓ Fork is up to date with upstream")
        return

    # origin/main is strictly behind upstream/main (can fast-forward)
    print()
    print(f"→ Fork is {upstream_ahead} commit(s) behind upstream")
    print("→ Pulling from upstream...")

    try:
        subprocess.run(
            git_cmd + ["pull", "--ff-only", "upstream", "main"],
            cwd=cwd,
            check=True,
        )
    except subprocess.CalledProcessError:
        print("  ✗ Failed to pull from upstream. You may need to resolve conflicts manually.")
        return

    print("  ✓ Updated from upstream")

    # Try to sync fork back to origin
    print("→ Syncing fork...")
    if _sync_fork_with_upstream(git_cmd, cwd):
        print("  ✓ Fork synced with upstream")
    else:
        print("  ℹ Got updates from upstream but couldn't push to fork (no write access?)")
        print("    Your local repo is updated, but your fork on GitHub may be behind.")


def _invalidate_update_cache():
    """Delete the update-check cache for ALL profiles so no banner
    reports a stale "commits behind" count after a successful update.

    The git repo is shared across profiles — when one profile runs
    ``hermes update``, every profile is now current.
    """
    homes = []
    # Default profile home (Docker-aware — uses /opt/data in Docker)
    from hermes_constants import get_default_hermes_root
    default_home = get_default_hermes_root()
    homes.append(default_home)
    # Named profiles under <root>/profiles/
    profiles_root = default_home / "profiles"
    if profiles_root.is_dir():
        for entry in profiles_root.iterdir():
            if entry.is_dir():
                homes.append(entry)
    for home in homes:
        try:
            cache_file = home / ".update_check"
            if cache_file.exists():
                cache_file.unlink()
        except Exception:
            pass


def _load_installable_optional_extras() -> list[str]:
    """Return the optional extras referenced by the ``all`` group.

    Only extras that ``[all]`` actually pulls in are retried individually.
    Extras outside ``[all]`` (e.g. ``rl``, ``yc-bench``) are intentionally
    excluded — they have heavy or platform-specific deps that most users
    never installed.
    """
    try:
        import tomllib
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
    except Exception:
        return []

    optional_deps = project.get("optional-dependencies", {})
    if not isinstance(optional_deps, dict):
        return []

    # Parse the [all] group to find which extras it references.
    # Entries look like "hermes-agent[matrix]" or "package-name[extra]".
    all_refs = optional_deps.get("all", [])
    referenced: list[str] = []
    for ref in all_refs:
        if "[" in ref and "]" in ref:
            name = ref.split("[", 1)[1].split("]", 1)[0]
            if name in optional_deps:
                referenced.append(name)

    return referenced



def _install_python_dependencies_with_optional_fallback(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Install base deps plus as many optional extras as the environment supports."""
    try:
        subprocess.run(
            install_cmd_prefix + ["install", "-e", ".[all]", "--quiet"],
            cwd=PROJECT_ROOT,
            check=True,
            env=env,
        )
        return
    except subprocess.CalledProcessError:
        print("  ⚠ Optional extras failed, reinstalling base dependencies and retrying extras individually...")

    subprocess.run(
        install_cmd_prefix + ["install", "-e", ".", "--quiet"],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )

    failed_extras: list[str] = []
    installed_extras: list[str] = []
    for extra in _load_installable_optional_extras():
        try:
            subprocess.run(
                install_cmd_prefix + ["install", "-e", f".[{extra}]", "--quiet"],
                cwd=PROJECT_ROOT,
                check=True,
                env=env,
            )
            installed_extras.append(extra)
        except subprocess.CalledProcessError:
            failed_extras.append(extra)

    if installed_extras:
        print(f"  ✓ Reinstalled optional extras individually: {', '.join(installed_extras)}")
    if failed_extras:
        print(f"  ⚠ Skipped optional extras that still failed: {', '.join(failed_extras)}")


def cmd_update(args):
    """Update Hermes Agent to the latest version."""
    import shutil
    from hermes_cli.config import is_managed, managed_error

    if is_managed():
        managed_error("update Hermes Agent")
        return

    gateway_mode = getattr(args, "gateway", False)
    # In gateway mode, use file-based IPC for prompts instead of stdin
    gw_input_fn = (lambda prompt, default="": _gateway_prompt(prompt, default)) if gateway_mode else None
    
    print("⚕ Updating Hermes Agent...")
    print()
    
    # Try git-based update first, fall back to ZIP download on Windows
    # when git file I/O is broken (antivirus, NTFS filter drivers, etc.)
    use_zip_update = False
    git_dir = PROJECT_ROOT / '.git'
    
    if not git_dir.exists():
        if sys.platform == "win32":
            use_zip_update = True
        else:
            print("✗ Not a git repository. Please reinstall:")
            print("  curl -fsSL https://raw.githubusercontent.com/FearW/hermes-agent/main/scripts/install.sh | bash")
            sys.exit(1)
    
    # On Windows, git can fail with "unable to write loose object file: Invalid argument"
    # due to filesystem atomicity issues. Set the recommended workaround.
    if sys.platform == "win32" and git_dir.exists():
        subprocess.run(
            ["git", "-c", "windows.appendAtomically=false", "config", "windows.appendAtomically", "false"],
            cwd=PROJECT_ROOT, check=False, capture_output=True
        )

    # Build git command once — reused for fork detection and the update itself.
    git_cmd = ["git"]
    if sys.platform == "win32":
        git_cmd = ["git", "-c", "windows.appendAtomically=false"]

    # Detect if we're updating from a fork (before any branch logic)
    origin_url = _get_origin_url(git_cmd, PROJECT_ROOT)
    is_fork = _is_fork(origin_url)

    if is_fork:
        print("⚠ Updating from fork:")
        print(f"  {origin_url}")
        print()

    if use_zip_update:
        # ZIP-based update for Windows when git is broken
        _update_via_zip(args)
        return

    # Fetch and pull
    try:

        print("→ Fetching updates...")
        fetch_result = subprocess.run(
            git_cmd + ["fetch", "origin"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            stderr = fetch_result.stderr.strip()
            if "Could not resolve host" in stderr or "unable to access" in stderr:
                print("✗ Network error — cannot reach the remote repository.")
                print(f"  {stderr.splitlines()[0]}" if stderr else "")
            elif "Authentication failed" in stderr or "could not read Username" in stderr:
                print("✗ Authentication failed — check your git credentials or SSH key.")
            else:
                print(f"✗ Failed to fetch updates from origin.")
                if stderr:
                    print(f"  {stderr.splitlines()[0]}")
            sys.exit(1)

        # Get current branch (returns literal "HEAD" when detached)
        result = subprocess.run(
            git_cmd + ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        current_branch = result.stdout.strip()

        # Always update against main
        branch = "main"

        # If user is on a non-main branch or detached HEAD, switch to main
        if current_branch != "main":
            label = "detached HEAD" if current_branch == "HEAD" else f"branch '{current_branch}'"
            print(f"  ⚠ Currently on {label} — switching to main for update...")
            # Stash before checkout so uncommitted work isn't lost
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)
            subprocess.run(
                git_cmd + ["checkout", "main"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
        else:
            auto_stash_ref = _stash_local_changes_if_needed(git_cmd, PROJECT_ROOT)

        prompt_for_restore = auto_stash_ref is not None and (
            gateway_mode or (sys.stdin.isatty() and sys.stdout.isatty())
        )

        # Check if there are updates
        result = subprocess.run(
            git_cmd + ["rev-list", f"HEAD..origin/{branch}", "--count"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_count = int(result.stdout.strip())

        if commit_count == 0:
            _invalidate_update_cache()
            # Restore stash and switch back to original branch if we moved
            if auto_stash_ref is not None:
                _restore_stashed_changes(
                    git_cmd, PROJECT_ROOT, auto_stash_ref,
                    prompt_user=prompt_for_restore,
                    input_fn=gw_input_fn,
                )
            if current_branch not in ("main", "HEAD"):
                subprocess.run(
                    git_cmd + ["checkout", current_branch],
                    cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
                )
            print("✓ Already up to date!")
            return

        print(f"→ Found {commit_count} new commit(s)")

        print("→ Pulling updates...")
        update_succeeded = False
        try:
            pull_result = subprocess.run(
                git_cmd + ["pull", "--ff-only", "origin", branch],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            if pull_result.returncode != 0:
                # ff-only failed — local and remote have diverged (e.g. upstream
                # force-pushed or rebase).  Since local changes are already
                # stashed, reset to match the remote exactly.
                print("  ⚠ Fast-forward not possible (history diverged), resetting to match remote...")
                reset_result = subprocess.run(
                    git_cmd + ["reset", "--hard", f"origin/{branch}"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                )
                if reset_result.returncode != 0:
                    print(f"✗ Failed to reset to origin/{branch}.")
                    if reset_result.stderr.strip():
                        print(f"  {reset_result.stderr.strip()}")
                    print("  Try manually: git fetch origin && git reset --hard origin/main")
                    sys.exit(1)
            update_succeeded = True
        finally:
            if auto_stash_ref is not None:
                # Don't attempt stash restore if the code update itself failed —
                # working tree is in an unknown state.
                if not update_succeeded:
                    print(f"  ℹ️  Local changes preserved in stash (ref: {auto_stash_ref})")
                    print(f"  Restore manually with: git stash apply")
                else:
                    _restore_stashed_changes(
                        git_cmd,
                        PROJECT_ROOT,
                        auto_stash_ref,
                        prompt_user=prompt_for_restore,
                        input_fn=gw_input_fn,
                    )
        
        _invalidate_update_cache()

        # Clear stale .pyc bytecode cache — prevents ImportError on gateway
        # restart when updated source references names that didn't exist in
        # the old bytecode (e.g. get_hermes_home added to hermes_constants).
        removed = _clear_bytecode_cache(PROJECT_ROOT)
        if removed:
            print(f"  ✓ Cleared {removed} stale __pycache__ director{'y' if removed == 1 else 'ies'}")

        # Fork upstream sync logic (only for main branch on forks)
        if is_fork and branch == "main":
            _sync_with_upstream_if_needed(git_cmd, PROJECT_ROOT)
        
        # Reinstall Python dependencies. Prefer .[all], but if one optional extra
        # breaks on this machine, keep base deps and reinstall the remaining extras
        # individually so update does not silently strip working capabilities.
        print("→ Updating Python dependencies...")
        uv_bin = shutil.which("uv")
        if uv_bin:
            uv_env = {**os.environ, "VIRTUAL_ENV": str(PROJECT_ROOT / "venv")}
            _install_python_dependencies_with_optional_fallback([uv_bin, "pip"], env=uv_env)
        else:
            # Use sys.executable to explicitly call the venv's pip module,
            # avoiding PEP 668 'externally-managed-environment' errors on Debian/Ubuntu.
            # Some environments lose pip inside the venv; bootstrap it back with
            # ensurepip before trying the editable install.
            pip_cmd = [sys.executable, "-m", "pip"]
            try:
                subprocess.run(pip_cmd + ["--version"], cwd=PROJECT_ROOT, check=True, capture_output=True)
            except subprocess.CalledProcessError:
                subprocess.run(
                    [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
                    cwd=PROJECT_ROOT,
                    check=True,
                )
            _install_python_dependencies_with_optional_fallback(pip_cmd)
        
        # Check for Node.js deps
        if (PROJECT_ROOT / "package.json").exists():
            import shutil
            if shutil.which("npm"):
                print("→ Updating Node.js dependencies...")
                subprocess.run(["npm", "install", "--silent"], cwd=PROJECT_ROOT, check=False)

        # Build web UI frontend (optional — requires npm)
        _build_web_ui(PROJECT_ROOT / "web")

        print()
        print("✓ Code updated!")
        
        # After git pull, source files on disk are newer than cached Python
        # modules in this process.  Reload hermes_constants so that any lazy
        # import executed below (skills sync, gateway restart) sees new
        # attributes like display_hermes_home() added since the last release.
        try:
            import importlib
            import hermes_constants as _hc
            importlib.reload(_hc)
        except Exception:
            pass  # non-fatal — worst case a lazy import fails gracefully
        
        # Sync bundled skills (copies new, updates changed, respects user deletions)
        try:
            from tools.skills_sync import sync_skills
            print()
            print("→ Syncing bundled skills...")
            result = sync_skills(quiet=True)
            if result["copied"]:
                print(f"  + {len(result['copied'])} new: {', '.join(result['copied'])}")
            if result.get("updated"):
                print(f"  ↑ {len(result['updated'])} updated: {', '.join(result['updated'])}")
            if result.get("user_modified"):
                print(f"  ~ {len(result['user_modified'])} user-modified (kept)")
            if result.get("cleaned"):
                print(f"  − {len(result['cleaned'])} removed from manifest")
            if not result["copied"] and not result.get("updated"):
                print("  ✓ Skills are up to date")
        except Exception as e:
            logger.debug("Skills sync during update failed: %s", e)

        # Sync bundled skills to all other profiles
        try:
            from hermes_cli.profiles import list_profiles, get_active_profile_name, seed_profile_skills
            active = get_active_profile_name()
            other_profiles = [p for p in list_profiles() if p.name != active]
            if other_profiles:
                print()
                print("→ Syncing bundled skills to other profiles...")
                for p in other_profiles:
                    try:
                        r = seed_profile_skills(p.path, quiet=True)
                        if r:
                            copied = len(r.get("copied", []))
                            updated = len(r.get("updated", []))
                            modified = len(r.get("user_modified", []))
                            parts = []
                            if copied: parts.append(f"+{copied} new")
                            if updated: parts.append(f"↑{updated} updated")
                            if modified: parts.append(f"~{modified} user-modified")
                            status = ", ".join(parts) if parts else "up to date"
                        else:
                            status = "sync failed"
                        print(f"  {p.name}: {status}")
                    except Exception as pe:
                        print(f"  {p.name}: error ({pe})")
        except Exception:
            pass  # profiles module not available or no profiles

        # Sync Honcho host blocks to all profiles
        try:
            from plugins.memory.honcho.cli import sync_honcho_profiles_quiet
            synced = sync_honcho_profiles_quiet()
            if synced:
                print(f"\n-> Honcho: synced {synced} profile(s)")
        except Exception:
            pass  # honcho plugin not installed or not configured

        # Check for config migrations
        print()
        print("→ Checking configuration for new options...")
        
        from hermes_cli.config import (
            get_missing_env_vars, get_missing_config_fields, 
            check_config_version, migrate_config
        )
        
        missing_env = get_missing_env_vars(required_only=True)
        missing_config = get_missing_config_fields()
        current_ver, latest_ver = check_config_version()
        
        needs_migration = missing_env or missing_config or current_ver < latest_ver
        
        if needs_migration:
            print()
            if missing_env:
                print(f"  ⚠️  {len(missing_env)} new required setting(s) need configuration")
            if missing_config:
                print(f"  ℹ️  {len(missing_config)} new config option(s) available")
            
            print()
            if gateway_mode:
                response = _gateway_prompt(
                    "Would you like to configure new options now? [Y/n]", "n"
                ).strip().lower()
            elif not (sys.stdin.isatty() and sys.stdout.isatty()):
                print("  ℹ Non-interactive session — skipping config migration prompt.")
                print("    Run 'hermes config migrate' later to apply any new config/env options.")
                response = "n"
            else:
                try:
                    response = input("Would you like to configure them now? [Y/n]: ").strip().lower()
                except EOFError:
                    response = "n"
            
            if response in ('', 'y', 'yes'):
                print()
                # In gateway mode, run auto-migrations only (no input() prompts
                # for API keys which would hang the detached process).
                results = migrate_config(interactive=not gateway_mode, quiet=False)
                
                if results["env_added"] or results["config_added"]:
                    print()
                    print("✓ Configuration updated!")
                if gateway_mode and missing_env:
                    print("  ℹ API keys require manual entry: hermes config migrate")
            else:
                print()
                print("Skipped. Run 'hermes config migrate' later to configure.")
        else:
            print("  ✓ Configuration is up to date")
        
        print()
        print("✓ Update complete!")
        
        # Write exit code *before* the gateway restart attempt.
        # When running as ``hermes update --gateway`` (spawned by the gateway's
        # /update command), this process lives inside the gateway's systemd
        # cgroup.  ``systemctl restart hermes-gateway`` kills everything in the
        # cgroup (KillMode=mixed → SIGKILL to remaining processes), including
        # us and the wrapping bash shell.  The shell never reaches its
        # ``printf $status > .update_exit_code`` epilogue, so the exit-code
        # marker file is never created.  The new gateway's update watcher then
        # polls for 30 minutes and sends a spurious timeout message.
        #
        # Writing the marker here — after git pull + pip install succeed but
        # before we attempt the restart — ensures the new gateway sees it
        # regardless of how we die.
        if gateway_mode:
            _exit_code_path = get_hermes_home() / ".update_exit_code"
            try:
                _exit_code_path.write_text("0")
            except OSError:
                pass
        
        # Auto-restart ALL gateways after update.
        # The code update (git pull) is shared across all profiles, so every
        # running gateway needs restarting to pick up the new code.
        try:
            from hermes_cli.gateway import (
                is_macos, supports_systemd_services, _ensure_user_systemd_env,
                find_gateway_pids,
                _get_service_pids,
            )
            import signal as _signal

            restarted_services = []
            killed_pids = set()

            # --- Systemd services (Linux) ---
            # Discover all hermes-gateway* units (default + profiles)
            if supports_systemd_services():
                try:
                    _ensure_user_systemd_env()
                except Exception:
                    pass

                for scope, scope_cmd in [("user", ["systemctl", "--user"]), ("system", ["systemctl"])]:
                    try:
                        result = subprocess.run(
                            scope_cmd + ["list-units", "hermes-gateway*", "--plain", "--no-legend", "--no-pager"],
                            capture_output=True, text=True, timeout=10,
                        )
                        for line in result.stdout.strip().splitlines():
                            parts = line.split()
                            if not parts:
                                continue
                            unit = parts[0]  # e.g. hermes-gateway.service or hermes-gateway-coder.service
                            if not unit.endswith(".service"):
                                continue
                            svc_name = unit.removesuffix(".service")
                            # Check if active
                            check = subprocess.run(
                                scope_cmd + ["is-active", svc_name],
                                capture_output=True, text=True, timeout=5,
                            )
                            if check.stdout.strip() == "active":
                                restart = subprocess.run(
                                    scope_cmd + ["restart", svc_name],
                                    capture_output=True, text=True, timeout=15,
                                )
                                if restart.returncode == 0:
                                    restarted_services.append(svc_name)
                                else:
                                    print(f"  ⚠ Failed to restart {svc_name}: {restart.stderr.strip()}")
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        pass

            # --- Launchd services (macOS) ---
            if is_macos():
                try:
                    from hermes_cli.gateway import launchd_restart, get_launchd_label, get_launchd_plist_path
                    plist_path = get_launchd_plist_path()
                    if plist_path.exists():
                        check = subprocess.run(
                            ["launchctl", "list", get_launchd_label()],
                            capture_output=True, text=True, timeout=5,
                        )
                        if check.returncode == 0:
                            try:
                                launchd_restart()
                                restarted_services.append(get_launchd_label())
                            except subprocess.CalledProcessError as e:
                                stderr = (getattr(e, "stderr", "") or "").strip()
                                print(f"  ⚠ Gateway restart failed: {stderr}")
                except (FileNotFoundError, subprocess.TimeoutExpired, ImportError):
                    pass

            # --- Manual (non-service) gateways ---
            # Kill any remaining gateway processes not managed by a service.
            # Exclude PIDs that belong to just-restarted services so we don't
            # immediately kill the process that systemd/launchd just spawned.
            service_pids = _get_service_pids()
            manual_pids = find_gateway_pids(exclude_pids=service_pids, all_profiles=True)
            for pid in manual_pids:
                try:
                    os.kill(pid, _signal.SIGTERM)
                    killed_pids.add(pid)
                except (ProcessLookupError, PermissionError):
                    pass

            if restarted_services or killed_pids:
                print()
                for svc in restarted_services:
                    print(f"  ✓ Restarted {svc}")
                if killed_pids:
                    print(f"  → Stopped {len(killed_pids)} manual gateway process(es)")
                    print("    Restart manually: hermes gateway run")
                    # Also restart for each profile if needed
                    if len(killed_pids) > 1:
                        print("    (or: hermes -p <profile> gateway run  for each profile)")

            if not restarted_services and not killed_pids:
                # No gateways were running — nothing to do
                pass

        except Exception as e:
            logger.debug("Gateway restart during update failed: %s", e)
        
        print()
        print("Tip: You can now select a provider and model:")
        print("  hermes model              # Select provider and model")
        
    except subprocess.CalledProcessError as e:
        if sys.platform == "win32":
            print(f"⚠ Git update failed: {e}")
            print("→ Falling back to ZIP download...")
            print()
            _update_via_zip(args)
        else:
            print(f"✗ Update failed: {e}")
            sys.exit(1)


