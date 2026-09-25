import { useMemo, useState } from "react";
import { Badge, Button, ConfigProvider, Drawer, Grid, Layout, Menu, Spin, Typography } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useQuery } from "@tanstack/react-query";
import { get, setToken } from "./api";
import { Dashboard } from "./views/Dashboard";
import { KnowledgeGraph } from "./views/KnowledgeGraph";
import { Wiki } from "./views/Wiki";
import { Map } from "./views/ServiceMap";
import { RepoAdd } from "./views/RepoAdd";
import { Requirements } from "./views/Requirements";
import { Plans } from "./views/Plans";
import { Workbench } from "./views/Workbench";
import { Cases } from "./views/Cases";
import { Runs } from "./views/Runs";
import { Defects } from "./views/Defects";
import { Logs } from "./views/Traces";
import { Quality } from "./views/Quality";
import { Login } from "./views/Login";
import { Jobs } from "./views/Jobs";

const { Sider, Header, Content } = Layout;

export const VIEWS = [
  { key: "dashboard", label: "仪表盘", icon: "📊", milestone: 0 },
  { key: "graph", label: "知识图谱", icon: "🕸", milestone: 5 },
  { key: "wiki", label: "代码库 / Wiki", icon: "📚", milestone: 2 },
  { key: "map", label: "服务地图 / 契约", icon: "🗺", milestone: 4 },
  { key: "repo-add", label: "仓库接入", icon: "📥", milestone: 1 },
  { key: "requirements", label: "需求录入", icon: "📝", milestone: 3 },
  { key: "plans", label: "测试计划", icon: "🎯", milestone: 5 },
  { key: "workbench", label: "生成工作台", icon: "🛠", milestone: 1 },
  { key: "jobs", label: "任务队列", icon: "⚙", milestone: 5 },
  { key: "cases", label: "用例库", icon: "🗂", milestone: 1 },
  { key: "runs", label: "执行记录", icon: "🧪", milestone: 1 },
  { key: "defects", label: "缺陷管理", icon: "🐞", milestone: 5 },
  { key: "logs", label: "日志 / 追溯", icon: "📜", milestone: 5 },
  { key: "quality", label: "需求质量流水线", icon: "⛓", milestone: 3 },
] as const;

export type ViewKey = (typeof VIEWS)[number]["key"];

function currentView(): ViewKey {
  const v = new URLSearchParams(window.location.search).get("view") as ViewKey | null;
  return VIEWS.some((x) => x.key === v) ? (v as ViewKey) : "dashboard";
}

const VIEW_COMPONENTS: Record<ViewKey, () => JSX.Element> = {
  dashboard: Dashboard,
  graph: KnowledgeGraph,
  wiki: Wiki,
  map: Map,
  "repo-add": RepoAdd,
  requirements: Requirements,
  plans: Plans,
  workbench: Workbench,
  jobs: Jobs,
  cases: Cases,
  runs: Runs,
  defects: Defects,
  logs: Logs,
  quality: Quality,
};

interface Me {
  username: string;
  role: string;
}

function Shell() {
  const view = currentView();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [navOpen, setNavOpen] = useState(false);
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => get<Record<string, number>>("/api/stats/summary"),
    refetchInterval: 10000,
  });
  const pendingReviews = stats?.pending_reviews ?? 0;

  const body = useMemo(() => {
    const C = VIEW_COMPONENTS[view];
    return <C />;
  }, [view]);

  const menuItems = VIEWS.map((v) => ({
    key: v.key,
    icon: <span>{v.icon}</span>,
    label:
      v.key === "cases" || v.key === "requirements" ? (
        <Badge count={pendingReviews} size="small" offset={[8, 0]}>
          {v.label}
        </Badge>
      ) : (
        v.label
      ),
  }));
  const onMenuClick = (e: { key: string }) => {
    setNavOpen(false);
    window.location.search = `?view=${e.key}`;
  };
  const logout = () => {
    setToken("");
    window.location.reload();
  };

  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#4f46e5", borderRadius: 6 } }}>
      <Layout style={{ minHeight: "100vh" }}>
        {!isMobile && (
          <Sider theme="dark" width={216}>
            <div style={{ color: "#fff", padding: "18px 16px 12px", fontWeight: 700, fontSize: 17 }}>
              ⚒ TestForge
              <div style={{ fontSize: 11, fontWeight: 400, opacity: 0.65 }}>AI 测试用例生成平台</div>
            </div>
            <Menu theme="dark" mode="inline" selectedKeys={[view]} items={menuItems} onClick={onMenuClick} />
          </Sider>
        )}
        <Layout>
          <Header
            style={{
              background: "#fff",
              borderBottom: "1px solid #eee",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: isMobile ? "0 12px" : "0 24px",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 4, minWidth: 0 }}>
              {isMobile && (
                <Button
                  type="text"
                  aria-label="打开导航菜单"
                  style={{ fontSize: 18, padding: "0 8px" }}
                  onClick={() => setNavOpen(true)}
                >
                  ☰
                </Button>
              )}
              <Typography.Text strong style={{ fontSize: 16 }} ellipsis>
                {isMobile ? "TestForge · " : ""}
                {VIEWS.find((v) => v.key === view)?.label}
              </Typography.Text>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <Badge count={pendingReviews} overflowCount={99}>
                <span style={{ fontSize: 13 }}>待人审</span>
              </Badge>
              <Button type="text" size="small" onClick={logout}>
                退出
              </Button>
            </div>
          </Header>
          <Content style={{ padding: isMobile ? 12 : 20, background: "#f5f6fa", overflow: "auto" }}>{body}</Content>
        </Layout>
        <Drawer
          title={<span style={{ fontWeight: 700 }}>⚒ TestForge</span>}
          placement="left"
          width={280}
          open={navOpen}
          onClose={() => setNavOpen(false)}
          styles={{ body: { padding: 0 } }}
        >
          <Menu
            mode="inline"
            selectedKeys={[view]}
            items={menuItems}
            onClick={onMenuClick}
            style={{ borderInlineEnd: "none" }}
          />
        </Drawer>
      </Layout>
    </ConfigProvider>
  );
}

export function App() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => get<Me>("/api/auth/me"),
    retry: false,
    staleTime: Infinity,
  });

  if (me.isLoading) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Spin size="large" />
      </div>
    );
  }
  if (me.isError || !me.data) {
    return <ConfigProvider locale={zhCN}>{<Login onLogin={() => me.refetch()} />}</ConfigProvider>;
  }
  return <Shell />;
}
