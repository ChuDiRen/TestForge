# TestForge 主界面视觉方向 · 第 03 轮设计说明

日期：2026-10-07 · 状态：**已选定 C 并实现（支持主题切换）**

## 实现记录（2026-10-07，用户选定 C · 暖灰杉青 + 主题切换）

用户选择：**C · 暖灰杉青**，并要求支持主题切换。已落地到真实前端：

- `src/theme.ts`：重写为双主题 Token——`lightTheme`（暖灰杉青，#0d7d72 主色、#f7f7f5 底、发丝线、小投影）+ `darkTheme`（杉青夜航，darkAlgorithm、#141618 底、亮杉青 #2fb3a4）；`getInitialThemeMode()` 读 localStorage(`tf_theme`)。
- `src/styles.css`：全部换为 CSS 变量（`--tf-*`），`html[data-theme="dark"]` 整站覆盖；删除深空渐变侧栏、光晕背景、渐变主按钮、统计卡角部光晕；侧栏改白纸面 + 发丝线，选中态 = 杉青底 + 左缘 2px 青条；Markdown 代码块与 highlight.js 提供暗色配色。
- `src/App.tsx`：主题状态在 App 根组件，顶栏新增月亮/太阳切换按钮（`.tf-theme-toggle`），选择写入 localStorage 并同步 `data-theme`；`main.tsx` 渲染前先同步主题避免首帧闪烁；Menu 从 dark 改 light（Token 全量接管，暗色下同套变量生效）。
- 视图层硬编码清理：StatCard 渐变芯片→变量平涂（6 tone）、Dashboard/Traces/AIChat 的 geekblue→cyan、Cases 代码查看器深底→中性墨、ServiceMap SVG→`var(--tf-primary)`、KnowledgeGraph 节点色→杉青族、Login 渐变按钮与阴影→品牌色。
- 布局零改动（用户硬约束）：所有视图组件的结构、栅格、组件用法原样保留。

### 验收证据（真实浏览器，`shots/` 目录）

- `light-dashboard/page.png`：亮色仪表盘（杉青选中态、平涂芯片、细线表格）。
- `dark-dashboard/page.png`：点击顶栏月亮按钮后整站转「杉青夜航」。
- `dark-cases/page.png`：暗色下最密集的用例库表格/标签/分页全部跟随。
- `dark-assistant/page.png`：暗色 AI 助手（会话选中态用 `--tf-acc-soft`）。
- `persist-after-reload/page.png`：切暗色后按 F5 刷新，主题保持暗色——localStorage 持久化成立。
- `login/page.png`：登录页换杉青夜色 hero + 杉青主按钮（旧靛蓝阴影已清）。
- `npx tsc -b` 通过；仓库无独立 lint 脚本。截图报告仅剩两类与画面无关项：登录前 /api/auth/me 的 401、AntD `destroyOnClose` 弃用警告（存量问题，未改业务代码）。

## 前两轮的结论（约束演化，留档）

- 第 1 轮（`../round-01/`）：公文/蓝晒/熔炉三个构图方案——被否，"从用户角度有问题"。
- 第 2 轮（`../round-02/`）：清单/审阅台/信号板三个效率骨架——被否，"还是需要重新设计"。
- **第 3 轮的定音锤（用户原话）**："现有的布局没有问题，页面展示风格需要调整一下" + 品质参照 "Linear / Vercel"。
- 结论：**布局不动，只换展示风格**。前两轮动构图全是误伤。

## 本轮做法

三个候选的 HTML 结构与现有 Dashboard **逐字节一致**（侧栏菜单分组 → PageHeader → 待办卡 → 四张指标卡 → 服务状态表），数据全部真实（用例 163 · 通过率 51.2% · Wiki 795 页 · 待办 21 · 服务 8/8 GREEN），只有 `<style>` 块不同——比的纯粹是风格。

## 三个风格

### A · 纸白高对比（Vercel Light，a-vercel-light.html）
- #fafafa 底 + 白卡 + #eaeaea 细线分层（细线代替阴影）；6px 小圆角；黑色是唯一重音，蓝 #0070f3 只给链接/徽标；无渐变无光晕。
- 字体：Segoe UI Variable 栈，标题 650 字重 -0.01em，数字 Consolas tabular。
- 对现状的改动：删紫渐变、删彩色渐变图标底、删大圆角和彩色投影，换黑白灰秩序。

### B · 暗夜石墨（Linear Dark，b-linear.html）
- #0f1011 近黑底 + rgba 发丝线，扁平面板同层堆叠；灰调靛蓝 #5e6ad2 只作点缀（徽标/头像/选中），状态徽标 = 半透明色块。
- 字体：同 A 的栈（暗色下 650 字重）。
- 对现状的改动：整站转 Linear 式暗色，渐变/光晕全删——气质变化最大的一版。

### C · 暖灰杉青（Linear Light 变体，c-warm.html）
- #f7f7f5 暖白纸面 + 米灰细线，12px 圆角；深杉青 #0d7d72 单强调色（选中态/图标底/徽标），侧栏选中 = 青底 + 左缘 2px 青条。
- 字体：**等线（DengXian）标题** + 雅黑正文 + Consolas 数字——亮色里身份感最强的一版。

## 差异检验（已通过）

布局三候选完全相同（本轮的刻意约束）；差异在：色彩身份（无彩+蓝 / 暗夜+灰靛 / 暖灰+杉青）、明暗（两亮一暗但色相族不同）、标题字体（Segoe 650 / Segoe 650 暗色 / 等线）、强调色语言。任意两候选至多共享一项（A/B 共享字体栈，其余全不同）。

## 实现口径

三个风格都能直接落到 `frontend/src/theme.ts`（AntD v5 token）+ `styles.css`（侧栏/卡片/光晕）：选定后改 token 一处提交，16 个视图同时生效，不需要动任何视图组件。动效不新增，沿用 AntD 默认过渡。

## 证据

截图：`shots/a-vercel-light|b-linear|c-warm/page.png`（1280×900）。截图检查仅剩无害 favicon 404，无溢出、无控制台错误。第 1/2 轮产物保留在 `../round-01/`、`../round-02/` 留档。
