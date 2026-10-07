# GitNexus + OpenWiki 功能复刻对照（2026-10-07）

> 源项目：[abhigyanpatwari/GitNexus](https://github.com/abhigyanpatwari/GitNexus)（代码智能引擎，Sigma.js+Graphology+FA2+tree-sitter+Leiden）
> [kdsz001/OpenWiki](https://github.com/kdsz001/OpenWiki)（Tauri 桌面 AI 知识管理：捕获→LLM 编译成个人 Wiki+图谱）
> 本文档记录两项目功能在 TestForge 的落点：已有 / 本轮实现 / 不适用（含原因）。

## 一、GitNexus → TestForge

### 本轮实现
| GitNexus 功能 | TestForge 落点 | 实现 |
|---|---|---|
| Sigma.js 图可视化（GraphCanvas+useSigma） | `frontend/src/views/KnowledgeGraph.tsx` 全量重写 | sigma@3.0.2 + graphology@0.26 + FA2 worker（参数按节点数分档：gravity 0.8→0.15 / scalingRatio 15→100 / barnesHut>200）+ noverlap 收尾 + @sigma/edge-curve 曲线边；labelDensity 0.16 + labelGridCellSize 80 + threshold 7 控标签密度（毛球根治）；nodeReducer/edgeReducer 高亮邻接暗化其余；相机动画聚焦；沉浸式工作台布局（全高画布+悬浮工具条+角标图例） |
| force / circles 双布局 | 同上 | FA2 力导 + 同心圆（类别分环），一键切换/重排 |
| 搜索定位（focusNode） | 画布左上搜索框 | AutoComplete 过滤节点 → 选中 → 相机 animate(ratio 0.35, 400ms) |
| impact / blast radius | 影响半径按钮 | 选中函数节点 → `GET /api/functions/{name}/impact`（存量接口）→ 命中节点红色高亮 + 其余暗化，边只保留命中间的 |
| trace 两符号最短路径 | `GET /api/functions/trace?src=&dst=` + MCP 工具 `trace_path` + 助手工具 `trace` | `services/repo_svc/impact.py::trace_path`（caller→callee BFS，逐跳返回）；实测 index_repo→get_session 3 跳 |
| 类别过滤 | 图例胶囊（左下角标） | 需求/用例/缺陷默认关（仓库接入→代码结构自闭环），点击叠加 |

### 已有（前轮借鉴，本轮核对确认）
tree-sitter 解析与调用图（repo_svc/indexer.py）、Leiden 式聚类（/api/repos/{id}/clusters）、混合检索 BM25+向量 RRF（/api/kg/query + search_cases）、符号 360° 上下文（MCP get_symbol_context）、impact 预计算表（FnImpact）、MCP stdio 服务器（services/mcp_server.py，本轮 6→7 工具）、detect_changes 等价物（wiki_builder.rebuild 的 changed_files 增量 + stale 传播）。

### 不移植（原因）
- Web 端 WASM 本地索引 / LadybugDB：TestForge 是服务端索引架构，已有 SQLite+RAG 层。
- PDG/污点分析（CFG/TAINTED 等）：GitNexus 自己也未默认启用（opt-in 且部分边类型未产出）。
- rename 多文件重命名、read_file/grep 工具：属代码编辑代理场景，与测试生成平台定位不符。

## 二、OpenWiki → TestForge

### 本轮实现
| OpenWiki 功能 | TestForge 落点 | 实现 |
|---|---|---|
| TF-IDF 页面互链（link_pages_by_shared_tags） | `GET /api/wiki/{id}/related` + Wiki 抽屉「相关页面」 | `services/wiki_builder/analytics.py::related_map`：ASCII 词+中文二元组、IDF=ln((N+1)/(df+1))、每页 top-40 判别 token、倒排稀疏点积、阈值 0.05（OpenWiki 0.3 是长文 tag 向量，中文短页实测 0.05 区分度最好）、top-6；实测 create_order 的邻居全部落在订单模块（release/reserve/get_price/测试/模块页，0.21-0.31） |
| Wiki 健康体检（lint） | `GET /api/wiki/lint?repo_id=` + Wiki 页「Wiki 体检」卡 | `analytics.py::lint_repo` 确定性规则：stale 页/超短页(<120字)/重复标题/孤儿页(无互链)/模块覆盖缺口，severity 三档；实测抓出 26 个「函数 main」重名页 |

### 已有映射
Wiki 问答 RAG → TestForge AI 助手（search/explore/knowledge_query 工具 + 引用溯源）；Wiki 自动编译 → wiki_builder（repo→repo/module/function 三级页面+增量重建）；多提供商 LLM → TestForge LLM 网关；MCP 只读服务器 → services/mcp_server.py。

### 不移植（原因）
- 剪贴板监听/捕获浮窗/Spotlight 热键/截图 OCR：桌面 OS 能力，Web 产品形态无法承接（OpenWiki 是 Tauri 应用）。
- 每周报告/注意力雷达/偏好学习：个人知识管理场景，与测试平台无关。
- Codex/Gemini OAuth、本地 Ollama 信号量门：TestForge 已有私有化 LLM 指引与网关。

## 三、验证证据（design/round-04-graph-fix/shots/）

- `v2-light-graph.png` / `v2-dark-graph.png`：Sigma 沉浸式图谱亮暗两态（FA2 收敛后 hub-spoke 可读、标签密度受控、主题跟随）。
- `final-light/dark-servicemap.png`：服务调用链路（分层贝塞尔+胶囊标签+breaking 红点，亮暗）。
- `final-light/dark-wiki.png`：Wiki 体检卡。
- 冒烟：`smoke_kg.py`（控制台零错误，仅存量 401/antd 弃用告警）；端点实测 trace 3 跳、lint 真发现、related 模块级召回。
