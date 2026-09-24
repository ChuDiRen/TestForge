# TestForge · 项目执行提示词（喂给 AI 编码代理即开工）

> 用法：将本文件全文作为第一条消息发给 AI 编码代理（Cursor / Claude Code / ZCode 等），工作目录指向本仓库根目录。PRD 与原型已在本仓库内，代理无需任何额外上下文。

---

## 1. 你的角色

你是资深全栈架构师兼开发工程师，负责把 TestForge（AI 测试用例生成平台）从 PRD 落地为可运行系统。你独立完成前后端编码、联调、测试与部署脚本。不确定的细节按本提示词的「默认决策」执行，不要停下来问。

## 2. 必读材料（动码前先读完）

- `docs/TestForge-PRD-v1.2.md` —— 唯一需求来源，与本提示词冲突时以 PRD 为准；
- `prototype/testforge-prototype.html` —— UI 布局与交互的视觉基准（12 个视图，浏览器打开对照）；
- 本提示词的「技术栈锁定」优先级高于你自己的技术偏好。

## 3. 产品目标（一句话）

需求是源头，知识预编译，AI 生成用例，沙箱验证闭环，全链路可回溯。本次交付**可本地一键运行的 M1+M2**（单仓闭环 + Wiki 层），架构上预留 M3~M5 扩展位。

## 4. 技术栈（锁定，不得替换）

- **仓库形态**：monorepo。后端 Python 3.12（uv 管依赖），前端 pnpm workspace；
- **前端**：React 18 + TypeScript + Vite + Ant Design 5 + React Query + Zustand + ECharts；
- **网关 gateway**：Python 3.12 + FastAPI。对外 HTTP/REST + SSE（生成进度流式），对内 gRPC 客户端；
- **服务间通信**：gRPC（grpcio + grpcio-tools），proto 统一放 `proto/` 目录，`make proto` 一次生成两端 stub；
- **微服务（Python 3.12）**：
  - `repo-svc`：Git 接入/拉取/webhook（GitPython 或子命令）；
  - `wiki-builder`：tree-sitter（py-tree-sitter + tree-sitter-languages）解析 + LLM 分层摘要；
  - `contract-registry`：OpenAPI/proto/topic 注册与 diff；
  - `req-svc`：需求解析（python-docx + markdown-it + LLM 规则抽取）；
  - `testgen-svc`：上下文组装 + 两阶段生成 + 覆盖守卫；
  - `runner-svc`：docker-py 沙箱执行 + pytest-cov 覆盖率解析 + 修复循环；
  - `trace-svc`：traceID 日志追加写（Postgres）；
- **存储**：PostgreSQL 16（结构化）+ 本地文件（wiki Markdown / 日志归档）+ pgvector（相似用例 RAG）；
- **LLM**：OpenAI 兼容 API（`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 全配置化）；结构化输出必须过 pydantic JSON Schema 校验；**必须提供 mock 实现**（`LLM_MODE=mock` 返回固定样例），保证无 Key 也能全流程跑通；
- **沙箱**：docker（--network none，mem 512m，cpu 1.0），同样提供 `SANDBOX_MODE=fake` 假执行器；
- **部署**：docker-compose 一键起 pg/redis/全部服务/前端；根 Makefile：`make dev` / `make proto` / `make test` / `make up` / `make demo-m1`。

## 5. 仓库结构（按此创建）

```
TestForge/
├── proto/                    # 全部 .proto，唯一事实源
├── gateway/                  # FastAPI 网关：REST + SSE + 鉴权桩
├── services/
│   ├── repo-svc/  wiki-builder/  contract-registry/
│   ├── req-svc/   testgen-svc/   runner-svc/  trace-svc/
│   └── shared/               # 公共库：trace 透传、LLM 客户端、DB、配置
├── frontend/                 # React 12 视图
├── fixtures/sample-repo/     # M1 验收用示例仓库（Python，含 create_order 及依赖，含存量测试）
├── deploy/docker-compose.yml
├── Makefile  README.md
└── docs/                     # PRD 与本文件
```

## 6. 核心数据模型（Postgres，按此建表，字段可增不可减）

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

## 7. 接口契约（REST 全部实现，逐条对照 PRD 3.2）

`POST /api/repos`、`POST /api/repos/{id}/pull`、`POST /api/requirements/ingest`、`POST /api/requirements/{id}/confirm`、`POST /api/generations`、`GET /api/generations/{id}/events`(SSE)、`GET /api/cases`、`POST /api/cases/{id}/review`、`GET /api/runs`、`POST /api/runs/{id}/rerun`、`POST /api/contracts/{id}/impact`、`POST /api/plans`、`GET /api/plans/{iter}`、`POST /api/defects`、`POST /api/defects/{id}/regression`、`POST /api/reports/{iter}`、`GET /api/traces/{traceId}`、`GET /api/quality/requirements`（需求质量流水线 G0~G5）。

统一响应 `{code, message, data}`；SSE 事件：`stage(plan|guard|codegen|sandbox|coverage)` / `log` / `result`。proto 按 PRD 3.3（WikiBuilder / ContractRegistry / TestGen / TestRunner / ReqIngest / TraceLog / DefectSvc / PlanSvc）。

## 8. 关键业务逻辑（这些是平台的灵魂，按此实现）

1. **上下文组装优先级（写死在 system prompt）**：`code > contract > wiki > trace > similar > bugs`，与源码/契约冲突时以源码/契约为准；
2. **两阶段生成**：阶段 A 只产出用例清单 JSON（pydantic schema 校验）→ 覆盖守卫静态检查表（每参数：NULL/空/极值/类型错/越权，缺类自动补）→ 阶段 B 按清单生成 pytest/httpx 代码；
3. **沙箱闭环**：执行 → 失败用例（报错+实际响应）回填 prompt 修复 ≤3 轮 → 通过后解析 coverage 缺口 → 定向补用例；
4. **traceID 全链路**：网关生成 `tr_` 前缀 ID 并透传到每个服务，所有写操作追加 `trace_events`；`GET /api/traces/{id}` 返回链路树；
5. **增量索引**：`git diff` → 仅重建受影响函数卡片与模块页，调用方页面置 `stale=true`；
6. **质量关卡 G0~G5**：可测性评分(<80 自动打回)/知识就绪/覆盖达标/执行通过/缺陷清零/准出，状态机落库，`GET /api/quality/requirements` 返回每个需求六关卡状态+质量分+人工介入次数；
7. **失败自动建缺陷**：run 失败(超修复轮次) → DefectSvc.CreateFromRun（关联 run/用例/需求/traceID）→ 回归只跑关联用例；
8. **mock 优先**：LLM 与沙箱双实现（real/mock）环境变量切换，`make demo-m1` 在全 mock 环境可完整演示。

## 9. 前端 12 视图（照原型 1:1 还原布局与交互）

仪表盘 / 代码库·Wiki / 服务地图·契约（SVG 依赖图+影响分析面板）/ 仓库接入 / 需求录入（四步解析管线动画）/ 测试计划（准入准出+报告）/ 生成工作台（六路上下文 tab + 五步管线动画）/ 用例库（五层 tab）/ 执行记录 / 缺陷管理（生命周期+回归按钮）/ 日志·追溯（链路树抽屉）/ 需求质量流水线（G0~G5 矩阵）。全局联动：待审计数、跨视图状态同步，数据全部来自真实接口（删除原型 mock）。

## 10. 里程碑与验收

- **M0 骨架**：compose 起 pg + 全服务 + 前端；gateway 打通一个 gRPC 调用；`make up` 一条命令全绿。
- **M1 单仓闭环（本次交付重点）**：接入 `fixtures/sample-repo` → tree-sitter 索引 → 选中 `create_order` → 生成 → 沙箱执行 → 用例入库。
  **验收**：`make demo-m1` 全 mock 环境跑通"接仓库→生成→执行→入库"，产出 ≥9 条用例、含边界/异常/权限三类、全部执行通过、每条带 traceID。
- **M2 Wiki 层**：分层摘要（repo/模块/函数卡片）+ git diff 增量重建 + stale 标记。验收：改 sample-repo 一行 → 相关页 stale → 重建后仅受影响页 rev+1。
- **M3 需求+RAG**：需求解析四步管线、可测性评分打回、pgvector 相似用例检索。
- **M4 契约+多仓**：契约注册/diff/breaking 影响分析、多仓接入、跨服务上下文。
- **M5 流程闭环**：迭代计划/准入准出/缺陷闭环/报告/日志追溯全量页面上线。
- 每个里程碑交付：可运行代码 + `make demo-mX` + 验收清单勾选 + 一个 git tag。

## 11. 质量要求

- 每服务核心逻辑 pytest 单测（守卫/分诊/覆盖率解析必须覆盖），gateway 接口测试（httpx TestClient）；
- ruff + mypy 通过；结构化 JSON 日志带 trace_id；
- 凭据不落明文（credential_ref 指向凭据管理）；trace 采样入库前过脱敏钩子；
- README 最终版：5 条命令内从零跑通 demo。

## 12. 工作方式

1. 先输出实现计划（目录树 + 里程碑拆解 + 表结构 DDL 草案），确认后动码；
2. 小步提交，语义化 commit，每里程碑打 tag；
3. 缺上下文时顺序：查 PRD → 查本提示词默认值 → 仍无解再一次问一个问题；
4. 禁止：TODO 占位交付、跳过测试、擅自更换技术栈、把 mock 数据写死进前端。
