# -*- coding: utf-8 -*-
"""User-visible Chinese copy for the messaging gateway (adapter.send, etc.).

Injection strings passed to ``run_conversation`` / the LLM stay English — see
``gateway/run.py`` inline text and ``agent/image_routing.py``.
"""

from __future__ import annotations

# adapter.send — STT 未配置时，聊天窗口里用户能看到的说明（中文）
STT_USER_SETUP_HERMES = (
    "已收到您的语音消息，但当前无法转写：未配置语音转文字（STT）。\n\n"
    "启用方式：在 Hermes 虚拟环境中执行 `pip install faster-whisper`，"
    "在 config.yaml 中设置 `stt.enabled: true`，然后用 /restart 重启网关。"
)

STT_USER_SETUP_SKILL_HINT = (
    "\n\n如需完整设置说明，请输入：`/skill hermes-agent-setup`"
)

# adapter.send — @ 上下文注入被策略拒绝时，用户侧可见
CONTEXT_INJECTION_REFUSED = "上下文注入被拒绝，请检查 @ 引用的路径或权限。"
