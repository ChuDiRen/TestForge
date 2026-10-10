/** 轻量 i18n（zh/en）：覆盖 LightRAG 全量移植的四个新页面（管线/检索实验室/文档图谱/设置）。
 *  存量页面保持中文原文；语言选择持久化 localStorage，antd locale 由 App.tsx 同步切换。 */

import { useSyncExternalStore } from 'react'

export type Lang = 'zh' | 'en'

const STORAGE_KEY = 'tf_lang'
let current: Lang = (localStorage.getItem(STORAGE_KEY) as Lang) || 'zh'
const listeners = new Set<() => void>()

export function setLang(lang: Lang): void {
  current = lang
  localStorage.setItem(STORAGE_KEY, lang)
  listeners.forEach((l) => l())
}

export function getLang(): Lang {
  return current
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

const dict = {
  pipeline: {
    title: { zh: '文档管线', en: 'Document Pipeline' },
    subtitle: {
      zh: 'LightRAG 式摄入：文件上传 → 智能分块 → 实体/关系抽取（gleaning）→ 知识图谱合并 → 六模式可检索',
      en: 'LightRAG-style ingestion: upload → chunking → entity/relation extraction (gleaning) → KG merge → searchable in 6 modes',
    },
    dropTitle: { zh: '点击或拖拽上传文档', en: 'Click or drag files to upload' },
    dropHint: {
      zh: '支持 pdf / docx / txt / md / csv · 多文件 · 自动分块抽取建图',
      en: 'pdf / docx / txt / md / csv · multi-file · auto chunk & extract',
    },
    warnKey: {
      zh: '抽取调用 LLM（llm_cache 缓存去重）：未配置 LLM_API_KEY 时文档会停在 failed 状态并给出原因。',
      en: 'Extraction calls LLM (dedup via llm_cache): without LLM_API_KEY docs fail with the reason attached.',
    },
    workspace: { zh: '工作区', en: 'Workspace' },
    buildCommunities: { zh: '构建社区报告', en: 'Build Communities' },
    exportGraph: { zh: '导出图谱', en: 'Export Graph' },
    listTitle: { zh: '管线文档', en: 'Pipeline Documents' },
    inflight: { zh: '{n} 篇在途', en: '{n} in flight' },
    allStatus: { zh: '全部状态', en: 'All statuses' },
    search: { zh: '搜索标题', en: 'Search title' },
    empty: { zh: '还没有管线文档——上传 PRD/设计文档/笔记，自动进知识图谱', en: 'No documents yet — upload PRDs/design docs/notes to build the KG' },
    docTitle: { zh: '文档', en: 'Document' },
    status: { zh: '状态', en: 'Status' },
    progress: { zh: '产出', en: 'Output' },
    updatedAt: { zh: '更新时间', en: 'Updated' },
    viewChunks: { zh: '查看分块', en: 'View chunks' },
    delConfirm: {
      zh: '删除该文档？将撤除其 chunk 与图谱贡献（从剩余抽取重建）',
      en: 'Delete? Removes chunks and KG contributions (rebuilt from remaining extractions)',
    },
    deleted: { zh: '文档已删除，图谱贡献已撤除', en: 'Deleted; KG contributions retracted' },
    uploaded: { zh: '《{title}》已入管线', en: 'Queued: {title}' },
    communitiesBuilt: { zh: '社区构建完成：{n} 个社区', en: 'Communities built: {n}' },
    chunksTitle: { zh: '分块预览', en: 'Chunk Preview' },
    noChunks: { zh: '暂无分块（文档可能仍在处理中）', en: 'No chunks yet (still processing?)' },
  },
  lab: {
    title: { zh: '检索实验室', en: 'Retrieval Lab' },
    subtitle: {
      zh: 'LightRAG 六模式检索对照台：双层关键词召回 → token 预算 → rerank → 流式生成（带引用溯源）',
      en: 'LightRAG 6-mode retrieval bench: dual-level keywords → token budgets → rerank → streaming answer with citations',
    },
    queryPh: { zh: '输入问题，如：支付回调怎么保证幂等？', en: 'Ask a question, e.g. how is payment callback made idempotent?' },
    mode: { zh: '模式', en: 'Mode' },
    modeDesc: {
      naive: { zh: '原文向量检索（不走图谱）', en: 'raw chunk vector search (no KG)' },
      local: { zh: '实体级（低层关键词 → 实体 + 1-hop）', en: 'entity-level (low-level keywords → entities + 1-hop)' },
      global: { zh: '主题级（高层关键词 → 关系 + 社区）', en: 'theme-level (high-level keywords → relations + communities)' },
      hybrid: { zh: 'local + global', en: 'local + global' },
      mix: { zh: 'hybrid + 原文（默认）', en: 'hybrid + chunks (default)' },
    } as Record<string, { zh: string; en: string }>,
    run: { zh: '检索', en: 'Search' },
    stop: { zh: '停止', en: 'Stop' },
    websearch: { zh: '检索枯竭时 Web 兜底', en: 'Web fallback when empty' },
    bypassCache: { zh: '绕过答案缓存', en: 'Bypass answer cache' },
    topk: { zh: '召回数', en: 'Top-K' },
    answer: { zh: '回答', en: 'Answer' },
    answerHint: {
      zh: '回答由 LLM 基于检索上下文生成；未配置 Key 时仅返回检索结果',
      en: 'Answer generated from retrieved context; retrieval-only without LLM key',
    },
    entities: { zh: '实体', en: 'Entities' },
    relations: { zh: '关系', en: 'Relations' },
    communities: { zh: '社区报告', en: 'Communities' },
    chunks: { zh: '原文片段', en: 'Chunks' },
    references: { zh: '引用来源', en: 'References' },
    webResults: { zh: 'Web 兜底', en: 'Web fallback' },
    keywordsHl: { zh: '高层关键词', en: 'High-level keywords' },
    keywordsLl: { zh: '低层关键词', en: 'Low-level keywords' },
    empty: { zh: '输入问题开始检索——右侧将展示召回明细与引用', en: 'Ask to start — hits and references show on the right' },
    cached: { zh: '缓存命中', en: 'cached' },
  },
  docgraph: {
    tabCode: { zh: '代码图谱', en: 'Code Graph' },
    tabDoc: { zh: '文档图谱', en: 'Doc Graph' },
    searchPh: { zh: '按实体名过滤…', en: 'Filter by entity…' },
    expand: { zh: '展开邻居', en: 'Expand' },
    rename: { zh: '改名', en: 'Rename' },
    merge: { zh: '合并到…', en: 'Merge into…' },
    edit: { zh: '编辑', en: 'Edit' },
    del: { zh: '删除', en: 'Delete' },
    exportJson: { zh: 'JSON', en: 'JSON' },
    exportCsv: { zh: 'CSV', en: 'CSV' },
    exportGraphml: { zh: 'GraphML', en: 'GraphML' },
    export: { zh: '导出', en: 'Export' },
    communities: { zh: '社区着色', en: 'Color by community' },
    maxNodes: { zh: '节点上限', en: 'Max nodes' },
    typeAll: { zh: '全部类型', en: 'All types' },
    empty: {
      zh: '图谱为空——到「知识资产」上传文档（图谱管线 Tab 可看处理进度）或跑 kg-build',
      en: 'Empty graph — upload documents in Knowledge Assets (see the Pipeline tab) or run kg-build first',
    },
    sources: { zh: '来源数', en: 'sources' },
    degree: { zh: '度数', en: 'degree' },
    relEdit: { zh: '改关系类型', en: 'Edit relation type' },
  },
  settings: {
    title: { zh: '系统设置', en: 'Settings' },
    subtitle: {
      zh: '检索参数运行时覆盖（优先于 .env）· 缓存管理 · 外观与语言 · API 文档入口',
      en: 'Runtime retrieval params (override .env) · cache management · appearance & language · API docs',
    },
    retrieval: { zh: '检索与分块参数', en: 'Retrieval & Chunking' },
    chunkStrategy: { zh: '分块策略', en: 'Chunk strategy' },
    fixed: { zh: '定长窗口', en: 'Fixed window' },
    recursive: { zh: '递归字符', en: 'Recursive' },
    vector: { zh: '向量语义', en: 'Vector semantic' },
    paragraph: { zh: '段落语义', en: 'Paragraph semantic' },
    chunkSize: { zh: '块大小（token）', en: 'Chunk size (tokens)' },
    chunkOverlap: { zh: '重叠（token）', en: 'Overlap (tokens)' },
    dropRefs: { zh: '丢弃参考引用块', en: 'Drop reference blocks' },
    gleaning: { zh: 'Gleaning 补抽轮数', en: 'Gleaning rounds' },
    queryCache: { zh: '查询答案缓存', en: 'Answer cache' },
    promptPrefix: { zh: '全局指令前缀（USER_PROMPT_PREFIX）', en: 'Global prompt prefix' },
    websearch: { zh: 'Web 搜索兜底', en: 'Web search fallback' },
    saved: { zh: '已保存（对新摄入/查询立即生效）', en: 'Saved (applies to new ingestion/queries)' },
    save: { zh: '保存', en: 'Save' },
    runtime: { zh: '运行时状态', en: 'Runtime' },
    embedding: { zh: 'Embedding 后端', en: 'Embedding backend' },
    rerank: { zh: 'Rerank', en: 'Rerank' },
    websearchAvail: { zh: 'ddgs 可用', en: 'ddgs available' },
    on: { zh: '开启', en: 'on' },
    off: { zh: '关闭', en: 'off' },
    cache: { zh: 'LLM 缓存管理', en: 'LLM Cache' },
    cacheClearQuery: { zh: '清除查询答案缓存', en: 'Clear answer cache' },
    cacheClearExtract: { zh: '清除抽取缓存（下次全量重付 token）', en: 'Clear extraction cache (re-pays tokens)' },
    ops: { zh: '运维入口', en: 'Ops' },
    apiDocs: { zh: 'API 文档（Swagger / OpenAPI）', en: 'API Docs (Swagger / OpenAPI)' },
    rebuildHint: {
      zh: '向量重建 / 完整性修复走 CLI：make kg-rebuild-vdb / kg-repair',
      en: 'Rebuild/repair via CLI: make kg-rebuild-vdb / kg-repair',
    },
    appearance: { zh: '外观与语言', en: 'Appearance & Language' },
    language: { zh: '界面语言', en: 'Language' },
  },
} as const

type Dict = typeof dict
type Template = { zh: string; en: string }
/** 深层把 {zh,en} 模板解析为当前语言字符串后的字典类型 */
export type Resolved<T> = T extends Template ? string : { [K in keyof T]: Resolved<T[K]> }

function resolve<T extends Dict>(node: T, lang: Lang): Resolved<T> {
  const walk = (n: unknown): unknown => {
    if (n && typeof n === 'object') {
      const isTpl = 'zh' in (n as Record<string, unknown>) && 'en' in (n as Record<string, unknown>) && typeof (n as Template).zh === 'string'
      if (isTpl) return (n as Template)[lang]
      const out: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(n as Record<string, unknown>)) out[k] = walk(v)
      return out
    }
    return n
  }
  return walk(node) as Resolved<T>
}

export function useLang(): { lang: Lang; t: Resolved<Dict> } {
  useSyncExternalStore(subscribe, getLang)
  return { lang: current, t: resolve(dict, current) }
}
