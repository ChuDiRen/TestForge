# TestForge PRD v2.0（As-Built · 以实测系统为准）

> v1.2 是初始设计稿；v2.0 是**交付后按实际系统回写的需求基准**。两者冲突时以本文档为准。
> 生成日期：2026-09-25。验收证据：里程碑脚本 m0~m5 共 76/76 项、seed_real 34/34 项、pytest 43 项，全部真实通过。

---

## 1. 产品定位（不变）

**AI 测试用例生成平台**：需求是源头，知识预编译，AI 生成用例，真实沙箱验证闭环，全链路可回溯。

三条不可妥协的产品原则（v2.0 起写入宪法，违反即缺陷）：

1. **无 mock 原则**：系统不存在任何确定性假实现。LLM 直连 DeepSeek（真实 Key），沙箱只有真实执行（local 子进程 / docker 容器），期望值来自对真实代码的执行捕获。
2. **现实即规格**：LLM/守卫只设计输入与打桩；断言期望值由探针对真实代码执行捕获，推测断言一律被真实执行结果覆写。
3. **诚实台账**：失败就失败、建缺陷；覆盖率低就显示低；打回的需求门禁如实扣分。不造假绿。

## 2. 系统架构（as-built）

- **形态**：单体优先（`MONO=1`，7 服务进程内 gRPC 直调，仅暴露网关端口）；保留微服务拓扑切换位。
- **后端**：Python 3.13 + FastAPI（gateway）+ 7 个 gRPC 服务（repo / wiki-builder / contract-registry / req / testgen / runner / trace）。
- **前端**：React 18 + TS + Vite + AntD 5 + React Query + ECharts；移动端自适应（抽屉导航/横滚表格）。
- **存储**：PostgreSQL 16（含 pgvector 相似检索）。
- **LLM**：DeepSeek（OpenAI 兼容）+ **deepagents 智能体框架**（领域工具：读源码/调用图/模块清单）。
- **任务队列**：jobs 表持久化 + gateway 内 worker 池（`JOB_WORKERS`，默认 2），FOR UPDATE SKIP LOCKED 抢占，进程崩溃重启自动重排队。
- **认证**：PBKDF2 口令 + HMAC 签名 token（12h），admin 全权 / viewer 只读；SSE 走 `?token=`。
- **部署**：单机 `make dev`；PG 可用 WSL docker（镜像网络 `hostAddressLoopback`）。compose 交付为 post-MVP（见 §8）。

## 3. 用例分层模型（v2.0 核心）

| 层 | 规划来源 | 执行方式 | 断言来源 |
|---|---|---|---|
| **ut 单元** | 精选清单（create_order/sanitize_text）/ 探针输入设计（embed/extract_rules/diff_specs）/ **deepagents 智能体**（任意函数） | 沙箱 pytest，`--cov` 分支覆盖 | 探针对真实代码执行捕获（双跑一致性校验，非确定性用例诚实剔除） |
| **api 接口** | 网关**实时 OpenAPI 契约**推导（健康检查/鉴权读/401/404/参数校验/错误口令） | httpx 对运行中服务真实发请求 | 契约结构断言（状态码/信封/键存在性） |
| **e2e 端到端** | 真实旅程：①登录→数据链一致性 ②需求录入→G0 打回→门禁扣分 | 跨端点多步 httpx 调用链（token 自动传递、跨步骤数据绑定） | 跨步骤真实数据关系 |

辅助机制：
- **覆盖守卫**：静态检查表（NULL/空/极值/类型错/越权），缺类自动补。
- **修复真回填**：失败经 `RegenerateAffected`（探针重捕获）重生成的测试文件**写回沙箱工作区**再执行，≤3 轮；timeline 记录 `regenerated`。
- **置信度分级**（证据规则，非拍脑袋）：0.98 = 期望值有真实执行依据；0.90 = 仅输入设计。
- **已替换机制**：同仓库+同目标+同层重新生成时，旧用例置 `已替换`（保留历史 trace 可查），杜绝用例库重复堆积。
- **覆盖率口径**：只统计被测模块（`cov_pkg`）；web 层用例 `off`（对服务发请求不对检出代码统计）。

## 4. 质量闭环（v2.0 核心）

1. **需求侧**：录入解析 → 可测性评分（<80 自动打回）→ 冲突确认 → 确认生效自动编排生成（目标函数按需求原文关键词路由，**必须在绑定仓库已索引，否则回退仓库内调用边最多的函数**）。
2. **执行侧**：失败超修复轮次 → 自动建缺陷（关联 run/用例/需求/trace，指派"未指派"，**不编造人名**）。
3. **缺陷侧**：AI 修复建议（DeepSeek 基于真实失败用例 + pytest 日志输出根因/建议/复测要点 Markdown）→ 定向回归（只重跑关联用例）→ 通过自动关闭。
4. **变更驱动回归（自愈）**：pull 检出函数源码 diff（含删除/改名）→ 关联用例标 stale → 自动回归（按 code_file 分组真执行）→ 通过清 stale / 失败保持待回归 + 建缺陷；已 stale 用例随下次变更强制重验。webhook（`POST /api/repos/{id}/webhook`，可选密钥）可远程触发。
5. **门禁**：G0 可测性 / G1 知识就绪 / G2 用例覆盖 / G3 执行验证 / G4 缺陷清零（**真实查询未关闭缺陷**）/ G5 准出，加权质量分。

## 5. 功能清单（14 视图 as-built）

仪表盘 · 知识图谱（真实关系图：repo→模块→函数→需求→用例→缺陷）· 代码库/Wiki（增量编译+stale，Markdown 渲染）· 服务地图/契约（注册/diff/breaking/影响分析/受影响重生成完整链）· 仓库接入（file:// 与 Git URL，pull+变更回归）· 需求录入 · 测试计划（准入/准出/报告）· 生成工作台（**层选择器** ut/api/e2e + 批量生成按模块圈选）· 任务队列（持久化台账）· 用例库（**服务端分页** + stale 待回归标记 + JSON 高亮组件）· 执行记录（**产物导出**：下载 .py / 写入仓库检出 `tests/testforge_generated/`）· 缺陷管理（回归验证 + AI 建议 Markdown 抽屉）· 日志/追溯 · 需求质量流水线。

前端基建：登录页、头部用户头像+角色+退出、JSON 语法高亮组件、Markdown 组件、移动端自适应。

## 6. REST API 面（增量摘要，全部过认证）

认证：`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/users`
生成：`POST /api/generations`（layer=ut|api|e2e）、`POST /api/generations/batch`、`GET /api/jobs`、`GET /api/generations/{id}/export`、`POST /api/generations/{id}/export-to-repo`、`GET /api/generations/{id}/events`(SSE)
仓库：`POST /api/repos/{id}/pull`（含变更回归摘要）、`POST /api/repos/{id}/webhook`
缺陷：`GET/POST /api/defects`、`POST /api/defects/{id}/regression|status|suggest`、`GET /api/defects/{id}/suggestion`
其余同 v1.2（需求/用例/执行/契约/计划/报告/追溯/质量/图谱），用例接口新增 `page/page_size/stale` 参数。

## 7. MVP 边界（企业试点口径）

**试点保留（演示与承诺范围）**：仓库接入与索引、ut+api 两层生成、真实沙箱执行+修复回填+覆盖率、缺陷闭环+变更驱动回归、用例/执行/缺陷/报告四台账、认证+任务队列。

**Post-MVP（已实现但试点不承诺稳定性，作差异化展示）**：知识图谱、RAG 相似检索、契约中心+影响分析、需求 G0~G5 流水线、计划/迭代、15 语言索引、e2e 层、移动端。

## 8. 已知问题与路线图（按优先级）

| 级别 | 事项 | 说明 |
|---|---|---|
| P0（MVP 必补） | **部署交付**：compose 一键起全栈 + PG 数据卷 + 备份脚本 | 当前单机 `make dev`，无备份 |
| P0 | **安全基线**：SECRET_KEY/ADMIN_PASSWORD 强制非默认、CORS 收紧、操作日志记操作人 | 现为开发默认值 |
| P1 | **LLM 用量计量**：每次生成记录 tokens/耗时/成本，按仓库汇总 | 企业必问"烧多少钱" |
| P1 | **失败自愈**：job failed 自动重试 1 次 + 手动重试按钮 | 现仅可见 |
| P2 | E2E 旅程 B 每次执行产生一条 `[E2E] 不可测需求样例` 需求行（真实写入，重复执行会累积） | 可按 source=e2e 过滤或接受为证据 |
| P2 | prune_runs 只清 runs，generations/generation_events 无限增长 | 需要生命周期策略 |
| P2 | 批量生成按函数名去重，跨模块同名函数会漏 | 边缘场景 |
| P3 | docker 沙箱默认关闭（local 直跑 = 生成代码在宿主机执行，半可信）；web 层用例在 `--network none` 下不可执行 | 企业内网建议启用 docker 沙箱 |
| P3 | 会话中 token 过期：视图查询报错但不自动跳登录页（刷新后正常） | 轻微 UX |
| 方向 | 图谱 v1.1 渲染换 AntV G6 v5；v1.2 NetworkX PageRank 关键函数评分+社区聚类；v1.3 图谱查询喂给生成智能体（GitNexus 思路原位实现） | 已评审，按需启动 |

## 9. 验收对照

- 里程碑：`make demo-m0`（11/11）~ `demo-m5`（13/13），共 **76/76**；
- 真实数据种子：`make seed-real` **34/34**（ut 41 / api 6 / e2e 2，通过率 100%）；
- 单测 43（含探针语义、修复回填、认证原语、回归映射、web 层规划渲染）；ruff/mypy/前端构建全绿；
- 全链路实证：变更→stale→回归→缺陷→还原→全绿自愈；接口用例首跑即发现真实 API 缺陷（契约 diffs 404）并修复。

## 10. 原型

`prototype/testforge-prototype.html` 已同步至 **v2.0**（14 视图导航、头像顶栏、三层用例层说明），与实测 UI 对应；视觉细节以线上实现为准。
