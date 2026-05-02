import { useEffect, useState } from "react";
import { Moon, Play, RefreshCcw, Sparkles } from "lucide-react";
import { api, type DreamStatusResponse } from "@/lib/api";
import { isoTimeAgo } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Toast } from "@/components/Toast";
import { useToast } from "@/hooks/useToast";

const PROFILE_LABELS: Record<string, string> = {
  off: "关闭",
  light: "浅睡",
  balanced: "平衡梦境",
  deep: "深度梦境",
};

function fmtTime(ts?: number | null): string {
  if (!ts) return "从未运行";
  return isoTimeAgo(new Date(ts * 1000).toISOString());
}

export default function DreamPage() {
  const [status, setStatus] = useState<DreamStatusResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { toast, showToast } = useToast();

  const load = async () => {
    setBusy("load");
    try {
      setStatus(await api.getDreamStatus());
    } catch (error) {
      showToast(error instanceof Error ? error.message : "加载梦境状态失败", "error");
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async (patch: { enabled?: boolean; profile?: string; report_actions?: boolean }) => {
    setBusy("save");
    try {
      const next = await api.saveDreamConfig(patch);
      setStatus(next);
      showToast("梦境配置已保存", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存梦境配置失败", "error");
    } finally {
      setBusy(null);
    }
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
    return <div className="flex items-center justify-center py-24"><div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;
  }

  const last = status.state.last_result;
  const sleep = status.sleep_mode;
  const selectedProfile = status.profile.includes(":") ? status.profile.split(":")[0] : status.profile;

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
                  空闲时自动整理记忆、技能候选和 L4 长期归档；也可以手动触发一次梦境整理。
                </CardDescription>
              </div>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={load} disabled={busy !== null}>
                <RefreshCcw className="h-4 w-4" />刷新
              </Button>
              <Button onClick={runNow} disabled={busy !== null || !status.enabled}>
                <Play className="h-4 w-4" />立即入梦
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">状态</p>
            <Badge variant={status.enabled ? "success" : "outline"} className="mt-2">
              {status.enabled ? "已开启" : "已关闭"}
            </Badge>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">梦境档位</p>
            <p className="mt-2 font-semibold">{PROFILE_LABELS[selectedProfile] ?? selectedProfile}</p>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">上次运行</p>
            <p className="mt-2 font-semibold">{fmtTime(status.state.last_run_at)}</p>
          </div>
          <div className="rounded-2xl border border-border bg-secondary/40 p-4">
            <p className="text-xs text-muted-foreground">累计次数</p>
            <p className="mt-2 font-semibold">{status.state.runs ?? 0}</p>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>梦境配置</CardTitle>
            <CardDescription>推荐保持“平衡梦境”，长期运行更稳。</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5">
            <div className="flex items-center justify-between gap-4 rounded-2xl border border-border p-4">
              <div>
                <Label>启用睡眠整理</Label>
                <p className="mt-1 text-xs text-muted-foreground">关闭后不会自动整理，也不能手动入梦。</p>
              </div>
              <Switch checked={status.enabled} onCheckedChange={(v) => save({ enabled: v })} disabled={busy !== null} />
            </div>
            <div className="grid gap-2">
              <Label>梦境档位</Label>
              <Select value={selectedProfile} onChange={(e) => save({ profile: e.target.value })} disabled={busy !== null}>
                <option value="light">浅睡</option>
                <option value="balanced">平衡梦境</option>
                <option value="deep">深度梦境</option>
                <option value="off">关闭</option>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-4 rounded-2xl border border-border p-4">
              <div>
                <Label>报告整理动作</Label>
                <p className="mt-1 text-xs text-muted-foreground">在网关/后台场景中保留整理摘要。</p>
              </div>
              <Switch checked={Boolean(sleep.report_actions)} onCheckedChange={(v) => save({ report_actions: v })} disabled={busy !== null} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" />梦境结果</CardTitle>
            <CardDescription>梦境会压缩内置记忆、维护技能生命周期，并清理 L4 归档。</CardDescription>
          </CardHeader>
          <CardContent>
            {!last ? (
              <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                还没有运行过梦境。点击“立即入梦”开始第一次整理。
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={last.ok ? "success" : "destructive"}>{last.ok ? "完成" : "异常"}</Badge>
                  <span className="text-sm text-muted-foreground">耗时 {last.duration_seconds ?? 0}s</span>
                  <span className="text-sm text-muted-foreground">原因：{last.reason ?? "manual"}</span>
                </div>
                {last.skipped && <p className="text-sm text-warning">已跳过：{last.skipped}</p>}
                {last.errors?.length ? (
                  <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                    {last.errors.map((err) => <div key={err}>{err}</div>)}
                  </div>
                ) : null}
                <pre className="max-h-[420px] overflow-auto rounded-2xl border border-border bg-background p-4 text-xs leading-5 text-muted-foreground">
                  {JSON.stringify(last.actions ?? {}, null, 2)}
                </pre>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
