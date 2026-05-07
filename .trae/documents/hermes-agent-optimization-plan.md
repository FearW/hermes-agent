# Hermes Agent 全面优化计划

## 摘要

基于对代码库的深入分析，本计划涵盖 6 大优化方向，共 18 个具体优化项。按优先级从高到低排列，采用激进重构策略，允许拆分大文件、提取公共模块、统一接口等。

---

## 当前状态分析

### 代码规模
- 核心模块 `agent/` 有 17 个 Python 文件
- `agent/auxiliary_client.py` 高达 **3826 行**，承担了过多职责
- `agent/anthropic_adapter.py` **1930 行**
- `agent/streaming/streaming_mixin.py` **1627 行**
- `agent/credential_pool.py` **1594 行**
- 项目总计约 **650 处 `except Exception`** 宽泛异常捕获
- 约 **2531 处 `print()` 语句**（含 skills/scripts，核心模块约 200+ 处）

### 已识别的关键问题
1. **重复代码**：`_OpenAIProxy` 在 `run_agent.py` 和 `agent/auxiliary_client.py` 中完全重复
2. **职责不清**：`auxiliary_client.py` 同时包含客户端路由、错误分类、凭证管理、API 调用等
3. **路径硬编码**：`get_hermes_home() / "config.yaml"` 在 10+ 处重复，已有 `get_config_path()` 但未统一使用
4. **错误处理重复**：`_is_auth_error` 在 `auxiliary_client.py` 和 `mcp_tool.py` 中各自实现
5. **print→logging**：`tools/` 和 `gateway/` 大量使用 `print()` 而非 `logging`
6. **缓存不足**：仅 1 处使用 `lru_cache`，频繁调用的函数缺少缓存

---

## 优化项详细说明

### P0：架构重构（最高优先级）

#### 1. 拆分 `agent/auxiliary_client.py`（3826 行 → 5~6 个模块）

**问题**：该文件承担了客户端路由、错误分类、凭证管理、API 调用、模型温度策略等 6+ 种职责，是项目最大的单一文件。

**方案**：拆分为以下模块：

| 新模块 | 来源行范围 | 职责 |
|--------|-----------|------|
| `agent/auxiliary_client.py` | 保留 2700-3826 | `call_llm()` / `async_call_llm()` 公共 API + 客户端缓存 |
| `agent/client_resolver.py` | 1791-2700 | `resolve_provider_client()`, `_resolve_auto()`, provider chain |
| `agent/client_errors.py` | 1584-1790 | `_is_payment_error()`, `_is_connection_error()`, `_is_auth_error()`, `_is_unsupported_parameter_error()` |
| `agent/client_factory.py` | 100-190, 1896-1950 | `_OpenAIProxy`, `_load_openai_cls()`, `_to_async_client()`, `_to_openai_base_url()` |
| `agent/temperature_policy.py` | 190-217 | `OMIT_TEMPERATURE`, `_fixed_temperature_for_model()`, `_is_kimi_model()` |
| `agent/vision_resolver.py` | 2446-2700 | `resolve_vision_provider_client()`, vision-specific 逻辑 |

**迁移策略**：
- 在 `agent/auxiliary_client.py` 中 re-export 所有公共 API，保持向后兼容
- 逐步将外部 import 迁移到新模块
- 添加 `__all__` 控制公共接口

#### 2. 提取 `_OpenAIProxy` 为共享模块

**问题**：`run_agent.py:L69` 和 `agent/auxiliary_client.py:L81` 完全重复了 `_OpenAIProxy` 类和 `_load_openai_cls()` 函数。

**方案**：
- 将 `_OpenAIProxy` 和 `_load_openai_cls()` 移至 `agent/client_factory.py`（与拆分项 1 合并）
- `run_agent.py` 和 `agent/auxiliary_client.py` 均从 `agent.client_factory` 导入
- 保持 `run_agent.OpenAI` 和 `agent.auxiliary_client.OpenAI` 的模块级名称兼容

#### 3. 统一错误分类函数

**问题**：
- `agent/auxiliary_client.py:L1584-1650` 有 `_is_payment_error`, `_is_connection_error`, `_is_auth_error`
- `tools/mcp_tool.py:L1531` 有独立的 `_is_auth_error`
- `agent/error_classifier.py` 已有结构化的 `FailoverReason` 枚举和 `classify_api_error()`

**方案**：
- 将 `auxiliary_client.py` 中的错误分类函数移至 `agent/client_errors.py`
- 让 `mcp_tool.py` 的 `_is_auth_error` 复用 `agent/client_errors.py` 中的实现
- 逐步让 `client_errors.py` 的内部实现与 `error_classifier.py` 的 `FailoverReason` 对齐

---

### P1：代码质量改善

#### 4. 统一路径访问：消除 `get_hermes_home() / "config.yaml"` 重复

**问题**：以下 10 处直接拼接路径，而 `hermes_constants.get_config_path()` 已存在：
- `tools/website_policy.py:L41`
- `plugins/memory/holographic/__init__.py:L98`
- `hermes_cli/main.py:L230`
- `hermes_cli/config/__init__.py:L252`（重复定义）
- `gateway/session.py:L872`
- `gateway/platforms/telegram.py:L599, L3109`
- `gateway/model_command.py:L30`

**方案**：
- 统一使用 `from hermes_constants import get_config_path`
- 删除 `hermes_cli/config/__init__.py:L250` 的重复定义，改为 re-export
- 同理统一其他常见路径：`get_hermes_home() / ".env"` → `get_env_path()`，`get_hermes_home() / "logs"` → `get_logs_dir()` 等

#### 5. `print()` → `logging` 替换

**问题**：核心模块中大量使用 `print()` 而非 `logging`：
- `tools/terminal_tool.py` — 50 处
- `tools/approval.py` — 15 处
- `tools/browser_tool.py` — 24 处
- `tools/vision_tools.py` — 31 处
- `tools/tts_tool.py` — 12 处
- `gateway/platforms/whatsapp.py` — 30+ 处
- `gateway/session.py` — 3 处
- `run_agent.py` — 1 处

**方案**：
- 每个文件顶部已有 `logger = logging.getLogger(__name__)`，直接替换
- `print(f"[{self.name}] ...")` → `logger.info("...")`
- `print(f"[gateway] Warning: ...")` → `logger.warning("...")`
- 保留 `if __name__ == "__main__"` 块中的 print（CLI 演示用途）
- 分批替换，每批 3~5 个文件

#### 6. 宽泛异常处理规范化

**问题**：项目中有约 650 处 `except Exception` 捕获，部分静默吞掉错误：

关键位置：
- `agent/credential_pool.py:L44` — `except Exception: return None`
- `agent/usage_pricing.py:L389-390` — `except Exception: return None`
- `agent/user_profile.py:L35-36` — `except Exception: return dict(DEFAULT_PROFILE)`
- `agent/transports/chat_completions.py:L446-447` — `except Exception: pass`

**方案**：
- 逐文件审查，将 `except Exception` 缩窄为具体异常类型
- 无法缩窄的，添加 `logger.debug("...", exc_info=True)` 保留堆栈
- 优先处理 `agent/` 和 `tools/` 中的核心路径

#### 7. 统一 `get_hermes_home()` 的导入来源

**问题**：`get_hermes_home()` 从两个位置导入：
- `from hermes_constants import get_hermes_home` — 30+ 处
- `from hermes_cli.config import get_hermes_home` — 14 处

`hermes_cli/config/__init__.py:L247` 已经 re-export 自 `hermes_constants`，但导入路径不统一增加了理解成本和循环依赖风险。

**方案**：
- 全部统一为 `from hermes_constants import get_hermes_home`
- `hermes_cli/config/__init__.py` 保留 re-export 但添加 deprecation 注释
- `tools/process_registry.py:L48` 等从 `hermes_cli.config` 导入的改为从 `hermes_constants` 导入

---

### P2：性能优化

#### 8. 添加 `lru_cache` 缓存频繁调用的纯函数

**问题**：项目中仅 1 处使用 `lru_cache`（`tools/browser_tool.py:L116`），但多个纯函数被频繁调用。

**候选函数**：
- `hermes_constants.get_hermes_home()` — 每次调用都检查环境变量和 Path 构造
- `hermes_constants.get_config_path()` — 每次调用都重新拼接路径
- `agent/model_metadata.fetch_model_metadata()` — 重复查询模型元数据
- `agent/auxiliary_client._normalize_aux_provider()` — 纯字符串处理

**方案**：
- 对 `get_hermes_home()` 添加 `lru_cache(maxsize=1)`，在 `HERMES_HOME` 环境变量不变时缓存结果
- 注意：需提供缓存失效机制（如 `clear_hermes_home_cache()`），供测试和运行时环境变更使用
- 对 `fetch_model_metadata()` 添加 TTL 缓存（如 5 分钟过期）

#### 9. 优化 `time.sleep()` 轮询为事件驱动

**问题**：多处使用 `time.sleep()` 进行轮询：
- `tools/process_registry.py:L615` — `time.sleep(2)` 每 2 秒轮询
- `tools/code_execution_tool.py:L276` — `time.sleep(poll_interval)` 轮询
- `tools/mcp_tool.py:L1616, L1755` — `time.sleep(0.25)` 短间隔轮询

**方案**：
- 对 `process_registry.py` 使用 `threading.Event.wait(timeout=)` 替代 `time.sleep()`
- 对 `code_execution_tool.py` 使用 `asyncio.Event` 或 `threading.Condition`
- 对 `mcp_tool.py` 的短间隔轮询，使用 `threading.Event` 实现可中断等待

#### 10. 客户端缓存优化

**问题**：`agent/auxiliary_client.py:L2717-2719` 的客户端缓存使用简单 dict，无 LRU 淘汰策略：
```python
_client_cache: Dict[tuple, tuple] = {}
_CLIENT_CACHE_MAX_SIZE = 64  # safety belt — 但未实际执行淘汰
```

**方案**：
- 使用 `collections.OrderedDict` 实现简单的 LRU 淘汰
- 在 `_store_cached_client()` 中检查缓存大小，超限时淘汰最旧条目
- 确保淘汰时正确关闭旧客户端连接

---

### P3：接口统一

#### 11. 统一 `_fixed_temperature_for_model` 和 `OMIT_TEMPERATURE` 的导出

**问题**：`_fixed_temperature_for_model` 和 `OMIT_TEMPERATURE` 是 `auxiliary_client.py` 的内部 API（以下划线开头），但被 4 个外部模块延迟导入：
- `trajectory_compressor.py:L70`
- `run_agent.py:L8419, L10158`
- `mini_swe_runner.py:L56`

**方案**：
- 将 `OMIT_TEMPERATURE` 和 `_fixed_temperature_for_model` 移至 `agent/temperature_policy.py`（与拆分项 1 合并）
- 去掉下划线前缀，作为公共 API 导出：`fixed_temperature_for_model()`
- `auxiliary_client.py` re-export 保持兼容

#### 12. 统一 `extract_content_or_reasoning` 的位置

**问题**：`extract_content_or_reasoning()` 定义在 `agent/auxiliary_client.py:L3547`，但被 4 个外部模块导入，它是一个通用的响应解析工具，不属于客户端路由逻辑。

**方案**：
- 移至 `agent/response_utils.py`（新模块）
- 包含 `extract_content_or_reasoning()` 和其他响应解析辅助函数
- `auxiliary_client.py` re-export 保持兼容

---

### P4：测试与安全

#### 13. 补充缺失的测试覆盖

**问题**：以下核心模块缺少对应测试：
- `agent/smart_model_routing.py` — 无测试
- `agent/shell_hooks.py` — 无测试
- `agent/dream_mode.py` — 无测试
- `agent/sleep_mode.py` — 无测试
- `agent/capability_lifecycle.py` — 无测试
- `agent/openai_client_factory.py` — 无测试

**方案**：
- 为每个模块创建 `tests/agent/test_<module>.py`
- 优先覆盖公共 API 和关键错误路径
- 使用 `pytest` + `unittest.mock` 隔离外部依赖

#### 14. 依赖安全审计

**问题**：`pyproject.toml` 中已标注 CVE 修复：
- `requests>=2.33.0` — CVE-2026-25645
- `PyJWT[crypto]>=2.12.0` — CVE-2026-32597

**方案**：
- 运行 `pip-audit` 或 `safety check` 进行全面安全扫描
- 更新所有存在已知漏洞的依赖
- 在 CI 中添加安全扫描步骤

#### 15. 敏感信息脱敏审查

**问题**：`agent/redact.py` 负责敏感信息脱敏，需确保覆盖所有场景。

**方案**：
- 审查 `redact.py` 的正则规则完整性
- 确保 API key、token、密码等在日志和错误消息中均被脱敏
- 添加针对新脱敏规则的单元测试

---

### P5：技术债务清理

#### 16. 清理 TODO 标记

**问题**：代码中存在明确的技术债务 TODO：
- `hermes_cli/providers.py:L285` — "User-defined providers (TODO: Phase 4)"
- `gateway/platforms/yuanbao.py:L4556` — "fetch real chat name/member-count from Yuanbao API"
- `agent/auxiliary_client.py:L2815` — OpenAI SDK TODO 标记

**方案**：
- 评估每个 TODO 的当前状态
- 已完成的删除 TODO 注释
- 未完成的创建 GitHub Issue 跟踪，在代码中引用 Issue 编号

#### 17. 清理备份文件

**问题**：项目中存在备份文件：
- `gateway/config.py.bak_before_official_sync_20260429`
- `hermes_cli/config/__init__.py.bak_cpa_`
- `hermes_cli/config/__init__.py.bak_cpa_patch`

**方案**：
- 确认备份文件不再需要后删除
- 如需保留历史，依赖 git 版本控制

#### 18. 统一 `get_config_path()` 定义

**问题**：`get_config_path()` 在两处定义：
- `hermes_constants.py:L243` — 主定义
- `hermes_cli/config/__init__.py:L250` — 重复定义

**方案**：
- `hermes_cli/config/__init__.py` 改为从 `hermes_constants` re-export
- 所有调用方统一使用 `from hermes_constants import get_config_path`

---

## 实施顺序

| 阶段 | 优化项 | 预计影响 | 风险 |
|------|--------|---------|------|
| 第1阶段 | #1 拆分 auxiliary_client.py, #2 提取 _OpenAIProxy, #3 统一错误分类 | 高 | 中（需保持向后兼容） |
| 第2阶段 | #4 统一路径访问, #7 统一导入来源, #11 统一温度策略导出, #12 统一响应解析 | 中 | 低 |
| 第3阶段 | #5 print→logging 替换, #6 异常处理规范化 | 中 | 低 |
| 第4阶段 | #8 lru_cache 缓存, #9 轮询优化, #10 客户端缓存 LRU | 中 | 中 |
| 第5阶段 | #13 补充测试, #14 安全审计, #15 脱敏审查 | 高 | 低 |
| 第6阶段 | #16 TODO 清理, #17 备份文件清理, #18 统一 get_config_path | 低 | 低 |

---

## 假设与决策

1. **向后兼容**：所有拆分和迁移均通过 re-export 保持旧导入路径可用，后续版本再标记 deprecation
2. **渐进式迁移**：不一次性修改所有调用方，而是先建立新模块，再逐步迁移
3. **测试先行**：每个重构步骤前确保现有测试通过，重构后运行全量测试验证
4. **print 替换范围**：仅替换核心模块（`agent/`, `tools/`, `gateway/`），`skills/` 和 `scripts/` 中的 print 保留
5. **缓存策略**：`get_hermes_home()` 缓存需提供清除机制，避免测试污染

---

## 验证步骤

1. 每个阶段完成后运行 `pytest tests/ -m 'not integration' -n auto`
2. 拆分 `auxiliary_client.py` 后验证所有 `from agent.auxiliary_client import ...` 仍正常工作
3. print→logging 替换后检查日志输出格式一致性
4. 缓存优化后进行性能基准测试对比
5. 安全审计后确认无已知 CVE 漏洞
