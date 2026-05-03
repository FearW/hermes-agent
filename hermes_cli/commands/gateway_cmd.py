"""Gateway and WhatsApp bridge commands for Hermes CLI."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Optional


def cmd_gateway(args):
    """Gateway management commands."""
    from hermes_cli.gateway import gateway_command
    gateway_command(args)


def cmd_whatsapp(args):
    """Set up WhatsApp: choose mode, configure, install bridge, pair via QR."""
    _require_tty("whatsapp")
    import subprocess
    from pathlib import Path
    from hermes_cli.config import get_env_value, save_env_value

    print()
    print("⚕ WhatsApp 设置")
    print("=" * 50)

    # ── Step 1: Choose mode ──────────────────────────────────────────────
    current_mode = get_env_value("WHATSAPP_MODE") or ""
    if not current_mode:
        print()
        print("你打算如何将 WhatsApp 与 Hermes 一起使用？")
        print()
        print("  1. 独立机器人号码（推荐）")
        print("     其他人直接给机器人的号码发消息，体验最干净。")
        print("     需要一个已安装 WhatsApp 的第二个手机号。")
        print()
        print("  2. 个人号码（和自己聊天）")
        print("     你给自己发消息来和代理对话。")
        print("     配置很快，但交互体验不够直观。")
        print()
        try:
            choice = input("  请选择 [1/2]：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n设置已取消。")
            return

        if choice == "1":
            save_env_value("WHATSAPP_MODE", "bot")
            wa_mode = "bot"
            print("  ✓ 模式：独立机器人号码")
            print()
            print("  ┌─────────────────────────────────────────────────┐")
            print("  │  为机器人准备第二个号码：           │")
            print("  │                                                 │")
            print("  │  最简单：安装 WhatsApp Business（免费应用）  │")
            print("  │  在手机上使用第二个号码：            │")
            print("  │    • 双卡：使用第二张 SIM 卡            │")
            print("  │    • Google Voice：免费美国号码（voice.google） │")
            print("  │    • 预付费 SIM：3-10 美元，验证一次即可            │")
            print("  │                                                 │")
            print("  │  WhatsApp Business 可以和你的个人 │")
            print("  │  WhatsApp 并行运行，不需要第二台手机。             │")
            print("  └─────────────────────────────────────────────────┘")
        else:
            save_env_value("WHATSAPP_MODE", "self-chat")
            wa_mode = "self-chat"
            print("  ✓ 模式：个人号码（自聊）")
    else:
        wa_mode = current_mode
        mode_label = "独立机器人号码" if wa_mode == "bot" else "个人号码（自聊）"
        print(f"\n✓ Mode: {mode_label}")

    # ── Step 2: Enable WhatsApp ──────────────────────────────────────────
    print()
    current = get_env_value("WHATSAPP_ENABLED")
    if current and current.lower() == "true":
        print("✓ WhatsApp 已启用")
    else:
        save_env_value("WHATSAPP_ENABLED", "true")
        print("✓ 已启用 WhatsApp")

    # ── Step 3: Allowed users ────────────────────────────────────────────
    current_users = get_env_value("WHATSAPP_ALLOWED_USERS") or ""
    if current_users:
        print(f"✓ 当前允许名单：{current_users}")
        try:
            response = input("\n  更新允许名单吗？[y/N] ").strip()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response.lower() in ("y", "yes"):
            if wa_mode == "bot":
                phone = input("  可给机器人发消息的号码（逗号分隔）：").strip()
            else:
                phone = input("  你的手机号（例如 15551234567）：").strip()
            if phone:
                save_env_value("WHATSAPP_ALLOWED_USERS", phone.replace(" ", ""))
                print(f"  ✓ 已更新为：{phone}")
    else:
        print()
        if wa_mode == "bot":
            print("  谁可以给机器人发消息？")
            phone = input("  手机号（逗号分隔，或 * 表示任何人）：").strip()
        else:
            phone = input("  你的手机号（例如 15551234567）：").strip()
        if phone:
            save_env_value("WHATSAPP_ALLOWED_USERS", phone.replace(" ", ""))
            print(f"  ✓ 已设置允许名单：{phone}")
        else:
            print("  ⚠ 未设置允许名单，代理将响应所有传入消息")

    # ── Step 4: Install bridge dependencies ──────────────────────────────
    project_root = Path(__file__).resolve().parents[1]
    bridge_dir = project_root / "scripts" / "whatsapp-bridge"
    bridge_script = bridge_dir / "bridge.js"

    if not bridge_script.exists():
        print(f"\n✗ 未找到桥接脚本：{bridge_script}")
        return

    if not (bridge_dir / "node_modules").exists():
        print("\n→ 正在安装 WhatsApp 桥接依赖...")
        result = subprocess.run(
            ["npm", "install"],
            cwd=str(bridge_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"  ✗ npm install 失败：{result.stderr}")
            return
        print("  ✓ 依赖已安装")
    else:
        print("✓ 桥接依赖已安装")

    # ── Step 5: Check for existing session ───────────────────────────────
    session_dir = get_hermes_home() / "whatsapp" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    if (session_dir / "creds.json").exists():
        print("✓ 已找到现有 WhatsApp 会话")
        try:
            response = input("\n  Re-pair? This will clear the existing session. [y/N] ").strip()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response.lower() in ("y", "yes"):
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
            session_dir.mkdir(parents=True, exist_ok=True)
            print("  ✓ 会话已清除")
        else:
            print("\n✓ WhatsApp is configured and paired!")
            print("  启动网关：hermes gateway")
            return

    # ── Step 6: QR code pairing ──────────────────────────────────────────
    print()
    print("─" * 50)
    if wa_mode == "bot":
        print("📱 打开 WhatsApp（或 WhatsApp Business），在")
        print("   绑定机器人号码的手机上扫描：")
    else:
        print("📱 在你的手机上打开 WhatsApp，然后扫描：")
    print()
    print("   设置 → 已关联设备 → 关联设备")
    print("─" * 50)
    print()

    try:
        subprocess.run(
            ["node", str(bridge_script), "--pair-only", "--session", str(session_dir)],
            cwd=str(bridge_dir),
        )
    except KeyboardInterrupt:
        pass

    # ── Step 7: Post-pairing ─────────────────────────────────────────────
    print()
    if (session_dir / "creds.json").exists():
        print("✓ WhatsApp 已配对成功！")
        print()
        if wa_mode == "bot":
            print("  下一步：")
            print("    1. 启动网关：hermes gateway")
            print("    2. 给机器人的 WhatsApp 号码发送一条消息")
            print("    3. 代理会自动回复你")
            print()
            print("  提示：代理回复会带有前缀 '⚕ Hermes Agent'")
        else:
            print("  下一步：")
            print("    1. 启动网关：hermes gateway")
            print("    2. 打开 WhatsApp → 给自己发消息")
            print("    3. 输入一条消息，代理就会回复")
            print()
            print("  提示：代理回复会带有前缀 '⚕ Hermes Agent'")
            print("  这样你就能和自己发出的消息区分开。")
        print()
        print("  也可以安装成服务：hermes gateway install")
    else:
        print("⚠ 配对可能还没有完成。可执行 `hermes whatsapp` 再试一次。")
