<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent · FearW Fork

<p align="center">
  <a href="https://github.com/FearW/hermes-agent"><img src="https://img.shields.io/badge/Fork-FearW%2Fhermes--agent-FFD700?style=for-the-badge" alt="FearW fork"></a>
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/Upstream-NousResearch%2Fhermes--agent-blueviolet?style=for-the-badge" alt="Upstream"></a>
  <a href="https://github.com/NousResearch/hermes-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
</p>

这是基于 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的个人增强版 Fork。

目标很简单：**保留原版 Hermes Agent 的完整能力，同时把常用体验、网关稳定性、记忆维护和本地使用细节修到更顺手。**

---

## 这个 Fork 改了什么

- **保留原版功能**：CLI、Gateway、Tools、Skills、Memory、Cron、Web Server、ACP 等能力都继续保留。
- **L4 记忆维护**：保留并启用更稳定的 L4 归档/遗忘维护循环，长期运行更干净。
- **网关健康修复**：修复部分 Gateway、快捷命令、安全确认、后台维护任务相关兼容问题。
- **配置路径修复**：减少硬编码路径，兼容 Hermes profiles 和不同运行环境。
- **Web/CLI 兼容性修复**：补齐缺失兼容函数，修复部分测试和接口字段问题。
- **隐私清理**：移除个人一次性脚本和临时数据，保留可公开发布的项目代码。

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/FearW/hermes-agent.git
cd hermes-agent
```

### 2. 安装依赖

推荐使用 `uv`：

```bash
uv venv venv --python 3.11
source venv/bin/activate
uv pip install -e ".[all,dev]"
```

Windows 建议在 WSL2 里运行；如果使用 PowerShell，本地虚拟环境激活命令通常是：

```powershell
.\venv\Scripts\Activate.ps1
```

### 3. 启动 Hermes

```bash
hermes
```

常用命令：

```bash
hermes setup      # 初始化配置
hermes model      # 选择模型
hermes tools      # 管理工具
hermes gateway    # 启动 Telegram/Discord/Slack 等网关
hermes doctor     # 检查环境问题
```

---

## 常用能力

| 能力 | 说明 |
| --- | --- |
| CLI 对话 | 终端里直接和 Agent 对话，支持工具调用和历史会话 |
| Messaging Gateway | 支持 Telegram、Discord、Slack、WhatsApp、Signal 等平台 |
| Tools | 文件、终端、浏览器、搜索、代码执行、MCP 等工具能力 |
| Skills | 可安装、调用和沉淀技能，让 Agent 越用越顺手 |
| Memory / L4 | 支持长期记忆、会话搜索、L4 归档和遗忘维护 |
| Cron | 可配置定时任务，例如日报、提醒、自动巡检 |

---

## 开发与测试

进入虚拟环境后运行：

```bash
python -m pytest tests/ -q
```

本 Fork 当前重点验证过的健康基线包括：

```bash
uv run --extra dev --extra web pytest tests/hermes_cli/test_web_server.py tests/cli/test_quick_commands.py -q
uv run --extra dev --extra web pytest tests/gateway/test_maintenance.py -q -o addopts= --tb=short
```

---

## 上游项目

- 原项目：<https://github.com/NousResearch/hermes-agent>
- 原文档：<https://hermes-agent.nousresearch.com/docs/>
- License：MIT

本仓库会尽量参考上游最新代码做修复，但原则是：**不删除本 Fork 已有的自定义功能。**
