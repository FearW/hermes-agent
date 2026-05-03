"""
Top-level argparse construction for the hermes CLI.

Lives in its own module so other modules (e.g. ``relaunch.py``) can
introspect the parser to discover which flags exist without running the
``main`` fn.

Only the top-level parser and the ``chat`` subparser live here. Every other
subparser (model, gateway, sessions, …) is built inline in ``main.py``
because its dispatch is tightly coupled to module-level ``cmd_*`` functions.
"""

import argparse


# `--profile` / `-p` is consumed by ``main._apply_profile_override`` before
# argparse runs (it sets ``HERMES_HOME`` and strips itself from ``sys.argv``),
# so it isn't on the parser. Listed here so all "carry over on relaunch"
# metadata lives in one file.
PRE_ARGPARSE_INHERITED_FLAGS: list[tuple[str, bool]] = [
    ("--profile", True),
    ("-p", True),
]


def _inherited_flag(parser, *args, **kwargs):
    """Register a flag that ``hermes_cli.relaunch`` should carry over when
    the CLI re-execs itself (e.g. after ``sessions browse`` picks a session,
    or after the setup wizard launches chat).

    Equivalent to ``parser.add_argument(...)`` plus tagging the resulting
    Action with ``inherit_on_relaunch = True`` so the relaunch table builder
    can find it via introspection.
    """
    action = parser.add_argument(*args, **kwargs)
    action.inherit_on_relaunch = True
    return action


_EPILOGUE = """
示例：
    hermes                        启动交互式聊天
    hermes chat -q "Hello"        单条查询模式
    hermes -c                     恢复最近一次会话
    hermes -c "my project"        按名称恢复会话（取谱系中最新的一个）
    hermes --resume <session_id>  按会话 ID 恢复指定会话
    hermes setup                  运行配置向导
    hermes logout                 清除已保存的认证信息
    hermes auth add <provider>    添加一个轮换凭证
    hermes auth list              列出轮换凭证
    hermes auth remove <p> <t>    按索引、ID 或标签移除轮换凭证
    hermes auth reset <provider>  清除某个 provider 的耗尽状态
    hermes model                  选择默认模型
    hermes fallback [list]        查看 CPA 回退模型链
    hermes fallback add           添加一个 CPA 回退模型
    hermes fallback remove        从链路中移除一个回退模型
    hermes config                 查看配置
    hermes config edit            在 $EDITOR 中编辑配置
    hermes config set model gpt-4 设置一个配置项
    hermes gateway                运行消息网关
    hermes -s hermes-agent-dev,github-auth
    hermes -w                     在隔离的 git worktree 中启动
    hermes gateway install        安装网关后台服务
    hermes sessions list          列出历史会话
    hermes sessions browse        打开交互式会话选择器
    hermes sessions rename ID T   重命名/设标题
    hermes logs                   查看 agent.log（最近 50 行）
    hermes logs -f                实时跟踪 agent.log
    hermes logs errors            查看 errors.log
    hermes logs --since 1h        查看最近 1 小时日志
    hermes debug share            上传调试报告以便支持排查
    hermes update                 更新到最新版本

查看某个命令的更多帮助：
    hermes <command> --help
"""


def build_top_level_parser():
    """Build the top-level parser, the subparsers action, and the ``chat`` subparser.

    Returns ``(parser, subparsers, chat_parser)``. The caller wires
    ``chat_parser.set_defaults(func=cmd_chat)`` and continues registering
    other subparsers via ``subparsers.add_parser(...)``.
    """
    parser = argparse.ArgumentParser(
        prog="hermes",
        description="Hermes Agent - 支持工具调用的 AI 助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOGUE,
    )

    parser.add_argument(
        "--version", "-V", action="store_true", help="显示版本并退出"
    )
    parser.add_argument(
        "-z",
        "--oneshot",
        metavar="PROMPT",
        default=None,
        help=(
            "单次模式：发送一条提示词，并且只把最终回复文本输出到 stdout。"
            "不会显示 banner、spinner、工具预览或 session_id 行。"
            "当前目录中的工具、记忆、规则和 AGENTS.md 仍会正常加载；审批会自动跳过。"
            "适用于脚本和管道。"
        ),
    )
    # --model is accepted at the top level so it can pair with -z without
    # needing the `chat` subcommand. Provider selection is CPA-only and no
    # longer exposed as a CLI flag.
    _inherited_flag(
        parser,
        "-m",
        "--model",
        default=None,
        help=(
            "本次调用的模型覆盖项（例如 anthropic/claude-sonnet-4.6）。"
            "适用于 -z/--oneshot 和 --tui，也可通过 HERMES_INFERENCE_MODEL 环境变量设置。"
        ),
    )
    parser.add_argument(
        "-t",
        "--toolsets",
        default=None,
        help="为本次调用启用的工具集，多个值用逗号分隔。适用于 -z/--oneshot 和 --tui。",
    )
    parser.add_argument(
        "--resume",
        "-r",
        metavar="SESSION",
        default=None,
        help="按 ID 或标题恢复一个历史会话",
    )
    parser.add_argument(
        "--continue",
        "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=None,
        metavar="SESSION_NAME",
        help="按名称恢复会话；如果不填名称，则恢复最近一次会话",
    )
    parser.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        default=False,
        help="在隔离的 git worktree 中运行（适合并行代理）",
    )
    _inherited_flag(
        parser,
        "--accept-hooks",
        action="store_true",
        default=False,
        help=(
            "自动批准 config.yaml 中声明但尚未确认的 shell hooks，"
            "不再弹出 TTY 提示。等价于设置 HERMES_ACCEPT_HOOKS=1，"
            "或在 config.yaml 中设置 hooks_auto_accept: true。"
            "适用于 CI 或无头环境。"
        ),
    )
    _inherited_flag(
        parser,
        "--skills",
        "-s",
        action="append",
        default=None,
        help="为当前会话预加载一个或多个技能（可重复传参或用逗号分隔）",
    )
    _inherited_flag(
        parser,
        "--yolo",
        action="store_true",
        default=False,
        help="跳过所有危险命令审批提示（风险自负）",
    )
    _inherited_flag(
        parser,
        "--pass-session-id",
        action="store_true",
        default=False,
        help="把会话 ID 注入到代理的系统提示词中",
    )
    _inherited_flag(
        parser,
        "--ignore-user-config",
        action="store_true",
        default=False,
        help="忽略 ~/.hermes/config.yaml，退回到内置默认值（仍会加载 .env 中的凭证）",
    )
    _inherited_flag(
        parser,
        "--ignore-rules",
        action="store_true",
        default=False,
        help="跳过自动注入 AGENTS.md、SOUL.md、.cursorrules、memory 和预加载技能",
    )
    _inherited_flag(
        parser,
        "--tui",
        action="store_true",
        default=False,
        help="启动现代 TUI，而不是经典 REPL",
    )
    _inherited_flag(
        parser,
        "--dev",
        dest="tui_dev",
        action="store_true",
        default=False,
        help="配合 --tui 使用：通过 tsx 运行 TypeScript 源码（跳过 dist 构建）",
    )

    subparsers = parser.add_subparsers(dest="command", help="要执行的命令")

    # =========================================================================
    # chat command
    # =========================================================================
    chat_parser = subparsers.add_parser(
        "chat",
        help="与代理进行交互式聊天",
        description="启动 Hermes Agent 的交互式聊天会话",
    )
    chat_parser.add_argument(
        "-q", "--query", help="单条查询（非交互模式）"
    )
    chat_parser.add_argument(
        "--image", help="可选：为单次查询附加一张本地图片"
    )
    _inherited_flag(
        chat_parser,
        "-m", "--model", help="要使用的模型（例如 anthropic/claude-sonnet-4）",
    )
    chat_parser.add_argument(
        "-t", "--toolsets", help="要启用的工具集，多个值用逗号分隔"
    )
    _inherited_flag(
        chat_parser,
        "-s",
        "--skills",
        action="append",
        default=argparse.SUPPRESS,
        help="为当前会话预加载一个或多个技能（可重复传参或用逗号分隔）",
    )
    chat_parser.add_argument(
        "-v", "--verbose", action="store_true", help="显示更详细的输出"
    )
    chat_parser.add_argument(
        "-Q",
        "--quiet",
        action="store_true",
        help="静默模式：隐藏 banner、spinner 和工具预览，仅输出最终回复和会话信息。",
    )
    chat_parser.add_argument(
        "--resume",
        "-r",
        metavar="SESSION_ID",
        default=argparse.SUPPRESS,
        help="按 ID 恢复一个历史会话（会在退出时显示）",
    )
    chat_parser.add_argument(
        "--continue",
        "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=argparse.SUPPRESS,
        metavar="SESSION_NAME",
        help="按名称恢复会话；如果不填名称，则恢复最近一次会话",
    )
    chat_parser.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        default=argparse.SUPPRESS,
        help="在隔离的 git worktree 中运行（适合在同一仓库中并行代理）",
    )
    _inherited_flag(
        chat_parser,
        "--accept-hooks",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "自动批准 config.yaml 中声明但尚未确认的 shell hooks，"
            "不再弹出 TTY 提示（另见 HERMES_ACCEPT_HOOKS 环境变量和 "
            "config.yaml 中的 hooks_auto_accept 配置）。"
        ),
    )
    chat_parser.add_argument(
        "--checkpoints",
        action="store_true",
        default=False,
        help="在破坏性文件操作前启用文件系统检查点（可用 /rollback 恢复）",
    )
    chat_parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help="每轮对话允许的最大工具调用迭代次数（默认 90，或取 agent.max_turns 配置）",
    )
    _inherited_flag(
        chat_parser,
        "--yolo",
        action="store_true",
        default=argparse.SUPPRESS,
        help="跳过所有危险命令审批提示（风险自负）",
    )
    _inherited_flag(
        chat_parser,
        "--pass-session-id",
        action="store_true",
        default=argparse.SUPPRESS,
        help="把会话 ID 注入到代理的系统提示词中",
    )
    _inherited_flag(
        chat_parser,
        "--ignore-user-config",
        action="store_true",
        default=argparse.SUPPRESS,
        help="忽略 ~/.hermes/config.yaml，退回到内置默认值（仍会加载 .env 中的凭证）。适合隔离 CI、问题复现和第三方集成。",
    )
    _inherited_flag(
        chat_parser,
        "--ignore-rules",
        action="store_true",
        default=argparse.SUPPRESS,
        help="跳过自动注入 AGENTS.md、SOUL.md、.cursorrules、memory 和预加载技能。可与 --ignore-user-config 组合以获得完全隔离的运行环境。",
    )
    chat_parser.add_argument(
        "--source",
        default=None,
        help="会话来源标签，用于过滤（默认：cli）。第三方集成若不应出现在用户会话列表中，可使用 'tool'。",
    )
    _inherited_flag(
        chat_parser,
        "--tui",
        action="store_true",
        default=False,
        help="启动现代 TUI，而不是经典 REPL",
    )
    _inherited_flag(
        chat_parser,
        "--dev",
        dest="tui_dev",
        action="store_true",
        default=False,
        help="配合 --tui 使用：通过 tsx 运行 TypeScript 源码（跳过 dist 构建）",
    )

    return parser, subparsers, chat_parser
