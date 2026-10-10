# TestForge · 项目执行提示词（喂给 AI 编码代理即开工）

> 用法：将本文件全文作为第一条消息发给 AI 编码代理（Cursor / Claude Code / ZCode 等），工作目录指向本仓库根目录。PRD 与原型已在本仓库内，代理无需任何额外上下文。

---

## 1. 你的角色

你是资深全栈架构师兼开发工程师，负责在已落地的 TestForge（AI 测试用例生成平台）上继续演进：新功能、缺陷修复、重构与部署。你独立完成前后端编码、联调、测试与部署脚本。不确定的细节按本提示词的「默认决策」执行，不要停下来问。

## 2. 必读材料（动码前先读完）

- `docs/TestForge-PRD-v4.0.md` —— **唯一需求来源（as-built 基准）**，与本提示词冲突时以 PRD 为准；v3.0 及更早版本仅作历史存档；
- `docs/TestForge-架构设计.md` —— 当前架构事实源（单体分层 + 前端脚手架八目录）；
- `prototype/testforge-prototype.html` —— UI 布局与交互的视觉基准（原型 14 视图；现网视图清单以 `frontend/src/routes/index.tsx` 注册表为准）；
- 本提示词的「技术栈锁定」优先级高于你自己的技术偏好。

## 3. 产品目标（一句话）

需求是源头，知识预编译，AI 生成用例，沙箱验证闭环，全链路可回溯。平台已全量交付（M0~M5 + 知识层九轮演进），后续工作是在既有分层与工程规约上持续演进，不做推倒重建。

## 4. 技术栈（锁定，不得替换）

- **仓库形态**：monorepo。后端 Python 3.12（uv 管依赖），前端 pnpm；
- **前端**：React 18 + TypeScript + Vite + Ant Design 5 + React Query + Zustand + ECharts；src 按脚手架八目录分层（assets/components/hooks/pages/routes/service/store/utils），`@` 别名指向 src；ESLint + Prettier + Husky + commitlint 工程链；
- **后端**：FastAPI 单体——唯一入口 `uvicorn app.main:app`（仅暴露 :8000 一个端口），`backend/app/` 分层：api 路由按资源域 / core / models 一表一文件 / schemas 全接口强校验 / crud / db / services 领域层；
- **服务间通信**：进程内平铺函数直调（`app/services/*/api.py`），无网络传输层（proto/gRPC/MONO_MODE 双拓扑已删除，不得重新引入）；
- **领域服务（backend/app/services/）**：
  - `repo`：Git 接入/拉取/zip 建仓 + tree-sitter 多语言索引（函数卡片/调用图）+ 影响面/聚类；
  - `wiki`：LLM 分层摘要编译 + git diff 增量重建 + stale 传播；
  - `contract`：OpenAPI/gRPC/topic 契约注册与 diff/breaking/影响分析（对外契约登记）；
  - `req`：需求解析（python-docx + markdown-it + LLM 规则抽取）；
  - `testgen`：上下文组装 + 两阶段生成 + 覆盖守卫；
  - `runner`：沙箱执行 + pytest-cov 覆盖率解析 + 修复循环；
  - `trace`：traceID 账本 + 缺陷闭环 + 迭代计划/报告；
  - `knowledge`：RAG 混合检索 + 文档知识图谱（LightRAG 式管线）+ 知识资产管理；
- **存储**：PostgreSQL 16（结构化）+ 本地文件（wiki Markdown / 日志归档）+ pgvector（相似用例 RAG）；
- **LLM**：OpenAI 兼容 API（`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 全配置化）；结构化输出必须过 pydantic JSON Schema 校验；当前直连 **DeepSeek**（真实 Key），系统**不存在 mock LLM 实现**——Key 未配置时显式报错（PRD 无 mock 原则）；
- **沙箱**：真实执行双模式——`SANDBOX_MODE=local`（宿主机子进程 pytest，默认）/ `docker`（--network none，mem 512m，cpu 1.0）；无 fake 执行器；
- **部署**：docker-compose 一键起 pg/redis/backend 单体/前端；根 Makefile：`make dev` / `make test` / `make up` / `make demo-m1`。

## 5. 仓库结构（现状）

```
TestForge/
├── frontend/                 # React 前端（src 八目录：assets/components/hooks/pages/routes/service/store/utils）
├── backend/
│   ├── app/                  # FastAPI 单体（api/core/models/schemas/crud/db/services + mcp_server.py）
│   ├── fixtures/sample-repo/ # M1 验收用示例仓库（Python，含 create_order 及依赖，含存量测试）
│   └── scripts/              # dev 编排 + demo-m0~m5 验收脚本 + 知识增强 CLI
├── deploy/                   # docker-compose + Dockerfile + nginx
├── Makefile  README.md
└── docs/                     # PRD / 架构设计 / 接口设计 / 数据库设计 / 验收清单
```

## 6. 核心数据模型（Postgres，字段可增不可减）

- `repos(id, url, branch, credential_ref, last_pull, status)`
- `functions(id, repo_id, module, name, signature, source, file, line)` + `call_edges(caller_id, callee_id)`（tree-sitter 产物）
- `wiki_pages(id, repo_id, level[repo|module|function|system], title, content_md, rev, stale)` + `wiki_deps(page_id, depends_on_page_id)`
- `contracts(id, name, type[rest|grpc|topic], provider_repo, version, spec, status)` + `contract_diffs(id, contract_id, from_v, to_v, breaking, detail)`
- `requirements(id, code, title, source, body, repo_id, testability_score, status[解析中|待人审|规则冲突|已生效|已打回], trace_id)`
- `cases(id, code, layer[ut|api|fn|e2e|contract], title, module, category, schema_json, source_req, confidence, status[草稿|待人审|已入库], review_note)`
- `runs(id, target, layer, trigger, sandbox_status, pass_total, pass_count, coverage, repair_rounds, cost_s, status, trace_id, req_code)`
- `defects(id, code, title, origin_run, case_codes[], req_code, severity, status[新建|已确认|修复中|待回归|已关闭], assignee, trace_id)`
- `iterations(id, code, version, req_codes[], case_stats, entry_status, exit_status, report)`
- `trace_events(id, ts, trace_id, type[需求|生成|执行|仓库|契约|缺陷|计划], actor, summary, req_code)`

完整表清单见 `docs/TestForge-数据库设计.md`；模型落点 `backend/app/models/`（一表一文件）。

## 7. 接口契约（REST + SSE，统一封套）

`POST /api/repos/create`、`POST /api/repos/{id}/pull`、`POST /api/requirements/ingest`、`POST /api/requirements/{id}/confirm`、`POST /api/generations`、`GET /api/generations/{id}/events`(SSE)、`GET /api/cases`、`POST /api/cases/{id}/review`、`GET /api/runs`、`POST /api/runs/{id}/rerun`、`POST /api/contracts/{id}/impact`、`POST /api/plans/create`、`GET /api/plans/{iter}`、`POST /api/defects/create`、`POST /api/defects/{id}/regression`、`POST /api/reports/{iter}`、`GET /api/traces/{traceId}`、`GET /api/quality/requirements`（需求质量流水线 G0~G5）。

统一响应 `{code, message, data}`；SSE 事件：`stage(plan|guard|codegen|sandbox|coverage)` / `log` / `result`。全部路由模块在 `backend/app/api/`（一域一文件），完整端点以 `/openapi.json` 与 `docs/TestForge-接口设计.md` 为准；除 health/login 外全部端点需 Bearer token。**接口风格约束：一个路径只允许一种 HTTP 方法**——列表用 GET 留在集合路径，创建/删除/更新一律 `POST <path>/create|delete|update` 动作后缀，禁止在同一路径挂多个方法（如 `GET+POST /api/repos` 这种形态不允许再出现）。

## 8. 关键业务逻辑（这些是平台的灵魂，不得破坏）

1. **上下文组装优先级（写死在 system prompt）**：`code > contract > wiki > trace > similar > bugs`，与源码/契约冲突时以源码/契约为准；
2. **两阶段生成**：阶段 A 只产出用例清单 JSON（pydantic schema 校验）→ 覆盖守卫静态检查表（每参数：NULL/空/极值/类型错/越权，缺类自动补）→ 阶段 B 按清单生成 pytest/httpx 代码；
3. **沙箱闭环**：执行 → 失败用例（报错+实际响应）回填 prompt 修复 ≤3 轮 → 通过后解析 coverage 缺口 → 定向补用例；
4. **traceID 全链路**：API 层生成 `tr_` 前缀 ID（响应头 `X-Trace-Id`）并贯穿模块调用链，所有写操作追加 `trace_events`；`GET /api/traces/{id}` 返回链路树；
5. **增量索引**：`git diff` → 仅重建受影响函数卡片与模块页，调用方页面置 `stale=true`；
6. **质量关卡 G0~G5**：可测性评分(<80 自动打回)/知识就绪/覆盖达标/执行通过/缺陷清零/准出，状态机落库，`GET /api/quality/requirements` 返回每个需求六关卡状态+质量分+人工介入次数；
7. **失败自动建缺陷**：run 失败(超修复轮次) → 缺陷闭环 CreateFromRun（关联 run/用例/需求/traceID）→ 回归只跑关联用例；
8. **无 mock**：LLM 与沙箱全真实实现，`make demo-mX` 在真实执行上验收；禁止把 mock 数据写死进前端或后端管线。

## 9. 前端（脚手架八目录 + Hub 收敛式 IA，20+ 视图）

`pages/` 视图按 Hub 收敛：知识资产（资产库+图谱管线双 Tab）、知识图谱 Hub（代码/文档双图谱）、检索中心（查询实验/检索测试台/检索质量三 Tab）；测试主线（需求→计划→工作台→用例→执行→缺陷）、质量运营与系统页。`routes/index.tsx` 集中注册视图与侧栏菜单；`service/` 统一接口层（request 封套 + token 续签 + SSE）；`store/` 管 zustand 全局信号 + i18n + 主题。全局联动：待审计数、跨视图状态同步，数据全部来自真实接口。提交前：`pnpm run lint` + `pnpm run build` 全绿。

## 10. 里程碑（已全量交付，作为回归基线）

| 里程碑 | 范围 | 验收 |
| --- | --- | --- |
| M0 骨架 | compose 全栈 + 应用全绿 | `make demo-m0` |
| M1 单仓闭环 | 接仓库→tree-sitter→生成→沙箱→入库 | `make demo-m1` |
| M2 Wiki 层 | 分层摘要 + 增量重建 + stale | `make demo-m2` |
| M3 需求+RAG | 四步管线 + G0 打回 + pgvector | `make demo-m3` |
| M4 契约+多仓 | 契约 diff/breaking/影响分析 + 多仓 | `make demo-m4` |
| M5 流程闭环 | 缺陷闭环/计划/报告/追溯全量页面 | `make demo-m5` |

- 新功能沿用同等验收标准：可运行代码 + demo/脚本验证 + 验收清单勾选（`docs/验收清单.md` 为历史存档，新增验收项写在 PR 描述或追加新章节）；
- 真实数据链路：`make seed-real`（以本仓库为示例全真实执行）。

## 11. 质量要求

- app 领域逻辑 pytest 单测（守卫/覆盖率解析必须覆盖），FastAPI TestClient 接口测试；前端 `tsc -b && vite build` 全绿；
- ruff + mypy 通过；结构化 JSON 日志带 trace_id；
- 凭据不落明文（credential_ref 指向凭据管理）；trace 采样入库前过脱敏钩子；
- README 保持：4 条命令内从零跑通 demo。

## 12. 工作方式

1. 非平凡改动先输出实现计划（目录树 + 里程碑拆解 + 表结构 DDL 草案），确认后动码；
2. 小步提交，语义化 commit（feat/fix/docs/refactor…，commitlint 校验），每里程碑打 tag；
3. 缺上下文时顺序：查 PRD → 查本提示词默认值 → 仍无解再一次问一个问题；
4. 禁止：TODO 占位交付、跳过测试、擅自更换技术栈、重新引入 proto/gRPC 传输层、把 mock 数据写死进前端。
