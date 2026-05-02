import { useState, useEffect } from "react";
import { Activity, BarChart3, Clock, FileText, KeyRound, Menu, MessageSquare, Moon, Package, Server, Settings, X } from "lucide-react";
import StatusPage from "@/pages/StatusPage";
import ConfigPage from "@/pages/ConfigPage";
import EnvPage from "@/pages/EnvPage";
import SessionsPage from "@/pages/SessionsPage";
import LogsPage from "@/pages/LogsPage";
import AnalyticsPage from "@/pages/AnalyticsPage";
import CronPage from "@/pages/CronPage";
import SkillsPage from "@/pages/SkillsPage";
import CPAPage from "@/pages/CPAPage";
import DreamPage from "@/pages/DreamPage";

const NAV_ITEMS = [
  { id: "status", label: "仪表盘", hint: "运行状态", icon: Activity },
  { id: "cpa", label: "CPA 管理", hint: "提供商 / OAuth", icon: Server },
  { id: "sessions", label: "会话", hint: "聊天记录", icon: MessageSquare },
  { id: "analytics", label: "分析", hint: "用量趋势", icon: BarChart3 },
  { id: "dream", label: "梦境", hint: "睡眠整理", icon: Moon },
  { id: "logs", label: "日志", hint: "运行输出", icon: FileText },
  { id: "cron", label: "定时任务", hint: "后台计划", icon: Clock },
  { id: "skills", label: "技能", hint: "能力扩展", icon: Package },
  { id: "config", label: "配置", hint: "系统设置", icon: Settings },
  { id: "env", label: "密钥", hint: "环境变量", icon: KeyRound },
] as const;

type PageId = (typeof NAV_ITEMS)[number]["id"];

const PAGE_COMPONENTS: Record<PageId, React.FC> = {
  status: StatusPage,
  sessions: SessionsPage,
  analytics: AnalyticsPage,
  cpa: CPAPage,
  dream: DreamPage,
  logs: LogsPage,
  cron: CronPage,
  skills: SkillsPage,
  config: ConfigPage,
  env: EnvPage,
};

export default function App() {
  const [page, setPage] = useState<PageId>("status");
  const [animKey, setAnimKey] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    setAnimKey((k) => k + 1);
  }, [page]);

  const PageComponent = PAGE_COMPONENTS[page];
  const activeItem = NAV_ITEMS.find((item) => item.id === page) ?? NAV_ITEMS[0];

  const goToPage = (id: PageId) => {
    setPage(id);
    setSidebarOpen(false);
  };

  return (
    <div className="cpa-shell flex min-h-screen bg-background text-foreground">
      <div className="noise-overlay" />
      <div className="warm-glow" />

      {sidebarOpen && (
        <button
          type="button"
          aria-label="关闭导航"
          className="fixed inset-0 z-40 bg-black/20 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`cpa-sidebar fixed inset-y-0 left-0 z-50 flex w-72 flex-col border-r p-4 transition-transform duration-200 lg:sticky lg:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="mb-5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="cpa-brand-mark grid h-11 w-11 place-items-center rounded-2xl text-sm font-bold">
              H
            </div>
            <div>
              <div className="text-base font-extrabold tracking-tight text-foreground">Hermes Agent</div>
              <div className="text-xs font-medium text-muted-foreground">CPA Management Console</div>
            </div>
          </div>
          <button
            type="button"
            className="rounded-xl p-2 text-muted-foreground hover:bg-accent lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="关闭导航"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-4 rounded-2xl border border-border bg-secondary/70 p-3 shadow-sm">
          <div className="text-xs text-muted-foreground">当前入口</div>
          <div className="mt-1 truncate text-sm font-semibold text-foreground">CPA-only /v1</div>
        </div>

        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto pr-1">
          {NAV_ITEMS.map(({ id, label, hint, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => goToPage(id)}
                className={`cpa-nav-item flex w-full items-center gap-3 px-3 py-2.5 text-left transition ${page === id ? "active" : ""}`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold leading-5">{label}</span>
                  <span className="block truncate text-xs text-muted-foreground">{hint}</span>
                </span>
              </button>
          ))}
        </nav>

        <div className="mt-4 rounded-2xl border border-border bg-card p-3 text-xs text-muted-foreground shadow-sm">
          <div className="font-semibold text-foreground">控制台</div>
          <div className="mt-1">轻量、清晰、兼容 CPA 管理体验。</div>
        </div>
      </aside>

      <div className="relative z-2 flex min-w-0 flex-1 flex-col">
        <header className="cpa-topbar sticky top-0 z-30 border-b">
          <div className="flex h-16 items-center justify-between gap-4 px-4 lg:px-8">
            <div className="flex min-w-0 items-center gap-3">
              <button
                type="button"
                className="rounded-xl border border-border bg-card p-2 text-muted-foreground shadow-sm hover:bg-accent lg:hidden"
                onClick={() => setSidebarOpen(true)}
                aria-label="打开导航"
              >
                <Menu className="h-5 w-5" />
              </button>
              <div className="min-w-0">
                <div className="truncate text-lg font-bold tracking-tight text-foreground">{activeItem.label}</div>
                <div className="truncate text-xs text-muted-foreground">{activeItem.hint}</div>
              </div>
            </div>
            <div className="hidden rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground shadow-sm sm:block">
              Hermes × CLIProxyAPI
            </div>
          </div>
        </header>

        <main
          key={animKey}
          className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8"
          style={{ animation: "fade-in 150ms ease-out" }}
        >
          <PageComponent />
        </main>

        <footer className="border-t border-border bg-card/40">
          <div className="mx-auto flex max-w-[1440px] items-center justify-between px-4 py-3 text-xs text-muted-foreground sm:px-6 lg:px-8">
            <span>Hermes Agent</span>
            <span>CPA Management Console</span>
          </div>
        </footer>
      </div>
    </div>
  );
}
