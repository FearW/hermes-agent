<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="600">
</p>

<h1 align="center">Hermes Agent · FearW Fork</h1>

<p align="center">
  <strong>自进化的 AI Agent —— 从经验中创建技能，在使用中改进，随处运行</strong>
</p>

<p align="center">
  <a href="https://github.com/FearW/hermes-agent"><img src="https://img.shields.io/badge/GitHub-FearW%2Fhermes--agent-181717?logo=github" alt="GitHub"></a>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/Version-0.8.0-blue" alt="Version">
</p>

---

## 目录

- [项目简介](#项目简介)
- [Fork 特色](#fork-特色)
- [功能概览](#功能概览)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [使用方式](#使用方式)
- [项目架构](#项目架构)
- [工具与技能](#工具与技能)
- [消息平台网关](#消息平台网关)
- [自进化闭环](#自进化闭环)
- [多实例 Profile](#多实例-profile)
- [皮肤/主题系统](#皮肤主题系统)
- [开发指南](#开发指南)
- [测试](#测试)
- [常见问题](#常见问题)
- [致谢与上游](#致谢与上游)
- [许可证](#许可证)

---

## 项目简介

Hermes Agent 是一个功能完备的 AI Agent 框架，具备工具调用、技能系统、长期记忆、多平台消息网关、定时任务、浏览器自动化、代码沙箱执行等能力。本 Fork（FearW/hermes-agent）在保留上游全部 Agent 能力的基础上，将模型接入层统一交由 [CLIProxyAPI (CPA)](https://github.com/Fwindy/CLIProxyAPI) 管理，实现「一个入口、任意模型」的极简架构。

## Fork 特色

| 特色 | 说明 |
|------|------|
| **只走 CPA** | Hermes 不再直连旧 provider，上游渠道全部在 CLIProxyAPI 里管理 |
| **保留全部能力** | CLI、Gateway、Tools、Skills、Memory、Cron、Web Server、ACP 等能力完整保留 |
| **L4 记忆保护** | 保留 L4 归档、遗忘、瘦身、相似记忆合并等长期运行优化 |
| **轻量高速** | Hermes 侧只维护一个 CPA 运行时入口，无多余依赖 |
| **傻瓜可用** | 新用户 clone 后启动 CPA，即可运行 Hermes |
| **CPA 自动更新** | 安装脚本和 `hermes update` 均支持 CPA 版本自动检测与升级 |

## 功能概览

- **多轮对话与工具调用** —— 支持 OpenAI 兼容 API 的完整 Agent 循环，含流式响应、推理模型、上下文压缩
- **30+ 内置工具** —— 终端执行、文件读写、Web 搜索/提取、浏览器自动化、代码沙箱、MCP 协议、图像生成、语音识别/TTS 等
- **技能系统** —— 从经验中自动创建技能，支持社区技能 Hub 搜索/安装/发布
- **长期记忆** —— L1~L4 四层记忆架构，含全息记忆插件、自动归档、遗忘与合并
- **消息平台网关** —— Telegram、Discord、WhatsApp、Slack、飞书、钉钉、企业微信、Signal、Matrix、Email 等 15+ 平台
- **定时任务** —— Cron 调度器，支持一次性/重复任务，跨平台消息投递
- **Web Dashboard** —— 内置 WebUI，支持公网部署、CPA API 代理、内嵌 TUI 聊天
- **ACP 协议** —— Agent Client Protocol 服务器，支持 VS Code / Zed / JetBrains 集成
- **多实例 Profile** —— 完全隔离的多实例支持，各自独立的配置、密钥、记忆、会话
- **皮肤/主题** —— 数据驱动的 CLI 视觉定制，内置 4 款主题，支持 YAML 自定义
- **自进化闭环** —— 运行结果与用户反馈自动采集，支持 A/B 对比与回归门禁

---

## 快速开始

### 前置要求

| 依赖 | 说明 |
|------|------|
| **Git** | 含 `--recurse-submodules` 支持 |
| **Python 3.11+** | uv 可自动安装 |
| **uv** | 快速 Python 包管理器 ([安装指南](https://docs.astral.sh/uv/)) |
| **Node.js 20+** | 可选 —— 浏览器工具和 WhatsApp 桥接需要 |

### 1. 安装 Hermes

**方式一：一键安装脚本（推荐）**

Linux / macOS / WSL：

```bash
curl -fsSL https://raw.githubusercontent.com/FearW/hermes-agent/main/scripts/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/FearW/hermes-agent/main/scripts/install.ps1 | iex
```

**方式二：手动安装**

```bash
git clone https://github.com/FearW/hermes-agent.git
cd hermes-agent
uv venv venv --python 3.11
source venv/bin/activate   # Windows: venv\Scripts\activate
uv pip install -e ".[all]"
```

### 2. 启动 CPA

本 Fork 将模型兼容完全交给 CLIProxyAPI，Hermes 只需连接一个 OpenAI 兼容地址：

```text
http://127.0.0.1:8080/v1
```

安装脚本会自动从 [GitHub Releases](https://github.com/Fwindy/CLIProxyAPI/releases) 检测并下载最新版 CPA。如需手动安装：

```bash
hermes setup   # 交互式向导会引导 CPA 安装
```

### 3. 配置模型

Hermes 默认配置即为 CPA 简洁版（`~/.hermes/config.yaml`）：

```yaml
model:
  default: "gpt-5(8192)"
  provider: "cliproxyapi"
  base_url: "http://127.0.0.1:8080/v1"
```

如果 CPA 需要 API Key，写入 `~/.hermes/.env`：

```bash
CLIPROXY_API_KEY=your-key-if-needed
```

也支持环境变量 `CPA_BASE_URL` 和 `CPA_API_KEY`。CPA 模型后缀（如 `gpt-5(8192)`）会原样传给后端，Hermes 不会剥离。

### 4. 运行

```bash
hermes              # 终端交互聊天
hermes dashboard    # WebUI（默认 http://127.0.0.1:9119）
hermes doctor       # 诊断检查
```

---

## 配置说明

所有用户配置存放在 `~/.hermes/` 目录下：

| 路径 | 用途 |
|------|------|
| `~/.hermes/config.yaml` | 主配置文件（模型、终端、工具集、压缩、平台等） |
| `~/.hermes/.env` | API 密钥和敏感信息 |
| `~/.hermes/auth.json` | OAuth 凭证（Nous Portal 等） |
| `~/.hermes/skills/` | 所有活跃技能（内置 + Hub 安装 + Agent 创建） |
| `~/.hermes/memories/` | 持久记忆（MEMORY.md, USER.md） |
| `~/.hermes/state.db` | SQLite 会话数据库（FTS5 全文搜索） |
| `~/.hermes/sessions/` | JSON 会话日志 |
| `~/.hermes/cron/` | 定时任务数据 |
| `~/.hermes/cpa/` | CLIProxyAPI 可执行文件与配置 |
| `~/.hermes/skins/` | 用户自定义皮肤 |

### 核心配置示例

```yaml
model:
  default: "gpt-5(8192)"
  provider: "cliproxyapi"
  base_url: "http://127.0.0.1:8080/v1"

terminal:
  backend: local          # local | docker | ssh | modal | singularity
  timeout: 60

compression:
  enabled: true
  threshold: 0.85
  summary_model: "google/gemini-3-flash-preview"

dashboard:
  host: "127.0.0.1"
  port: 9119
  public: false
  password: ""
  cpa_api_proxy: false

display:
  skin: default           # default | ares | mono | slate | 自定义
```

### 公网部署

公网 WebUI + CPA API 同端口入口：

```yaml
dashboard:
  host: "0.0.0.0"
  port: 9119
  public: true
  password: "你的强密码"
  cpa_api_proxy: true
```

启动后：

- 面板：`http://你的服务器IP:9119/`
- OpenAI/CPA 接口：`http://你的服务器IP:9119/v1`
- Anthropic/CPA 接口：`http://你的服务器IP:9119/anthropic`
- 接口 Key：`dashboard.password`，使用 `Authorization: Bearer 你的强密码`

---

## 使用方式

### CLI 常用命令

```bash
hermes                    # 启动交互式聊天
hermes chat -q "你好"     # 单次问答模式
hermes setup              # 交互式配置向导
hermes model              # 切换模型/Provider
hermes tools              # 管理工具集
hermes skills browse      # 浏览技能 Hub
hermes gateway            # 启动消息平台网关
hermes dashboard          # 启动 WebUI
hermes dashboard --tui    # WebUI + 内嵌聊天
hermes doctor             # 诊断检查
hermes memory doctor      # 记忆系统诊断
hermes update             # 更新 Hermes + CPA
hermes version            # 查看版本
```

### 交互式斜杠命令

在聊天界面中可使用：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/model` | 切换模型 |
| `/skin` | 切换主题 |
| `/tools` | 管理工具 |
| `/skills` | 浏览/安装技能 |
| `/compress` | 手动压缩上下文 |
| `/memory` | 记忆管理 |
| `/title` | 设置会话标题 |
| `/background` | 后台运行命令 |
| `/stop` | 停止当前任务 |
| `/clear` | 清空会话 |
| `/exit` | 退出 |

---

## 项目架构

```
hermes-agent/
├── run_agent.py              # AIAgent 核心类 —— 对话循环、工具调度、会话持久化
├── cli.py                    # HermesCLI —— 交互式 TUI、prompt_toolkit 集成
├── model_tools.py            # 工具编排层（tools/registry.py 的薄封装）
├── toolsets.py               # 工具集分组与预设
├── hermes_state.py           # SQLite 会话数据库（FTS5 全文搜索）
├── hermes_constants.py       # 全局常量、路径工具函数
├── batch_runner.py           # 并行批处理
│
├── agent/                    # Agent 内部模块
│   ├── prompt_builder.py         # 系统提示词组装
│   ├── context_compressor.py     # 上下文自动压缩
│   ├── auxiliary_client.py       # 辅助 LLM 客户端（视觉、摘要）
│   ├── client_factory.py         # OpenAI 客户端工厂（懒加载代理）
│   ├── client_errors.py          # 错误分类函数
│   ├── temperature_policy.py     # 温度策略（Kimi 等特殊模型）
│   ├── response_utils.py         # 响应提取工具
│   ├── credential_pool.py        # 凭证池管理
│   ├── model_metadata.py         # 模型上下文长度、Token 估算
│   ├── display.py                # KawaiiSpinner、工具进度格式化
│   ├── streaming/                # 流式响应处理
│   └── transports/               # 多 Provider 传输层
│
├── hermes_cli/               # CLI 命令实现
│   ├── main.py                   # 入口点、参数解析、命令分发
│   ├── config/                   # 配置管理、迁移、环境变量定义
│   ├── auth.py                   # Provider 解析、OAuth
│   ├── setup.py                  # 交互式配置向导
│   ├── skin_engine.py            # 皮肤/主题引擎
│   ├── commands/                  # 子命令实现
│   └── snapshot/                  # 快照备份与恢复
│
├── tools/                    # 工具实现（自注册模式）
│   ├── registry.py               # 中央工具注册表
│   ├── approval.py               # 危险命令检测 + 硬线拦截
│   ├── terminal_tool.py          # 终端编排
│   ├── process_registry.py       # 后台进程管理
│   ├── file_operations.py        # 文件读写搜索
│   ├── web_tools.py              # Web 搜索/提取
│   ├── browser_tool.py           # 浏览器自动化
│   ├── code_execution_tool.py    # 沙箱代码执行
│   ├── delegate_tool.py          # 子 Agent 委派
│   ├── mcp_tool.py               # MCP 协议客户端
│   ├── vision_tools.py           # 图像分析
│   ├── voice_mode.py             # 语音模式
│   └── environments/             # 终端执行后端
│       ├── local.py, docker.py, ssh.py, modal.py, daytona.py, singularity.py
│
├── gateway/                  # 消息平台网关
│   ├── run.py                    # GatewayRunner —— 平台生命周期、消息路由
│   ├── config.py                 # 平台配置解析
│   ├── session.py                # 会话存储、上下文提示、重置策略
│   ├── stream_consumer.py        # 流式响应消费
│   └── platforms/                # 平台适配器
│       ├── telegram.py, discord.py, slack.py, whatsapp.py
│       ├── feishu.py, dingtalk.py, wecom.py, weixin.py
│       ├── signal.py, matrix.py, mattermost.py
│       ├── email.py, sms.py, homeassistant.py
│       └── api_server.py, webhook.py, bluebubbles.py
│
├── acp_adapter/              # ACP 服务器（IDE 集成）
├── cron/                     # 定时任务调度器
├── plugins/                  # 插件系统
│   ├── memory/                   # 记忆插件（含全息记忆）
│   └── context_engine/           # 上下文引擎
├── environments/             # RL 训练环境
├── tui_gateway/              # TUI 网关
├── scripts/                  # 安装脚本与工具
│   ├── install.sh, install.ps1     # 一键安装器（含 CPA 自动版本检测）
│   └── whatsapp-bridge/            # Node.js WhatsApp 桥接
├── tests/                    # 测试套件
├── docs/                     # 文档
└── website/                  # 文档站点
```

### 核心循环

```
用户消息 → AIAgent._run_agent_loop()
  ├── 构建系统提示词 (prompt_builder.py)
  ├── 构建 API 参数 (模型、消息、工具、推理配置)
  ├── 调用 LLM (OpenAI 兼容 API)
  ├── 如果响应包含 tool_calls:
  │     ├── 通过注册表分发执行每个工具
  │     ├── 将工具结果追加到对话
  │     └── 循环回到 LLM 调用
  ├── 如果是文本响应:
  │     ├── 持久化会话到数据库
  │     └── 返回最终响应
  └── 接近 Token 限制时自动压缩上下文
```

---

## 工具与技能

### 内置工具

| 工具集 | 工具 | 说明 |
|--------|------|------|
| **terminal** | `terminal` | 终端命令执行（支持 local/docker/ssh/modal/singularity/daytona 后端） |
| **file** | `read_file`, `write_file`, `search_files`, `list_directory`, `patch_file` | 文件操作 |
| **web** | `web_search`, `web_extract` | Web 搜索与提取（Parallel + Firecrawl + Gemini 摘要） |
| **browser** | `browser_navigate`, `browser_click`, `browser_screenshot` 等 | 浏览器自动化（Browserbase） |
| **code** | `execute_code` | 沙箱 Python 执行（含 RPC 工具访问） |
| **delegate** | `delegate_task` | 子 Agent 委派与并行任务执行 |
| **vision** | `analyze_image` | 多模态图像分析 |
| **memory** | `save_memory`, `search_memory` | 持久记忆管理 |
| **cron** | `create_cron`, `list_crons`, `delete_cron` | 定时任务管理 |
| **skills** | `skills_list`, `skills_search`, `skills_install` | 技能搜索与管理 |
| **mcp** | MCP 协议客户端 | 动态发现与调用外部 MCP 工具 |
| **tts** | `text_to_speech` | 文本转语音（Edge TTS 免费 / ElevenLabs 高级） |
| **image** | `generate_image` | AI 图像生成（fal.ai） |

### 技能系统

技能是纯指令 + 脚本的组合，无需编写 Python 代码即可扩展 Agent 能力：

- **内置技能** —— 随安装附带，覆盖文档处理、Web 研究、开发工作流等
- **官方可选技能** —— `optional-skills/` 目录，通过 `hermes skills browse` 发现安装
- **社区技能** —— Skills Hub 上传分享，`hermes skills install <name>` 安装
- **Agent 自创技能** —— Agent 从经验中自动生成并保存到 `~/.hermes/skills/`

技能支持条件激活（仅在特定工具集可用/不可用时显示）、平台限制（macOS/Linux/Windows）、安全环境变量收集等高级特性。

---

## 消息平台网关

Hermes 内置消息平台网关，可将 Agent 接入 15+ 即时通讯平台：

| 平台 | 状态 | 说明 |
|------|------|------|
| Telegram | ✅ | Bot 模式，支持 Webhook / 长轮询、话题、内联按钮 |
| Discord | ✅ | Bot + Slash 命令、语音频道、线程持久化 |
| WhatsApp | ✅ | 内置 Baileys 桥接，`hermes whatsapp` 配对 |
| Slack | ✅ | Socket Mode、频道技能、审批按钮 |
| 飞书 | ✅ | 事件订阅、评论机器人、审批按钮 |
| 钉钉 | ✅ | Stream 模式 |
| 企业微信 | ✅ | 回调模式 + 直连模式 |
| 微信 | ✅ | 企微通道 |
| Signal | ✅ | signal-cli 桥接 |
| Matrix | ✅ | mautrix 桥接，支持 E2E 加密 |
| Email | ✅ | IMAP/SMTP 收发 |
| SMS | ✅ | 短信网关 |
| Home Assistant | ✅ | 智能家居控制 |
| API Server | ✅ | HTTP API 接入 |
| Webhook | ✅ | 通用 Webhook 接入 |
| BlueBubbles | ✅ | iMessage 桥接（macOS） |

启动网关：

```bash
hermes gateway
```

---

## 自进化闭环

本仓库已落地最小自进化闭环能力（v1）：

- **运行结果事件**：`$HERMES_HOME/evolution/outcomes.jsonl`
- **用户反馈事件**：`$HERMES_HOME/evolution/feedback.jsonl`
- **汇总脚本**：`scripts/build_evolution_summary.py`
- **基线校验**：`scripts/check_evolution_baseline.py`
- **A/B 对比**：`scripts/compare_evolution_runs.py`

### 零指令反馈（默认开启）

用户正常对话即可，系统自动从使用行为中提取反馈信号写入 `feedback.jsonl`，用于策略评估与版本对比。v1 已接入低侵入行为信号（如重试/撤销）。

### 评测与回归门禁

```bash
python scripts/build_evolution_summary.py
python scripts/check_evolution_baseline.py --summary ~/.hermes/evolution/summary.json --baseline scripts/evolution_baseline.json
python scripts/compare_evolution_runs.py --a control_summary.json --b candidate_summary.json
```

---

## 多实例 Profile

Hermes 支持完全隔离的多实例（Profile），每个实例拥有独立的 `HERMES_HOME` 目录：

```bash
hermes -p coder          # 使用 coder profile
hermes -p researcher     # 使用 researcher profile
```

每个 Profile 拥有独立的配置、API 密钥、记忆、会话、技能和网关状态。

---

## 皮肤主题系统

数据驱动的 CLI 视觉定制，无需代码修改即可创建新主题。

### 内置主题

| 主题 | 风格 |
|------|------|
| `default` | 经典 Hermes 金色/可爱风 |
| `ares` | 深红/青铜战神主题，自定义 Spinner 翅膀 |
| `mono` | 简洁灰度单色 |
| `slate` | 冷蓝色开发者主题 |

### 自定义主题

创建 `~/.hermes/skins/<name>.yaml`：

```yaml
name: cyberpunk
description: 赛博朋克终端主题

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "
```

激活：`/skin cyberpunk` 或在 `config.yaml` 中设置 `display.skin: cyberpunk`。

---

## 开发指南

### 开发环境搭建

```bash
git clone https://github.com/FearW/hermes-agent.git
cd hermes-agent
uv venv venv --python 3.11
source venv/bin/activate
uv pip install -e ".[all,dev]"
```

### 添加新工具

1. 创建 `tools/your_tool.py`，调用 `registry.register()` 自注册
2. 在 `model_tools.py` 的 `_modules` 列表中添加导入
3. 在 `toolsets.py` 中添加到相应工具集

### 添加新技能

在 `skills/` 或 `optional-skills/` 下创建目录，包含 `SKILL.md` 指令文件和可选的辅助脚本。

### 添加新平台适配器

在 `gateway/platforms/` 下创建适配器文件，继承 `BasePlatformAdapter`，实现 `connect()`、`disconnect()`、`format_message()` 等方法。

### 代码规范

- **PEP 8**，不强制行长度
- **注释**：仅在解释非显而易见的意图、权衡或 API 怪癖时添加
- **错误处理**：捕获具体异常，使用 `logger.warning()`/`logger.error()`，意外错误加 `exc_info=True`
- **路径安全**：使用 `get_hermes_home()` 而非硬编码 `~/.hermes`，使用 `get_config_path()` 而非手动拼接
- **跨平台**：不假设 Unix，`termios`/`fcntl` 需捕获 `ImportError`

### 安全注意事项

| 层级 | 实现 |
|------|------|
| Sudo 密码管道 | 使用 `shlex.quote()` 防止 Shell 注入 |
| 危险命令检测 | `tools/approval.py` 正则匹配 + 硬线命令无条件拦截 |
| Cron 提示注入 | `tools/cronjob_tools.py` 扫描阻断指令覆写模式 |
| 写入拒绝列表 | 保护路径（`~/.ssh/authorized_keys` 等）经 `os.path.realpath()` 解析防符号链接绕过 |
| 技能安全扫描 | `tools/skills_guard.py` 对 Hub 安装技能进行安全检查 |
| 代码执行沙箱 | `execute_code` 子进程剥离 API 密钥 |
| 容器加固 | Docker：丢弃所有能力、禁止提权、PID 限制、tmpfs 大小限制 |

---

## 测试

```bash
pytest tests/ -v                      # 完整测试套件
pytest tests/tools/ -v                # 工具测试
pytest tests/gateway/ -v              # 网关测试
pytest tests/run_agent/ -v            # Agent 核心测试
pytest tests/cli/ -v                  # CLI 测试
pytest -m "not integration" -v        # 跳过需要外部服务的集成测试
```

测试套件使用 `HERMES_HOME` 隔离（`tests/conftest.py` 自动 fixture），不会写入 `~/.hermes/`。

---

## 常见问题

<details>
<summary><strong>CPA 是什么？为什么需要它？</strong></summary>

CLIProxyAPI (CPA) 是一个 OpenAI 兼容的代理服务，将各种 LLM Provider（OpenAI、Anthropic、Google、Kimi 等）统一为一个 API 入口。本 Fork 使用 CPA 作为唯一的模型接入层，简化了 Hermes 侧的 Provider 管理，同时获得了 CPA 的负载均衡、故障转移、多 Key 轮换等能力。
</details>

<details>
<summary><strong>如何更新 CPA？</strong></summary>

```bash
hermes update    # 自动检测并更新 CPA 到最新版本
```

安装脚本（`install.sh` / `install.ps1`）也会自动从 GitHub API 检测最新版本。
</details>

<details>
<summary><strong>如何使用多个 Profile？</strong></summary>

```bash
hermes -p myprofile setup    # 初始化新 Profile
hermes -p myprofile          # 使用该 Profile 启动
```

每个 Profile 拥有完全独立的配置、密钥、记忆和会话。
</details>

<details>
<summary><strong>如何切换模型？</strong></summary>

在聊天中输入 `/model`，或编辑 `~/.hermes/config.yaml`：

```yaml
model:
  default: "claude-sonnet-4-20250514"
  provider: "cliproxyapi"
  base_url: "http://127.0.0.1:8080/v1"
```

模型名称需与 CPA 中配置的模型名一致。
</details>

<details>
<summary><strong>支持哪些终端后端？</strong></summary>

- **local** —— 本地执行（默认）
- **docker** —— Docker 容器隔离
- **ssh** —— 远程 SSH 执行
- **modal** —— Modal 云端执行
- **singularity** —— Singularity 容器
- **daytona** —— Daytona 开发环境

在 `config.yaml` 中设置 `terminal.backend`。
</details>

---

## 致谢与上游

- **上游项目**：[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
- **上游文档**：[hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com/docs/)
- **CPA 项目**：[Fwindy/CLIProxyAPI](https://github.com/Fwindy/CLIProxyAPI)
- **社区**：[Nous Research Discord](https://discord.gg/NousResearch)

### Fork 原则

- 不删除本 Fork 已有魔改功能
- 不整包合并上游大改，只做可验证、低风险同步
- 模型接入只允许 CPA；旧 Provider 直连接口不再作为兜底

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。

Copyright (c) 2025 Nous Research · FearW Fork Modifications
