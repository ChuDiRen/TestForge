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
make demo-m1        # ④ M1 验收：接仓库→生成→执行→入库（全 mock，无需 LLM Key）
make test           # ⑤ 单测（23 passed，含 gateway TestClient 接口测试）
```

更多验收：`make demo-m0`（骨架全绿） / `demo-m2`（Wiki 增量+stale） / `demo-m3`（需求+RAG+G0~G5） / `demo-m4`（契约 breaking 影响分析） / `demo-m5`（缺陷闭环+计划报告+12 视图）。

前端：打开 http://127.0.0.1:5173 —— 12 个视图全部来自真实接口（需求录入→工作台 SSE 管线动画→用例库→执行记录→缺陷回归→测试计划报告→日志追溯→质量流水线）。

## 技术栈（锁定）

- **仓库形态**：monorepo。后端 Python 3.12（uv），前端 pnpm + Vite；
- **前端**：React 18 + TypeScript + Ant Design 5 + React Query + Zustand + ECharts；
- **后端单体**：一个 FastAPI 进程 = REST/SSE 网关 + 全部 9 个服务（repo/wiki/契约/需求/生成/执行/trace/缺陷/计划），服务间经进程内直调（`MONO_MODE=1` 默认）；模块边界与 proto 契约保持不变，`MONO_MODE=0` 可退回微服务拓扑（各 `services/*/main.py` 仍可独立起 gRPC 进程）；
- **proto**：`proto/testforge.proto` 为消息与服务契约唯一事实源，`make proto` 生成 stub；
- **存储**：PostgreSQL 16 + pgvector（相似用例 RAG）+ 本地文件；SQLite 可经 `DATABASE_URL` 切换（开发兜底）；
- **LLM**：OpenAI 兼容 API 全配置化（`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`），**`LLM_MODE=mock` 无 Key 全流程可跑**；结构化输出过 pydantic 校验；
- **沙箱**：`SANDBOX_MODE=docker`（--network none / 512m / 1cpu）/ `local`（本机 pytest）/ `fake`（确定性模拟）；
- **部署**：`deploy/docker-compose.yml` 一键起 postgres + redis + backend(单体) + frontend；`make stack-up`。

## 目录结构

```
TestForge/
├── proto/testforge.proto      # 全部 .proto，唯一事实源（PRD 3.3 全部 9 服务）
├── gateway/                   # FastAPI 网关：REST + SSE + 统一封套 + trace 中间件
├── services/
│   ├── repo_svc/              # Git 接入/拉取 + 多语言 tree-sitter 索引（函数卡片/调用图；py/go/java/ts/js/rs/c/cpp/cs/rb/php/kt/swift/bash）
│   ├── wiki_builder/          # 分层 Wiki 编译 + git diff 增量重建 + stale 传播
│   ├── contract_registry/     # 契约注册/diff/breaking/影响分析
│   ├── req_svc/               # 需求四步解析管线 + 可测性评分（G0）
│   ├── testgen_svc/           # 六路上下文 + 两阶段生成 + 覆盖守卫
│   ├── runner_svc/            # 沙箱执行 + 修复循环≤3轮 + junit/coverage 解析
│   ├── trace_svc/             # traceID 账本（+缺陷闭环 + 迭代计划/报告）
│   └── shared/                # 配置/JSON 日志/DB 模型/LLM 双实现/RAG/脱敏
├── frontend/                  # React 12 视图（数据全部来自真实接口）
├── fixtures/sample-repo/      # M1 被测仓库（create_order 及依赖，含存量测试）
├── fixtures/api-repo/         # M4 多仓第二仓库（submit_payment）
├── deploy/                    # docker-compose + Dockerfile + nginx
├── scripts/                   # dev 编排 + demo-m0~m5 验收脚本
├── Makefile  README.md  docs/
```

## 关键设计（平台灵魂）

1. **上下文优先级写死**：`code > contract > wiki > trace > similar > bugs`，冲突以源码/契约为准；
0. **多语言索引**：按扩展名映射语言（15 种，`services/repo_svc/indexer.py` 配置表），新语言加一行即可；
2. **两阶段生成**：阶段 A 用例清单 JSON（pydantic 校验）→ 覆盖守卫静态检查表（NULL/空/极值/类型错/越权，缺类自动补）→ 阶段 B 按清单渲染 pytest；
3. **沙箱闭环**：执行 → 失败回填修复 ≤3 轮 → 覆盖率回填 → 缺口补齐；失败超轮次自动建缺陷；
4. **traceID 全链路**：网关 `tr_` 前缀，gRPC metadata 透传，全部写操作进 `trace_events`（入库前过脱敏钩子）；
5. **增量索引**：`git diff` → 仅重建受影响页，调用方页跨模块置 stale；
6. **质量关卡 G0~G5**：可测性<80 自动打回 / 知识就绪 / 覆盖达标 / 执行通过 / 缺陷清零 / 准出，`GET /api/quality/requirements` 返回六关卡+质量分+人工介入次数；
7. **mock 优先**：LLM 与沙箱双实现环境变量切换，全 mock 无外部依赖跑通全部 demo。

## Windows 主机注意

## 双平台执行（Windows + Linux 服务器）

全部业务代码跨平台可移植，两端均已验证：

| 平台 | 后端 | 测试 | 业务链路验证 |
| --- | --- | --- | --- |
| Linux 服务器 / WSL | `python scripts/dev_up.py`（单体进程） | `pytest` 27 passed（全量含 TestClient） | `python scripts/verify_native.py` 11/11 |
| Windows（健康环境） | 同上，原生运行 | 同上，原生运行 | 同上 |
| Windows（本开发机：asyncio 被三方注入破坏） | `make dev` 自动切 WSL 后端 + Windows 前端 | TestClient 用例启动时探测 asyncio，不可用自动 skip（20 passed + 7 skipped） | `verify_native.py` 11/11（mono 直调，无 asyncio/HTTP） |

- demo 验收脚本在两端通用：仓库 URL 按平台自动推导（Windows `file:///E:/...`，Linux `file:///mnt/e/...`），`TF_SAMPLE_REPO_URL` 可覆盖；
- `MONO_MODE=0` 可切回微服务拓扑（各服务 `main.py` 保留独立 gRPC 入口）。

依赖 PyPI 源慢时可 `UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple make install`。

## REST 端点（PRD 3.2 全量）

`POST /api/repos`、`POST /api/repos/{id}/pull`、`POST /api/requirements/ingest`、`POST /api/requirements/{id}/confirm`、`POST /api/generations`、`GET /api/generations/{id}/events`(SSE)、`GET /api/cases`、`POST /api/cases/{id}/review`、`GET /api/runs`、`POST /api/runs/{id}/rerun`、`POST /api/contracts/{id}/impact`、`POST /api/plans`、`GET /api/plans/{iter}`、`POST /api/defects`、`POST /api/defects/{id}/regression`、`POST /api/reports/{iter}`、`GET /api/traces/{traceId}`、`GET /api/quality/requirements`。统一响应 `{code, message, data}`；SSE 事件 `stage(plan|guard|codegen|sandbox|coverage)` / `log` / `result`。

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
