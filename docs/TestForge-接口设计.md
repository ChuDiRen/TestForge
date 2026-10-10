# TestForge 接口设计（As-Built · 2026-10-10）

> 配套：[PRD v4.0](./TestForge-PRD-v4.0.md) · [架构设计](./TestForge-架构设计.md) · [数据库设计](./TestForge-数据库设计.md)。
> 数据来源：`backend/app/api/` 全部路由模块实测扫描，共 **127 个 HTTP 接口** + 5 个 Ollama 兼容端点 + 13 个 MCP 工具。
> 2026-10-10 复核：前端信息架构收敛与脚手架分层重构纯前端变更；后端单体化后 `app/api/` 为唯一路由面，接口面零变化，本文内容仍然有效。

---

## 1. 全局约定

### 1.1 基础信息

| 项 | 值 |
|---|---|
| Base URL | `http://<host>:8000`（后端唯一入口；前端 dev 经 vite :5173 反代） |
| 协议 | REST + SSE（4 个流式端点）+ NDJSON（Ollama chat 流式） |
| 认证 | `Authorization: Bearer <token>`；SSE/EventSource 允许 `?token=<token>` 兜底 |
| Token | 无状态 HMAC：`username:expiry_ts:role:hmac_sig`（HMAC-SHA256），TTL 12h；剩余 <6h 时响应头 `X-Renewed-Token` 自动续签 |

### 1.2 响应信封

```jsonc
// 成功
{ "code": 0, "message": "ok", "data": <任意> }
// 失败（HTTP 状态码与业务 code 通常一致）
{ "code": <int>, "message": <str>, "data": null }
```

### 1.3 业务错误码

| code | 含义 | | code | 含义 |
|---|---|---|---|---|
| 401 | 未登录/登录过期/账号停用 | | 1001 | 参数缺失/非法 |
| 403 | 越权（viewer 写操作）/webhook 密钥不符/默认口令拦截 | | 1002 | 用户名已存在 |
| 404 | 资源不存在 | | 1003 | git URL 校验失败 |
| 409 | Wiki 编译锁冲突（同仓库重建中） | | 1004 | 密码策略不合规 |
| 422 | 门卫拒绝（敏感信息/评估分低/文件类型/qa 无来源） | | 1005 | 用户管理防呆（最后一个 admin/操作自己） |
| 429 | 登录限速锁定 | | 502 | URL 抓取失败 |
| 500 | 未捕获异常 | | 503 | LLM 不可用 |

### 1.4 分页约定

- 服务端分页仅两处：`GET /api/cases`（`page/page_size`≤200，响应带 total）与 `GET /api/kg/documents`（`page/page_size`≤100）。
- 其余列表端点用 `limit` 硬上限（jobs 200 / traces 500 / functions 500 / knowledge documents 200 / wiki 会话 50 等）。

### 1.5 认证中间件豁免

仅 `/api/health`、`/api/auth/login`、非 `/api` 前缀路径（`/ollama/*`）、`OPTIONS` 预检。其余 `/api/*` 一律需 Bearer token；**viewer 仅可 GET**（例外：`/api/auth/change-password`、`/api/assistant/chat`）；must_change 用户仅可用上述两个自助路径。

### 1.6 追溯

非 GET 请求自动生成 trace_id 并回写响应头 `X-Trace-Id`；全链路事件可在 `GET /api/traces/{trace_id}` 回放。

---

## 2. 接口清单

> 权限列：🟢 登录用户 · 🔴 admin（中间件非 GET 拦 viewer / 处理器内 `_require_admin` 双重校验）· ⚪ 公开。

### 2.1 系统 / 健康（2）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/health` | 健康检查 `{status, version}` | ⚪ |
| GET | `/api/system/services` | 领域模块链路探测 `{services[], all_green}` | 🟢 |

### 2.2 认证 / 用户（8）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/auth/login` | 登录 `{username, password}` → `{token, username, role, must_change_password}`；限速 429 | ⚪ |
| GET | `/api/auth/me` | 当前用户 `{username, role, exp}` | 🟢 |
| POST | `/api/auth/change-password` | 自助改密 `{old_password, new_password}`（≥8 位含字母数字） | 🟢 自助 |
| GET | `/api/auth/users` | 用户清单 | 🔴 |
| POST | `/api/auth/users` | 创建账号 `{username, password, role?=viewer}` | 🔴 |
| PUT | `/api/auth/users/{username}` | 改角色/停启用 `{role?, disabled?}`（不能操作自己/保底 admin） | 🔴 |
| DELETE | `/api/auth/users/{username}` | 删除账号（防呆同上） | 🔴 |
| POST | `/api/auth/users/{username}/reset-password` | 管理员重置密码 `{new_password}`；viewer 置 must_change | 🔴 |

### 2.3 仪表盘（1）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/stats/summary` | 全平台统计：repos/cases(分层/状态)/待审/执行通过率/需求/任务/缺陷/Wiki/契约/stale | 🟢 |

### 2.4 仓库管理（5）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/repos` | 仓库列表 | 🟢 |
| POST | `/api/repos` | 注册 git 仓库 `{url*, branch?=main, credential_ref?, webhook?}`（URL 校验 1003） | 🔴 |
| POST | `/api/repos/{repo_id}/pull` | 拉取+增量索引+变更回归（标 stale→异步回归）+Wiki 增量重建；响应含 `regression{affected_cases, job_code}` | 🔴 |
| POST | `/api/repos/{repo_id}/webhook` | git webhook；可选 `X-Webhook-Secret` 校验；后台执行立即返回 `{accepted}` | 🔴（Bearer + 可选共享密钥） |
| POST | `/api/repos/upload` | **入口①** zip 上传建仓：multipart `file*` + `name?` + `branch?`；安全解压（zip-slip/200MB/2 万成员/1GB）；同名重传=更新图谱；响应含 functions/call_edges/wiki_pages/steps | 🔴 |

### 2.5 函数索引 / 代码分析（9）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/functions` | 函数列表（repo_id/module/name 模糊，≤500） | 🟢 |
| POST | `/api/repos/{repo_id}/analyze` | 重算影响面 + 功能聚类 | 🔴 |
| GET | `/api/repos/{repo_id}/clusters` | Louvain 测试域聚类清单 | 🟢 |
| GET | `/api/repos/{repo_id}/cycles` | Tarjan SCC 调用环（max_cycles=20） | 🟢 |
| GET | `/api/repos/{repo_id}/chains` | 入口最长调用链（max_chains=10, max_len=14） | 🟢 |
| GET | `/api/repos/{repo_id}/processes` | 入口→出口执行流识别（max_processes=8） | 🟢 |
| GET | `/api/repos/{repo_id}/changes` | git diff 变更检测（scope=unstaged/staged/all, base_ref?） | 🟢 |
| GET | `/api/functions/trace` | 两函数最短调用路径（src*, dst*, max_depth=10；**须注册在 impact 前**） | 🟢 |
| GET | `/api/functions/{function_name}/impact` | 变更影响面（受影响调用方+深度+置信度，标注 lower-bound） | 🟢 |

### 2.6 用例生成 / 任务（10）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/generations` | 创建生成任务 `{function*\|target, repo_id?, layer?=ut, source_req?, trace_id?}` → 异步入队 | 🔴 |
| POST | `/api/generations/batch` | 批量生成 `{repo_id*, module?\|names[], layer}`（去重 ≤50） | 🔴 |
| GET | `/api/jobs` | 任务台账（status?, limit=50） | 🟢 |
| GET | `/api/jobs/{job_code}` | 单任务详情（含 payload） | 🟢 |
| GET | `/api/generations/{gen_code}` | 生成状态 + 事件列表 | 🟢 |
| GET | `/api/generations/{gen_code}/events` | **SSE** 生成进度（stage/result/ping，断线回放，600s 超时） | 🟢 |
| GET | `/api/generations/{gen_code}/export` | 导出测试文件内容 `{filename, content, lines}` | 🟢 |
| POST | `/api/generations/{gen_code}/export-to-repo` | 写入仓库 `tests/testforge_generated/` | 🔴 |
| POST | `/api/runs/{run_code}/rerun` | 重跑执行（按原 generation 参数重新入队） | 🔴 |
| GET | `/api/runs` | 执行记录列表（≤100） | 🟢 |

### 2.7 用例库（5）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/cases` | 用例分页（layer/module/status/category/source_req/stale 过滤；status 缺省隐藏「已替换」；响应带 total/by_layer/by_category） | 🟢 |
| GET | `/api/cases/{case_code}/file` | 用例源码文件预览 `{filename, content, size}` | 🟢 |
| PUT | `/api/cases/{case_id}` | 编辑元数据 `{title?, status?, review_note?, confidence?, stale?}` | 🔴 |
| DELETE | `/api/cases/{case_id}` | 删除用例（历史 run/trace 保留） | 🔴 |
| POST | `/api/cases/{case_id}/review` | 人审 `{action: approve\|reject, note?}` | 🔴 |

### 2.8 追溯（2）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/traces` | 全量事件台账（limit≤500, type?） | 🟢 |
| GET | `/api/traces/{trace_id}` | 单 trace 链路回放 | 🟢 |

### 2.9 需求管理（5）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/requirements/ingest` | 需求录入四步管线 `{title*, body*\|text*, repo_id?, source?=paste}`；可测性 <80 打回 | 🔴 |
| GET | `/api/requirements` | 需求列表（≤200，含 by_status） | 🟢 |
| POST | `/api/requirements/{req_id}/confirm` | 冲突确认 `{action, note?}`；approve→生效+规则回写 Wiki+**自动编排生成** | 🔴 |
| GET | `/api/requirements/{req_code}/similar` | 相似用例 RAG 检索（limit=5） | 🟢 |
| GET | `/api/quality/requirements` | G0~G5 质量流水线（关卡/加权分/人工介入） | 🟢 |

### 2.10 契约中心（6）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/contracts` | 注册契约 `{name*, type?=rest, provider_repo?, version?, spec?, consumers[]?}` | 🔴 |
| GET | `/api/contracts` | 契约列表（≤200） | 🟢 |
| POST | `/api/contracts/{contract_id}/impact` | 影响分析（进程内聚合，`{to_v?}`） | 🔴 |
| POST | `/api/contracts/{contract_id}/regenerate` | 契约变更闭环：影响分析→受影响用例重生成入队 | 🔴 |
| GET | `/api/contracts/{contract_id}/diffs` | diff 历史（from_v/to_v/breaking/changes） | 🟢 |
| POST | `/api/regenerate` | 定向重生成 `{repo_id*, target_function*, case_ids[]?, reason?}` | 🔴 |

### 2.11 缺陷闭环（6）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/defects` | 建缺陷 `{run_id?\|origin_run, case_codes[]?, req_code?, trace_id?, reason?}` | 🔴 |
| GET | `/api/defects` | 缺陷列表（≤200） | 🟢 |
| GET | `/api/defects/{defect_id}/suggestion` | 查看修复建议 | 🟢 |
| POST | `/api/defects/{defect_id}/suggest` | AI 生成根因分析+修复建议（缓存落库；LLM 不可用 503） | 🔴 |
| POST | `/api/defects/{defect_id}/regression` | 定向回归（过→自动关闭/败→重开） | 🔴 |
| POST | `/api/defects/{defect_id}/status` | 生命周期流转 `{status*, actor?}`；关闭时沉淀 lesson 入知识库 | 🔴 |

### 2.12 迭代计划 / 报告（4）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/plans` | 建迭代 `{version?, req_codes[]?}`；准入/准出自动判定；入检索库(kind=plan) | 🔴 |
| GET | `/api/plans` | 迭代列表（≤100） | 🟢 |
| GET | `/api/plans/{iter_code}` | 迭代详情（实时判定 + report + checks） | 🟢 |
| POST | `/api/reports/{iter_code}` | 生成测试报告（平台真实数据汇总） | 🔴 |

### 2.13 Wiki / 知识编译（13）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/wiki` | Wiki 页列表（repo_id?） | 🟢 |
| GET | `/api/wiki/health` | 各仓库页数/stale 数 | 🟢 |
| GET | `/api/wiki/lint` | Wiki 体检（stale/超短/重复/孤儿/覆盖缺口）`repo_id*` | 🟢 |
| POST | `/api/wiki/lint/fix-duplicates` | 处置重复标题（先清依赖边再删页） | 🔴 |
| GET | `/api/wiki/graph` | 互链图谱（TF-IDF 边 + qa_reference 边） | 🟢 |
| GET | `/api/wiki/insights` | 知识洞察（LLM 归纳失败降级纯统计；300s 缓存） | 🟢 |
| GET | `/api/wiki/{page_id}` | 页详情（content_md/rev/stale） | 🟢 |
| GET | `/api/wiki/{page_id}/related` | 相关页推荐（TF-IDF top-K） | 🟢 |
| POST | `/api/wiki/ask` | 三阶段 RAG 问答 `{question*, repo_id?=0, history?, session_id?}`；反幻觉 no_data | 🟢 |
| GET | `/api/wiki/chat/sessions` | 问答会话列表（≤50） | 🟢 |
| POST | `/api/wiki/chat/sessions` | 新建会话 | 🔴 |
| DELETE | `/api/wiki/chat/sessions/{session_id}` | 删除会话 | 🔴 |
| GET | `/api/wiki/chat/sessions/{session_id}/messages` | 会话消息（≤200） | 🟢 |
| POST | `/api/wiki/rebuild` | 一键重建 `{repo_id*, full?=false}`；并发 409 编译锁 | 🔴 |

> 路由顺序约束：`/api/wiki/health|lint|graph|insights|ask|chat|rebuild` 等静态段必须注册在 `/api/wiki/{page_id}` 之前。

### 2.14 知识文档 / 检索（5）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/knowledge/documents` | 用户知识文档列表（kind=user_doc） | 🟢 |
| POST | `/api/knowledge/documents` | 知识入库（upsert；三道门卫：敏感 422 / qa 无来源 422 / 评估分 <0.5 拒绝 422）`{title*, content*, repo_id?, kind_hint?, sources[]?, assess?}` | 🔴 |
| DELETE | `/api/knowledge/documents` | 删除（doc_key*） | 🔴 |
| GET | `/api/knowledge/search` | 检索测试台：七路混合检索（q*, repo_id?, limit=8, hide_sensitive 打码） | 🟢 |
| POST | `/api/knowledge/import-url` | URL 一键入库（公众号/GitHub README 专用路由+通用 HTML；<30 字 422） | 🔴 |

### 2.15 知识资产（入口②，2）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/knowledge/assets/upload` | 四类资产统一上传：multipart `file*` + `kind*`（req_doc/tech_doc/test_plan/defect）+ `repo_id?` + `title?`；流程=解析→敏感扫描→评估门卫→双写（rag 即时检索 + KG 异步管线）；req_doc 走需求管线；defect 仅 csv/xlsx（中文表头模糊映射） | 🔴 |
| GET | `/api/knowledge/assets/summary` | 闭环总览（各 kind 计数 + 需求/用例/Wiki/函数/缺陷消费计数） | 🟢 |

### 2.16 知识图谱 KG·简版 / 分析（8）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/kg/build` | 构建文档图谱（wiki+缺陷+需求→实体/关系） | 🔴 |
| GET | `/api/kg/query` | 图谱双层检索（q*, mode=local/global/mix, limit=6） | 🟢 |
| GET | `/api/kg/stats` | 图谱规模统计 | 🟢 |
| GET | `/api/knowledge/status` | 摄入健康视图（kind×status + 最近失败） | 🟢 |
| GET | `/api/llm/cache` | LLM 抽取缓存台账 | 🟢 |
| DELETE | `/api/llm/cache` | 清缓存（role? 按角色） | 🔴 |
| GET | `/api/rag/eval` | 检索质量评估（黄金集 recall@k/MRR，混合 vs 向量对照） | 🟢 |
| GET | `/api/graph` | 业务知识图谱（repo→模块→函数调用→用例←需求→缺陷，ECharts 可渲染；max_functions≤600） | 🟢 |

### 2.17 KG·LightRAG 全量（24）

**文档管理（异步管线）**

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/kg/documents` | 纯文本入库→异步管线 `{title*, content*, workspace?, repo_id?}` | 🔴 |
| POST | `/api/kg/documents/upload` | 文件上传（pdf/docx/txt/md/rst/csv；parser?=native；文本类敏感扫描） | 🔴 |
| GET | `/api/kg/documents` | 分页列表（repo_id/workspace/status/q 过滤，page_size≤100） | 🟢 |
| GET | `/api/kg/documents/track` | 单文档状态（doc_key*） | 🟢 |
| GET | `/api/kg/documents/chunks` | 某文档分块清单 | 🟢 |
| GET | `/api/kg/chunk-content` | 单 chunk 全文 | 🟢 |
| DELETE | `/api/kg/documents` | 删除文档（撤 chunk + 从抽取缓存重建图谱） | 🔴 |

**查询 / 流式**

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/kg/search` | 六模式纯召回 `{query*, mode?=mix, top_k=8, websearch?=false}` | 🟢 |
| POST | `/api/kg/query/stream` | **SSE** 流式问答（retrieved→delta*→done；use_cache?=true） | 🟢 |
| DELETE | `/api/kg/query-cache` | 清答案缓存 | 🔴 |

**图谱 / 实体 / 关系**

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/kg/graph` | 图谱数据（max_nodes≤2000, search?, focus?, depth≤4） | 🟢 |
| GET | `/api/kg/entity/exists` | 实体存在性 | 🟢 |
| PATCH | `/api/kg/entity` | 实体编辑（改名/类型/描述） | 🔴 |
| POST | `/api/kg/entity/merge` | 实体合并 `{into*, sources[]*}` | 🔴 |
| DELETE | `/api/kg/entity` | 实体级联删除 | 🔴 |
| PATCH | `/api/kg/relation` | 关系编辑 | 🔴 |
| DELETE | `/api/kg/relation` | 关系删除 | 🔴 |

**社区 / 导出 / 工作区 / 设置**

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/kg/communities/build` | 社区检测+报告（`llm?=true` 控制是否 LLM 摘要） | 🔴 |
| GET | `/api/kg/communities` | 社区清单（level=0/1/2） | 🟢 |
| GET | `/api/kg/export` | 导出（what=entities/relations/communities/chunks/graph × fmt=json/csv/md/xlsx/graphml/zip；base64 内嵌） | 🟢 |
| GET | `/api/kg/workspaces` | 工作区清单 | 🟢 |
| GET | `/api/kg/settings` | 运行时参数 + 能力状态 + communities dirty 标记 | 🟢 |
| PUT | `/api/kg/settings` | 更新运行时参数（白名单键：chunk_strategy/size/overlap/drop_references、gleaning_rounds、query_cache、user_prompt_prefix、websearch_*） | 🔴 |

### 2.18 AI 助手（5）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/assistant/threads` | 会话列表（≤50） | 🟢 |
| POST | `/api/assistant/threads` | 新建会话 `{title?, repo_id?}` | 🟢* |
| DELETE | `/api/assistant/threads/{thread_id}` | 删除会话 | 🔴 |
| GET | `/api/assistant/threads/{thread_id}/messages` | 历史消息（≤200，含 tool_events/citations） | 🟢 |
| POST | `/api/assistant/chat` | **SSE** 对话 `{thread_id*, content*}`；事件 token/tool_start/tool_end/done/error；写操作工具层 `_require_admin` 二次拦截 | 🟢（viewer 可聊天） |

---

## 3. SSE / 流式协议（4 个端点）

| 端点 | 格式 | 事件序 |
|---|---|---|
| `GET /api/generations/{gen}/events` | text/event-stream | `event: stage`（阶段+progress）→ `event: result`；空闲发 `event: ping`；600s 超时 |
| `POST /api/assistant/chat` | text/event-stream | `token`（增量文本）→ `tool_start {name,args}` / `tool_end {name,summary,ms,result}` 交错 → `done {citations[], tool_events[]}`；异常 `error {message}` |
| `POST /api/kg/query/stream` | text/event-stream（仅 `data:` 帧） | `retrieved`（含 contexts/references）→ `delta`*（增量回答）→ `done`（完整 answer） |
| `POST /ollama/api/chat`（stream=true） | application/x-ndjson | 逐行 `{model, created_at, message:{role,content}, done}`（Ollama 原生格式） |

---

## 4. Ollama 兼容层（5 个端点，路径不在 /api 下 → 不走认证中间件）

模块内自校验 `_ollama_auth`：`ollama_compat=false` → 404；配置 `ollama_api_key` 时校验 `X-Api-Key` 或 `Authorization: Bearer`；未配 key 且 `env=prod` → 403。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/ollama/api/version` | `{"version": …}`（原生格式，无信封） |
| GET | `/ollama/api/tags` | 模型列表（单模型 `testforge-kg:latest`） |
| POST | `/ollama/api/chat` | 对话=完整 KG 检索→生成管线；body：`messages[]` 或 `prompt` + `stream?` + `repo_id?/workspace?/mode?` |
| POST | `/ollama/api/generate` | 单轮生成 `{response, done}` |
| POST | `/ollama/api/embeddings` | 文本向量化 `{embedding:[...]}` |

---

## 5. MCP 工具面（stdio，只读 13 个）

启动：`make mcp`（`python -m services.mcp_server`，换行分隔 JSON-RPC 2.0，MCP 规范子集：initialize / tools/list / tools/call / ping）。写操作不旁路 REST 鉴权。

| 工具 | 用途 |
|---|---|
| `list_functions` | 函数清单 / 混合检索 |
| `function_impact` | blast radius 影响面 |
| `trace_path` | 两函数间最短调用路径（BFS） |
| `detect_changes` | git diff → 受影响函数 |
| `call_cycles` | Tarjan SCC 调用环 |
| `entry_chains` | 入口最长调用链 |
| `list_processes` | 入口→出口业务执行流 |
| `wiki_ask` | Wiki 问答（带来源） |
| `wiki_lint` | Wiki 体检 |
| `search_cases` | 用例混合检索 |
| `get_symbol_context` | 符号 360° 上下文包（与生成管线同源，带 citations） |
| `knowledge_query` | KG 六模式检索 |
| `repo_status` | 仓库/摄入/LLM 缓存健康总览 |

---

## 6. Webhook

`POST /api/repos/{repo_id}/webhook`：中间件 Bearer 之外，配置 `webhook_secret` 时额外校验请求头 `X-Webhook-Secret`（hmac.compare_digest）；后台线程拉取+回归，立即返回 `{accepted:true, repo_id}`。
