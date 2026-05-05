import { useEffect, useState } from "react";
import { Moon, Play, RefreshCcw, Sparkles } from "lucide-react";
import { api, type DreamRunResult, type DreamStatusResponse, type DreamTaskStateRow } from "@/lib/api";
import { isoTimeAgo } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Toast } from "@/components/Toast";
import { useToast } from "@/hooks/useToast";
import { Input } from "@/components/ui/input";

const PROFILE_LABELS: Record<string, string> = {
  off: "关闭",
  light: "浅睡",
  balanced: "平衡梦境",
  deep: "深度梦境",
};

/** Human-readable labels for scheduler task keys in last_result.actions */
const TASK_LABELS: Record<string, string> = {
  builtin_memory: "内置记忆压缩",
  capability_lifecycle: "技能/工作流生命周期",
  l4_compaction: "L4 归档瘦身",
  retention_archive: "会话过期归档（网关）",
  l4_periodic_archive: "L4 周期性归档（网关）",
};

/**
 * 档位与代码中的 `_PROFILES`（agent/sleep_mode.py）一致。
 * 数值越小表示唤醒记忆梳理 / 网关维护越频繁，开销越高。
 */
const PROFILE_HELP: { key: string; title: string; body: string }[] = [
  {
    key: "off",
    title: "关闭",
    body:
      "关闭睡眠整理：不写内置记忆压缩、不做后台记忆/技能梳理、不同步外置记忆、不进行网关侧的会话归档与 L4 维护节奏（与配置文件中 sleep_mode.enabled=false 一致）。",
  },
  {
    key: "light",
    title: "浅睡",
    body:
      "最低开销：记忆/技能梳理间隔较长，网关维护周期最长（例如记忆梳理约每 12 轮用户发言、技能梳理约每 20 次工具迭代）。适合模型贵或机器资源紧的场景。",
  },
  {
    key: "balanced",
    title: "平衡梦境（推荐）",
    body:
      "默认档位：在开销与整理频率之间折中（例如约每 8 轮 / 12 次工具迭代触发后台梳理，网关维护适中）。适合长期日常使用。",
  },
  {
    key: "deep",
    title: "深度梦境",
    body:
      "整理最勤快：记忆与技能梳理更频繁，网关归档与 L4 维护更密。适合会话很长、希望记忆与归档跟得更紧的常驻代理（耗电与 API 调用更多）。",
  },
];

function fmtTime(ts?: number | null): string {
  if (!ts) return "从未运行";
  return isoTimeAgo(new Date(ts * 1000).toISOString());
}

function fmtTs(ts?: number | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

function sleepIdleMinutes(sleepMode: Record<string, unknown> | undefined): string {
  const raw = sleepMode?.idle_before_maintenance_seconds;
  const seconds = typeof raw === "number" ? raw : Number(raw ?? 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return "0";
  return String(Math.round(seconds / 60));
}

function actionSummary(actions: DreamRunResult["actions"]): { label: string; detail: string }[] {
  if (!actions || typeof actions !== "object") return [];
  const rows: { label: string; detail: string }[] = [];
  for (const [key, raw] of Object.entries(actions)) {
    const label = TASK_LABELS[key] ?? key;
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const o = raw as Record<string, unknown>;
      const changed = o.changed;
      const skipped = o.skipped;
      const err = o.error;
      let detail = "";
      if (skipped) detail = `已跳过：${skipped}`;
      else if (err) detail = `错误：${String(err)}`;
      else if (changed !== undefined) detail = `变更计数：${String(changed)}`;
      else detail = JSON.stringify(o);
      rows.push({ label, detail });
    } else {
      rows.push({ label, detail: String(raw) });
    }
  }
  return rows;
}

export default function DreamPage() {
  const [status, setStatus] = useState<DreamStatusResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [idleMinutes, setIdleMinutes] = useState("30");
  const { toast, showToast } = useToast();

  const load = async () => {
    setBusy("load");
    try {
      const next = await api.getDreamStatus();
      setStatus(next);
      setIdleMinutes(sleepIdleMinutes(next.sleep_mode));
    } catch (error) {
      showToast(error instanceof Error ? error.message : "加载梦境状态失败", "error");
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async (patch: {
    enabled?: boolean;
    profile?: string;
    report_actions?: boolean;
    idle_before_maintenance_seconds?: number;
  }) => {
    setBusy("save");
    try {
      const next = await api.saveDreamConfig(patch);
      setStatus(next);
      setIdleMinutes(sleepIdleMinutes(next.sleep_mode));
      showToast("梦境配置已保存", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存梦境配置失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const onProfileChange = (value: string) => {
    if (value === "off") {
      void save({ profile: "off", enabled: false });
    } else {
      void save({ profile: value, enabled: true });
    }
  };

  const onEnabledChange = (next: boolean) => {
    void save({ enabled: next });
  };

  const runNow = async () => {
    setBusy("run");
    try {
      const result = await api.runDreamNow();
      await load();
      showToast(result.ok ? "梦境整理完成" : "梦境整理已跳过或部分失败", result.ok ? "success" : "error");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "运行梦境失败", "error");
    } finally {
      setBusy(null);
    }
  };

  if (!status) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  const last = status.state.last_result;
  const sleep = status.sleep_mode;
  const selectedProfile = status.profile.includes(":") ? status.profile.split(":")[0] : status.profile;
  const taskStates = status.task_states ?? (status.state.tasks as Record<string, DreamTaskStateRow> | undefined);

  const saveIdleThreshold = async () => {
    const minutes = Number(idleMinutes);
    if (!Number.isFinite(minutes) || minutes < 0) {
      showToast("静止时长必须是大于或等于 0 的分钟数", "error");
      return;
    }
    await save({ idle_before_maintenance_seconds: Math.round(minutes * 60) });
  };

  const idleSec = Number(sleep.idle_before_maintenance_seconds ?? 0);

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-start gap-3">
              <div className="rounded-2xl border border-border bg-secondary p-3">
                <Moon className="h-6 w-6 text-primary" />
              </div>
              <div>
                <CardTitle className="text-xl">梦境 / 睡眠模式</CardTitle>
                <CardDescription>
                  与配置文件 <code className="text-xs">sleep_mode</code> 及 CLI{" "}
                  <code className="text-xs">/sleep</code>、<code className="text-xs">/dream</code>{" "}
                  使用同一套逻辑。下方开关与档位会写入{" "}
                  <code className="text-xs">~/.hermes/config.yaml</code>。
                </CardDescription>
              </div>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={load} disabled={busy !== null}>
                <RefreshCcw className="h-4 w-4" />
                刷新
              </Button>
              <Button onClick={runNow} disabled={busy !== null || !status.enabled}>
                <Play className="h-4 w-4" />
                立即入梦
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">睡眠开关</p>
            <div className="mt-2 flex items-center gap-2">
              <Switch checked={status.enabled} onCheckedChange={onEnabledChange} disabled={busy !== null} />
              <span className="text-sm font-medium">{status.enabled ? "已开启" : "已关闭"}</span>
            </div>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">梦境档位</p>
            <p className="mt-2 font-semibold">{PROFILE_LABELS[selectedProfile] ?? selectedProfile}</p>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">上次整理结束</p>
            <p className="mt-2 font-semibold">{fmtTime(last?.finished_at ?? status.state.last_run_at)}</p>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">累计次数</p>
            <p className="mt-2 font-semibold">{status.state.runs ?? 0}</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">档位说明</CardTitle>
          <CardDescription>不同档位主要差在「多久做一次后台记忆梳理 / 技能梳理 / 网关归档」——频率越高，消耗越大。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          {PROFILE_HELP.map((h) => (
            <div key={h.key} className="rounded-2xl border border-border bg-secondary/30 p-4">
              <p className="font-semibold text-foreground">{h.title}</p>
              <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{h.body}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>梦境配置（与 Web API 对齐）</CardTitle>
            <CardDescription>
              「静止多久后自动整理」仅作用于<strong className="text-foreground">网关</strong>
              侧的定时维护（会话空闲达到该时长后才跑归档类任务）。在此页点击「立即入梦」不受该闲置门限制。
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5">
            <p className="text-xs text-muted-foreground rounded-xl border border-dashed border-border bg-secondary/20 p-3">
              睡眠总开关见上方概览卡片。关闭后：后台记忆/技能梳理与网关侧 sleep 维护按配置停止；重新开启时若配置文件里档位仍为「关闭」，会自动切回「平衡梦境」。
            </p>
            <div className="grid gap-2">
              <Label>梦境档位（sleep_mode.profile）</Label>
              <Select
                value={
                  ["light", "balanced", "deep", "off"].includes(selectedProfile)
                    ? selectedProfile
                    : "balanced"
                }
                onChange={(e) => onProfileChange(e.target.value)}
                disabled={busy !== null}
              >
                <option value="light">浅睡 — 最低开销</option>
                <option value="balanced">平衡梦境 — 推荐</option>
                <option value="deep">深度梦境 — 更频繁整理</option>
                <option value="off">关闭 — 等同关闭睡眠</option>
              </Select>
              <p className="text-xs text-muted-foreground">
                当前已保存：idle 门限 {idleSec <= 0 ? "0（不等待空闲）" : `${Math.round(idleSec / 60)} 分钟`}。
              </p>
            </div>
            <div className="flex items-center justify-between gap-4 rounded-2xl border border-border p-4">
              <div>
                <Label>报告整理动作（report_actions）</Label>
                <p className="mt-1 text-xs text-muted-foreground">在网关等场景输出简短「记忆已更新」类摘要。</p>
              </div>
              <Switch
                checked={Boolean(sleep.report_actions)}
                onCheckedChange={(v) => save({ report_actions: v })}
                disabled={busy !== null}
              />
            </div>
            <div className="grid gap-3 rounded-2xl border border-border p-4">
              <div>
                <Label>静止多久后允许网关自动整理（idle_before_maintenance_seconds）</Label>
                <p className="mt-1 text-xs text-muted-foreground">
                  自最近一次会话活动起算；填 <strong>0</strong> 表示不等待空闲。修改后需<strong>保存</strong>；网关进程一般会读配置，极端情况需重启网关。
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  type="number"
                  min="0"
                  step="1"
                  value={idleMinutes}
                  onChange={(event) => setIdleMinutes(event.target.value)}
                  disabled={busy !== null}
                />
                <span className="text-sm text-muted-foreground">分钟</span>
                <Button variant="outline" onClick={saveIdleThreshold} disabled={busy !== null}>
                  保存
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                上一次睡眠/梦境整理结果
              </CardTitle>
              <CardDescription>
                数据来自本机 <code className="text-xs">$HERMES_HOME/dream_state.json</code>
                。包含网页「立即入梦」、CLI <code className="text-xs">/dream</code> 与网关侧调度写入的最新一次结果。
              </CardDescription>
            </CardHeader>
            <CardContent>
              {!last ? (
                <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                  还没有运行过梦境整理。点击「立即入梦」开始第一次整理。
                </div>
              ) : (
                <div className="grid gap-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={last.ok ? "success" : "destructive"}>{last.ok ? "完成" : "异常"}</Badge>
                    <span className="text-sm text-muted-foreground">耗时 {last.duration_seconds ?? 0}s</span>
                    <span className="text-sm text-muted-foreground">原因：{last.reason ?? "manual"}</span>
                    {last.finished_at != null && (
                      <span className="text-sm text-muted-foreground">结束时间：{fmtTs(last.finished_at)}</span>
                    )}
                  </div>
                  {last.skipped && (
                    <p className="text-sm text-amber-600 dark:text-amber-400">已跳过：{last.skipped}</p>
                  )}
                  {last.errors?.length ? (
                    <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                      {last.errors.map((err) => (
                        <div key={err}>{err}</div>
                      ))}
                    </div>
                  ) : null}

                  {actionSummary(last.actions).length > 0 && (
                    <div className="rounded-2xl border border-border overflow-hidden">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-border bg-secondary/50 text-left">
                            <th className="p-3 font-medium">任务</th>
                            <th className="p-3 font-medium">结果</th>
                          </tr>
                        </thead>
                        <tbody>
                          {actionSummary(last.actions).map((row) => (
                            <tr key={row.label} className="border-b border-border/60 last:border-0">
                              <td className="p-3 align-top text-muted-foreground">{row.label}</td>
                              <td className="p-3 align-top font-mono text-xs">{row.detail}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <details className="rounded-2xl border border-border bg-background/50">
                    <summary className="cursor-pointer px-4 py-3 text-sm font-medium">原始 JSON（调试）</summary>
                    <pre className="max-h-[320px] overflow-auto border-t border-border p-4 text-xs leading-5 text-muted-foreground">
                      {JSON.stringify(last, null, 2)}
                    </pre>
                  </details>
                </div>
              )}
            </CardContent>
          </Card>

          {taskStates && Object.keys(taskStates).length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">各任务调度状态</CardTitle>
                <CardDescription>来自最近一次写入 state 的调度器快照（退避节奏 / 上次变更计数等）。</CardDescription>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left">
                      <th className="py-2 pr-4 font-medium">任务</th>
                      <th className="py-2 pr-4 font-medium">当前周期(s)</th>
                      <th className="py-2 pr-4 font-medium">连续空跑</th>
                      <th className="py-2 font-medium">上次变更</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(taskStates).map(([name, row]) => (
                      <tr key={name} className="border-b border-border/60 last:border-0">
                        <td className="py-2 pr-4">{TASK_LABELS[name] ?? name}</td>
                        <td className="py-2 pr-4 font-mono text-xs">{row.current_cadence_s ?? "—"}</td>
                        <td className="py-2 pr-4 font-mono text-xs">{row.consecutive_empty ?? "—"}</td>
                        <td className="py-2 font-mono text-xs">{row.last_changed ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
