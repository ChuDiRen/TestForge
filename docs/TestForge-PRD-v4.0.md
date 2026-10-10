# TestForge PRD v4.0（As-Built · 以当前代码实测为准）

> 版本沿革：v1.2 初始设计 → v2.0 交付回写 → v3.0 智能体驱动重构 + 四层生成达标 → **v4.0 知识层九轮演进：LightRAG 全量移植 + 知识资产两入口闭环**。
> 生成日期：2026-10-10；同日复核更新：前端信息架构收敛（知识图谱双视图 Hub / 检索中心三合一 / 知识资产统一 Hub，视图 24→20）。
> 口径：与仓库当前工作区代码（含未提交的知识层演进）一致，冲突时以本文档为准。
> 配套文档：[架构设计](./TestForge-架构设计.md) · [数据库设计](./TestForge-数据库设计.md) · [接口设计](./TestForge-接口设计.md)。

---

## 1. 产品定位

**AI 智能体驱动的测试用例生成平台**：需求是源头，知识预编译，AI 深度代理生成四层用例（单元 / 功能 / 接口 / E2E），真实沙箱验证闭环，全链路引用溯源。

一句话价值：把「读代码、编 Wiki、写用例、跑沙箱、提缺陷、出报告」整条测试生产线交给平台自动串联，人只做需求确认与用例人审两个决策点。

### 1.1 产品原则（违反即缺陷）

1. **无 mock 原则**：不存在任何确定性假实现。LLM 真实调用，沙箱只有真实执行，期望值来自真实代码执行捕获。
2. **现实即规格**：LLM 只设计输入与场景；断言期望值由探针对真实代码执行捕获并**反向校正**（LLM 猜错的断言一律被真实结果覆写）。
3. **诚实台账**：失败建缺陷、覆盖率如实显示、打回如实扣分；探针无法观测的断言诚实剔除而非编造。
4. **智能体驱动**：AI 助手是平台主操作入口——四层生成、知识收集、质量查询都在对话中完成；表单页面是精确操作的补充而非替代。
5. **确定性优先**：能不用 LLM 的环节一律确定性实现（Wiki 渲染、需求解析、契约 breaking 判定、覆盖守卫、准出核对），LLM 只出现在知识抽取 / 规划 / 问答 / 助手环节且全部经持久缓存。

### 1.2 解决的痛点

| 痛点 | 平台对策 |
|---|---|
| 需求不可测、口头需求满天飞 | 需求四步解析管线 + 可测性 G0 评分门禁（<80 自动打回） |
| 测试不了解代码真实行为 | 仓库全量函数索引 + Wiki 三层知识编译 + 六路上下文组装 |
| 用例生成靠手写、覆盖率无保证 | 两阶段 AI 生成（规划→确定性渲染）+ 四类覆盖守卫 + 沙箱验证 |
| AI 幻觉断言不可信 | 探针执行捕获期望值反向校正；修复循环 ≤3 轮以真实行为重写断言 |
| 代码一改用例就烂、没人管 | pull 变更归因 → 用例标 stale → 自动回归自愈 → 失败自动建缺陷 |
| 缺陷修复无回归保障 | 缺陷四向关联 + AI 修复建议 + 定向回归（过则自动关闭） |
| 质量状态说不清 | G0~G5 需求质量关卡、迭代准入/准出自动判定、测试报告一键生成 |
| 知识散落各处无法检索 | 七路混合检索（向量+全文 RRF）+ 文档知识图谱六模式查询 |

## 2. 用户与角色

| 角色 | 权限 | 典型场景 |
|---|---|---|
| **admin** | 全权（所有写操作 + 用户管理） | 测试负责人 / 平台运营：接仓库、录需求、生成用例、人审、管缺陷、出报告 |
| **viewer** | 只读（非 GET 一律 403） | 开发 / QA 浏览：看用例库、执行记录、追溯链路；例外可聊天（AI 助手对话）与自助改密，AI 写操作在工具层按角色二次拦截 |

账号机制：PBKDF2-SHA256 10 万轮 + 盐；首启引导 admin 账号；普通用户保留默认口令时强制改密（must_change）；登录限速（同 IP 60s×5 次 / 同账号 15min×10 次锁定）。认证为无状态 HMAC token（12h TTL，剩余 <6h 自动续签）。

## 3. 系统组成总览

- **形态**：单体优先（`MONO_MODE=1` 默认，9 个业务 Servicer 进程内直调，仅暴露网关 8000 端口）；保留微服务拓扑（`MONO_MODE=0`，gRPC 50051~50057）。
- **后端**：Python ≥3.12 + FastAPI 网关 + 9 个 gRPC 业务 Servicer（DefectSvc / PlanSvc 与 TraceLog 同进程托管）。
- **前端**：React 18 + TS + Vite + AntD 5 + React Query + Sigma.js / ECharts（20 视图 · Hub 收敛式信息架构）。
- **存储**：PostgreSQL 16 + pgvector（33 张表，向量 + tsvector 双索引混合检索）。
- **LLM**：OpenAI 兼容协议（默认 DeepSeek；`LLM_BASE_URL` 可指向内部网关 / vLLM 私有化，企业落地 LLM 不出域）。
- **智能体框架**：deepagents 0.7（langgraph 流式），AI 助手 + 规划智能体共用。
- **知识引擎**：LightRAG 式全量自研移植（四策略分块 / gleaning 抽取 / 三阶段合并 / Louvain 社区 / 六模式查询 / 六格式导出）。
-详见 [架构设计](./TestForge-架构设计.md)。

## 4. 业务能力地图

```
TestForge
├── 知识资产层
│   ├── 仓库接入（git 拉取 / zip 上传）→ 函数索引 + 调用图 + 影响面 + 测试域聚类
│   ├── Wiki 知识编译（repo/module/function 三层页 + 互链图谱 + 体检 + 洞察 + RAG 问答）
│   ├── 知识资产上传（需求文档 / 技术文档 / 测试方案 / 历史缺陷四类 + URL 一键导入）
│   ├── 文档 KG 管线（分块 → LLM 抽取 → 合并 → 社区报告，状态机可观测可恢复）
│   └── 检索体系（七路混合检索 + KG 六模式查询 + 检索质量评估）
├── 测试生产线
│   ├── 需求管理（四步解析 + 可测性门禁 + 冲突确认 + G0~G5 质量流水线）
│   ├── 契约中心（注册版本化 + breaking 检测 + 影响分析 + 受影响用例重生成）
│   ├── 用例生成（四层 ut/fn/api/e2e + 两阶段生成 + 覆盖守卫 + 探针校正 + SSE 进度）
│   ├── 执行验证（真实沙箱 + 修复循环 ≤3 轮 + 分支覆盖率）
│   ├── 用例库（五层归档 + 人审 + 文件形态预览 + 相似 RAG）
│   ├── 缺陷闭环（失败自动建缺陷 + AI 修复建议 + 定向回归）
│   └── 计划与报告（迭代准入/准出自动判定 + 测试报告生成）
├── 质量运营
│   ├── 任务队列（持久化 + 崩溃恢复 + 台账）
│   ├── 全链路追溯（traceID 贯穿八类事件）
│   └── 需求质量流水线（G0~G5 关卡 + 加权质量分）
└── 平台底座
    ├── 认证与权限（HMAC token + admin/viewer + 限速 + prod 拒启检查）
    ├── AI 助手（deepagents 深度代理 + 12+ 工具 + 强制引用溯源 + 跨会话记忆）
    ├── 开放接入（MCP Server 只读 13 工具 + Ollama 兼容层 + git webhook）
    └── 运维工具链（make demo-m0~m5 / kg 运维脚本 / RAG 评估 / LLM 缓存管理）
```

## 5. 核心业务流程

### 5.1 端到端主链路

```
仓库接入（git / zip）→ 全量索引（函数卡片 + 调用图 + 影响面 + 聚类）→ Wiki 三层编译
     ↓
需求录入 → 四步解析（可测性 G0 评分 <80 打回）→ 冲突确认 → 自动编排生成
     ↓
上下文组装（六路：code > contract > wiki > kg > trace > similar > bugs，token 预算裁剪）
     ↓
两阶段生成（阶段 A 规划用例清单 → 覆盖守卫补类 → 探针捕获真实期望 → 阶段 B 确定性渲染 pytest）
     ↓
沙箱执行（local 子进程 / docker 容器）→ 失败修复循环（≤3 轮，探针重捕获重写断言）
     ↓
入库（通过→待人审→已入库；同目标旧用例置「已替换」）→ RAG 索引 → 人审
     ↓
迭代计划（准入/准出自动判定）→ 测试报告 → G0~G5 质量流水线
```

### 5.2 两条无人值守自动闭环

1. **变更驱动回归闭环**：`pull`/webhook → git diff 行级 hunk 归因受影响函数 → 命中用例标 `stale` → 回归任务入队 → 按文件真实执行 → 通过清 stale、失败保持 stale 并自动建缺陷 → 同时增量重建受影响 Wiki 页（per-repo 编译锁）。
2. **缺陷闭环**：执行失败（超修复轮）→ 自动建缺陷（四向关联 run/用例/需求/trace，严重度按失败数定级）→ AI 生成根因分析 + 修复建议 → 修复后定向回归 → 通过自动关闭并沉淀教训入知识库（kind=lesson）。

### 5.3 知识文档管线（入口②）

上传（四类资产 / KG 文档 / URL 导入）→ 敏感门卫（11 条正则，写入侧命中即拒）→ 评估门卫（知识分 <0.5 拒绝；qa 类无来源拒绝）→ 双写汇流：业务检索库（rag_documents 即时可检索）+ KG 异步管线（pending→processing→ok/failed 状态机）。历史缺陷特殊：结构化入 defects 表 + 摘要索引（KG 只做投影，不逐行走 LLM）。

## 6. 功能需求详述

> 编号规则：FR-<域><序号>。「验收」列为可演示口径。

### FR-A 系统与账号

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-A1 | 登录认证 | 用户名密码登录签发 HMAC token；限速防爆破；停用账号即时拒绝（60s 缓存） | 错误 5 次锁定；token 12h 过期 |
| FR-A2 | 令牌续签 | 剩余寿命 <6h 时响应头 `X-Renewed-Token` 自动续签，前端无感换新 | 抓包可见续签头 |
| FR-A3 | 用户管理 | admin 创建/改角色/停用/删除账号、重置密码；防呆：不能操作自己、保底最后一个可用 admin | 删自己被拒 1005 |
| FR-A4 | 强制改密 | 非 admin 默认口令用户仅可改密与聊天 | must_change 用户访问其他接口 403 |
| FR-A5 | 仪表盘 | 汇总仓库/用例（分层/状态）/待审/执行通过率/需求/任务/缺陷/Wiki/契约全平台统计 | 空库显示四步快速开始引导 |
| FR-A6 | 服务健康 | 网关→7 个微服务链路状态探测（mono 模式直调 Ping） | `/api/system/services` all_green |

### FR-B 仓库与代码知识

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-B1 | git 仓库接入 | URL 校验（本地路径需显式开关）→ clone → 全量索引 → Wiki 编译 → 全链路留痕 | 注册后 functions/wiki 立即可查 |
| FR-B2 | zip 上传建仓（入口①） | multipart zip 安全解压（zip-slip 防护 / ≤200MB / ≤2 万成员 / 解压总量 ≤1GB / 排除目录剪枝 / GitHub 单根剥壳）→ 同构索引流水线；同名重传 = 更新图谱 | 上传即出函数数/调用边数/Wiki 页数 |
| FR-B3 | 增量拉取 | `git pull --ff-only` → 行级 hunk 变更归因 → 事务内原子换索引 → 触发回归 + Wiki 增量重建 | pull 返回 changed_functions |
| FR-B4 | webhook 触发 | git webhook 接收端，可选 `X-Webhook-Secret` 校验，后台拉取立即响应 | push 后自动回归 |
| FR-B5 | 多语言函数索引 | tree-sitter 15 语言函数卡片 + 调用边（py/go/java/js…） | 混合语言仓索引正确 |
| FR-B6 | 变更影响面 | 索引期预计算 blast radius（反向可达集/深度/评分，置信度 1/depth，标注 lower-bound） | impact 接口返回受影响调用方集合 |
| FR-B7 | 图分析四件套 | Tarjan SCC 调用环检测 / 入口最长调用链 / 入口→出口执行流识别 / Louvain 功能聚类（测试域） | clusters/cycles/chains/processes 四接口 |
| FR-B8 | 跨函数追溯 | 两函数间最短调用路径（BFS 逐跳，max_depth=10） | trace 接口给出完整路径 |

### FR-C Wiki 知识编译

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-C1 | 三层页编译 | repo/module/function 三层 Markdown 页，**确定性渲染无 LLM**；函数级含签名/文档/调用关系 | rebuild 返回 pages_rebuilt |
| FR-C2 | 互链图谱 | TF-IDF 余弦相似自动建边 + 问答存档 qa_reference 边，前端可视化 | graph 接口返回 nodes/edges |
| FR-C3 | 依赖传播 | 代码变更 → 受影响页 stale 标记 → 增量重建仅重编受影响页（rev 递增） | 重建后 stale 清零 |
| FR-C4 | Wiki 体检 | stale / 超短页 / 重复标题 / 孤儿页 / 模块覆盖缺口五类 findings；重复标题一键处置（先清依赖边再删页） | lint 接口返回 findings |
| FR-C5 | 知识洞察 | 总览 / 知识缺口 / 过期风险 / 焦点 / 建议，LLM 归纳失败降级纯统计，300s 缓存 | insights 接口 |
| FR-C6 | Wiki 问答 | 三阶段 RAG：指代解析 + 时间过滤 → 混合检索 → 依据资料作答 → 引用修补；无资料时诚实回 `no_data`（反幻觉） | 答案带 sources |
| FR-C7 | 问答会话 | 多会话管理（消息落库 / 首问自动标题 / 跨轮指代解析取上轮来源） | 会话列表/消息/删除 |
| FR-C8 | 一键重建 | 全量/增量重建，per-repo 编译锁防并发（冲突 409） | 并发第二个重建请求 409 |

### FR-D 需求管理

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-D1 | 需求录入四步管线 | 建单 → 解析（正则/关键词规则抽取，全确定性）→ Wiki diff/冲突检测 → 编排建议；入 RAG 检索库 | ingest 返回 REQ 编号 + 评分 |
| FR-D2 | 可测性门禁（G0） | 评分 <80 自动打回；冲突置「待确认」 | 低分需求被拒 |
| FR-D3 | 冲突确认 | 人工 approve → 需求生效 + 新增规则回写 Wiki 页 + **自动编排生成**（响应带 generation_id）；reject → 打回 | 确认后生成任务自动入队 |
| FR-D4 | 质量流水线 | G0 可测性(0.2) / G1 知识就绪(0.1) / G2 用例覆盖(0.25) / G3 执行验证(0.25) / G4 缺陷清零(0.1) / G5 准出报告(0.1)，加权质量分 + 人工介入计数 | quality 接口六关卡全展示 |
| FR-D5 | 相似用例推荐 | 需求维度向量检索相似历史用例 | similar 接口 top-K |

### FR-E 契约中心

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-E1 | 契约注册 | rest/grpc/topic 三类契约版本化注册，消费方按函数源码扫描自动匹配 | 同名注册产生新版本 |
| FR-E2 | breaking 检测 | 确定性规则：删端点/删 rpc/删字段/新增必填/改类型 → breaking 标记 + diff 历史 | diff 接口返回 breaking 布尔 |
| FR-E3 | 影响分析 | breaking → Wiki stale → 用例打标 → 受影响用例清单（gRPC 流式聚合） | impact 返回资产列表 |
| FR-E4 | 契约变更重生成 | 一键触发受影响用例定向重生成入队 | regenerate 返回 generations 列表 |

### FR-F 用例生成（核心能力）

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-F1 | 四层用例生成 | ut 单元 / fn 功能（共用函数级管线）/ api 接口 / e2e 端到端（共用 web 管线：实时抓运行中服务 openapi.json，target 传 `web-{layer}` 无需选函数） | 工作台四层 Segmented 各出用例 |
| FR-F2 | 两阶段生成 | 阶段 A：用例清单规划（LLM 智能体只设计输入与场景，禁编造期望值；精选/探针靶标走确定性路径）；阶段 B：按清单**确定性渲染** pytest 源码 | 同一清单重复渲染结果一致 |
| FR-F3 | 覆盖守卫 | 四类用例（normal/boundary/exception/permission）缺类自动补；数值补极值、字符串补 NULL/空、任意参数补类型错；权限关键词触发 permission 用例强制存在 | 生成清单四类齐备 |
| FR-F4 | 探针校正 | 对仓库检出真实代码执行捕获期望值，双跑一致性校验（不一致判非确定性诚实跳过）；LLM 错误断言被真实结果覆写；固定时钟保证时间/HMAC 断言可重放 | ut 层入库率 ≥95%（实测 97%） |
| FR-F5 | 六路上下文 | code > contract > wiki > kg > trace > similar > bugs 优先级组装，token 预算 16000 裁剪，citations 引用登记 | 生成事件含上下文引用 |
| FR-F6 | 批量生成 | 按模块或函数名清单批量入队（去重上限 50） | batch 返回任务列表 |
| FR-F7 | 生成进度 SSE | 五阶段事件（plan→guard→probe→codegen→result）实时推送 + 断线回放 | 前端进度卡逐阶段点亮 |
| FR-F8 | 定向重生成 | 契约影响 / 缺陷修复 / 手动指定目标的重生成（`RegenerateAffected` 修复回填） | rerun 后旧用例置已替换 |

### FR-G 执行验证

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-G1 | 真实沙箱 | local=本机子进程真实 pytest（默认）；docker=`testforge-sandbox:py312` 容器（`--network none` / 512m / 1cpu）；产物 junitxml + coverage json | runs 记录 sandbox_status |
| FR-G2 | 修复循环 | 失败用例 ≤3 轮 `RegenerateAffected`：探针重捕获期望值重写断言（「修复不是重试」），新代码回填重执行 | repair_rounds 如实记录 |
| FR-G3 | 覆盖率 | 分支覆盖率按被测模块精确统计口径回填（web 层 cov=off） | coverage 字段 |
| FR-G4 | 执行台账 | runs 保留最近 500 条；通过率/耗时/触发方式全记录 | runs 列表 |

### FR-H 用例库

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-H1 | 分层归档 | 五层（ut/fn/api/e2e/contract）+ 四分类（normal/boundary/exception/permission）；服务端分页 | cases 接口 total/by_layer |
| FR-H2 | 人审流 | 草稿 → 待人审 → approve 已入库 / reject 回草稿，review_note 留痕 | review 接口状态流转 |
| FR-H3 | 文件形态 | 用例详情为 `.py` 文件预览 + 下载；可一键导出回写仓库 `tests/testforge_generated/` | file 接口 + export-to-repo |
| FR-H4 | 资产更新语义 | 同仓库+同目标+同层重新生成时旧用例置「已替换」（历史可查不堆积） | cases 缺省隐藏已替换 |
| FR-H5 | stale 管理 | 目标函数变更自动标 stale；回归通过自动清除 | stale 过滤查询 |
| FR-H6 | 相似检索 | 用例进混合检索库（kind=case），供上下文组装与相似推荐 | search 命中 |

### FR-I 缺陷闭环

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-I1 | 自动建缺陷 | 执行失败超修复轮自动建 BUG 单；四向关联（run/用例/需求/trace）；严重度按失败数定级 | 失败 run 后 defects 出现新单 |
| FR-I2 | AI 修复建议 | 根因分析 + 修复建议 Markdown，落库带缓存；LLM 不可用 503 | suggestion 接口 |
| FR-I3 | 定向回归 | 重跑关联用例：全过自动关闭 / 失败重开（状态先置修复中） | regression 接口 |
| FR-I4 | 生命周期 | 新建→已确认→修复中→待回归→已关闭；关闭时沉淀教训入知识库（kind=lesson 供后续生成检索） | status 流转 + lesson 入库 |
| FR-I5 | 知识资产缺陷导入 | 历史 defect CSV/XLSX 批量导入（中文表头模糊映射），结构化入表 + 摘要索引 | 上传返回导入条数 |

### FR-J 计划与报告

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-J1 | 迭代计划 | 按版本 + 需求集合建迭代；实时准入/准出核对（冒烟通过、评审通过率 ≥90%、执行 100%、致命缺陷清零等确定性规则） | plans 接口 entry/exit 状态 |
| FR-J2 | 测试报告 | 平台真实数据汇总生成报告，结论自动生成；需求 G5 关卡联动 | report 接口 |

### FR-K 任务队列

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-K1 | 持久化队列 | jobs 表落库（generate/regression 两类），worker 并发 2，`FOR UPDATE SKIP LOCKED` 幂等取任务 | 崩溃重启任务重排队 |
| FR-K2 | 台账 | 任务状态/耗时/错误全可查 | jobs 列表 + 详情 |

### FR-L 全链路追溯

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-L1 | traceID 贯穿 | 非 GET 请求自动生成 trace_id（响应头 `X-Trace-Id`），gRPC metadata 透传，八类事件（需求/生成/执行/仓库/契约/缺陷/计划/认证）同 ID 串联 | traces/{id} 全链可回放 |
| FR-L2 | 脱敏 | 事件入库前 sanitize（Bearer/password/token/sk- 掩码） | 日志无明文密钥 |

### FR-M 知识资产（入口②）

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-M1 | 四类资产统一上传 | req_doc 需求文档 / tech_doc 技术文档 / test_plan 测试方案 / defect 历史缺陷；支持 txt/md/rst/csv/pdf/docx（defect 仅 csv/xlsx）；req_doc 走需求四步管线（评分即门禁不重复评估）。**知识资产页为统一 Hub**：「资产库」Tab（每类资产带去向说明 + 多文件批量上传逐文件反馈 + 粘贴/URL 导入）+「图谱管线」Tab（原独立「文档管线」菜单收拢，LightRAG 处理状态轮询） | 四类各传一份全闭环 |
| FR-M2 | 门卫链 | 解析（pdf/docx 全文）→ 敏感扫描（11 条正则命中 422）→ 评估门卫（知识分 <0.5 拒绝）→ 双写入库 | 敏感文件被拒 |
| FR-M3 | 双写汇流 | 业务检索库（rag_documents 即时可检索）+ KG 异步管线（workspace=repo:{id}）；kind 不映射 workspace（保住跨 kind 连边与社区） | 上传后立即检索命中 + KG 管线 ok |
| FR-M4 | URL 一键导入 | 抓取网页正文（微信公众号/GitHub README 专用路由 + 通用 HTML）→ 敏感扫描 → 入库 | 贴 URL 即入检索库 |
| FR-M5 | 检索测试台 | 七路混合检索（req/plan/case/defect/user_doc/lesson/wiki），limit/hide_sensitive（命中片段打码）可调；**收拢至「检索中心 · 检索测试台」Tab**（原知识资产页内嵌入口移出） | search 返回打分片段 |
| FR-M6 | 闭环总览 | 各 kind 计数 + 业务消费计数（需求/用例/Wiki/函数/缺陷） | summary 接口 |
| FR-M7 | 摄入健康 | doc_status 聚合视图（kind×status）+ 最近失败原因 | status 接口 |

### FR-N 知识图谱（LightRAG 式全量能力）

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-N1 | 文档异步管线 | 纯文本/文件上传 → pending→processing→ok/failed 状态机（后台 worker，崩溃恢复 recover_and_start）；管线=解析→四策略分块（fixed/recursive/vector/paragraph）→chunk 持久化→逐 chunk LLM 抽取+gleaning→三阶段合并→社区 dirty 标记 | 上传后轮询到 ok |
| FR-N2 | 删除重建 | 删文档撤 chunk + 从剩余抽取结果重建图谱（不重跑 LLM，kg_extractions 留存） | 删除后实体/关系同步收敛 |
| FR-N3 | 六模式查询 | naive（原文）/ local（实体层）/ global（关系+社区层）/ hybrid / mix（默认）/ +websearch 兜底；双层关键词（high/low）分头召回 → token 预算 → source 反查引用 → rerank → 流式生成 + 答案缓存 | 六模式各出结果 |
| FR-N4 | SSE 流式问答 | `retrieved → delta* → done` 帧协议，带 contexts/references | 流式回答带引用 |
| FR-N5 | 图谱可视化与编辑 | **知识图谱 Hub 双 Tab**：「代码图谱」（函数/调用边/影响面/执行流，Sigma）+「文档图谱」（KG 实体/关系/社区，Sigma，类型图例/社区着色/focus 子图展开 depth≤4/实体改名合并删除/导出）；Tab 懒渲染（首次激活才挂载，Sigma 初始化延后） | 双 Tab 各自可交互编辑 |
| FR-N6 | 社区检测与报告 | Louvain 两层社区（level1 基础 + level2 map-reduce 报告），LLM 摘要失败回退确定性拼接；报告入检索库供 global 查询 | communities 接口 |
| FR-N7 | 多格式导出 | entities/relations/communities/chunks/graph × json/csv/md/xlsx/graphml/zip（base64 内嵌下载） | 六格式可下载 |
| FR-N8 | 多工作区 | workspace 列隔离（`repo:{id}` / `default` / legacy 空串），老数据零破坏 | workspaces 接口 |
| FR-N9 | 运行时参数 | 分块策略/大小/重叠、gleaning 轮数、查询缓存、websearch 开关等白名单参数运行时可调（kg_settings 表，优先于 .env） | settings PUT 后生效 |
| FR-N10 | Ollama 兼容 | `/ollama/api/*`（version/tags/chat/generate/embeddings），chat 走完整 KG 检索→生成管线；dev 放行、prod 必须配 key | 任意 Ollama 客户端直连 |

### FR-O AI 助手（平台主入口）

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-O1 | 深度代理对话 | deepagents 0.7 + ChatDeepSeek 流式；langgraph 双流映射（token 流 + 工具事件流）；长对话自动摘要；跨会话记忆（memory.md 注入） | 18+ 工具轮次深调查不爆上下文 |
| FR-O2 | 工具面 | 领域工具：search 混合检索 / explore KG 查询 / read 源码 / impact 影响面 / changes 变更 / trace 调用路径 / overview 概览 / cases 用例 / **generate_case 四层生成** / generation_status / **create_requirement 需求录入** / **upload_knowledge 文档入库** + deepagents 内置 grep/glob/ls/read_file（`/repo/` 只读）与 execute（thread 沙箱） | 对话中完成生成与录入闭环 |
| FR-O3 | 强制引用溯源 | grounding 协议：每个结论带 `[[路径:起-止行]]` / `[[Function:名]]`，前端渲染可点击标签直达对应页面；无证据明说 | 答案均带引用标签 |
| FR-O4 | 写操作鉴权 | 工具层 `_require_admin` 二次拦截（viewer 可聊天不可写）；会话未绑仓库时生成工具自动反查真实 repo | viewer 触发生成被拒 |
| FR-O5 | 执行沙箱 | thread-scoped 工作区 + 环境变量白名单（.env 密钥不透传）+ 超时 180s + 输出截断 2 万字符 + 仓库 `/repo/` 只读挂载 | execute 跑通且拿不到宿主密钥 |

### FR-P 开放接入

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-P1 | MCP Server | stdio JSON-RPC 2.0，**只读 13 工具**（函数检索/影响面/调用路径/变更/环/链/执行流/wiki 问答与体检/用例检索/符号上下文/KG 查询/健康总览）；写操作不旁路 REST 鉴权 | Claude Desktop 一键配置接入 |
| FR-P2 | git webhook | 见 FR-B4 |
| FR-P3 | Ollama 兼容层 | 见 FR-N10 |
| FR-P4 | 开放接入页 | MCP / webhook / Ollama 三种接入方式的指引页 | openaccess 视图 |

### FR-Q 系统设置与运维

| 编号 | 需求 | 说明 | 验收 |
|---|---|---|---|
| FR-Q1 | KG 运行时设置 | 检索参数白名单热更新 + 能力状态（embedding/rerank/websearch）+ 缓存管理 | settings 视图 |
| FR-Q2 | LLM 缓存管理 | 抽取缓存台账 + 按角色清除 | cache 接口 |
| FR-Q3 | 运维脚本 | make：kg-rebuild-vdb 重嵌全库 / kg-clean-cache / kg-repair / kg-communities / rag-eval 检索质量（recall@k/MRR 对照）/ seed-real 真实数据种子 | 脚本一键可跑 |
| FR-Q4 | 生产安全检查 | ENV=prod 拒启五项：默认 SECRET_KEY/ADMIN_PASSWORD、缺 LLM key、沙箱未容器化、允许本地仓库路径、Ollama 未设密钥 | 默认配置 prod 启动被拦 |

## 7. 非功能需求

| 维度 | 要求 | 现状 |
|---|---|---|
| 性能 | 生成/回归异步化不阻塞网关；检索秒级响应 | jobs 队列 worker 并发 2；RRF 融合检索 |
| 可靠性 | 任务崩溃可恢复；文档管线断点可续 | jobs SKIP LOCKED 重排队 + doc_status recover_and_start |
| 可观测 | 全链路 traceID + 结构化 JSON 日志 + 生成事件流 + 摄入状态台账 | trace_events / generation_events / doc_status |
| 安全 | 认证/角色/限速/prod 拒启/敏感双端拦截/zip-slip 防护/沙箱网络隔离/环境白名单/MCP 只读 | 见架构文档 §安全设计 |
| 可移植 | LLM 出域配置即达；单机零 Docker 可跑（PG 走 WSL） | LLM_BASE_URL + pg_tunnel + dev_up |
| 质量 | pytest 全绿 + ruff + mypy + tsc/vite build | 130 pytest 全绿（2026-10-10） |
| 数据治理 | 用例资产更新语义（已替换不堆积）；runs 保留 500 条；软删除留痕 | prune_runs / 已替换状态 |

## 8. 页面清单（20 视图 · Hub 收敛式信息架构）

| 菜单组 | 视图 |
|---|---|
| 顶级 | 仪表盘（空库=四步快速开始 / 有库=待办直达）· AI 助手（第一项·主入口） |
| 测试流程 | 需求录入 → 测试计划 → 生成工作台（四层 Segmented）→ 用例库（文件预览）→ 执行记录 → 缺陷管理 |
| 知识资产 | 仓库接入（git/zip 双入口）→ 知识资产（统一 Hub：资产库 + 图谱管线双 Tab）→ 代码库 Wiki（互链图谱/问答/体检）→ 知识图谱（双视图 Hub：代码图谱 + 文档图谱）→ 检索中心（三合一 Hub：查询实验 + 检索测试台 + 检索质量） |
| 质量运营 | 需求质量流水线（G0~G5）· 服务地图/契约 · 任务队列 · 日志/追溯 |
| 系统 | 开放接入（MCP/webhook/Ollama）· 系统设置（KG 运行时参数/LLM 缓存/语言）· 用户管理（admin） |

**Hub 收敛原则**：同域功能收拢为一个页面多 Tab，砍掉重复菜单入口——「文档管线」并入知识资产页、「文档图谱」并入知识图谱页（双 Tab 懒渲染，Sigma 初始化延后到首次激活）、「检索实验室/检索测试台/检索质量」三处检索入口合并为检索中心；知识资产入口按知识生产链排序提前到 Wiki 之前。

交互约定：NextStep 引导（接入成功→看 Wiki→去生成）；AI 引用标签点击直达对应页面；中英双语（antd 组件文案随语言切换联动）+ 亮暗主题。

## 9. 里程碑与验收证据

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0~M2 | 仓库索引 / Wiki 编译 / 需求管线 / 生成管线骨架 | ✅ |
| M3 | 沙箱执行 + 修复循环 + 缺陷闭环 | ✅ |
| M4 | 契约中心 + 变更回归 + 计划报告 | ✅ |
| M5 | AI 助手（deepagents）+ 执行沙箱 + 四层生成达标 | ✅ ut 97% / fn 100% / api 100% / e2e 100% |
| 知识层九轮演进 | GitNexus 图谱/影响面/聚类 + OpenWiki 门卫/敏感/多会话问答 + LightRAG 全量移植（六模式/分块/gleaning/社区/管线/导出/Ollama）+ 知识资产两入口闭环 | ✅ 130 pytest 全绿 + 路由冒烟全 200 + 真实网关端到端 6 步全过 |

## 10. 已知边界与路线图

| 级别 | 事项 |
|---|---|
| P1 | `interrupt_on` 写操作人工确认（SSE 下确认 UI + resume 协议）——写操作当前依赖角色鉴权 |
| P1 | 强时间/随机依赖函数的自动打桩深化（时钟已固定，签名盐/随机源待自动 monkeypatch） |
| P1 | LLM 用量计量（tokens/成本按仓库汇总）；compose 一键交付 + 备份脚本 |
| P2 | SSO/LDAP；共享任务队列（多实例）；Alembic 正式迁移（现为轻量 ADD COLUMN）；generations 生命周期策略 |
| P2 | MinerU/Docling 高级文档解析引擎包装（当前按需安装未接入）；RAGAS/Langfuse 观测接入（contexts 已 RAGAS-ready） |
| P2 | GitNexus 边 confidence+reason 落库；lint 覆盖盲区规则 |
| P3 | token 过期前端自动跳登录；图谱渲染升级 AntV G6；E2E 旅程样例数据过滤优化 |
