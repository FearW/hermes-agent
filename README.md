# Hermes Agent · FearW Fork

这是基于 `NousResearch/hermes-agent` 的增强 Fork。目标是：保留原版 Hermes Agent 的工具、网关、Skills、记忆、Web/TUI、ACP 等能力，同时让日常使用更轻量、更快、更稳定。

## 这个 Fork 的重点

- 保留原版功能：CLI、Gateway、Tools、Skills、Memory、Cron、Web Server、ACP 等能力继续存在。
- 默认走 CPA：推荐通过 `CLIProxyAPI` 统一接入模型，Hermes 侧只需要 OpenAI-compatible `/v1` 接口。
- 保护 L4 记忆：保留 L4 归档、遗忘、瘦身、相似记忆合并等长期运行优化。
- 轻量高速：安装默认只拉基础依赖，重能力按需安装。
- 傻瓜可用：新用户 clone 后少配置，启动 CPA 后即可运行 Hermes。

## 快速开始

### 1. 安装 Hermes

```bash
git clone https://github.com/FearW/hermes-agent.git
cd hermes-agent
uv run hermes setup
uv run hermes
```

Windows PowerShell 也可以使用一键安装器：

```powershell
irm https://raw.githubusercontent.com/FearW/hermes-agent/main/scripts/install.ps1 | iex
```

Linux / macOS / WSL：

```bash
curl -fsSL https://raw.githubusercontent.com/FearW/hermes-agent/main/scripts/install.sh | bash
```

### 2. 启动 CPA

本 Fork 推荐把模型兼容交给 `CLIProxyAPI`，Hermes 只连一个 OpenAI-compatible 地址：

```text
http://127.0.0.1:8080/v1
```

Hermes 默认配置就是 CPA 简洁版：

```yaml
model:
  default: "gpt-5(8192)"
  provider: "cliproxyapi"
  base_url: "http://127.0.0.1:8080/v1"
```

如果你的 CPA 需要 key，可以放到环境变量或 `~/.hermes/.env`：

```bash
CLIPROXY_API_KEY=your-key-if-needed
```

也支持别名：`provider: "cpa"`、`CPA_BASE_URL`、`CPA_API_KEY`。
CPA 模型后缀（例如 `gpt-5(8192)`）会原样传给后端，Hermes 不会剥离。

### 3. 运行 Hermes

终端聊天：

```bash
uv run hermes
```

WebUI：

```bash
uv run hermes dashboard
```

默认打开：`http://127.0.0.1:9119`

如果想在 WebUI 里打开内嵌聊天页：

```bash
uv run hermes dashboard --tui
```

## 常用命令

```bash
uv run hermes version
uv run hermes setup
uv run hermes dashboard
uv run hermes dashboard --tui
uv run hermes tools
uv run hermes gateway
uv run hermes memory doctor
uv run hermes doctor
```

## 可选能力按需安装

```bash
uv pip install -e ".[messaging]"   # Telegram / Discord / Slack 等网关
uv pip install -e ".[web]"         # Web Dashboard / TUI 网页服务
uv pip install -e ".[voice]"       # 本地语音能力
uv pip install -e ".[all]"         # 全量安装，不建议新手第一步使用
```

## 原则

- 不删除本 Fork 已有魔改功能。
- 不整包合并上游大改，只做可验证、低风险同步。
- 默认使用 CPA 简化模型接入；旧 provider 代码保留为兼容兜底。

## 上游项目

- 原项目：<https://github.com/NousResearch/hermes-agent>
- 原文档：<https://hermes-agent.nousresearch.com/docs/>
- License：MIT
