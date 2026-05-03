"""
hermes fallback — manage the CPA fallback model chain.

Fallback models are tried in order when the primary CPA model fails with
rate-limit, overload, or connection errors.

Subcommands:
  hermes fallback [list]   Show the current fallback chain (default when no subcommand)
  hermes fallback add      Add a CPA model name to the chain
  hermes fallback remove   Pick an entry to delete from the chain
  hermes fallback clear    Remove all fallback entries

Storage: ``fallback_providers`` in ``~/.hermes/config.yaml`` (top-level, list
of model strings). The legacy ``fallback_model`` format is migrated on write.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_chain(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the normalized fallback chain as a list of dicts.

    Accepts both the list format (``fallback_providers``) and the legacy
    ``fallback_model`` key. Provider fields in old configs are ignored.
    """
    chain = config.get("fallback_providers") or []
    if isinstance(chain, list):
        result = []
        for entry in chain:
            if isinstance(entry, str) and entry.strip():
                result.append({"provider": "cliproxyapi", "model": entry.strip()})
            elif isinstance(entry, dict) and entry.get("model"):
                item = dict(entry)
                item["provider"] = "cliproxyapi"
                result.append(item)
        if result:
            return result
    legacy = config.get("fallback_model")
    if isinstance(legacy, str) and legacy.strip():
        return [{"provider": "cliproxyapi", "model": legacy.strip()}]
    if isinstance(legacy, dict) and legacy.get("model"):
        item = dict(legacy)
        item["provider"] = "cliproxyapi"
        return [item]
    if isinstance(legacy, list):
        return _read_chain({"fallback_providers": legacy})
    return []


def _write_chain(config: Dict[str, Any], chain: List[Dict[str, Any]]) -> None:
    """Persist the chain to ``fallback_providers`` and clear legacy key."""
    config["fallback_providers"] = [entry.get("model", "") for entry in chain if entry.get("model")]
    # Drop the legacy single-dict key on write so there's only one source of truth.
    if "fallback_model" in config:
        config.pop("fallback_model", None)


def _format_entry(entry: Dict[str, Any]) -> str:
    """One-line human-readable rendering of a fallback entry."""
    model = entry.get("model", "?")
    context = entry.get("context_length")
    suffix = f"  [{context} ctx]" if context else ""
    return f"{model}  (CPA){suffix}"


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_fallback_list(args) -> None:  # noqa: ARG001
    """Print the current fallback chain."""
    from hermes_cli.config import load_config

    config = load_config()
    chain = _read_chain(config)

    print()
    if not chain:
        print("  No CPA fallback models configured.")
        print()
        print("  Add one with:  hermes fallback add")
        print()
        return

    primary = _describe_primary(config)
    if primary:
        print(f"  Primary:   {primary}")
        print()
    print(f"  Fallback chain ({len(chain)} {'entry' if len(chain) == 1 else 'entries'}):")
    for i, entry in enumerate(chain, 1):
        print(f"    {i}. {_format_entry(entry)}")
    print()
    print("  Tried in order when the primary CPA model fails (rate-limit, 5xx, connection errors).")
    print()


def _describe_primary(config: Dict[str, Any]) -> Optional[str]:
    """One-line description of the primary model for display purposes."""
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        provider = (model_cfg.get("provider") or "?").strip() or "?"
        model = (model_cfg.get("default") or model_cfg.get("model") or "?").strip() or "?"
        return f"{model}  (CPA)"
    if isinstance(model_cfg, str) and model_cfg.strip():
        return model_cfg.strip()
    return None


def cmd_fallback_add(args) -> None:
    """Prompt for a CPA model name, then append it to the chain."""
    from hermes_cli.main import _require_tty
    from hermes_cli.config import load_config, save_config

    _require_tty("fallback add")

    print()
    print("  Adding a CPA fallback model. Hermes keeps the same CPA endpoint")
    print("  and only switches the model name sent to CPA.")
    print()
    try:
        model = input("  CPA fallback model name: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        print("  No fallback added.")
        return
    if not model:
        print("  No fallback added.")
        return

    final_cfg = load_config()
    chain = _read_chain(final_cfg)
    new_entry = {"provider": "cliproxyapi", "model": model}
    for existing in chain:
        if existing.get("model") == model:
            print()
            print(f"  {_format_entry(new_entry)} is already in the fallback chain — skipped.")
            return
    chain.append(new_entry)
    _write_chain(final_cfg, chain)
    save_config(final_cfg)
    print()
    print(f"  Added fallback: {_format_entry(new_entry)}")
    print(f"  Chain is now {len(chain)} {'entry' if len(chain) == 1 else 'entries'} long.")
    print()
    print("  Run `hermes fallback list` to view, or `hermes fallback remove` to delete.")


def cmd_fallback_remove(args) -> None:  # noqa: ARG001
    """Pick an entry from the chain and remove it."""
    from hermes_cli.config import load_config, save_config

    config = load_config()
    chain = _read_chain(config)

    if not chain:
        print()
        print("  No CPA fallback models configured — nothing to remove.")
        print()
        return

    choices = [_format_entry(e) for e in chain]
    choices.append("Cancel")

    try:
        from hermes_cli.setup import _curses_prompt_choice
        idx = _curses_prompt_choice("Select a fallback to remove:", choices, 0)
    except Exception:
        idx = _numbered_pick("Select a fallback to remove:", choices)

    if idx is None or idx < 0 or idx >= len(chain):
        print()
        print("  Cancelled — no change.")
        return

    removed = chain.pop(idx)
    _write_chain(config, chain)
    save_config(config)

    print()
    print(f"  Removed fallback: {_format_entry(removed)}")
    if chain:
        print(f"  Chain is now {len(chain)} {'entry' if len(chain) == 1 else 'entries'} long.")
    else:
        print("  Fallback chain is now empty.")
    print()


def cmd_fallback_clear(args) -> None:  # noqa: ARG001
    """Remove all fallback entries (with confirmation)."""
    from hermes_cli.config import load_config, save_config

    config = load_config()
    chain = _read_chain(config)

    if not chain:
        print()
        print("  No CPA fallback models configured — nothing to clear.")
        print()
        return

    print()
    print(f"  Current fallback chain ({len(chain)} {'entry' if len(chain) == 1 else 'entries'}):")
    for i, entry in enumerate(chain, 1):
        print(f"    {i}. {_format_entry(entry)}")
    print()
    try:
        resp = input("  Clear all entries? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        print("  Cancelled.")
        return
    if resp not in ("y", "yes"):
        print("  Cancelled — no change.")
        return

    _write_chain(config, [])
    save_config(config)
    print()
    print("  Fallback chain cleared.")
    print()


def _numbered_pick(question: str, choices: List[str]) -> Optional[int]:
    """Fallback numbered-list picker when curses is unavailable."""
    print(question)
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    print()
    while True:
        try:
            val = input(f"Choice [1-{len(choices)}]: ").strip()
            if not val:
                return None
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return idx
            print(f"Please enter 1-{len(choices)}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def cmd_fallback(args) -> None:
    """Top-level dispatcher for ``hermes fallback [subcommand]``."""
    sub = getattr(args, "fallback_command", None)
    if sub in (None, "", "list", "ls"):
        cmd_fallback_list(args)
    elif sub == "add":
        cmd_fallback_add(args)
    elif sub in ("remove", "rm"):
        cmd_fallback_remove(args)
    elif sub == "clear":
        cmd_fallback_clear(args)
    else:
        print(f"Unknown fallback subcommand: {sub}")
        print("Use one of: list, add, remove, clear")
        raise SystemExit(2)
