#!/usr/bin/env python3
"""
Skills Hub CLI — Unified interface for the Hermes Skills Hub.

Powers both:
  - `hermes skills <subcommand>` (CLI argparse entry point)
  - `/skills <subcommand>` (slash command in the interactive chat)

All logic lives in shared do_* functions. The CLI entry point and slash command
handler are thin wrappers that parse args and delegate.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Lazy imports to avoid circular dependencies and slow startup.
# tools.skills_hub and tools.skills_guard are imported inside functions.
from hermes_constants import display_hermes_home

_console = Console()


# ---------------------------------------------------------------------------
# Shared do_* functions
# ---------------------------------------------------------------------------

def _resolve_short_name(name: str, sources, console: Console) -> str:
    """
    Resolve a short skill name (e.g. 'pptx') to a full identifier by searching
    all sources. If exactly one match is found, returns its identifier. If multiple
    matches exist, shows them and asks the user to use the full identifier.
    Returns empty string if nothing found or ambiguous.
    """
    from tools.skills_hub import unified_search

    c = console or _console
    c.print(f"[dim]Resolving '{name}'...[/]")

    results = unified_search(name, sources, source_filter="all", limit=20)

    # Filter to exact name matches (case-insensitive)
    exact = [r for r in results if r.name.lower() == name.lower()]

    if len(exact) == 1:
        c.print(f"[dim]Resolved to: {exact[0].identifier}[/]")
        return exact[0].identifier

    if len(exact) > 1:
        c.print(f"\n[yellow]Multiple skills named '{name}' found:[/]")
        table = Table()
        table.add_column("Source", style="dim")
        table.add_column("Trust", style="dim")
        table.add_column("Identifier", style="bold cyan")
        for r in exact:
            trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow"}.get(r.trust_level, "dim")
            trust_label = "official" if r.source == "official" else r.trust_level
            table.add_row(r.source, f"[{trust_style}]{trust_label}[/]", r.identifier)
        c.print(table)
        c.print("[bold]请使用完整标识符安装指定技能。[/]\n")
        return ""

    # No exact match — check if there are partial matches to suggest
    if results:
        c.print(f"[yellow]未找到与“{name}”完全匹配的结果。你是不是想找下面这些？[/]")
        for r in results[:5]:
            c.print(f"  [cyan]{r.name}[/] — {r.identifier}")
        c.print()
        return ""

    c.print(f"[bold red]错误：[/]在任何来源中都未找到名为“{name}”的技能。\n")
    return ""


def _format_extra_metadata_lines(extra: Dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if not extra:
        return lines

    if extra.get("repo_url"):
        lines.append(f"[bold]仓库：[/] {extra['repo_url']}")
    if extra.get("detail_url"):
        lines.append(f"[bold]详情页：[/] {extra['detail_url']}")
    if extra.get("index_url"):
        lines.append(f"[bold]索引：[/] {extra['index_url']}")
    if extra.get("endpoint"):
        lines.append(f"[bold]端点：[/] {extra['endpoint']}")
    if extra.get("install_command"):
        lines.append(f"[bold]安装命令：[/] {extra['install_command']}")
    if extra.get("installs") is not None:
        lines.append(f"[bold]安装量：[/] {extra['installs']}")
    if extra.get("weekly_installs"):
        lines.append(f"[bold]周安装量：[/] {extra['weekly_installs']}")

    security = extra.get("security_audits")
    if isinstance(security, dict) and security:
        ordered = ", ".join(f"{name}={status}" for name, status in sorted(security.items()))
        lines.append(f"[bold]安全审计：[/] {ordered}")

    return lines


def _resolve_source_meta_and_bundle(identifier: str, sources):
    """Resolve metadata and bundle for a specific identifier."""
    meta = None
    bundle = None
    matched_source = None

    for src in sources:
        if meta is None:
            try:
                meta = src.inspect(identifier)
                if meta:
                    matched_source = src
            except Exception:
                meta = None
        try:
            bundle = src.fetch(identifier)
        except Exception:
            bundle = None
        if bundle:
            matched_source = src
            if meta is None:
                try:
                    meta = src.inspect(identifier)
                except Exception:
                    meta = None
            break

    return meta, bundle, matched_source


def _derive_category_from_install_path(install_path: str) -> str:
    path = Path(install_path)
    parent = str(path.parent)
    return "" if parent == "." else parent


def do_search(query: str, source: str = "all", limit: int = 10,
              console: Optional[Console] = None) -> None:
    """Search registries and display results as a Rich table."""
    from tools.skills_hub import GitHubAuth, create_source_router, unified_search

    c = console or _console
    c.print(f"\n[bold]正在搜索：[/] {query}")

    auth = GitHubAuth()
    sources = create_source_router(auth)
    with c.status("[bold]正在搜索注册表..."):
        results = unified_search(query, sources, source_filter=source, limit=limit)

    if not results:
        c.print("[dim]没有找到与查询匹配的技能。[/]\n")
        return

    table = Table(title=f"Skills Hub — 共 {len(results)} 条结果")
    table.add_column("名称", style="bold cyan")
    table.add_column("描述", max_width=60)
    table.add_column("来源", style="dim")
    table.add_column("可信度", style="dim")
    table.add_column("标识符", style="dim")

    for r in results:
        trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow"}.get(r.trust_level, "dim")
        trust_label = "official" if r.source == "official" else r.trust_level
        table.add_row(
            r.name,
            r.description[:60] + ("..." if len(r.description) > 60 else ""),
            r.source,
            f"[{trust_style}]{trust_label}[/]",
            r.identifier,
        )

    c.print(table)
    c.print("[dim]使用：hermes skills inspect <标识符> 预览，"
            "hermes skills install <标识符> 安装[/]\n")


def do_browse(page: int = 1, page_size: int = 20, source: str = "all",
              console: Optional[Console] = None) -> None:
    """Browse all available skills across registries, paginated.

    Official skills are always shown first, regardless of source filter.
    """
    from tools.skills_hub import (
        GitHubAuth, create_source_router, parallel_search_sources,
    )

    # Clamp page_size to safe range
    page_size = max(1, min(page_size, 100))

    c = console or _console

    auth = GitHubAuth()
    sources = create_source_router(auth)

    # Collect results from all (or filtered) sources in parallel.
    # Per-source limits are generous — parallelism + 30s timeout cap prevents hangs.
    _TRUST_RANK = {"builtin": 3, "trusted": 2, "community": 1}
    _PER_SOURCE_LIMIT = {
        "official": 200, "skills-sh": 200, "well-known": 50,
        "github": 200, "clawhub": 500, "claude-marketplace": 100,
        "lobehub": 500,
    }

    with c.status("[bold]Fetching skills from registries..."):
        all_results, source_counts, timed_out = parallel_search_sources(
            sources,
            query="",
            per_source_limits=_PER_SOURCE_LIMIT,
            source_filter=source,
            overall_timeout=30,
        )

    if not all_results:
        c.print("[dim]技能中心里没有找到技能。[/]\n")
        return

    # Deduplicate by name, preferring higher trust
    seen: dict = {}
    for r in all_results:
        rank = _TRUST_RANK.get(r.trust_level, 0)
        if r.name not in seen or rank > _TRUST_RANK.get(seen[r.name].trust_level, 0):
            seen[r.name] = r
    deduped = list(seen.values())

    # Sort: official first, then by trust level (desc), then alphabetically
    deduped.sort(key=lambda r: (
        -_TRUST_RANK.get(r.trust_level, 0),
        r.source != "official",
        r.name.lower(),
    ))

    # Paginate
    total = len(deduped)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_items = deduped[start:end]

    # Count official vs other
    official_count = sum(1 for r in deduped if r.source == "official")

    # Build header
    source_label = f"— {source}" if source != "all" else "— 所有来源"
    loaded_label = f"已加载 {total} 个技能"
    if timed_out:
        loaded_label += f"，还有 {len(timed_out)} 个来源仍在加载"
    c.print(f"\n[bold]技能中心 — 浏览 {source_label}[/]"
            f"  [dim]({loaded_label}，第 {page}/{total_pages} 页)[/]")
    if official_count > 0 and page == 1:
        c.print(f"[bright_cyan]★ 来自 Nous Research 的 {official_count} 个官方可选技能[/]")
    c.print()

    # Build table
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("名称", style="bold cyan", max_width=25)
    table.add_column("描述", max_width=50)
    table.add_column("来源", style="dim", width=12)
    table.add_column("可信度", width=10)

    for i, r in enumerate(page_items, start=start + 1):
        trust_style = {"builtin": "bright_cyan", "trusted": "green",
                       "community": "yellow"}.get(r.trust_level, "dim")
        trust_label = "★ official" if r.source == "official" else r.trust_level

        desc = r.description[:50]
        if len(r.description) > 50:
            desc += "..."

        table.add_row(
            str(i),
            r.name,
            desc,
            r.source,
            f"[{trust_style}]{trust_label}[/]",
        )

    c.print(table)

    # Navigation hints
    nav_parts = []
    if page > 1:
        nav_parts.append(f"[cyan]--page {page - 1}[/] ← 上一页")
    if page < total_pages:
        nav_parts.append(f"[cyan]--page {page + 1}[/] → 下一页")

    if nav_parts:
        c.print(f"  {' | ' .join(nav_parts)}")

    # Source summary
    if source == "all" and source_counts:
        parts = [f"{sid}: {ct}" for sid, ct in sorted(source_counts.items())]
        c.print(f"  [dim]来源：{', '.join(parts)}[/]")

    if timed_out:
        c.print(f"  [yellow]⚡ 已跳过慢速来源：{', '.join(timed_out)} "
                f"— 重新运行可使用缓存结果[/]")

    c.print("[dim]提示：`hermes skills search <query>` 会跨所有注册表做更深入的搜索[/]\n")


def do_install(identifier: str, category: str = "", force: bool = False,
               console: Optional[Console] = None, skip_confirm: bool = False,
               invalidate_cache: bool = True) -> None:
    """Fetch, quarantine, scan, confirm, and install a skill."""
    from tools.skills_hub import (
        GitHubAuth, create_source_router, ensure_hub_dirs,
        quarantine_bundle, install_from_quarantine, HubLockFile,
    )
    from tools.skills_guard import scan_skill, should_allow_install, format_scan_report

    c = console or _console
    ensure_hub_dirs()

    # Resolve which source adapter handles this identifier
    auth = GitHubAuth()
    sources = create_source_router(auth)

    # If identifier looks like a short name (no slashes), resolve it via search
    if "/" not in identifier:
        identifier = _resolve_short_name(identifier, sources, c)
        if not identifier:
            return

    c.print(f"\n[bold]正在获取：[/] {identifier}")

    meta, bundle, _matched_source = _resolve_source_meta_and_bundle(identifier, sources)

    if not bundle:
        # Check if any source hit GitHub API rate limit
        rate_limited = any(
            getattr(src, "is_rate_limited", False)
            or getattr(getattr(src, "github", None), "is_rate_limited", False)
            for src in sources
        )
        c.print(f"[bold red]错误：[/]无法从任何来源获取“{identifier}”。")
        if rate_limited:
            c.print(
                "[yellow]提示：[/]GitHub API 速率限制已耗尽 "
                "(unauthenticated: 60 requests/hour).\n"
                "请在 .env 中设置 [bold]GITHUB_TOKEN[/]，或安装 "
                "[bold]gh[/] CLI and run [bold]gh auth login[/] "
                "to raise the limit to 5,000/hr.\n"
            )
        else:
            c.print()
        return

    # Auto-detect category for official skills (e.g. "official/autonomous-ai-agents/blackbox")
    if bundle.source == "official" and not category:
        id_parts = bundle.identifier.split("/")  # ["official", "category", "skill"]
        if len(id_parts) >= 3:
            category = id_parts[1]

    # Check if already installed
    lock = HubLockFile()
    existing = lock.get_installed(bundle.name)
    if existing:
        c.print(f"[yellow]警告：[/]“{bundle.name}” 已安装在 {existing['install_path']}")
        if not force:
            c.print("使用 --force 重新安装。\n")
            return

    extra_metadata = dict(getattr(meta, "extra", {}) or {})
    extra_metadata.update(getattr(bundle, "metadata", {}) or {})

    # Quarantine the bundle
    try:
        q_path = quarantine_bundle(bundle)
    except ValueError as exc:
        c.print(f"[bold red]Installation blocked:[/] {exc}\n")
        from tools.skills_hub import append_audit_log
        append_audit_log("BLOCKED", bundle.name, bundle.source,
                         bundle.trust_level, "invalid_path", str(exc))
        return
    c.print(f"[dim]已隔离到 {q_path.relative_to(q_path.parent.parent.parent)}[/]")

    # Scan
    c.print("[bold]正在执行安全扫描...[/]")
    scan_source = getattr(bundle, "identifier", "") or getattr(meta, "identifier", "") or identifier
    result = scan_skill(q_path, source=scan_source)
    c.print(format_scan_report(result))

    # Check install policy
    allowed, reason = should_allow_install(result, force=force)
    if not allowed:
        c.print(f"\n[bold red]Installation blocked:[/] {reason}")
        # Clean up quarantine
        shutil.rmtree(q_path, ignore_errors=True)
        from tools.skills_hub import append_audit_log
        append_audit_log("BLOCKED", bundle.name, bundle.source,
                         bundle.trust_level, result.verdict,
                         f"{len(result.findings)}_findings")
        return

    if extra_metadata:
        metadata_lines = _format_extra_metadata_lines(extra_metadata)
        if metadata_lines:
            c.print(Panel("\n".join(metadata_lines), title="上游元数据", border_style="blue"))

    # Confirm with user — show appropriate warning based on source
    # skip_confirm bypasses the prompt (needed in TUI mode where input() hangs)
    if not force and not skip_confirm:
        c.print()
        if bundle.source == "official":
            c.print(Panel(
                "[bold bright_cyan]This is an official optional skill maintained by Nous Research.[/]\n\n"
                "It ships with hermes-agent but is not activated by default.\n"
                "Installing will copy it to your skills directory where the agent can use it.\n\n"
                f"Files will be at: [cyan]{display_hermes_home()}/skills/{category + '/' if category else ''}{bundle.name}/[/]",
                title="官方技能",
                border_style="bright_cyan",
            ))
        else:
            c.print(Panel(
                "[bold yellow]You are installing a third-party skill at your own risk.[/]\n\n"
                "External skills can contain instructions that influence agent behavior,\n"
                "shell commands, and scripts. Even after automated scanning, you should\n"
                "review the installed files before use.\n\n"
                f"Files will be at: [cyan]{display_hermes_home()}/skills/{category + '/' if category else ''}{bundle.name}/[/]",
                title="免责声明",
                border_style="yellow",
            ))
        c.print(f"[bold]安装“{bundle.name}”？[/]")
        try:
            answer = input("确认 [y/N]：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            c.print("[dim]已取消安装。[/]\n")
            shutil.rmtree(q_path, ignore_errors=True)
            return

    # Install
    try:
        install_dir = install_from_quarantine(q_path, bundle.name, category, bundle, result)
    except ValueError as exc:
        c.print(f"[bold red]Installation blocked:[/] {exc}\n")
        shutil.rmtree(q_path, ignore_errors=True)
        from tools.skills_hub import append_audit_log
        append_audit_log("BLOCKED", bundle.name, bundle.source,
                         bundle.trust_level, "invalid_path", str(exc))
        return
    from tools.skills_hub import SKILLS_DIR
    c.print(f"[bold green]已安装：[/] {install_dir.relative_to(SKILLS_DIR)}")
    c.print(f"[dim]文件：{', '.join(bundle.files.keys())}[/]\n")

    if invalidate_cache:
        # Invalidate the skills prompt cache so the new skill appears immediately
        try:
            from agent.prompt_builder import clear_skills_system_prompt_cache
            clear_skills_system_prompt_cache(clear_snapshot=True)
        except Exception:
            pass
    else:
        c.print("[dim]该技能会在下一次会话中可用。[/]")
        c.print("[dim]可使用 /reset 立即开始新会话，或用 --now 立刻生效（会使 prompt cache 失效）。[/]\n")


def do_inspect(identifier: str, console: Optional[Console] = None) -> None:
    """Preview a skill's SKILL.md content without installing."""
    from tools.skills_hub import GitHubAuth, create_source_router

    c = console or _console
    auth = GitHubAuth()
    sources = create_source_router(auth)

    if "/" not in identifier:
        identifier = _resolve_short_name(identifier, sources, c)
        if not identifier:
            return

    meta, bundle, _matched_source = _resolve_source_meta_and_bundle(identifier, sources)

    if not meta:
        c.print(f"[bold red]错误：[/]在任何来源中都找不到“{identifier}”。\n")
        return

    c.print()
    trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow"}.get(meta.trust_level, "dim")
    trust_label = "official" if meta.source == "official" else meta.trust_level

    info_lines = [
        f"[bold]名称：[/] {meta.name}",
        f"[bold]描述：[/] {meta.description}",
        f"[bold]来源：[/] {meta.source}",
        f"[bold]可信度：[/] [{trust_style}]{trust_label}[/]",
        f"[bold]标识符：[/] {meta.identifier}",
    ]
    if meta.tags:
        info_lines.append(f"[bold]标签：[/] {', '.join(meta.tags)}")
    info_lines.extend(_format_extra_metadata_lines(meta.extra))

    c.print(Panel("\n".join(info_lines), title=f"技能：{meta.name}"))

    if bundle and "SKILL.md" in bundle.files:
        content = bundle.files["SKILL.md"]
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        # Show first 50 lines as preview
        lines = content.split("\n")
        preview = "\n".join(lines[:50])
        if len(lines) > 50:
            preview += f"\n\n...（还有 {len(lines) - 50} 行）"
        c.print(Panel(preview, title="SKILL.md 预览", subtitle="使用 hermes skills install <id> 安装"))

    c.print()


def do_list(source_filter: str = "all", console: Optional[Console] = None) -> None:
    """List installed skills, distinguishing hub, builtin, and local skills."""
    from tools.skills_hub import HubLockFile, ensure_hub_dirs
    from tools.skills_sync import _read_manifest
    from tools.skills_tool import _find_all_skills

    c = console or _console
    ensure_hub_dirs()
    lock = HubLockFile()
    hub_installed = {e["name"]: e for e in lock.list_installed()}
    builtin_names = set(_read_manifest())

    all_skills = _find_all_skills()

    table = Table(title="已安装技能")
    table.add_column("Name", style="bold cyan")
    table.add_column("Category", style="dim")
    table.add_column("Source", style="dim")
    table.add_column("Trust", style="dim")

    hub_count = 0
    builtin_count = 0
    local_count = 0

    for skill in sorted(all_skills, key=lambda s: (s.get("category") or "", s["name"])):
        name = skill["name"]
        category = skill.get("category", "")
        hub_entry = hub_installed.get(name)

        if hub_entry:
            source_type = "hub"
            source_display = hub_entry.get("source", "hub")
            trust = hub_entry.get("trust_level", "community")
            hub_count += 1
        elif name in builtin_names:
            source_type = "builtin"
            source_display = "builtin"
            trust = "builtin"
            builtin_count += 1
        else:
            source_type = "local"
            source_display = "local"
            trust = "local"
            local_count += 1

        if source_filter != "all" and source_filter != source_type:
            continue

        trust_style = {"builtin": "bright_cyan", "trusted": "green", "community": "yellow", "local": "dim"}.get(trust, "dim")
        trust_label = "official" if source_display == "official" else trust
        table.add_row(name, category, source_display, f"[{trust_style}]{trust_label}[/]")

    c.print(table)
    c.print(
        f"[dim]{hub_count} 个 hub 安装，{builtin_count} 个内置，{local_count} 个本地[/]\n"
    )


def do_check(name: Optional[str] = None, console: Optional[Console] = None) -> None:
    """Check hub-installed skills for upstream updates."""
    from tools.skills_hub import check_for_skill_updates

    c = console or _console
    results = check_for_skill_updates(name=name)
    if not results:
        c.print("[dim]没有可检查的 hub 安装技能。[/]\n")
        return

    table = Table(title="技能更新")
    table.add_column("Name", style="bold cyan")
    table.add_column("Source", style="dim")
    table.add_column("Status", style="dim")

    for entry in results:
        table.add_row(entry.get("name", ""), entry.get("source", ""), entry.get("status", ""))

    c.print(table)
    update_count = sum(1 for entry in results if entry.get("status") == "update_available")
    c.print(f"[dim]已检查 {len(results)} 个技能，其中 {update_count} 个有可用更新[/]\n")


def do_update(name: Optional[str] = None, console: Optional[Console] = None) -> None:
    """Update hub-installed skills with upstream changes."""
    from tools.skills_hub import HubLockFile, check_for_skill_updates

    c = console or _console
    lock = HubLockFile()
    updates = [entry for entry in check_for_skill_updates(name=name) if entry.get("status") == "update_available"]
    if not updates:
        c.print("[dim]没有可用更新。[/]\n")
        return

    for entry in updates:
        installed = lock.get_installed(entry["name"])
        category = _derive_category_from_install_path(installed.get("install_path", "")) if installed else ""
        c.print(f"[bold]正在更新：[/] {entry['name']}")
        do_install(entry["identifier"], category=category, force=True, console=c)

    c.print(f"[bold green]已更新 {len(updates)} 个技能。[/]\n")


def do_audit(name: Optional[str] = None, console: Optional[Console] = None) -> None:
    """Re-run security scan on installed hub skills."""
    from tools.skills_hub import HubLockFile, SKILLS_DIR
    from tools.skills_guard import scan_skill, format_scan_report

    c = console or _console
    lock = HubLockFile()
    installed = lock.list_installed()

    if not installed:
        c.print("[dim]没有可审计的 hub 安装技能。[/]\n")
        return

    targets = installed
    if name:
        targets = [e for e in installed if e["name"] == name]
        if not targets:
            c.print(f"[bold red]错误：[/]“{name}” 不是通过 hub 安装的技能。\n")
            return

    c.print(f"\n[bold]正在审计 {len(targets)} 个技能...[/]\n")

    for entry in targets:
        skill_path = SKILLS_DIR / entry["install_path"]
        if not skill_path.exists():
            c.print(f"[yellow]警告：[/] {entry['name']} - 路径缺失：{entry['install_path']}")
            continue

        result = scan_skill(skill_path, source=entry.get("identifier", entry["source"]))
        c.print(format_scan_report(result))
        c.print()


def do_uninstall(name: str, console: Optional[Console] = None,
                 skip_confirm: bool = False,
                 invalidate_cache: bool = True) -> None:
    """Remove a hub-installed skill with confirmation."""
    from tools.skills_hub import uninstall_skill

    c = console or _console

    # skip_confirm bypasses the prompt (needed in TUI mode where input() hangs)
    if not skip_confirm:
        c.print(f"\n[bold]卸载“{name}”？[/]")
        try:
            answer = input("确认 [y/N]：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            c.print("[dim]已取消。[/]\n")
            return

    success, msg = uninstall_skill(name)
    if success:
        c.print(f"[bold green]{msg}[/]\n")
        if invalidate_cache:
            try:
                from agent.prompt_builder import clear_skills_system_prompt_cache
                clear_skills_system_prompt_cache(clear_snapshot=True)
            except Exception:
                pass
        else:
            c.print("[dim]更改会在下一次会话中生效。[/]")
            c.print("[dim]可使用 /reset 立即开始新会话，或用 --now 立即生效（会使 prompt cache 失效）。[/]\n")
    else:
        c.print(f"[bold red]Error:[/] {msg}\n")


def do_tap(action: str, repo: str = "", console: Optional[Console] = None) -> None:
    """Manage taps (custom GitHub repo sources)."""
    from tools.skills_hub import TapsManager

    c = console or _console
    mgr = TapsManager()

    if action == "list":
        taps = mgr.list_taps()
        if not taps:
            c.print("[dim]没有配置自定义 tap，仅使用默认来源。[/]\n")
            return
        table = Table(title="已配置 Taps")
        table.add_column("Repo", style="bold cyan")
        table.add_column("Path", style="dim")
        for t in taps:
            label = t.get("repo") or t.get("name") or t.get("path", "unknown")
            table.add_row(label, t.get("path", "skills/"))
        c.print(table)
        c.print()

    elif action == "add":
        if not repo:
            c.print("[bold red]错误：[/]需要提供 repo。用法：hermes skills tap add owner/repo\n")
            return
        if mgr.add(repo):
            c.print(f"[bold green]已添加 tap：[/] {repo}\n")
        else:
            c.print(f"[yellow]tap 已存在：[/] {repo}\n")

    elif action == "remove":
        if not repo:
            c.print("[bold red]错误：[/]需要提供 repo。用法：hermes skills tap remove owner/repo\n")
            return
        if mgr.remove(repo):
            c.print(f"[bold green]已移除 tap：[/] {repo}\n")
        else:
            c.print(f"[bold red]错误：[/]未找到 tap：{repo}\n")

    else:
        c.print(f"[bold red]未知 tap 操作：[/] {action}。可用：list、add、remove\n")


def do_publish(skill_path: str, target: str = "github", repo: str = "",
               console: Optional[Console] = None) -> None:
    """Publish a local skill to a registry (GitHub PR or ClawHub submission)."""
    from tools.skills_hub import GitHubAuth, SKILLS_DIR
    from tools.skills_guard import scan_skill, format_scan_report

    c = console or _console
    path = Path(skill_path)

    # Resolve relative to skills dir if not absolute
    if not path.is_absolute():
        path = SKILLS_DIR / path
    if not path.exists() or not (path / "SKILL.md").exists():
        c.print(f"[bold red]Error:[/] No SKILL.md found at {path}\n")
        return

    # Validate the skill
    import yaml
    skill_md = (path / "SKILL.md").read_text(encoding="utf-8")
    fm = {}
    if skill_md.startswith("---"):
        import re
        match = re.search(r'\n---\s*\n', skill_md[3:])
        if match:
            try:
                fm = yaml.safe_load(skill_md[3:match.start() + 3]) or {}
            except yaml.YAMLError:
                pass

    name = fm.get("name", path.name)
    description = fm.get("description", "")
    if not description:
        c.print("[bold red]Error:[/] SKILL.md must have a 'description' in frontmatter.\n")
        return

    # Self-scan before publishing
    c.print(f"[bold]Scanning '{name}' before publish...[/]")
    result = scan_skill(path, source="self")
    c.print(format_scan_report(result))
    if result.verdict == "dangerous":
        c.print("[bold red]Cannot publish a skill with DANGEROUS verdict.[/]\n")
        return

    if target == "github":
        if not repo:
            c.print("[bold red]Error:[/] --repo required for GitHub publish.\n"
                    "Usage: hermes skills publish <path> --to github --repo owner/repo\n")
            return

        auth = GitHubAuth()
        if not auth.is_authenticated():
            c.print("[bold red]Error:[/] GitHub authentication required.\n"
                    f"Set GITHUB_TOKEN in {display_hermes_home()}/.env or run 'gh auth login'.\n")
            return

        c.print(f"[bold]Publishing '{name}' to {repo}...[/]")
        success, msg = _github_publish(path, name, repo, auth)
        if success:
            c.print(f"[bold green]{msg}[/]\n")
        else:
            c.print(f"[bold red]Error:[/] {msg}\n")

    elif target == "clawhub":
        c.print("[yellow]ClawHub publishing is not yet supported. "
                "Submit manually at https://clawhub.ai/submit[/]\n")
    else:
        c.print(f"[bold red]Unknown target:[/] {target}. Use 'github' or 'clawhub'.\n")


def _github_publish(skill_path: Path, skill_name: str, target_repo: str,
                    auth) -> tuple:
    """Create a PR to a GitHub repo with the skill. Returns (success, message)."""
    import httpx

    headers = auth.get_headers()

    # 1. Fork the repo
    try:
        resp = httpx.post(
            f"https://api.github.com/repos/{target_repo}/forks",
            headers=headers, timeout=30,
        )
        if resp.status_code in (200, 202):
            fork = resp.json()
            fork_repo = fork["full_name"]
        elif resp.status_code == 403:
            return False, "GitHub token lacks permission to fork repos"
        else:
            return False, f"Failed to fork {target_repo}: {resp.status_code}"
    except httpx.HTTPError as e:
        return False, f"Network error forking repo: {e}"

    # 2. Get default branch
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{target_repo}",
            headers=headers, timeout=15,
        )
        default_branch = resp.json().get("default_branch", "main")
    except Exception:
        default_branch = "main"

    # 3. Get the base tree SHA
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{fork_repo}/git/refs/heads/{default_branch}",
            headers=headers, timeout=15,
        )
        base_sha = resp.json()["object"]["sha"]
    except Exception as e:
        return False, f"Failed to get base branch: {e}"

    # 4. Create a new branch
    branch_name = f"add-skill-{skill_name}"
    try:
        httpx.post(
            f"https://api.github.com/repos/{fork_repo}/git/refs",
            headers=headers, timeout=15,
            json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
    except Exception as e:
        return False, f"Failed to create branch: {e}"

    # 5. Upload skill files
    for f in skill_path.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(skill_path))
        upload_path = f"skills/{skill_name}/{rel}"
        try:
            import base64
            content_b64 = base64.b64encode(f.read_bytes()).decode()
            httpx.put(
                f"https://api.github.com/repos/{fork_repo}/contents/{upload_path}",
                headers=headers, timeout=15,
                json={
                    "message": f"Add {skill_name} skill: {rel}",
                    "content": content_b64,
                    "branch": branch_name,
                },
            )
        except Exception as e:
            return False, f"Failed to upload {rel}: {e}"

    # 6. Create PR
    try:
        resp = httpx.post(
            f"https://api.github.com/repos/{target_repo}/pulls",
            headers=headers, timeout=15,
            json={
                "title": f"Add skill: {skill_name}",
                "body": f"Submitting the `{skill_name}` skill via Hermes Skills Hub.\n\n"
                        f"This skill was scanned by the Hermes Skills Guard before submission.",
                "head": f"{fork_repo.split('/')[0]}:{branch_name}",
                "base": default_branch,
            },
        )
        if resp.status_code == 201:
            pr_url = resp.json().get("html_url", "")
            return True, f"PR created: {pr_url}"
        else:
            return False, f"Failed to create PR: {resp.status_code} {resp.text[:200]}"
    except httpx.HTTPError as e:
        return False, f"Network error creating PR: {e}"


def do_snapshot_export(output_path: str, console: Optional[Console] = None) -> None:
    """Export current hub skill configuration to a portable JSON file."""
    from tools.skills_hub import HubLockFile, TapsManager

    c = console or _console
    lock = HubLockFile()
    taps = TapsManager()

    installed = lock.list_installed()
    tap_list = taps.list_taps()

    snapshot = {
        "hermes_version": "0.1.0",
        "exported_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "skills": [
            {
                "name": entry["name"],
                "source": entry.get("source", ""),
                "identifier": entry.get("identifier", ""),
                "category": str(Path(entry.get("install_path", "")).parent)
                            if "/" in entry.get("install_path", "") else "",
            }
            for entry in installed
        ],
        "taps": tap_list,
    }

    payload = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    if output_path == "-":
        import sys
        sys.stdout.write(payload)
    else:
        out = Path(output_path)
        out.write_text(payload)
        c.print(f"[bold green]Snapshot exported:[/] {out}")
        c.print(f"[dim]{len(installed)} skill(s), {len(tap_list)} tap(s)[/]\n")


def do_snapshot_import(input_path: str, force: bool = False,
                       console: Optional[Console] = None) -> None:
    """Re-install skills from a snapshot file."""
    from tools.skills_hub import TapsManager

    c = console or _console
    inp = Path(input_path)
    if not inp.exists():
        c.print(f"[bold red]Error:[/] File not found: {inp}\n")
        return

    try:
        snapshot = json.loads(inp.read_text())
    except json.JSONDecodeError:
        c.print(f"[bold red]Error:[/] Invalid JSON in {inp}\n")
        return

    # Restore taps first
    taps = snapshot.get("taps", [])
    if taps:
        mgr = TapsManager()
        for tap in taps:
            repo = tap.get("repo", "")
            if repo:
                mgr.add(repo, tap.get("path", "skills/"))
        c.print(f"[dim]Restored {len(taps)} tap(s)[/]")

    # Install skills
    skills = snapshot.get("skills", [])
    if not skills:
        c.print("[dim]No skills in snapshot to install.[/]\n")
        return

    c.print(f"[bold]Importing {len(skills)} skill(s) from snapshot...[/]\n")
    for entry in skills:
        identifier = entry.get("identifier", "")
        category = entry.get("category", "")
        if not identifier:
            c.print(f"[yellow]Skipping entry with no identifier: {entry.get('name', '?')}[/]")
            continue

        c.print(f"[bold]--- {entry.get('name', identifier)} ---[/]")
        do_install(identifier, category=category, force=force, console=c)

    c.print("[bold green]Snapshot import complete.[/]\n")


# ---------------------------------------------------------------------------
# CLI argparse entry point
# ---------------------------------------------------------------------------

def skills_command(args) -> None:
    """Router for `hermes skills <subcommand>` — called from hermes_cli/main.py."""
    action = getattr(args, "skills_action", None)

    if action == "browse":
        do_browse(page=args.page, page_size=args.size, source=args.source)
    elif action == "search":
        do_search(args.query, source=args.source, limit=args.limit)
    elif action == "install":
        do_install(args.identifier, category=args.category, force=args.force,
                   skip_confirm=getattr(args, "yes", False))
    elif action == "inspect":
        do_inspect(args.identifier)
    elif action == "list":
        do_list(source_filter=args.source)
    elif action == "check":
        do_check(name=getattr(args, "name", None))
    elif action == "update":
        do_update(name=getattr(args, "name", None))
    elif action == "audit":
        do_audit(name=getattr(args, "name", None))
    elif action == "uninstall":
        do_uninstall(args.name)
    elif action == "publish":
        do_publish(
            args.skill_path,
            target=getattr(args, "to", "github"),
            repo=getattr(args, "repo", ""),
        )
    elif action == "snapshot":
        snap_action = getattr(args, "snapshot_action", None)
        if snap_action == "export":
            do_snapshot_export(args.output)
        elif snap_action == "import":
            do_snapshot_import(args.input, force=getattr(args, "force", False))
        else:
            _console.print("用法：hermes skills snapshot [export|import]\n")
    elif action == "tap":
        tap_action = getattr(args, "tap_action", None)
        repo = getattr(args, "repo", "") or getattr(args, "name", "")
        if not tap_action:
            _console.print("用法：hermes skills tap [list|add|remove]\n")
            return
        do_tap(tap_action, repo=repo)
    else:
        _console.print("用法：hermes skills [browse|search|install|inspect|list|check|update|audit|uninstall|publish|snapshot|tap]\n")
        _console.print("执行 `hermes skills <command> --help` 查看详细说明。\n")


# ---------------------------------------------------------------------------
# Slash command entry point (/skills in chat)
# ---------------------------------------------------------------------------

def handle_skills_slash(cmd: str, console: Optional[Console] = None) -> None:
    """
    Parse and dispatch `/skills <subcommand> [args]` from the chat interface.

    Examples:
        /skills search kubernetes
        /skills install openai/skills/skill-creator
        /skills install openai/skills/skill-creator --force
        /skills inspect openai/skills/skill-creator
        /skills list
        /skills list --source hub
        /skills check
        /skills update
        /skills audit
        /skills audit my-skill
        /skills uninstall my-skill
        /skills tap list
        /skills tap add owner/repo
        /skills tap remove owner/repo
    """
    c = console or _console
    parts = cmd.strip().split()

    # Strip the leading "/skills" if present
    if parts and parts[0].lower() == "/skills":
        parts = parts[1:]

    if not parts:
        _print_skills_help(c)
        return

    action = parts[0].lower()
    args = parts[1:]

    if action == "browse":
        page = 1
        page_size = 20
        source = "all"
        i = 0
        while i < len(args):
            if args[i] == "--page" and i + 1 < len(args):
                try:
                    page = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            elif args[i] == "--size" and i + 1 < len(args):
                try:
                    page_size = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            elif args[i] == "--source" and i + 1 < len(args):
                source = args[i + 1]
                i += 2
            else:
                i += 1
        do_browse(page=page, page_size=page_size, source=source, console=c)

    elif action == "search":
        if not args:
            c.print("[bold red]用法：[/] /skills search <query> [--source skills-sh|well-known|github|official] [--limit N]\n")
            return
        source = "all"
        limit = 10
        query_parts = []
        i = 0
        while i < len(args):
            if args[i] == "--source" and i + 1 < len(args):
                source = args[i + 1]
                i += 2
            elif args[i] == "--limit" and i + 1 < len(args):
                try:
                    limit = int(args[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                query_parts.append(args[i])
                i += 1
        do_search(" ".join(query_parts), source=source, limit=limit, console=c)

    elif action == "install":
        if not args:
            c.print("[bold red]用法：[/] /skills install <identifier> [--category <cat>] [--force] [--now]\n")
            return
        identifier = args[0]
        category = ""
        # Slash commands run inside prompt_toolkit where input() hangs.
        # Always skip confirmation — the user typing the command is implicit consent.
        skip_confirm = True
        force = "--force" in args
        # --now invalidates prompt cache immediately (costs more money).
        # Default: defer to next session to preserve cache.
        invalidate_cache = "--now" in args
        for i, a in enumerate(args):
            if a == "--category" and i + 1 < len(args):
                category = args[i + 1]
        do_install(identifier, category=category, force=force,
                   skip_confirm=skip_confirm, invalidate_cache=invalidate_cache,
                   console=c)

    elif action == "inspect":
        if not args:
            c.print("[bold red]用法：[/] /skills inspect <identifier>\n")
            return
        do_inspect(args[0], console=c)

    elif action == "list":
        source_filter = "all"
        if "--source" in args:
            idx = args.index("--source")
            if idx + 1 < len(args):
                source_filter = args[idx + 1]
        do_list(source_filter=source_filter, console=c)

    elif action == "check":
        name = args[0] if args else None
        do_check(name=name, console=c)

    elif action == "update":
        name = args[0] if args else None
        do_update(name=name, console=c)

    elif action == "audit":
        name = args[0] if args else None
        do_audit(name=name, console=c)

    elif action == "uninstall":
        if not args:
            c.print("[bold red]用法：[/] /skills uninstall <name> [--now]\n")
            return
        # Slash commands run inside prompt_toolkit where input() hangs.
        skip_confirm = True
        invalidate_cache = "--now" in args
        do_uninstall(args[0], console=c, skip_confirm=skip_confirm,
                     invalidate_cache=invalidate_cache)

    elif action == "publish":
        if not args:
            c.print("[bold red]用法：[/] /skills publish <skill-path> [--to github] [--repo owner/repo]\n")
            return
        skill_path = args[0]
        target = "github"
        repo = ""
        for i, a in enumerate(args):
            if a == "--to" and i + 1 < len(args):
                target = args[i + 1]
            if a == "--repo" and i + 1 < len(args):
                repo = args[i + 1]
        do_publish(skill_path, target=target, repo=repo, console=c)

    elif action == "snapshot":
        if not args:
            c.print("[bold red]用法：[/] /skills snapshot export <file> | /skills snapshot import <file>\n")
            return
        snap_action = args[0]
        if snap_action == "export" and len(args) > 1:
            do_snapshot_export(args[1], console=c)
        elif snap_action == "import" and len(args) > 1:
            force = "--force" in args
            do_snapshot_import(args[1], force=force, console=c)
        else:
            c.print("[bold red]用法：[/] /skills snapshot export <file> | /skills snapshot import <file>\n")

    elif action == "tap":
        if not args:
            do_tap("list", console=c)
            return
        tap_action = args[0]
        repo = args[1] if len(args) > 1 else ""
        do_tap(tap_action, repo=repo, console=c)

    elif action in ("help", "--help", "-h"):
        _print_skills_help(c)

    else:
        c.print(f"[bold red]未知操作：[/] {action}")
        _print_skills_help(c)


def _print_skills_help(console: Console) -> None:
    """Print help for the /skills slash command."""
    console.print(Panel(
        "[bold]Skills Hub 命令：[/]\n\n"
        "  [cyan]browse[/] [--source official]   分页浏览全部可用技能\n"
        "  [cyan]search[/] <query>              在注册表中搜索技能\n"
        "  [cyan]install[/] <identifier>        安装技能（含安全扫描）\n"
        "  [cyan]inspect[/] <identifier>        仅预览技能，不安装\n"
        "  [cyan]list[/] [--source hub|builtin|local] 列出已安装技能\n"
        "  [cyan]check[/] [name]                检查 hub 技能是否有上游更新\n"
        "  [cyan]update[/] [name]               更新 hub 技能\n"
        "  [cyan]audit[/] [name]                重新执行技能安全扫描\n"
        "  [cyan]uninstall[/] <name>            卸载一个通过 hub 安装的技能\n"
        "  [cyan]publish[/] <path> --repo <r>   通过 PR 发布技能到 GitHub\n"
        "  [cyan]snapshot[/] export|import      导出/导入技能配置\n"
        "  [cyan]tap[/] list|add|remove         管理技能源\n",
        title="/skills",
    ))
