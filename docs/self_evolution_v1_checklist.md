# 自进化闭环 v1 清单（已落地）

## 目标
- 建立最小闭环：`运行结果采集 -> 指标汇总 -> 基线校验 -> A/B对比`
- 不改现有对外行为，仅增加观测与评测能力。

## 已落地项
- [x] 统一运行结果事件（run outcome）结构化落盘
  - 文件：`agent/evolution_loop.py`
  - 接入：`run_agent.py`（`run_conversation()` 结束路径）
  - 输出：`$HERMES_HOME/evolution/outcomes.jsonl`

- [x] 用户反馈事件能力（v1 API）
  - 文件：`agent/evolution_loop.py`
  - 方法：`record_user_feedback(...)`
  - 输出：`$HERMES_HOME/evolution/feedback.jsonl`

- [x] 指标汇总脚本
  - 文件：`scripts/build_evolution_summary.py`
  - 输出：summary JSON（默认 `$HERMES_HOME/evolution/summary.json`）

- [x] 回归基线校验脚本
  - 文件：`scripts/check_evolution_baseline.py`
  - 基线：`scripts/evolution_baseline.json`
  - 可校验 completion/latency/cost 的回归阈值

- [x] A/B 对比脚本
  - 文件：`scripts/compare_evolution_runs.py`
  - 输入两份 summary，输出关键指标 delta

## 运行方式
- 生成汇总：
  - `python scripts/build_evolution_summary.py`
- 基线校验：
  - `python scripts/check_evolution_baseline.py --summary <summary.json> --baseline scripts/evolution_baseline.json`
- A/B 对比：
  - `python scripts/compare_evolution_runs.py --a <control_summary.json> --b <candidate_summary.json>`

## 下一步（建议）
- [ ] 增加 `/feedback` 命令（CLI/Gateway）把用户评分写入 `feedback.jsonl`
- [ ] 将 `strategy_version` 与 `experiment_bucket` 从 env 迁移到会话级持久字段
- [ ] 在 CI nightly 接入：`build_summary -> baseline_check -> compare_report`

