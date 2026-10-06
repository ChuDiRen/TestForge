# TestForge · AI 测试用例生成平台

> **需求是源头，知识预编译，AI 生成用例，沙箱验证闭环，全链路可回溯。**

面向测试/研发团队的 AI 测试用例生成与验证平台。核心价值链：

```
需求录入 → 知识编译(Wiki/契约) → 上下文组装 → 两阶段 AI 生成
→ 沙箱执行验证 → 分层用例库 → 缺陷闭环 → 准出报告 → 全链路追溯
```

## 5 条命令从零跑通 demo

```bash
make install        # ① 装依赖（uv sync + pnpm install）
make proto          # ② 生成 gRPC stub（proto/ 唯一事实源）
make dev            # ③ 一键拉起 7 服务 + gateway + 前端（等全绿）
make demo-m1        # ④ M1 验收：接仓库→生成→执行→入库（确定性规划 + 真实沙箱执行）
make test           # ⑤ 单测（23 passed，含 gateway TestClient 接口测试）
```

更多验收：`make demo-m0`（骨架全绿） / `demo-m2`（Wiki 增量+stale） / `demo-m3`（需求+RAG+G0~G5） / `demo-m4`（契约 breaking 影响分析） / `demo-m5`（缺陷闭环+计划报告+12 视图）。

真实数据（推荐）：

```bash
make reset-data      # 清空演示/历史数据，只留真实产生
make seed-real       # 以 TestForge 本仓库为示例：接入→索引 425 个真实函数→Wiki 编译→
                     # 4 个真实函数套件生成并 local 沙箱真跑 pytest（sanitize_text 精选 +
                     # embed/extract_rules/diff_specs 探针式特征化：期望值来自对真实代码
                     # 的实际执行）→ 真实需求×2 → 自动编排 → 真实契约（proto+openapi）→
                     # 迭代计划 → 测试报告
```

前端：打开 http://127.0.0.1:5173 —— 12 个视图全部来自真实接口（需求录入→工作台 SSE 管线动画→用例库→执行记录→缺陷回归→测试计划报告→日志追溯→质量流水线）。

### 手机访问（同一 Wi-Fi）

vite 监听 `0.0.0.0`，手机直接访问 `http://<本机局域网IP>:5173`（`make dev` 启动时会打印该地址；API/SSE 经 vite 代理转发，无需暴露后端）。首次使用需放行 Windows 防火墙：

```powershell
netsh advfirewall firewall add rule name="TestForge Vite 5173" dir=in action=allow protocol=TCP localport=5173
```

前端已做移动端自适应（<768px 自动切换为抽屉导航，表格横向滚动，卡片纵向堆叠），手机/平板/桌面共用同一套视图。

## 技术栈（锁定）

- **仓库形态**：前后端分离 monorepo——`frontend/`（React）+ `backend/`（Python 3.12，uv 工程根，.env/data/.run 均在其下）；根 Makefile 总入口；
- **前端**：React 18 + TypeScript + Ant Design 5 + React Query + Zustand + ECharts；
- **后端**（backend/）：一个 FastAPI 进程 = REST/SSE 网关 + 全部 9 个服务（repo/wiki/契约/需求/生成/执行/trace/缺陷/计划），服务间经进程内直调（`MONO_MODE=1` 默认）；模块边界与 proto 契约保持不变，`MONO_MODE=0` 可退回微服务拓扑（各 `services/*/main.py` 仍可独立起 gRPC 进程）；
- **proto**：`backend/proto/testforge.proto` 为消息与服务契约唯一事实源，`make proto` 生成 stub；
- **存储**：PostgreSQL 16 + pgvector（相似用例 RAG）。本机开发库跑在 WSL docker（`testforge-pg`，host 网络，镜像网络经局域网 IP 直达）；`make dev` 启动时自动做 PG 握手健康检查，不通则依次回退 wsl 控制台隧道（`scripts/pg_tunnel.py`）与直连候选；
- **LLM**：DeepSeek（OpenAI 兼容：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-chat`），全管线无 mock 实现——填入 `LLM_API_KEY` → `make llm-check` 验证后即启用；
- **规划智能体**：[deepagents](https://github.com/langchain-ai/deepagents) 框架 + DeepSeek 大模型——智能体带领域工具（读源码/模块清单/调用图）自主探索被测函数上下文，只设计输入与打桩，期望值由探针对真实代码执行捕获（现实即规格）；精选/探针靶标保持确定性策略；
- **知识图谱**：`GET /api/graph` 由真实业务关系（仓库→模块→函数调用→需求→用例→缺陷）构建图数据，前端 ECharts 力导图交互（点节点看属性、按模块过滤、按调用度数取前 N）；
- **沙箱**：`SANDBOX_MODE=local`（默认，本机子进程**真实执行 pytest**，junit/coverage 真解析）/ `docker`（--network none / 512m / 1cpu）/ `fake`（确定性模拟，仅演示）；
- **部署**：`deploy/docker-compose.yml` 一键起 postgres + redis + backend(单体) + frontend；`make stack-up`。

## 目录结构

```
TestForge/                       # 前后端分离 monorepo：frontend/ + backend/
├── frontend/                    # React 前端（14 视图，数据全部来自真实接口）
├── backend/                     # Python 后端工程根（uv；.env/data/.run 也在其下）
│   ├── proto/testforge.proto    # 全部 .proto，唯一事实源（PRD 3.3 全部 9 服务）
│   ├── gateway/                 # FastAPI 网关：REST + SSE + 统一封套 + trace 中间件
│   ├── services/
│   │   ├── repo_svc/            # Git 接入/拉取 + 多语言 tree-sitter 索引（函数卡片/调用图；15 语言）
│   │   ├── wiki_builder/        # 分层 Wiki 编译 + git diff 增量重建 + stale 传播
│   │   ├── contract_registry/   # 契约注册/diff/breaking/影响分析
│   │   ├── req_svc/             # 需求四步解析管线 + 可测性评分（G0）
│   │   ├── testgen_svc/         # 六路上下文 + 两阶段生成 + 覆盖守卫
│   │   ├── runner_svc/          # 沙箱执行 + 修复循环≤3轮 + junit/coverage 解析
│   │   ├── trace_svc/           # traceID 账本（+缺陷闭环 + 迭代计划/报告）
│   │   └── shared/              # 配置/JSON 日志/DB 模型/LLM 角色路由/RAG/知识图谱/脱敏
│   ├── fixtures/sample-repo/    # M1 被测仓库（create_order 及依赖，含存量测试）
│   ├── fixtures/api-repo/       # M4 多仓第二仓库（submit_payment）
│   ├── scripts/                 # dev 编排 + demo-m0~m5 验收脚本 + 知识增强 CLI
│   ├── tests/                   # pytest 单测（独立 testforge_test 库）
│   └── pyproject.toml  uv.lock
├── deploy/                      # docker-compose + Dockerfile + nginx
├── Makefile  README.md  docs/   # 根 Makefile 为总入口（命令自动进入 backend/ 执行）
```

## 关键设计（平台灵魂）

1. **上下文优先级写死**：`code > contract > wiki > trace > similar > bugs`，冲突以源码/契约为准；
0. **多语言索引**：按扩展名映射语言（15 种，`services/repo_svc/indexer.py` 配置表），新语言加一行即可；
2. **两阶段生成**：阶段 A 用例清单 JSON（pydantic 校验）→ 覆盖守卫静态检查表（NULL/空/极值/类型错/越权，缺类自动补）→ 阶段 B 按清单渲染 pytest；
3. **沙箱闭环**：执行 → 失败真回填修复 ≤3 轮（RegenerateAffected 探针重捕获期望值，重生成的测试文件写回工作区，非原样重试）→ 覆盖率回填 → 缺口补齐；失败超轮次自动建缺陷；
4. **traceID 全链路**：网关 `tr_` 前缀，gRPC metadata 透传，全部写操作进 `trace_events`（入库前过脱敏钩子）；
5. **增量索引**：`git diff` → 仅重建受影响页，调用方页跨模块置 stale；
6. **质量关卡 G0~G5**：可测性<80 自动打回 / 知识就绪 / 覆盖达标 / 执行通过 / 缺陷清零 / 准出，`GET /api/quality/requirements` 返回六关卡+质量分+人工介入次数；
7. **真实数据**：沙箱只有真实执行（local/docker），用例期望值来自真实行为与真实执行捕获；通用函数规划走 DeepSeek。
8. **变更驱动回归**：pull 检出函数源码 diff → 关联用例标 stale → 自动回归（按 code_file 分组执行真实沙箱）→ 通过清 stale / 失败自动建缺陷并保持待回归（自愈闭环）；`POST /api/repos/{id}/webhook` 可远程触发；
9. **任务队列**：生成/回归全部持久化入 jobs 表，gateway 内 worker 池消费（并发 `JOB_WORKERS`），进程崩溃重启自动重排队，`GET /api/jobs` 全程可观测；
10. **认证**：除 health/login 外全部端点需 Bearer token（HMAC 签名 12h，剩余 <6h 自动续签 `X-Renewed-Token`），admin 全权 / viewer 只读；登录限速（同 IP 60s×5 / 同账号 15min×10 锁定）、密码策略（≥8 位含字母数字）、自助改密、默认口令强制修改、账号停用、用户管理（列表/创建/改角色/重置密码/删除，保底一个可用 admin）、登录/停用/删除全量审计（trace_events type=认证）；SSE 走 `?token=` 查询参数。
    认证端点：`POST /api/auth/login`、`POST /api/auth/change-password`、`GET|POST /api/auth/users`、`PUT|DELETE /api/auth/users/{username}`、`POST /api/auth/users/{username}/reset-password`。
    **已知限制**（内网工具可接受）：无状态 token 无法单个吊销（改 `SECRET_KEY` 全员下线）；SSE token 走 URL 查询参数可能进代理日志；`SECRET_KEY`/`ADMIN_PASSWORD` 生产部署必须改默认值。

## 知识增强（GitNexus / LightRAG 借鉴改造）

对照 GitNexus（企业代码库上下文引擎）与 LightRAG（图基 RAG）完成的一轮能力升级：

| 能力 | 来源思路 | TestForge 落地 |
| --- | --- | --- |
| 混合检索 | GitNexus（BM25+语义+RRF）/ LightRAG（双层检索） | 统一文档表 `rag_documents`（case/wiki/defect/function/kg 多语料），pgvector 向量 + tsvector 全文双路召回，RRF(k=60) 融合；`shared/rag.py` |
| LLM 角色路由 | LightRAG 四角色分工 | `extract`（抽取/摘要，快）/ `query`（生成/报告，可插拔 deepseek-reasoner）/ `keyword`（检索词，轻量）；`LLM_MODEL_EXTRACT/QUERY/KEYWORD` 配置，空则回退主模型 |
| 抽取缓存 | LightRAG 增量更新复用 LLM 缓存 | `llm_cache` 表按 (role, model, system, prompt) hash 键控，同样的输入只付一次 token，增量重建零成本；`make kg-build` 幂等 |
| 索引原子发布 | GitNexus copy-and-swap | reindex 单事务提交（读者要么完整旧索引要么完整新索引）；批量 RAG 索引/影响面/聚类同为单事务换内容 |
| 变更精确归因 | GitNexus detect_changes | `git diff -U0` 行级 hunk → tree-sitter 函数 span（end_line）→ 变更函数精确集合（源码 diff ∪ 删除 ∪ hunk 命中），不再文件级误伤 |
| 影响面预计算 | GitNexus impact/blast radius | 索引期反向 BFS 预计算每个函数的可达集/深度/影响分（`fn_impacts` 表），`GET /api/functions/{name}/impact` 带深度+置信度；回归只跑直接变更，上游出影响报告；`/api/graph` 节点带影响分 |
| 功能聚类 | GitNexus Leiden → Louvain | 索引期社区检测自动划分测试域（`fn_clusters` 表），`GET /api/repos/{id}/clusters`；networkx Louvain，缺失回退连通分量 |
| 文档知识图谱 | LightRAG 实体/关系抽取 + local/global 检索 | LLM 从 Wiki/需求/缺陷文本抽实体+关系入 `kg_entities/kg_relations`，`GET /api/kg/query?q=&mode=local\|global\|mix` 双层检索；内容 hash 跳过未变更文档；按 source_ref 选择性删除；Key 未配置时构建显式失败、检索自动降级确定性关键词 |
| 生成引用溯源 | LightRAG citations | 六路上下文每一路登记 citation（file:line/契约@版本/wiki 页/kg 实体/CASE/BUG），随生成管线进入用例 schema 与 result 事件——生成物逐条可回溯 |
| 上下文 token 预算 | GitNexus token budget | 六路上下文超 `CTX_TOKEN_BUDGET`（默认 16000）按优先级从低到高裁剪，GROUND TRUTH（code/contract）只截不清 |
| 文档状态跟踪 | LightRAG 文档状态机 | `doc_status` 表记录索引/wiki/kg 摄入状态（ok/failed/stale），`GET /api/knowledge/status` 摄入健康视图 |
| RAG 评估闭环 | LightRAG RAGAS 思路 | 黄金集自动构建（已入库用例为正例），recall@k + MRR，混合检索 vs 向量单路对照；`make rag-eval` 或 `GET /api/rag/eval` |
| 跨仓契约匹配 | GitNexus group_sync | 契约注册时扫描全部仓库函数源码，命中端点/rpc/字段词的仓库自动登记为消费方；breaking 影响分析双通道（源码精确命中 + 域词启发式） |
| MCP 工具面 | GitNexus 19 MCP tools | `make mcp` 起 stdio MCP server（零依赖 JSON-RPC 2.0）：`list_functions` / `function_impact` / `search_cases` / `get_symbol_context` / `knowledge_query` / `repo_status`，只读面供 Cursor/Claude Code 等接入；写操作仍走带鉴权的 REST |

相关命令：`make analyze`（影响面+聚类重算）、`make kg-build`（文档图谱构建，需 LLM Key）、`make rag-eval`（检索质量评估）、`make mcp`（MCP server）。
新增端点：`POST /api/kg/build`、`GET /api/kg/query`、`GET /api/kg/stats`、`POST /api/repos/{id}/analyze`、`GET /api/repos/{id}/clusters`、`GET /api/functions/{name}/impact`、`GET /api/knowledge/status`、`GET/DELETE /api/llm/cache`、`GET /api/rag/eval`。

## Windows 主机注意

## 双平台执行（Windows + Linux 服务器）

后端单体在 **Windows 原生** 与 **Linux 服务器** 上均已全量验证（27 测试全过、demo-m0~m5 全 PASS）：

| 平台 | 启动 | 测试 | 业务链路验证 |
| --- | --- | --- | --- |
| Linux 服务器 | `python scripts/dev_up.py` | `pytest` 27 passed（全量含 TestClient） | `python scripts/verify_native.py` 11/11 |
| Windows | 同左（原生） | 同左 | 同左 |

> 注：Python 解释器选择影响 Windows asyncio——本机曾因 uv 的 CPython 3.12.10 构建（python-build-standalone）被三方软件干扰导致 asyncio 挂死，切换 `.python-version` 到 3.13 并重装依赖后恢复正常。若主机出现 `asyncio.run` 挂起，换 3.13/3.11 解释器重装依赖即可；`scripts/dev_up_win.py`（WSL 后端备选）与 `needs_asyncio` 自动跳过标记仍保留作兜底。

- demo 验收脚本在两端通用：仓库 URL 按平台自动推导（Windows `file:///E:/...`，Linux `file:///mnt/e/...`），`TF_SAMPLE_REPO_URL` 可覆盖；
- `MONO_MODE=0` 可切回微服务拓扑（各服务 `main.py` 保留独立 gRPC 入口）。

依赖 PyPI 源慢时可 `UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple make install`。

## REST 端点（PRD 3.2 全量）

`POST /api/repos`、`POST /api/repos/{id}/pull`、`POST /api/requirements/ingest`、`POST /api/requirements/{id}/confirm`、`POST /api/generations`、`GET /api/generations/{id}/events`(SSE)、`GET /api/cases`、`POST /api/cases/{id}/review`、`GET /api/runs`、`POST /api/runs/{id}/rerun`、`POST /api/generations/batch`、`GET /api/jobs`、`GET /api/generations/{id}/export`、`POST /api/generations/{id}/export-to-repo`、`POST /api/contracts/{id}/regenerate`、`POST /api/repos/{id}/webhook`、`POST /api/defects/{id}/suggest`、`POST /api/auth/login`、`POST /api/contracts/{id}/impact`、`POST /api/plans`、`GET /api/plans/{iter}`、`POST /api/defects`、`POST /api/defects/{id}/regression`、`POST /api/reports/{iter}`、`GET /api/traces/{traceId}`、`GET /api/quality/requirements`。统一响应 `{code, message, data}`；SSE 事件 `stage(plan|guard|codegen|sandbox|coverage)` / `log` / `result`。

## 里程碑与 tag

| 里程碑 | 范围 | 验收 | tag |
| --- | --- | --- | --- |
| M0 骨架 | compose 全服务 + gateway→gRPC | demo-m0 11/11 | `m0` |
| M1 单仓闭环 | 接仓库→tree-sitter→生成→沙箱→入库 | demo-m1 12/12（17 用例三类全过带 traceID + 真实 pytest 复核） | `m1` |
| M2 Wiki 层 | 分层摘要 + 增量重建 + stale | demo-m2 10/10 | `m2` |
| M3 需求+RAG | 四步管线 + G0 打回 + pgvector | demo-m3 16/16 | `m3` |
| M4 契约+多仓 | 注册/diff/breaking/影响分析/定向重生成 | demo-m4 12/12 | `m4` |
| M5 流程闭环 | 缺陷闭环/计划/报告/追溯 + 12 视图 | demo-m5 13/13 | `m5` |

验收清单详见 `docs/验收清单.md`。

## 文档

- `docs/TestForge-PRD-v1.2.md` —— 唯一需求来源；
- `prototype/testforge-prototype.html` —— UI 视觉基准（浏览器直接打开）；
- `docs/验收清单.md` —— 里程碑验收项逐条勾选。
