import { useEffect, useMemo, useState } from "react";
import {
  ExternalLink,
  FileKey2,
  KeyRound,
  Link2,
  RefreshCcw,
  Save,
  Server,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  CPAAuthFileItem,
  CPAConfigResponse,
  CPAOAuthProvider,
  CPAOAuthStartResponse,
  CPAOAuthStatusResponse,
  CPAProviderKind,
} from "@/lib/api";
import { useToast } from "@/hooks/useToast";
import { Toast } from "@/components/Toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const PROVIDERS: Array<{ kind: CPAProviderKind; label: string; hint: string }> = [
  { kind: "gemini", label: "Gemini", hint: "Gemini API Key 渠道" },
  { kind: "codex", label: "Codex", hint: "OpenAI / Codex OAuth 或 Key" },
  { kind: "claude", label: "Claude", hint: "Anthropic Claude 渠道" },
  { kind: "vertex", label: "Vertex", hint: "Google Vertex AI 凭据" },
  { kind: "openai", label: "OpenAI 兼容", hint: "任意兼容 OpenAI 的上游" },
];

const OAUTH_PROVIDERS: Array<{ provider: CPAOAuthProvider; label: string; hint: string; needsCallback?: boolean }> = [
  { provider: "codex", label: "Codex", hint: "打开授权链接后自动轮询状态", needsCallback: true },
  { provider: "anthropic", label: "Claude / Anthropic", hint: "Claude 官方 OAuth 登录", needsCallback: true },
  { provider: "gemini-cli", label: "Gemini CLI", hint: "支持复制回调 URL 写入 CPA", needsCallback: true },
  { provider: "kimi", label: "Kimi", hint: "由 CPA 后端处理授权状态" },
  { provider: "antigravity", label: "Antigravity", hint: "保留 CPA 原生 WebUI 登录能力", needsCallback: true },
];

function normalizeCount(payload: unknown): number {
  if (Array.isArray(payload)) return payload.length;
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    for (const key of ["items", "configs", "providers", "keys", "data"]) {
      if (Array.isArray(record[key])) return record[key].length;
    }
    return Object.keys(record).length > 0 ? 1 : 0;
  }
  return 0;
}

function normalizeFiles(payload: unknown): CPAAuthFileItem[] {
  if (Array.isArray(payload)) return payload as CPAAuthFileItem[];
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (Array.isArray(record.files)) return record.files as CPAAuthFileItem[];
    if (Array.isArray(record.items)) return record.items as CPAAuthFileItem[];
  }
  return [];
}

export default function CPAPage() {
  const [config, setConfig] = useState<CPAConfigResponse | null>(null);
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [providerCounts, setProviderCounts] = useState<Partial<Record<CPAProviderKind, number>>>({});
  const [providerRaw, setProviderRaw] = useState<Partial<Record<CPAProviderKind, string>>>({});
  const [providerEdits, setProviderEdits] = useState<Partial<Record<CPAProviderKind, string>>>({});
  const [oauthStarts, setOauthStarts] = useState<Partial<Record<CPAOAuthProvider, CPAOAuthStartResponse>>>({});
  const [oauthStatuses, setOauthStatuses] = useState<Partial<Record<CPAOAuthProvider, CPAOAuthStatusResponse>>>({});
  const [callbackUrls, setCallbackUrls] = useState<Partial<Record<CPAOAuthProvider, string>>>({});
  const [authFiles, setAuthFiles] = useState<CPAAuthFileItem[]>([]);
  const [togglingAuthFiles, setTogglingAuthFiles] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const { toast, showToast } = useToast();

  const managementBase = useMemo(() => baseUrl.replace(/\/?v1\/?$/, ""), [baseUrl]);

  const loadConfig = async () => {
    const data = await api.getCPAConfig();
    setConfig(data);
    setModel(data.model);
    setBaseUrl(data.base_url);
  };

  const loadProviders = async () => {
    const nextCounts: Partial<Record<CPAProviderKind, number>> = {};
    const nextRaw: Partial<Record<CPAProviderKind, string>> = {};
    await Promise.all(PROVIDERS.map(async ({ kind }) => {
      try {
        const data = await api.getCPAProviderConfigs(kind);
        nextCounts[kind] = normalizeCount(data);
        nextRaw[kind] = JSON.stringify(data, null, 2);
      } catch (error) {
        nextRaw[kind] = JSON.stringify({ error: error instanceof Error ? error.message : String(error) }, null, 2);
      }
    }));
    setProviderCounts(nextCounts);
    setProviderRaw(nextRaw);
    setProviderEdits(nextRaw);
  };

  const loadAuthFiles = async () => {
    const data = await api.listCPAAuthFiles();
    setAuthFiles(normalizeFiles(data));
  };

  const refreshAll = async () => {
    setBusy("refresh");
    try {
      await loadConfig();
      await Promise.all([loadProviders(), loadAuthFiles()]);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "CPA 管理信息加载失败", "error");
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    refreshAll();
  }, []);

  const saveTransport = async () => {
    setBusy("transport");
    try {
      const result = await api.saveCPAConfig({ model, base_url: baseUrl, api_key: apiKey || undefined });
      setConfig(result.config);
      setModel(result.config.model);
      setBaseUrl(result.config.base_url);
      setApiKey("");
      showToast("CPA 连接配置已保存", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存 CPA 配置失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const saveProvider = async (kind: CPAProviderKind) => {
    setBusy(`provider-${kind}`);
    try {
      const raw = providerEdits[kind] || "[]";
      await api.saveCPAProviderConfigs(kind, JSON.parse(raw));
      showToast("AI 提供商配置已同步到 CPA", "success");
      await loadProviders();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存提供商失败，请检查 JSON", "error");
    } finally {
      setBusy(null);
    }
  };

  const startOAuth = async (provider: CPAOAuthProvider) => {
    setBusy(`oauth-${provider}`);
    try {
      const data = await api.startCPAOAuth(provider);
      setOauthStarts((prev) => ({ ...prev, [provider]: data }));
      const authUrl = data.url || data.auth_url || data.authUrl || data.login_url || "";
      if (authUrl) {
        window.open(authUrl, "_blank", "noopener,noreferrer");
        showToast("已打开 CPA OAuth 授权链接", "success");
      } else {
        showToast("CPA 已响应，但没有返回授权链接；请检查 CPA 管理端日志", "error");
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "启动 OAuth 失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const pollOAuth = async (provider: CPAOAuthProvider) => {
    const state = oauthStarts[provider]?.state;
    if (!state) return;
    setBusy(`poll-${provider}`);
    try {
      const status = await api.getCPAOAuthStatus(state);
      setOauthStatuses((prev) => ({ ...prev, [provider]: status }));
    } catch (error) {
      showToast(error instanceof Error ? error.message : "查询 OAuth 状态失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const submitCallback = async (provider: CPAOAuthProvider) => {
    const redirectUrl = callbackUrls[provider]?.trim();
    if (!redirectUrl) return;
    setBusy(`callback-${provider}`);
    try {
      await api.submitCPAOAuthCallback(provider, redirectUrl, oauthStarts[provider]?.state);
      showToast("OAuth 回调已提交给 CPA", "success");
      await loadAuthFiles();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "提交回调失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const uploadAuthFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy("upload");
    try {
      await api.uploadCPAAuthFiles(files);
      showToast("认证文件已上传到 CPA", "success");
      await loadAuthFiles();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "上传认证文件失败", "error");
    } finally {
      setBusy(null);
    }
  };

  const toggleAuthFile = async (file: CPAAuthFileItem) => {
    setTogglingAuthFiles((prev) => new Set(prev).add(file.name));
    try {
      await api.setCPAAuthFileStatus(file.name, !Boolean(file.disabled));
      setAuthFiles((prev) =>
        prev.map((item) =>
          item.name === file.name ? { ...item, disabled: !item.disabled } : item
        )
      );
      showToast(`认证文件 ${file.name} 已${file.disabled ? "启用" : "停用"}`, "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : `切换 ${file.name} 失败`, "error");
    } finally {
      setTogglingAuthFiles((prev) => {
        const next = new Set(prev);
        next.delete(file.name);
        return next;
      });
    }
  };

  if (!config) {
    return <div className="flex items-center justify-center py-24"><div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />
      <div className="overflow-hidden rounded-3xl border border-border/80 bg-card/75 shadow-[0_24px_90px_rgba(0,0,0,0.28)] backdrop-blur-sm">
        <div className="flex flex-col gap-5 p-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-border/70 bg-background/40 px-3 py-1 text-xs text-muted-foreground">
              <ShieldCheck className="h-3.5 w-3.5 text-success" />
              Hermes CPA-only 控制台
            </div>
            <h1 className="text-3xl font-semibold tracking-tight text-foreground">CPA 管理中心</h1>
            <p className="mt-3 text-sm leading-7 text-muted-foreground">
              集中管理 AI 提供商、OAuth 登录和认证文件。Hermes 只连接 CPA，由 CPA 接管所有上游渠道与兼容协议。
            </p>
          </div>
          <Button variant="outline" onClick={refreshAll} disabled={busy === "refresh"}>
            <RefreshCcw className="h-4 w-4" />刷新状态
          </Button>
        </div>
        <div className="grid border-t border-border/70 bg-background/20 md:grid-cols-3">
          <div className="border-b border-border/70 p-4 md:border-b-0 md:border-r">
            <p className="text-xs text-muted-foreground">当前提供商</p>
            <p className="mt-1 font-mono-ui text-sm text-foreground">{config.provider}</p>
          </div>
          <div className="border-b border-border/70 p-4 md:border-b-0 md:border-r">
            <p className="text-xs text-muted-foreground">模型入口</p>
            <p className="mt-1 truncate font-mono-ui text-sm text-foreground">{model || "未设置"}</p>
          </div>
          <div className="p-4">
            <p className="text-xs text-muted-foreground">管理地址</p>
            <p className="mt-1 truncate font-mono-ui text-sm text-foreground">{managementBase || "未配置"}</p>
          </div>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Server className="h-5 w-5" />Hermes → CPA 连接</CardTitle>
          <CardDescription>
            这里是 Hermes 唯一模型入口；上游渠道全部交给内置 CPA 管理。保存后 Hermes 会请求 CPA 的兼容 /v1。
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5 md:grid-cols-3">
          <div className="grid gap-2"><Label>模型名</Label><Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="gpt-5(8192)" /></div>
          <div className="grid gap-2"><Label>CPA /v1 地址</Label><Input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8080/v1" /></div>
          <div className="grid gap-2"><Label>CPA API Key</Label><Input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={config.api_key_set ? config.api_key_preview ?? "已设置" : "本地无鉴权可留空"} /></div>
          <div className="flex flex-wrap items-center gap-3 md:col-span-3">
            <Button onClick={saveTransport} disabled={busy === "transport"}><Save className="h-4 w-4" />保存连接</Button>
            <Badge variant="success">{config.provider}</Badge>
            <span className="font-mono-ui text-xs text-muted-foreground">{managementBase || "未配置"}</span>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="providers">
        {(active, setActive) => (
          <>
            <TabsList>
              <TabsTrigger value="providers" active={active === "providers"} onClick={() => setActive("providers")}>AI 提供商</TabsTrigger>
              <TabsTrigger value="oauth" active={active === "oauth"} onClick={() => setActive("oauth")}>OAuth 登录</TabsTrigger>
              <TabsTrigger value="auth-files" active={active === "auth-files"} onClick={() => setActive("auth-files")}>认证文件</TabsTrigger>
            </TabsList>

            {active === "providers" && <div className="grid gap-5 lg:grid-cols-2">
              {PROVIDERS.map(({ kind, label, hint }) => (
                <Card key={kind}>
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2"><KeyRound className="h-5 w-5" />{label}</span>
                      <Badge variant={providerCounts[kind] ? "success" : "outline"}>{providerCounts[kind] ?? 0} 项</Badge>
                    </CardTitle>
                    <CardDescription>{hint}</CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-4">
                    <textarea
                      className="min-h-52 rounded-2xl border border-border/80 bg-background/60 p-4 font-mono-ui text-sm leading-6 outline-none transition-colors placeholder:text-muted-foreground focus:border-foreground/25 focus:ring-1 focus:ring-foreground/30"
                      value={providerEdits[kind] ?? providerRaw[kind] ?? ""}
                      onChange={(event) => setProviderEdits((prev) => ({ ...prev, [kind]: event.target.value }))}
                      placeholder="粘贴或编辑该提供商的 CPA 配置 JSON / Key 列表"
                      spellCheck={false}
                    />
                    <Button size="sm" className="w-fit" onClick={() => saveProvider(kind)} disabled={busy === `provider-${kind}`}><Save className="h-4 w-4" />保存到 CPA</Button>
                  </CardContent>
                </Card>
              ))}
            </div>}

            {active === "oauth" && <div className="grid gap-5 lg:grid-cols-2">
              {OAUTH_PROVIDERS.map(({ provider, label, hint, needsCallback }) => (
                <Card key={provider}>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2"><Link2 className="h-5 w-5" />{label}</CardTitle>
                    <CardDescription>{hint}</CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-4">
                    <div className="flex flex-wrap gap-3">
                      <Button size="sm" onClick={() => startOAuth(provider)} disabled={busy === `oauth-${provider}`}><ExternalLink className="h-4 w-4" />开始登录</Button>
                      <Button size="sm" variant="outline" onClick={() => pollOAuth(provider)} disabled={!oauthStarts[provider]?.state || busy === `poll-${provider}`}>查询状态</Button>
                    </div>
                    {(oauthStarts[provider]?.url || oauthStarts[provider]?.auth_url || oauthStarts[provider]?.authUrl || oauthStarts[provider]?.login_url) && (
                      <Input
                        readOnly
                        value={oauthStarts[provider]?.url || oauthStarts[provider]?.auth_url || oauthStarts[provider]?.authUrl || oauthStarts[provider]?.login_url || ""}
                      />
                    )}
                    {oauthStarts[provider]?.state && <div className="rounded-xl border border-border/60 bg-background/35 px-3 py-2 font-mono-ui text-xs text-muted-foreground">状态：{oauthStarts[provider]?.state}</div>}
                    {oauthStatuses[provider] && <Badge variant={oauthStatuses[provider]?.status === "ok" ? "success" : oauthStatuses[provider]?.status === "error" ? "destructive" : "warning"}>{oauthStatuses[provider]?.status}</Badge>}
                    {needsCallback && <div className="grid gap-2">
                      <Label>回调 URL</Label>
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <Input value={callbackUrls[provider] ?? ""} onChange={(event) => setCallbackUrls((prev) => ({ ...prev, [provider]: event.target.value }))} placeholder="粘贴浏览器最终跳转 URL" />
                        <Button variant="outline" onClick={() => submitCallback(provider)} disabled={busy === `callback-${provider}`}>提交</Button>
                      </div>
                    </div>}
                  </CardContent>
                </Card>
              ))}
            </div>}

            {active === "auth-files" && <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><FileKey2 className="h-5 w-5" />认证文件</CardTitle>
                <CardDescription>只管理 CPA 认证文件池，不接管 Hermes 原有 OAuth 兼容逻辑。</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-5">
                <div className="flex flex-wrap items-center gap-3">
                  <Input type="file" multiple className="max-w-sm" onChange={(event) => uploadAuthFiles(event.target.files)} />
                  <Button variant="outline" size="sm" onClick={loadAuthFiles}><RefreshCcw className="h-4 w-4" />刷新文件</Button>
                  <Badge variant="outline">{authFiles.length} 个文件</Badge>
                </div>
                <div className="overflow-hidden rounded-2xl border border-border/80">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-muted/45 text-xs text-muted-foreground"><tr><th className="p-3">名称</th><th className="p-3">类型</th><th className="p-3">渠道</th><th className="p-3">状态</th><th className="p-3">启停</th><th className="p-3">更新时间</th></tr></thead>
                    <tbody>
                      {authFiles.map((file) => (
                        <tr key={file.name} className="border-t border-border/70 transition-colors hover:bg-foreground/5">
                          <td className="p-3 font-mono-ui">{file.name}</td>
                          <td className="p-3">{file.type ?? "-"}</td>
                          <td className="p-3">{file.channel ?? file.provider ?? "-"}</td>
                          <td className="p-3"><Badge variant={file.disabled ? "outline" : "success"}>{file.disabled ? "停用" : "启用"}</Badge></td>
                          <td className="p-3">
                            <Switch
                              checked={!file.disabled}
                              onCheckedChange={() => toggleAuthFile(file)}
                              disabled={togglingAuthFiles.has(file.name)}
                            />
                          </td>
                          <td className="p-3 text-muted-foreground">{file.modified ?? file.mtime ?? "-"}</td>
                        </tr>
                      ))}
                      {authFiles.length === 0 && <tr><td className="p-4 text-center text-muted-foreground" colSpan={6}><Upload className="mx-auto mb-2 h-4 w-4" />暂无认证文件</td></tr>}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>}
          </>
        )}
      </Tabs>

      <Card>
        <CardContent className="flex items-start gap-2 pt-4 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 h-4 w-4 text-foreground" />
          <span>CPA-only 边界：Hermes 不再直连旧 provider；所有上游渠道、OAuth 和路由都在 CPA 内管理。</span>
        </CardContent>
      </Card>
    </div>
  );
}
