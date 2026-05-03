"""Profile, dashboard, completion, and log commands for Hermes CLI."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Optional, List


def _coalesce_session_name_args(argv: list) -> list:
    """Join unquoted multi-word session names after -c/--continue and -r/--resume.

    When a user types ``hermes -c Pokemon Agent Dev`` without quoting the
    session name, argparse sees three separate tokens.  This function merges
    them into a single argument so argparse receives
    ``['-c', 'Pokemon Agent Dev']`` instead.

    Tokens are collected after the flag until we hit another flag (``-*``)
    or a known top-level subcommand.
    """
    _SUBCOMMANDS = {
        "chat", "model", "gateway", "setup", "whatsapp", "login", "logout", "auth",
        "status", "cron", "doctor", "config", "pairing", "skills", "tools",
        "mcp", "sessions", "insights", "version", "update", "uninstall",
        "profile", "dashboard",
    }
    _SESSION_FLAGS = {"-c", "--continue", "-r", "--resume"}

    result = []
    i = 0
    while i < len(argv):
        token: REDACTED
        if token in _SESSION_FLAGS:
            result.append(token)
            i += 1
            # Collect subsequent non-flag, non-subcommand tokens as one name
            parts: list = []
            while i < len(argv) and not argv[i].startswith("-") and argv[i] not in _SUBCOMMANDS:
                parts.append(argv[i])
                i += 1
            if parts:
                result.append(" ".join(parts))
        else:
            result.append(token)
            i += 1
    return result


def cmd_profile(args):
    """Profile management — create, delete, list, switch, alias."""
    from hermes_cli.profiles import (
        list_profiles, create_profile, delete_profile, seed_profile_skills,
        set_active_profile, get_active_profile_name,
        check_alias_collision, create_wrapper_script, remove_wrapper_script,
        _is_wrapper_dir_in_path, _get_wrapper_dir,
    )
    from hermes_constants import display_hermes_home

    action = getattr(args, "profile_action", None)

    if action is None:
        # Bare `hermes profile` — show current profile status
        profile_name = get_active_profile_name()
        dhh = display_hermes_home()
        print(f"\n当前配置档：{profile_name}")
        print(f"路径：         {dhh}")

        profiles = list_profiles()
        for p in profiles:
            if p.name == profile_name or (profile_name == "default" and p.is_default):
                if p.model:
                    print(f"模型：         {p.model}" + (f" ({p.provider})" if p.provider else ""))
                print(f"网关：         {'运行中' if p.gateway_running else '已停止'}")
                print(f"技能：         已安装 {p.skill_count} 个")
                if p.alias_path:
                    print(f"别名：         {p.name} → hermes -p {p.name}")
                break
        print()
        return

    if action == "list":
        profiles = list_profiles()
        active = get_active_profile_name()

        if not profiles:
            print("未找到任何配置档。")
            return

        # Header
        print(f"\n {'配置档':<16} {'模型':<28} {'网关':<12} {'别名'}")
        print(f" {'─' * 15}    {'─' * 27}    {'─' * 11}    {'─' * 12}")

        for p in profiles:
            marker = " ◆" if (p.name == active or (active == "default" and p.is_default)) else "  "
            name = p.name
            model = (p.model or "—")[:26]
            gw = "运行中" if p.gateway_running else "已停止"
            alias = p.name if p.alias_path else "—"
            if p.is_default:
                alias = "—"
            print(f"{marker}{name:<15} {model:<28} {gw:<12} {alias}")
        print()

    elif action == "use":
        name = args.profile_name
        try:
            set_active_profile(name)
            if name == "default":
                print("已切换到：default (~/.hermes)")
            else:
                print(f"已切换到：{name}")
        except (ValueError, FileNotFoundError) as e:
            print(f"错误：{e}")
            sys.exit(1)

    elif action == "create":
        name = args.profile_name
        clone = getattr(args, "clone", False)
        clone_all = getattr(args, "clone_all", False)
        no_alias = getattr(args, "no_alias", False)

        try:
            clone_from = getattr(args, "clone_from", None)

            profile_dir = create_profile(
                name=name,
                clone_from=clone_from,
                clone_all=clone_all,
                clone_config=clone,
                no_alias=no_alias,
            )
            print(f"\n已创建配置档“{name}”：{profile_dir}")

            if clone or clone_all:
                source_label = getattr(args, "clone_from", None) or get_active_profile_name()
                if clone_all:
                    print(f"已从 {source_label} 完整复制。")
                else:
                    print(f"已从 {source_label} 复制 config、.env 和 SOUL.md。")

            # Auto-clone Honcho config for the new profile (only with --clone/--clone-all)
            if clone or clone_all:
                try:
                    from plugins.memory.honcho.cli import clone_honcho_for_profile
                    if clone_honcho_for_profile(name):
                        print(f"已复制 Honcho 配置（peer：{name}）")
                except Exception:
                    pass  # Honcho plugin not installed or not configured

            # Seed bundled skills (skip if --clone-all already copied them)
            if not clone_all:
                result = seed_profile_skills(profile_dir)
                if result:
                    copied = len(result.get("copied", []))
                    print(f"已同步 {copied} 个内置技能。")
                else:
                    print("⚠ 技能初始化失败。可运行 `{} update` 重试。".format(name))

            # Create wrapper alias
            if not no_alias:
                collision = check_alias_collision(name)
                if collision:
                    print(f"\n⚠ 无法创建别名“{name}”——{collision}")
                    print(f"  可自定义别名：hermes profile alias {name} --name <custom>")
                    print(f"  或通过参数访问：hermes -p {name} chat")
                else:
                    wrapper_path = create_wrapper_script(name)
                    if wrapper_path:
                        print(f"已创建包装脚本：{wrapper_path}")
                        if not _is_wrapper_dir_in_path():
                            print(f"\n⚠ {_get_wrapper_dir()} 不在你的 PATH 中。")
                            print("  请把下面这行加入 shell 配置（如 ~/.bashrc 或 ~/.zshrc）：")
                            print('    export PATH="$HOME/.local/bin:$PATH"')

            # Profile dir for display
            try:
                profile_dir_display = "~/" + str(profile_dir.relative_to(Path.home()))
            except ValueError:
                profile_dir_display = str(profile_dir)

            # Next steps
            print(f"\n下一步：")
            print(f"  {name} setup              配置 API 密钥和模型")
            print(f"  {name} chat               开始聊天")
            print(f"  {name} gateway start      启动消息网关")
            if clone or clone_all:
                print(f"\n  编辑 {profile_dir_display}/.env 以使用不同的 API 密钥")
                print(f"  编辑 {profile_dir_display}/SOUL.md 以设置不同人格")
            else:
                print(f"\n  ⚠ 这个 profile 还没有 API 密钥。请先执行 `{name} setup`，")
                print(f"    否则它会继承你当前 shell 环境中的密钥。")
                print(f"  编辑 {profile_dir_display}/SOUL.md 可自定义人格")
            print()

        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f"错误：{e}")
            sys.exit(1)

    elif action == "delete":
        name = args.profile_name
        yes = getattr(args, "yes", False)
        try:
            delete_profile(name, yes=yes)
        except (ValueError, FileNotFoundError) as e:
            print(f"错误：{e}")
            sys.exit(1)

    elif action == "show":
        name = args.profile_name
        from hermes_cli.profiles import get_profile_dir, profile_exists, _read_config_model, _check_gateway_running, _count_skills
        if not profile_exists(name):
            print(f"错误：配置档“{name}”不存在。")
            sys.exit(1)
        profile_dir = get_profile_dir(name)
        model, provider = _read_config_model(profile_dir)
        gw = _check_gateway_running(profile_dir)
        skills = _count_skills(profile_dir)
        wrapper = _get_wrapper_dir() / name

        print(f"\n配置档：{name}")
        print(f"路径：   {profile_dir}")
        if model:
            print(f"模型：   {model}" + (f" ({provider})" if provider else ""))
        print(f"网关：   {'运行中' if gw else '已停止'}")
        print(f"技能：   {skills}")
        print(f".env：   {'已存在' if (profile_dir / '.env').exists() else '未配置'}")
        print(f"SOUL.md：{'已存在' if (profile_dir / 'SOUL.md').exists() else '未配置'}")
        if wrapper.exists():
            print(f"别名：   {wrapper}")
        print()

    elif action == "alias":
        name = args.profile_name
        remove = getattr(args, "remove", False)
        custom_name = getattr(args, "alias_name", None)

        from hermes_cli.profiles import profile_exists
        if not profile_exists(name):
            print(f"错误：配置档“{name}”不存在。")
            sys.exit(1)

        alias_name = custom_name or name

        if remove:
            if remove_wrapper_script(alias_name):
                print(f"✓ 已删除别名“{alias_name}”")
            else:
                print(f"未找到要删除的别名“{alias_name}”。")
        else:
            collision = check_alias_collision(alias_name)
            if collision:
                print(f"错误：{collision}")
                sys.exit(1)
            wrapper_path = create_wrapper_script(alias_name)
            if wrapper_path:
                # If custom name, write the profile name into the wrapper
                if custom_name:
                    wrapper_path.write_text(f'#!/bin/sh\nexec hermes -p {name} "$@"\n')
                print(f"✓ 已创建别名：{wrapper_path}")
                if not _is_wrapper_dir_in_path():
                    print(f"⚠ {_get_wrapper_dir()} 不在你的 PATH 中。")

    elif action == "rename":
        from hermes_cli.profiles import rename_profile
        try:
            new_dir = rename_profile(args.old_name, args.new_name)
            print(f"\n已重命名配置档：{args.old_name} → {args.new_name}")
            print(f"路径：{new_dir}\n")
        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f"错误：{e}")
            sys.exit(1)

    elif action == "export":
        from hermes_cli.profiles import export_profile
        name = args.profile_name
        output = args.output or f"{name}.tar.gz"
        try:
            result_path = export_profile(name, output)
            print(f"✓ 已导出配置档“{name}”到 {result_path}")
        except (ValueError, FileNotFoundError) as e:
            print(f"错误：{e}")
            sys.exit(1)

    elif action == "import":
        from hermes_cli.profiles import import_profile
        try:
            profile_dir = import_profile(args.archive, name=getattr(args, "import_name", None))
            name = profile_dir.name
            print(f"✓ 已导入配置档“{name}”：{profile_dir}")

            # Offer to create alias
            collision = check_alias_collision(name)
            if not collision:
                wrapper_path = create_wrapper_script(name)
                if wrapper_path:
                    print(f"  已创建包装脚本：{wrapper_path}")
            print()
        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f"错误：{e}")
            sys.exit(1)


def cmd_dashboard(args):
    """Start the web UI server."""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("尚未安装 Web UI 依赖。")
        print("可运行：pip install hermes-agent[web]")
        sys.exit(1)

    if not _build_web_ui(PROJECT_ROOT / "web", fatal=True):
        sys.exit(1)

    from hermes_cli.web_server import start_server
    start_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
    )


def cmd_completion(args):
    """Print shell completion script."""
    from hermes_cli.profiles import generate_bash_completion, generate_zsh_completion
    shell = getattr(args, "shell", "bash")
    if shell == "zsh":
        print(generate_zsh_completion())
    else:
        print(generate_bash_completion())


def cmd_logs(args):
    """View and filter Hermes log files."""
    from hermes_cli.logs import tail_log, list_logs

    log_name = getattr(args, "log_name", "agent") or "agent"

    if log_name == "list":
        list_logs()
        return

    tail_log(
        log_name,
        num_lines=getattr(args, "lines", 50),
        follow=getattr(args, "follow", False),
        level=getattr(args, "level", None),
        session=getattr(args, "session", None),
        since=getattr(args, "since", None),
        component=getattr(args, "component", None),
    )
