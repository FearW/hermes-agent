"""Chat command for Hermes CLI."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional


def _require_tty(command_name: str) -> None:
    if not sys.stdin.isatty():
        print(f"错误：`hermes {command_name}` 需要交互式终端。请直接在你的终端中运行。", file=sys.stderr)
        sys.exit(1)


def cmd_chat(args):
    """Run interactive chat CLI."""
    # Resolve --continue into --resume with the latest CLI session or by name
    continue_val = getattr(args, "continue_last", None)
    if continue_val and not getattr(args, "resume", None):
        if isinstance(continue_val, str):
            # -c "session name" — resolve by title or ID
            resolved = _resolve_session_by_name_or_id(continue_val)
            if resolved:
                args.resume = resolved
            else:
                print(f"未找到与“{continue_val}”匹配的会话。")
                print("可运行 `hermes sessions list` 查看可用会话。")
                sys.exit(1)
        else:
            # -c with no argument — continue the most recent session
            last_id = _resolve_last_cli_session()
            if last_id:
                args.resume = last_id
            else:
                print("没有找到可继续的上一条 CLI 会话。")
                sys.exit(1)

    # Resolve --resume by title if it's not a direct session ID
    resume_val = getattr(args, "resume", None)
    if resume_val:
        resolved = _resolve_session_by_name_or_id(resume_val)
        if resolved:
            args.resume = resolved
        # If resolution fails, keep the original value — _init_agent will
        # report "Session not found" with the original input

    # First-run guard: check if any provider is configured before launching
    if not _has_any_provider_configured():
        print()
        print("看起来 Hermes 还没有完成配置，目前未找到 API Key 或模型提供方。")
        print()
        print("  运行：hermes setup")
        print()

        from hermes_cli.setup import is_interactive_stdin, print_noninteractive_setup_guidance

        if not is_interactive_stdin():
            print_noninteractive_setup_guidance(
                "首次运行的设置向导未检测到交互式 TTY。"
            )
            sys.exit(1)

        try:
            reply = input("现在运行设置向导吗？[Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = "n"
        if reply in ("", "y", "yes"):
            cmd_setup(args)
            return
        print()
        print("你可以随时运行 `hermes setup` 完成配置。")
        sys.exit(1)

    # Start update check in background (runs while other init happens)
    try:
        from hermes_cli.banner import prefetch_update_check
        prefetch_update_check()
    except Exception:
        pass

    # Sync bundled skills on every CLI launch (fast -- skips unchanged skills)
    try:
        from tools.skills_sync import sync_skills
        sync_skills(quiet=True)
    except Exception:
        pass

    # --yolo: bypass all dangerous command approvals
    if getattr(args, "yolo", False):
        os.environ["HERMES_YOLO_MODE"] = "1"

    # --source: tag session source for filtering (e.g. 'tool' for third-party integrations)
    if getattr(args, "source", None):
        os.environ["HERMES_SESSION_SOURCE"] = args.source

    # Import and run the CLI
    from cli import main as cli_main
    
    # Build kwargs from args
    kwargs = {
        "model": args.model,
        "provider": getattr(args, "provider", None),
        "toolsets": args.toolsets,
        "skills": getattr(args, "skills", None),
        "verbose": args.verbose,
        "quiet": getattr(args, "quiet", False),
        "query": args.query,
        "image": getattr(args, "image", None),
        "resume": getattr(args, "resume", None),
        "worktree": getattr(args, "worktree", False),
        "checkpoints": getattr(args, "checkpoints", False),
        "pass_session_id": getattr(args, "pass_session_id", False),
        "max_turns": getattr(args, "max_turns", None),
    }
    # Filter out None values
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    
    try:
        cli_main(**kwargs)
    except ValueError as e:
        print(f"错误：{e}")
        sys.exit(1)

