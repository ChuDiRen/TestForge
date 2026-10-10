/** routes 路由层：视图注册表 + 侧栏菜单分组 + ?view= URL 解析。
 *  SPA 内切换走 React 状态，URL 仅作刷新恢复；跨页跳转统一派发 tf-navigate CustomEvent。 */
import type { ReactElement } from 'react'
import {
  AimOutlined,
  ApartmentOutlined,
  ApiOutlined,
  BookOutlined,
  BugOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  FileDoneOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  NodeIndexOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Assistant } from '@/pages/Assistant'
import { Cases } from '@/pages/Cases'
import { Dashboard } from '@/pages/Dashboard'
import { Defects } from '@/pages/Defects'
import { Jobs } from '@/pages/Jobs'
import { KnowledgeDocs } from '@/pages/KnowledgeDocs'
import { KnowledgeGraphHub } from '@/pages/KnowledgeGraphHub'
import { Logs } from '@/pages/Traces'
import { Map } from '@/pages/ServiceMap'
import { OpenAccess } from '@/pages/OpenAccess'
import { Plans } from '@/pages/Plans'
import { Quality } from '@/pages/Quality'
import { RepoAdd } from '@/pages/RepoAdd'
import { Requirements } from '@/pages/Requirements'
import { RetrievalHub } from '@/pages/RetrievalHub'
import { Runs } from '@/pages/Runs'
import { Settings } from '@/pages/Settings'
import { Users } from '@/pages/Users'
import { Wiki } from '@/pages/Wiki'
import { Workbench } from '@/pages/Workbench'

export const VIEWS = [
  { key: 'dashboard', label: '仪表盘', icon: <DashboardOutlined /> },
  { key: 'assistant', label: 'AI 助手', icon: <RobotOutlined /> },
  // 测试主线六步（按工程旅程顺序）
  { key: 'requirements', label: '需求录入', icon: <FileTextOutlined /> },
  { key: 'plans', label: '测试计划', icon: <AimOutlined /> },
  { key: 'workbench', label: '生成工作台', icon: <ThunderboltOutlined /> },
  { key: 'cases', label: '用例库', icon: <DatabaseOutlined /> },
  { key: 'runs', label: '执行记录', icon: <ExperimentOutlined /> },
  { key: 'defects', label: '缺陷管理', icon: <BugOutlined /> },
  // 知识资产（接入 → 编译 → 文档 → 图谱 → 检索质量）
  { key: 'repo-add', label: '仓库接入', icon: <CloudDownloadOutlined /> },
  { key: 'wiki', label: '代码库 / Wiki', icon: <BookOutlined /> },
  { key: 'knowledge-docs', label: '知识资产', icon: <FileTextOutlined /> },
  { key: 'graph', label: '知识图谱', icon: <ApartmentOutlined /> },
  { key: 'retrieval', label: '检索中心', icon: <SearchOutlined /> },
  // 质量运营（横切视角）
  { key: 'quality', label: '需求质量流水线', icon: <SafetyCertificateOutlined /> },
  { key: 'map', label: '服务地图 / 契约', icon: <DeploymentUnitOutlined /> },
  { key: 'jobs', label: '任务队列', icon: <SettingOutlined /> },
  { key: 'logs', label: '日志 / 追溯', icon: <FileSearchOutlined /> },
  // 系统
  { key: 'openaccess', label: '开放接入', icon: <ApiOutlined /> },
  { key: 'settings', label: '系统设置', icon: <SettingOutlined /> },
  { key: 'users', label: '用户管理', icon: <TeamOutlined />, adminOnly: true },
] as const

export type ViewKey = (typeof VIEWS)[number]['key']

// 侧栏多级菜单分组：单视图组自动平铺为顶级项，多视图组渲染为可展开子菜单
interface MenuGroup {
  key: string
  label: string
  icon: ReactElement
  views: ViewKey[]
  adminOnly?: boolean
}

const MENU_GROUPS: MenuGroup[] = [
  // 总览与智能入口平铺为顶级项
  { key: 'g-overview', label: '仪表盘', icon: <DashboardOutlined />, views: ['dashboard'] },
  { key: 'g-ai', label: 'AI 助手', icon: <RobotOutlined />, views: ['assistant'] },
  // 测试主线：需求 → 计划 → 生成 → 用例 → 执行 → 缺陷，一条旅程走完
  { key: 'g-flow', label: '测试流程', icon: <FileDoneOutlined />, views: ['requirements', 'plans', 'workbench', 'cases', 'runs', 'defects'] },
  // 知识资产：接入 → 资产/管线 → 编译 → 图谱双视图 → 检索中心，知识生产链（5 项）
  { key: 'g-knowledge', label: '知识资产', icon: <NodeIndexOutlined />, views: ['repo-add', 'knowledge-docs', 'wiki', 'graph', 'retrieval'] },
  // 质量运营：横切视角（需求质量关 / 系统契约 / 作业 / 追溯）
  { key: 'g-quality', label: '质量运营', icon: <CheckCircleOutlined />, views: ['quality', 'map', 'jobs', 'logs'] },
  // 系统：开放接入对所有人可见，用户管理仅管理员（view 级 adminOnly 过滤）
  { key: 'g-system', label: '系统', icon: <SettingOutlined />, views: ['openaccess', 'settings', 'users'] },
]

export function currentView(): ViewKey {
  const v = new URLSearchParams(window.location.search).get('view') as ViewKey | null
  return VIEWS.some((x) => x.key === v) ? (v as ViewKey) : 'dashboard'
}

export const VIEW_COMPONENTS: Record<ViewKey, () => ReactElement> = {
  dashboard: Dashboard,
  assistant: Assistant,
  graph: KnowledgeGraphHub,
  wiki: Wiki,
  map: Map,
  'repo-add': RepoAdd,
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
  'knowledge-docs': KnowledgeDocs,
  settings: Settings,
  users: Users,
}

/** 组装侧栏菜单项：按角色过滤 adminOnly 视图，单视图组平铺为顶级项 */
export function buildMenuItems(role: string) {
  const canSee = (key: ViewKey) => {
    const v = VIEWS.find((x) => x.key === key)
    if (v === undefined) return false
    return role === 'admin' || !('adminOnly' in v && v.adminOnly)
  }
  const leafItem = (key: ViewKey) => {
    const v = VIEWS.find((x) => x.key === key)!
    return { key: v.key, icon: <span style={{ fontSize: 14 }}>{v.icon}</span>, label: v.label }
  }
  return MENU_GROUPS.filter((g) => g.views.some(canSee)).map((g) => {
    const children = g.views.filter(canSee).map(leafItem)
    // 单视图组（如 仪表盘 / 系统管理）不必展开，直接平铺为顶级项
    return children.length === 1 ? children[0] : { key: g.key, icon: <span style={{ fontSize: 14 }}>{g.icon}</span>, label: g.label, children }
  })
}

/** 首屏所在分组自动展开（仅初始值，之后交由用户手动收展） */
export function defaultOpenKeysFor(view: ViewKey): string[] {
  const g = MENU_GROUPS.find((x) => x.views.includes(view))
  return g && g.views.length > 1 ? [g.key] : []
}
