import { PageHeader } from '@/components/PageHeader'
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Popconfirm, Segmented, Space, Tabs, Tag, Tooltip, Upload, message } from 'antd'
import { DeleteOutlined, DownloadOutlined, FileTextOutlined, InboxOutlined, LinkOutlined, SearchOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post, uploadKnowledgeAsset } from '@/service'
import { repoName } from '@/utils'
import { useIsMobile } from '@/hooks'
import { KnowledgePipeline } from './KnowledgePipeline'

interface KnowledgeDoc {
  doc_key: string
  repo_id: number
  title: string
  chars: number
  updated_at: string
  kind: string
  source: string
  source_url: string
  sources_count: number
  object_key: string
  req_code?: string
  req_status?: string
}

/** kind → 展示标签（与总览卡配色一致） */
const KIND_TAG: Record<string, { label: string; color: string }> = {
  user_doc: { label: '技术文档', color: 'cyan' },
  req: { label: '需求文档', color: 'purple' },
  plan: { label: '测试方案', color: 'gold' },
}
interface AssetSummary {
  kinds: Record<string, number>
  requirements: number
  cases: number
  wiki_pages: number
  functions: number
  defects_total: number
  defects_open: number
}

type AssetKind = 'tech_doc' | 'req_doc' | 'test_plan' | 'defect'
type KnowledgeDocsTab = 'assets' | 'pipeline'

const ASSET_TABS: { key: AssetKind; label: string; accept: string; hint: string }[] = [
  {
    key: 'tech_doc',
    label: '技术文档',
    accept: '.txt,.md,.markdown,.rst,.csv,.pdf,.docx',
    hint: '接口约定 / 业务规则 / 设计说明——入检索库 + 知识图谱，生成用例的 wiki 路自动引用',
  },
  {
    key: 'req_doc',
    label: '需求文档',
    accept: '.txt,.md,.markdown,.rst,.csv,.pdf,.docx',
    hint: 'PRD 文件走需求解析管线：可测性评分 → G0 门禁 → 待人审；通过后自动建立 REQ 单并进入检索与图谱',
  },
  {
    key: 'test_plan',
    label: '测试方案',
    accept: '.txt,.md,.markdown,.rst,.csv,.pdf,.docx',
    hint: '测试计划/方案文档入 plan 检索路与知识图谱，供检索中心与问答调用',
  },
  {
    key: 'defect',
    label: '历史缺陷',
    accept: '.csv,.xlsx',
    hint: 'csv/xlsx 批量导入（首行表头：标题*, 描述, 严重, 状态, 模块, 需求编号）——入 defects 结构化表 + 摘要进检索库，同模块生成时 bugs 路自动召回',
  },
]

/** 知识资产（入口②统一 Hub）：资产库（四类上传 + 管理，全站唯一上传入口）| 图谱运维（LightRAG 处理状态）。
 *  上传入口曾与图谱管线 Tab 重复且后者无门卫/不进检索库（用户上传后生成不引用），已收拢：管线上传框退役，本页 Tab 降级为纯运维视图。 */
export function KnowledgeDocs() {
  const qc = useQueryClient()
  const isMobile = useIsMobile()
  const [tab, setTab] = useState<KnowledgeDocsTab>('assets')
  const [repoId, setRepoId] = useState(0)
  const [docTitle, setDocTitle] = useState('')
  const [docBody, setDocBody] = useState('')
  const [assetKind, setAssetKind] = useState<AssetKind>('tech_doc')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [results, setResults] = useState<{ name: string; ok: boolean; msg: string }[]>([])
  const repos = useQuery({ queryKey: ['repos'], queryFn: () => get<any[]>('/api/repos') })
  // 有仓库时默认选中第一个（与 Wiki/图谱页一致，消除初始 disabled）
  useEffect(() => {
    if (repoId === 0 && repos.data?.length) setRepoId(repos.data[0].id)
  }, [repos.data, repoId])

  const summaryQ = useQuery({ queryKey: ['asset-summary'], queryFn: () => get<AssetSummary>('/api/knowledge/assets/summary') })
  const docsQ = useQuery({
    queryKey: ['knowledge-docs', repoId],
    queryFn: () => get<KnowledgeDoc[]>(`/api/knowledge/documents${repoId ? `?repo_id=${repoId}` : ''}`),
  })

  const uploadDoc = useMutation({
    mutationFn: () => post('/api/knowledge/documents/create', { title: docTitle, content: docBody, repo_id: repoId || undefined }),
    onSuccess: () => {
      message.success('文档已入库（向量+全文双索引）——AI 生成用例时会自动检索引用')
      setDocTitle('')
      setDocBody('')
      qc.invalidateQueries({ queryKey: ['knowledge-docs'] })
      qc.invalidateQueries({ queryKey: ['asset-summary'] })
    },
    onError: (e: any) => message.error(e.message),
  })
  // URL 一键导入（OpenWiki url_reader 移植·Web 版）：抓取→正文提取→敏感扫描→入库
  const [importUrl, setImportUrl] = useState('')
  const importUrlDoc = useMutation({
    mutationFn: () => post<{ title: string; chars: number }>('/api/knowledge/import-url', { url: importUrl, repo_id: repoId || undefined }),
    onSuccess: (r) => {
      message.success(`已导入《${r.title}》（${r.chars} 字）`)
      setImportUrl('')
      qc.invalidateQueries({ queryKey: ['knowledge-docs'] })
      qc.invalidateQueries({ queryKey: ['asset-summary'] })
    },
    onError: (e: any) => message.error(e.message),
  })
  const delDoc = useMutation({
    mutationFn: (docKey: string) => post(`/api/knowledge/documents/delete?doc_key=${encodeURIComponent(docKey)}`),
    onSuccess: () => {
      message.success('文档已从检索索引移除')
      qc.invalidateQueries({ queryKey: ['knowledge-docs'] })
      qc.invalidateQueries({ queryKey: ['asset-summary'] })
    },
    onError: (e: any) => message.error(e.message),
  })

  const tab_ = ASSET_TABS.find((t) => t.key === assetKind)!

  const summarize = (r: Record<string, unknown>): string => {
    if (r.req_code) return `REQ ${r.updated ? '已更新' : '已建立'}（可测性 ${Number(r.testability ?? 0).toFixed(0)}，${r.status}）`
    if (typeof r.imported === 'number') return `导入 ${r.imported} 条缺陷`
    return `已入库（${r.chars} 字）`
  }

  const uploadAssets = async () => {
    if (!pendingFiles.length) return
    setUploading(true)
    const out: { name: string; ok: boolean; msg: string }[] = []
    for (const f of pendingFiles) {
      try {
        const r = await uploadKnowledgeAsset(f, { kind: assetKind, repo_id: repoId || undefined })
        out.push({ name: f.name, ok: true, msg: summarize(r) })
      } catch (e: any) {
        out.push({ name: f.name, ok: false, msg: e.message ?? String(e) })
      }
    }
    setResults(out)
    setPendingFiles([])
    setUploading(false)
    qc.invalidateQueries({ queryKey: ['knowledge-docs'] })
    qc.invalidateQueries({ queryKey: ['asset-summary'] })
  }

  const s = summaryQ.data
  const stat = (v: number | undefined, label: string, color?: string) => (
    <div key={label} style={{ border: '1px solid var(--tf-line)', borderRadius: 8, padding: '10px 4px', textAlign: 'center' }}>
      <b style={{ fontSize: 20, color }}>{v ?? '-'}</b>
      <div style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>{label}</div>
    </div>
  )

  const assetsPane = (
    <>
      <Card title="上传知识资产" size="small" style={{ marginBottom: 16 }}>
        <Segmented
          options={ASSET_TABS.map((t) => ({ label: t.label, value: t.key }))}
          value={assetKind}
          onChange={(v) => {
            setAssetKind(v as AssetKind)
            setPendingFiles([])
            setResults([])
          }}
          style={{ marginBottom: 10 }}
        />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>归属仓库</span>
          <select value={repoId} onChange={(e) => setRepoId(Number(e.target.value))} style={{ padding: 4 }}>
            <option value={0}>未绑定</option>
            {(repos.data ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {repoName(r.url)}
              </option>
            ))}
          </select>
          <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>{tab_.hint}</span>
        </div>
        <Upload.Dragger
          multiple
          accept={tab_.accept}
          showUploadList={false}
          beforeUpload={(_file, fileList) => {
            setPendingFiles(fileList)
            return Upload.LIST_IGNORE
          }}
          style={{ marginBottom: 10 }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽文件到此处（支持多选）</p>
          <p className="ant-upload-hint">
            {assetKind === 'defect'
              ? '支持 .csv · .xlsx —— 结构化导入 defects 表 + 检索索引'
              : '支持 .pdf · .docx · .md · .txt —— 解析 → 敏感扫描 → 评估门卫 → 入库'}
          </p>
        </Upload.Dragger>
        {pendingFiles.length > 0 && (
          <div style={{ marginBottom: 10 }}>
            {pendingFiles.map((f) => (
              <Tag key={f.name} closable onClose={() => setPendingFiles((arr) => arr.filter((x) => x !== f))}>
                {f.name}（{(f.size / 1024).toFixed(0)} KB）
              </Tag>
            ))}
          </div>
        )}
        <Button type="primary" loading={uploading} disabled={!pendingFiles.length} onClick={uploadAssets}>
          上传 {pendingFiles.length > 0 ? `（${pendingFiles.length} 个文件）` : ''}
        </Button>
        {results.length > 0 && (
          <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed var(--tf-line)', fontSize: 13 }}>
            {results.map((r) => (
              <div key={r.name} style={{ color: r.ok ? 'var(--tf-ok, #15803d)' : '#c93a2e' }}>
                {r.ok ? '✓' : '✗'} {r.name} —— {r.msg}
              </div>
            ))}
          </div>
        )}
        {assetKind === 'tech_doc' && (
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px dashed var(--tf-line)' }}>
            <Input
              placeholder="或直接粘贴：文档标题（如：支付模块业务规则 / 订单接口约定）"
              value={docTitle}
              onChange={(e) => setDocTitle(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <Input.TextArea
              rows={3}
              placeholder="文档正文（markdown）——PRD 片段、接口约定、验收标准、业务规则等。入库后进入检索索引，AI 生成用例会自动引用。"
              value={docBody}
              onChange={(e) => setDocBody(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <Button
              type="primary"
              ghost
              loading={uploadDoc.isPending}
              disabled={!docTitle.trim() || !docBody.trim()}
              onClick={() => uploadDoc.mutate()}
            >
              粘贴入库
            </Button>
            <Space.Compact style={{ width: isMobile ? '100%' : 480, marginTop: 10 }}>
              <Input
                placeholder="URL 一键导入：公众号文章 / GitHub README / 通用页面"
                value={importUrl}
                onChange={(e) => setImportUrl(e.target.value)}
                onPressEnter={() => importUrl.trim() && importUrlDoc.mutate()}
              />
              <Button icon={<LinkOutlined />} loading={importUrlDoc.isPending} disabled={!importUrl.trim()} onClick={() => importUrlDoc.mutate()}>
                抓取入库
              </Button>
            </Space.Compact>
          </div>
        )}
      </Card>

      <Card
        title={`已入库知识文档（${docsQ.data?.length ?? 0} 篇）`}
        size="small"
        extra={
          <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>技术文档同步进入图谱——「图谱运维」Tab 看处理状态，「知识图谱」页看实体关系</span>
        }
      >
        {(docsQ.data?.length ?? 0) === 0 ? (
          <Empty description="还没有知识文档——上传 PRD 片段、接口约定等，生成用例时会自动引用" style={{ margin: '24px 0' }} />
        ) : (
          <div style={{ display: 'grid', gap: 8 }}>
            {docsQ.data!.map((d) => {
              const kindTag = KIND_TAG[d.kind] ?? KIND_TAG.user_doc
              return (
                <div key={d.doc_key} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
                  <FileTextOutlined style={{ color: 'var(--tf-ink-3)', flexShrink: 0 }} />
                  <span style={{ fontWeight: 500, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.title}</span>
                  <Tag color={kindTag.color} style={{ flexShrink: 0 }}>
                    {kindTag.label}
                  </Tag>
                  {d.kind === 'req' && d.req_status && (
                    <Tooltip title={`需求单 ${d.req_code || ''} 状态：${d.req_status}——评审走「需求」页`}>
                      <Tag
                        color={d.req_status === '待人审' ? 'orange' : d.req_status === '已打回' ? 'red' : 'green'}
                        style={{ flexShrink: 0, cursor: 'default' }}
                      >
                        {d.req_status}
                      </Tag>
                    </Tooltip>
                  )}
                  {d.source === 'qa-archive' && (
                    <Tag color="purple" style={{ flexShrink: 0 }}>
                      问答沉淀
                    </Tag>
                  )}
                  {d.source === 'asset-upload' && (
                    <Tag color="cyan" style={{ flexShrink: 0 }}>
                      资产上传
                    </Tag>
                  )}
                  {d.source === 'url-import' && (
                    <Tooltip title={d.source_url}>
                      <Tag color="blue" style={{ flexShrink: 0, cursor: 'default' }}>
                        URL 导入
                      </Tag>
                    </Tooltip>
                  )}
                  {d.sources_count > 0 && (
                    <Tooltip title={`引用了 ${d.sources_count} 个 Wiki 来源页——互链图谱可看见 qa_reference 边`}>
                      <Tag color="geekblue" style={{ flexShrink: 0 }}>
                        引 {d.sources_count} 页
                      </Tag>
                    </Tooltip>
                  )}
                  <Tag style={{ flexShrink: 0 }}>{d.chars} 字</Tag>
                  <span style={{ color: 'var(--tf-ink-3)', fontSize: 12, flexShrink: 0 }}>{(d.updated_at || '').slice(0, 16).replace('T', ' ')}</span>
                  <span style={{ flex: 1 }} />
                  {d.object_key && (
                    <Tooltip title="下载原件（对象存储）">
                      <Button
                        size="small"
                        type="text"
                        icon={<DownloadOutlined />}
                        onClick={() => window.open(`/api/knowledge/assets/download?object_key=${encodeURIComponent(d.object_key)}`, '_blank')}
                        style={{ flexShrink: 0 }}
                      />
                    </Tooltip>
                  )}
                  {d.kind === 'req' ? (
                    <Tooltip title="需求文档由需求单托管——如需删除请到「需求」页处理，此处移除会造成检索与需求单脱钩">
                      <Button size="small" type="text" disabled icon={<DeleteOutlined />} style={{ flexShrink: 0 }} />
                    </Tooltip>
                  ) : (
                    <Popconfirm title="从检索索引移除该文档？" onConfirm={() => delDoc.mutate(d.doc_key)}>
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        loading={delDoc.isPending && delDoc.variables === d.doc_key}
                      />
                    </Popconfirm>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Card>
    </>
  )

  return (
    <div>
      <PageHeader
        title="知识资产"
        subtitle="统一入口：需求文档 · 技术文档 · 测试方案 · 历史缺陷 → 门卫 → 检索索引 + 知识图谱 → 生成自动引用 → 缺陷回流"
      />

      <Card title="知识闭环总览" size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 10 }}>
          {stat(s?.kinds?.req, '需求文档', '#7c3aed')}
          {stat(s?.kinds?.user_doc, '技术/知识文档', '#0891b2')}
          {stat(s?.kinds?.plan, '测试方案', '#b45309')}
          {stat(s?.defects_total, '缺陷（开放 ' + (s?.defects_open ?? 0) + '）', '#c93a2e')}
          {stat(s?.cases, '测试用例', '#15803d')}
          {stat(s?.wiki_pages, 'Wiki 页', '#0d7d72')}
          {stat(s?.functions, '索引函数')}
        </div>
        <Alert
          type="success"
          showIcon={false}
          style={{ marginTop: 12 }}
          message={
            <span>
              ↻ 闭环：资产入检索库 → 生成用例六路上下文自动召回 → 沙箱执行 → 失败自动建缺陷 / 关闭沉淀教训 → 回流知识库。
              <Button
                type="link"
                size="small"
                icon={<SearchOutlined />}
                onClick={() => window.dispatchEvent(new CustomEvent('tf-navigate', { detail: 'ask' }))}
              >
                去知识问答验证召回
              </Button>
            </span>
          }
        />
      </Card>

      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as KnowledgeDocsTab)}
        items={[
          { key: 'assets', label: '资产库', children: assetsPane },
          { key: 'pipeline', label: '图谱运维', children: <KnowledgePipeline embedded /> },
        ]}
      />
    </div>
  )
}
