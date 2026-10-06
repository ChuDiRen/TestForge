import { useMemo, useState } from "react";
import { Avatar, Button, ConfigProvider, Dropdown, Drawer, Grid, Layout, Menu, Typography } from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  AimOutlined,
  ApartmentOutlined,
  BookOutlined,
  BugOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileDoneOutlined,
  FileTextOutlined,
  FileSearchOutlined,
  LogoutOutlined,
  NodeIndexOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  MenuUnfoldOutlined,
  DeploymentUnitOutlined,
} from "@ant-design/icons";
import type { ThemeConfig } from "antd";
import { useQuery } from "@tanstack/react-query";
import { get, setToken } from "./api";
import { theme } from "./theme";
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
import { Users } from "./views/Users";
import { Login } from "./views/Login";
import { Jobs } from "./views/Jobs";

const { Sider, Header, Content } = Layout;

export const VIEWS = [
  { key: "dashboard", label: "仪表盘", icon: <DashboardOutlined /> },
  { key: "graph", label: "知识图谱", icon: <ApartmentOutlined /> },
  { key: "wiki", label: "代码库 / Wiki", icon: <BookOutlined /> },
  { key: "map", label: "服务地图 / 契约", icon: <DeploymentUnitOutlined /> },
  { key: "repo-add", label: "仓库接入", icon: <CloudDownloadOutlined /> },
  { key: "requirements", label: "需求录入", icon: <FileTextOutlined /> },
  { key: "plans", label: "测试计划", icon: <AimOutlined /> },
  { key: "workbench", label: "生成工作台", icon: <ThunderboltOutlined /> },
  { key: "jobs", label: "任务队列", icon: <SettingOutlined /> },
  { key: "cases", label: "用例库", icon: <DatabaseOutlined /> },
  { key: "runs", label: "执行记录", icon: <ExperimentOutlined /> },
  { key: "defects", label: "缺陷管理", icon: <BugOutlined /> },
  { key: "logs", label: "日志 / 追溯", icon: <FileSearchOutlined /> },
  { key: "quality", label: "需求质量流水线", icon: <SafetyCertificateOutlined /> },
  { key: "users", label: "用户管理", icon: <TeamOutlined />, adminOnly: true },
] as const;

export type ViewKey = (typeof VIEWS)[number]["key"];

// 侧栏多级菜单分组：单视图组自动平铺为顶级项，多视图组渲染为可展开子菜单
interface MenuGroup {
  key: string;
  label: string;
  icon: JSX.Element;
  views: ViewKey[];
  adminOnly?: boolean;
}

const MENU_GROUPS: MenuGroup[] = [
  { key: "g-overview", label: "总览", icon: <DashboardOutlined />, views: ["dashboard"] },
  { key: "g-knowledge", label: "知识资产", icon: <NodeIndexOutlined />, views: ["graph", "wiki", "map", "repo-add"] },
  { key: "g-flow", label: "测试流程", icon: <FileDoneOutlined />, views: ["requirements", "plans", "workbench", "jobs"] },
  { key: "g-quality", label: "质量运营", icon: <CheckCircleOutlined />, views: ["cases", "runs", "defects", "logs", "quality"] },
  { key: "g-system", label: "系统管理", icon: <TeamOutlined />, views: ["users"], adminOnly: true },
];

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
  users: Users,
};

function Brand({ compact }: { compact?: boolean }) {
  return (
    <div className="tf-brand">
      <div className="tf-brand-mark">TF</div>
      <div>
        <div className="tf-brand-name">TestForge</div>
        {!compact && <div className="tf-brand-sub">AI 测试用例生成平台</div>}
      </div>
    </div>
  );
}

interface Me {
  username: string;
  role: string;
}

function Shell({ user }: { user: Me }) {
  // 视图切换用 React 状态（SPA），URL 仅作刷新恢复用——绝不全页 reload
  const [view, setView] = useState<ViewKey>(currentView);
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [navOpen, setNavOpen] = useState(false);
  const current = VIEWS.find((v) => v.key === view);

  const body = useMemo(() => {
    const C = VIEW_COMPONENTS[view];
    return <C />;
  }, [view]);

  const canSee = (key: ViewKey) => {
    const v = VIEWS.find((x) => x.key === key);
    if (v === undefined) return false;
    return user.role === "admin" || !("adminOnly" in v && v.adminOnly);
  };
  const leafItem = (key: ViewKey) => {
    const v = VIEWS.find((x) => x.key === key)!;
    return { key: v.key, icon: <span style={{ fontSize: 14 }}>{v.icon}</span>, label: v.label };
  };
  const menuItems = MENU_GROUPS.filter((g) => g.views.some(canSee)).map((g) => {
    const children = g.views.filter(canSee).map(leafItem);
    // 单视图组（如 仪表盘 / 系统管理）不必展开，直接平铺为顶级项
    return children.length === 1
      ? children[0]
      : { key: g.key, icon: <span style={{ fontSize: 14 }}>{g.icon}</span>, label: g.label, children };
  });
  // 首屏所在的分组自动展开（仅初始值，之后交由用户手动收展）
  const [defaultOpenKeys] = useState(() => {
    const g = MENU_GROUPS.find((x) => x.views.includes(view));
    return g && g.views.length > 1 ? [g.key] : [];
  });
  const onMenuClick = (e: { key: string }) => {
    const key = e.key as ViewKey;
    setNavOpen(false);
    setView(key);
    window.history.replaceState(null, "", `?view=${key}`);
  };
  const logout = () => {
    setToken("");
    window.location.reload();
  };
  const userMenu = {
    items: [{ key: "logout", icon: <LogoutOutlined />, label: "退出登录" }],
    onClick: logout,
  };

  return (
    <ConfigProvider locale={zhCN} theme={theme as ThemeConfig}>
      <Layout style={{ minHeight: "100vh" }}>
        {!isMobile && (
          <Sider width={220} className="tf-sider" style={{ overflow: "hidden" }}>
            <Brand />
            <Menu
              theme="dark"
              mode="inline"
              selectedKeys={[view]}
              defaultOpenKeys={defaultOpenKeys}
              items={menuItems}
              onClick={onMenuClick}
              style={{ height: "calc(100vh - 72px)", overflowY: "auto", paddingBlock: 6 }}
            />
          </Sider>
        )}
        <Layout>
          <Header className="tf-header">
            <div className="tf-header-view">
              {isMobile && (
                <Button
                  type="text"
                  aria-label="打开导航菜单"
                  icon={<MenuUnfoldOutlined style={{ fontSize: 16 }} />}
                  onClick={() => setNavOpen(true)}
                />
              )}
              {!isMobile && <span className="tf-header-icon">{current?.icon}</span>}
              <Typography.Text strong style={{ fontSize: 15.5 }} ellipsis>
                {isMobile ? "TestForge · " : ""}
                {current?.label}
              </Typography.Text>
            </div>
            <Dropdown menu={userMenu} placement="bottomRight">
              <div className="tf-user-chip">
                <Avatar size={26} style={{ background: "linear-gradient(135deg, #4f46e5, #7c3aed)", flexShrink: 0 }}>
                  {(user.username || "U").slice(0, 1).toUpperCase()}
                </Avatar>
                {!isMobile && (
                  <Typography.Text style={{ fontSize: 12.5 }}>
                    {user.username}
                    <span style={{ color: "var(--tf-ink-2)", marginLeft: 6, fontSize: 11.5 }}>
                      {user.role === "admin" ? "管理员" : "只读"}
                    </span>
                  </Typography.Text>
                )}
              </div>
            </Dropdown>
          </Header>
          <Content className="tf-content">
            <div className="tf-page">{body}</div>
          </Content>
        </Layout>
        <Drawer
          title={<Brand compact />}
          placement="left"
          width={280}
          open={navOpen}
          onClose={() => setNavOpen(false)}
          styles={{ body: { padding: 0 } }}
        >
          <Menu
            mode="inline"
            selectedKeys={[view]}
            defaultOpenKeys={defaultOpenKeys}
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
    // 认证校验通常 <100ms：渲染空白而不是全屏 Spin，避免刷新时的转圈动画
    return null;
  }
  if (me.isError || !me.data) {
    return (
      <ConfigProvider locale={zhCN} theme={theme as ThemeConfig}>
        <Login onLogin={() => me.refetch()} />
      </ConfigProvider>
    );
  }
  return <Shell user={me.data} />;
}
