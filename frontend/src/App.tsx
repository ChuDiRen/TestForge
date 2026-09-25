import { useMemo } from "react";
import { Badge, ConfigProvider, Layout, Menu, Typography } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useQuery } from "@tanstack/react-query";
import { get } from "./api";
import { Dashboard } from "./views/Dashboard";
import { Placeholder } from "./views/Placeholder";
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

const { Sider, Header, Content } = Layout;

export const VIEWS = [
  { key: "dashboard", label: "仪表盘", icon: "📊", milestone: 0 },
  { key: "wiki", label: "代码库 / Wiki", icon: "📚", milestone: 2 },
  { key: "map", label: "服务地图 / 契约", icon: "🗺", milestone: 4 },
  { key: "repo-add", label: "仓库接入", icon: "📥", milestone: 1 },
  { key: "requirements", label: "需求录入", icon: "📝", milestone: 3 },
  { key: "plans", label: "测试计划", icon: "🎯", milestone: 5 },
  { key: "workbench", label: "生成工作台", icon: "🛠", milestone: 1 },
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

const VIEW_COMPONENTS: Partial<Record<ViewKey, () => JSX.Element>> = {
  dashboard: Dashboard,
  wiki: Wiki,
  map: Map,
  "repo-add": RepoAdd,
  requirements: Requirements,
  plans: Plans,
  workbench: Workbench,
  cases: Cases,
  runs: Runs,
  defects: Defects,
  logs: Logs,
  quality: Quality,
};

export function App() {
  const view = currentView();
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => get<Record<string, number>>("/api/stats/summary"),
    refetchInterval: 10000,
  });
  const pendingReviews = stats?.pending_reviews ?? 0;

  const body = useMemo(() => {
    const meta = VIEWS.find((v) => v.key === view)!;
    const C = VIEW_COMPONENTS[view];
    return C ? <C /> : <Placeholder view={meta.label} milestone={meta.milestone} />;
  }, [view]);

  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#4f46e5", borderRadius: 6 } }}>
      <Layout style={{ minHeight: "100vh" }}>
        <Sider theme="dark" width={216}>
          <div style={{ color: "#fff", padding: "18px 16px 12px", fontWeight: 700, fontSize: 17 }}>
            ⚒ TestForge
            <div style={{ fontSize: 11, fontWeight: 400, opacity: 0.65 }}>AI 测试用例生成平台</div>
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[view]}
            items={VIEWS.map((v) => ({
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
            }))}
            onClick={(e) => {
              window.location.search = `?view=${e.key}`;
            }}
          />
        </Sider>
        <Layout>
          <Header
            style={{
              background: "#fff",
              borderBottom: "1px solid #eee",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0 24px",
            }}
          >
            <Typography.Text strong style={{ fontSize: 16 }}>
              {VIEWS.find((v) => v.key === view)?.label}
            </Typography.Text>
            <Badge count={pendingReviews} overflowCount={99}>
              <span style={{ fontSize: 13 }}>待人审</span>
            </Badge>
          </Header>
          <Content style={{ padding: 20, background: "#f5f6fa", overflow: "auto" }}>{body}</Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
}
