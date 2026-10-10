import { useEffect, useMemo, useState } from "react";
import { Avatar, Button, ConfigProvider, Dropdown, Drawer, Grid, Layout, Menu, Typography } from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import {
  AimOutlined,
  ApartmentOutlined,
  ApiOutlined,
  BookOutlined,
  BugOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileDoneOutlined,
  FileTextOutlined,
  FileSearchOutlined,
  LogoutOutlined,
  MoonOutlined,
  SunOutlined,
  NodeIndexOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  MenuUnfoldOutlined,
  DeploymentUnitOutlined,
} from "@ant-design/icons";
import type { ThemeConfig } from "antd";
import { useQuery } from "@tanstack/react-query";
import { get, setToken } from "./api";
import { themes, getInitialThemeMode, THEME_STORAGE_KEY, type ThemeMode } from "./theme";
import { Dashboard } from "./views/Dashboard";
import { KnowledgeGraphHub } from "./views/KnowledgeGraphHub";
import { RetrievalHub } from "./views/RetrievalHub";
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
import { Assistant } from "./views/Assistant";
import { OpenAccess } from "./views/OpenAccess";
import { KnowledgeDocs } from "./views/KnowledgeDocs";
import { Settings } from "./views/Settings";
import { getLang, useLang } from "./i18n";

const { Sider, Header, Content } = Layout;

export const VIEWS = [
  { key: "dashboard", label: "仪表盘", icon: <DashboardOutlined /> },
  { key: "assistant", label: "AI 助手", icon: <RobotOutlined /> },
  // 测试主线六步（按工程旅程顺序）
  { key: "requirements", label: "需求录入", icon: <FileTextOutlined /> },
  { key: "plans", label: "测试计划", icon: <AimOutlined /> },
  { key: "workbench", label: "生成工作台", icon: <ThunderboltOutlined /> },
  { key: "cases", label: "用例库", icon: <DatabaseOutlined /> },
  { key: "runs", label: "执行记录", icon: <ExperimentOutlined /> },
  { key: "defects", label: "缺陷管理", icon: <BugOutlined /> },
  // 知识资产（接入 → 编译 → 文档 → 图谱 → 检索质量）
  { key: "repo-add", label: "仓库接入", icon: <CloudDownloadOutlined /> },
  { key: "wiki", label: "代码库 / Wiki", icon: <BookOutlined /> },
  { key: "knowledge-docs", label: "知识资产", icon: <FileTextOutlined /> },
  { key: "graph", label: "知识图谱", icon: <ApartmentOutlined /> },
  { key: "retrieval", label: "检索中心", icon: <SearchOutlined /> },
  // 质量运营（横切视角）
  { key: "quality", label: "需求质量流水线", icon: <SafetyCertificateOutlined /> },
  { key: "map", label: "服务地图 / 契约", icon: <DeploymentUnitOutlined /> },
  { key: "jobs", label: "任务队列", icon: <SettingOutlined /> },
  { key: "logs", label: "日志 / 追溯", icon: <FileSearchOutlined /> },
  // 系统
  { key: "openaccess", label: "开放接入", icon: <ApiOutlined /> },
  { key: "settings", label: "系统设置", icon: <SettingOutlined /> },
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
  // 总览与智能入口平铺为顶级项
  { key: "g-overview", label: "仪表盘", icon: <DashboardOutlined />, views: ["dashboard"] },
  { key: "g-ai", label: "AI 助手", icon: <RobotOutlined />, views: ["assistant"] },
  // 测试主线：需求 → 计划 → 生成 → 用例 → 执行 → 缺陷，一条旅程走完
  { key: "g-flow", label: "测试流程", icon: <FileDoneOutlined />, views: ["requirements", "plans", "workbench", "cases", "runs", "defects"] },
  // 知识资产：接入 → 资产/管线 → 编译 → 图谱双视图 → 检索中心，知识生产链（5 项）
  { key: "g-knowledge", label: "知识资产", icon: <NodeIndexOutlined />, views: ["repo-add", "knowledge-docs", "wiki", "graph", "retrieval"] },
  // 质量运营：横切视角（需求质量关 / 系统契约 / 作业 / 追溯）
  { key: "g-quality", label: "质量运营", icon: <CheckCircleOutlined />, views: ["quality", "map", "jobs", "logs"] },
  // 系统：开放接入对所有人可见，用户管理仅管理员（view 级 adminOnly 过滤）
  { key: "g-system", label: "系统", icon: <SettingOutlined />, views: ["openaccess", "settings", "users"] },
];

function currentView(): ViewKey {
  const v = new URLSearchParams(window.location.search).get("view") as ViewKey | null;
  return VIEWS.some((x) => x.key === v) ? (v as ViewKey) : "dashboard";
}

const VIEW_COMPONENTS: Record<ViewKey, () => JSX.Element> = {
  dashboard: Dashboard,
  assistant: Assistant,
  graph: KnowledgeGraphHub,
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
  retrieval: RetrievalHub,
  openaccess: OpenAccess,
  "knowledge-docs": KnowledgeDocs,
  settings: Settings,
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

function Shell({ user, mode, onToggleTheme }: { user: Me; mode: ThemeMode; onToggleTheme: () => void }) {
  // 视图切换用 React 状态（SPA），URL 仅作刷新恢复用——绝不全页 reload
  const [view, setView] = useState<ViewKey>(currentView);
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [navOpen, setNavOpen] = useState(false);
  const current = VIEWS.find((v) => v.key === view);

  useEffect(() => {
    // AI 回答里的引用标签（[[Function:x]] 等）通过 tf-navigate 事件请求跳转
    const onNav = (e: Event) => {
      const key = (e as CustomEvent<string>).detail as ViewKey;
      if (VIEWS.some((v) => v.key === key)) {
        setView(key);
        window.history.replaceState(null, "", `?view=${key}`);
      }
    };
    window.addEventListener("tf-navigate", onNav);
    return () => window.removeEventListener("tf-navigate", onNav);
  }, []);

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
    <ConfigProvider locale={getLang() === "en" ? enUS : zhCN} theme={themes[mode] as ThemeConfig}>
      <Layout style={{ minHeight: "100vh" }}>
        {!isMobile && (
          <Sider width={220} className="tf-sider" style={{ overflow: "hidden" }}>
            <Brand />
            <Menu
              theme="light"
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
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Button
                className="tf-theme-toggle"
                type="text"
                aria-label={mode === "dark" ? "切换到亮色主题" : "切换到暗色主题"}
                title={mode === "dark" ? "切换到亮色主题" : "切换到暗色主题"}
                icon={mode === "dark" ? <SunOutlined style={{ fontSize: 15 }} /> : <MoonOutlined style={{ fontSize: 15 }} />}
                onClick={onToggleTheme}
              />
              <Dropdown menu={userMenu} placement="bottomRight">
              <div className="tf-user-chip">
                <Avatar size={26} style={{ background: "var(--tf-primary)", flexShrink: 0 }}>
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
            </div>
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
  // 语言：订阅 i18n store，切换时触发 ConfigProvider locale 联动（antd 组件文案同步）
  useLang();

  // 主题模式：亮色「暖灰杉青」/ 暗色「杉青夜航」，选择持久化并同步 html[data-theme]
  const [mode, setMode] = useState<ThemeMode>(getInitialThemeMode);
  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    localStorage.setItem(THEME_STORAGE_KEY, mode);
  }, [mode]);
  const toggleTheme = () => setMode((m) => (m === "dark" ? "light" : "dark"));

  if (me.isLoading) {
    // 认证校验通常 <100ms：渲染空白而不是全屏 Spin，避免刷新时的转圈动画
    return null;
  }
  if (me.isError || !me.data) {
    return (
      <ConfigProvider locale={zhCN} theme={themes[mode] as ThemeConfig}>
        <Login onLogin={() => me.refetch()} />
      </ConfigProvider>
    );
  }
  return <Shell user={me.data} mode={mode} onToggleTheme={toggleTheme} />;
}
