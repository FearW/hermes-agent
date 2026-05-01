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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const PROVIDERS: Array<{ kind: CPAProviderKind; label: string; hint: string }> = [
  { kind: "gemini", label: "Gemini", hint: "Gemini API Key 渠道" },
  { kind: "codex", label: "Codex", hint: "OpenAI / Codex OAuth 或 Key" },
  { kind: "claude", label: "Claude", hint: "Anthropic Claude 渠道" },
  { kind: "vertex", label: "Vertex", hint: "Google Vertex AI 凭据" },
  { kind: "openai", label: "OpenAI 兼容", hint: "任意 OpenAI-compatible 上游" },
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
      if (data.url) window.open(data.url, "_blank", "noopener,noreferrer");
      showToast("已向 CPA 请求 OAuth 授权链接", "success");
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
      await api.submitCPAOAuthCallback(provider, redirectUrl);
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

  if (!config) {
    return <div className="flex items-center justify-center py-24"><div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;
  }

  return (
    <div className="flex flex-col gap-4">
      <Toast toast={toast} />
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="font-expanded text-xl font-bold tracking-[0.08em] uppercase blend-lighter">CPA 管理中心</h1>
          <p className="mt-1 font-display text-xs text-muted-foreground">
            借鉴 CPA management 页面，只保留 AI 提供商、OAuth 登录、认证文件；Hermes 只连接 CPA。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refreshAll} disabled={busy === "refresh"}>
          <RefreshCcw className="h-3.5 w-3.5" />刷新
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Server className="h-4 w-4" />Hermes → CPA</CardTitle>
          <CardDescription>这里是 Hermes 唯一模型入口；上游渠道全部交给内置 CPA 管理。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="grid gap-2"><Label>模型名</Label><Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="gpt-5(8192)" /></div>
          <div className="grid gap-2"><Label>CPA /v1 地址</Label><Input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8080/v1" /></div>
          <div className="grid gap-2"><Label>CPA API Key</Label><Input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={config.api_key_set ? config.api_key_preview ?? "已设置" : "本地无鉴权可留空"} /></div>
          <div className="flex items-center gap-2 md:col-span-3">
            <Button onClick={saveTransport} disabled={busy === "transport"}><Save className="h-3.5 w-3.5" />保存连接</Button>
            <Badge variant="success">{config.provider}</Badge>
            <span className="text-xs text-muted-foreground">管理地址：{managementBase || "未配置"}</span>
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

            {active === "providers" && <div className="grid gap-4 lg:grid-cols-2">
              {PROVIDERS.map(({ kind, label, hint }) => (
                <Card key={kind}>
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2"><KeyRound className="h-4 w-4" />{label}</span>
                      <Badge variant={providerCounts[kind] ? "success" : "outline"}>{providerCounts[kind] ?? 0} 项</Badge>
                    </CardTitle>
                    <CardDescription>{hint}</CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-3">
                    <textarea
                      className="min-h-48 rounded-none border border-border bg-background/70 p-3 font-mono text-xs outline-none focus:ring-1 focus:ring-ring"
                      value={providerEdits[kind] ?? providerRaw[kind] ?? ""}
                      onChange={(event) => setProviderEdits((prev) => ({ ...prev, [kind]: event.target.value }))}
                      spellCheck={false}
                    />
                    <Button size="sm" className="w-fit" onClick={() => saveProvider(kind)} disabled={busy === `provider-${kind}`}><Save className="h-3.5 w-3.5" />保存到 CPA</Button>
                  </CardContent>
                </Card>
              ))}
            </div>}

            {active === "oauth" && <div className="grid gap-4 lg:grid-cols-2">
              {OAUTH_PROVIDERS.map(({ provider, label, hint, needsCallback }) => (
                <Card key={provider}>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2"><Link2 className="h-4 w-4" />{label}</CardTitle>
                    <CardDescription>{hint}</CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-3">
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" onClick={() => startOAuth(provider)} disabled={busy === `oauth-${provider}`}><ExternalLink className="h-3.5 w-3.5" />开始登录</Button>
                      <Button size="sm" variant="outline" onClick={() => pollOAuth(provider)} disabled={!oauthStarts[provider]?.state || busy === `poll-${provider}`}>查询状态</Button>
                    </div>
                    {oauthStarts[provider]?.url && <Input readOnly value={oauthStarts[provider]?.url ?? ""} />}
                    {oauthStarts[provider]?.state && <div className="text-xs text-muted-foreground">state: {oauthStarts[provider]?.state}</div>}
                    {oauthStatuses[provider] && <Badge variant={oauthStatuses[provider]?.status === "ok" ? "success" : oauthStatuses[provider]?.status === "error" ? "destructive" : "warning"}>{oauthStatuses[provider]?.status}</Badge>}
                    {needsCallback && <div className="grid gap-2">
                      <Label>回调 URL</Label>
                      <div className="flex gap-2">
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
                <CardTitle className="flex items-center gap-2"><FileKey2 className="h-4 w-4" />认证文件</CardTitle>
                <CardDescription>只管理 CPA 认证文件池，不接管 Hermes 原有 OAuth 兼容逻辑。</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Input type="file" multiple className="max-w-sm" onChange={(event) => uploadAuthFiles(event.target.files)} />
                  <Button variant="outline" size="sm" onClick={loadAuthFiles}><RefreshCcw className="h-3.5 w-3.5" />刷新文件</Button>
                  <Badge variant="outline">{authFiles.length} 个文件</Badge>
                </div>
                <div className="overflow-hidden border border-border">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-muted/40 text-muted-foreground"><tr><th className="p-2">名称</th><th className="p-2">类型</th><th className="p-2">渠道</th><th className="p-2">状态</th><th className="p-2">更新时间</th></tr></thead>
                    <tbody>
                      {authFiles.map((file) => (
                        <tr key={file.name} className="border-t border-border">
                          <td className="p-2 font-mono">{file.name}</td>
                          <td className="p-2">{file.type ?? "-"}</td>
                          <td className="p-2">{file.channel ?? file.provider ?? "-"}</td>
                          <td className="p-2"><Badge variant={file.disabled ? "outline" : "success"}>{file.disabled ? "停用" : "启用"}</Badge></td>
                          <td className="p-2 text-muted-foreground">{file.modified ?? file.mtime ?? "-"}</td>
                        </tr>
                      ))}
                      {authFiles.length === 0 && <tr><td className="p-4 text-center text-muted-foreground" colSpan={5}><Upload className="mx-auto mb-2 h-4 w-4" />暂无认证文件</td></tr>}
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
          <span>无损边界：Hermes 保留旧 provider / OAuth 代码作为兼容兜底；默认路径只通过 CPA。</span>
        </CardContent>
      </Card>
    </div>
  );
}
