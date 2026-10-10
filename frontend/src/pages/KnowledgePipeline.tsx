import { PageHeader } from '@/components/PageHeader'
import { useEffect, useState } from 'react'
import { Button, Card, Drawer, Empty, Input, Popconfirm, Progress, Select, Space, Table, Tag, Tooltip, Upload, message } from 'antd'
import {
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileTextOutlined,
  InboxOutlined,
  NodeIndexOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { downloadB64, get, post, uploadKgDoc } from '@/service'
import { useIsMobile } from '@/hooks'
import { useLang } from '@/store'
interface KgDoc {
  doc_key: string
  title: string
  workspace: string
  repo_id: number
  status: 'pending' | 'processing' | 'ok' | 'failed'
  error: string
  chunks: number
  entities: number
  relations: number
  content_chars: number
  filename: string
  updated_at: string | null
}
interface KgChunk {
  doc_key: string
  title: string
  chars: number
  index: number
  strategy: string
}

const STATUS_META: Record<string, { color: string; zh: string; en: string }> = {
  pending: { color: 'default', zh: '排队中', en: 'Pending' },
  processing: { color: 'processing', zh: '处理中', en: 'Processing' },
  ok: { color: 'success', zh: '完成', en: 'Done' },
  failed: { color: 'error', zh: '失败', en: 'Failed' },
}

/** 文档管线（LightRAG WebUI Documents 对齐）：文件上传 → 异步分块/抽取/建图 → 状态轮询 → 删除重建。 */
export function KnowledgePipeline({ embedded = false }: { embedded?: boolean } = {}) {
  const { t, lang } = useLang()
  const qc = useQueryClient()
  const isMobile = useIsMobile()
  const [workspace, setWorkspace] = useState('default')
  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [chunkDoc, setChunkDoc] = useState<KgDoc | null>(null)

  const docsQ = useQuery({
    queryKey: ['kg-docs', workspace, statusFilter, search, page],
    queryFn: () =>
      get<{ items: KgDoc[]; total: number }>(
        `/api/kg/documents?workspace=${encodeURIComponent(workspace)}&status=${statusFilter}&q=${encodeURIComponent(search)}&page=${page}&page_size=15`,
      ),
    // 管线有 pending/processing 在途时 3s 轮询（LightRAG WebUI 同款）
    refetchInterval: (q) => (q.state.data?.items.some((d) => d.status === 'pending' || d.status === 'processing') ? 3000 : false),
  })
  const wsQ = useQuery({ queryKey: ['kg-workspaces'], queryFn: () => get<string[]>('/api/kg/workspaces') })
  const chunksQ = useQuery({
    queryKey: ['kg-chunks', chunkDoc?.doc_key],
    queryFn: () =>
      get<KgChunk[]>(`/api/kg/documents/chunks?doc_key=${encodeURIComponent(chunkDoc!.doc_key)}&workspace=${encodeURIComponent(workspace)}`),
    enabled: !!chunkDoc,
  })

  useEffect(() => setPage(1), [workspace, statusFilter, search])

  const uploadProps = {
    multiple: true,
    accept: '.txt,.md,.markdown,.rst,.csv,.pdf,.docx',
    showUploadList: false,
    customRequest: async (opts: { file: unknown; onSuccess?: (body: unknown) => void; onError?: (e: Error) => void }) => {
      try {
        const res = await uploadKgDoc(opts.file as File, { workspace })
        message.success(t.pipeline.uploaded.replace('{title}', res.title))
        opts.onSuccess?.(res)
        qc.invalidateQueries({ queryKey: ['kg-docs'] })
      } catch (e) {
        message.error((e as Error).message)
        opts.onError?.(e as Error)
      }
    },
  }

  const delDoc = useMutation({
    mutationFn: (docKey: string) => post(`/api/kg/documents/delete?doc_key=${encodeURIComponent(docKey)}`),
    onSuccess: () => {
      message.success(t.pipeline.deleted)
      qc.invalidateQueries({ queryKey: ['kg-docs'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const buildCommunities = useMutation({
    mutationFn: () => post<{ clusters: number; level2: number; reports: number }>('/api/kg/communities/build', { workspace }),
    onSuccess: (r) => {
      message.success(t.pipeline.communitiesBuilt.replace('{n}', String(r.clusters + r.level2)))
      qc.invalidateQueries({ queryKey: ['kg-communities'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const exportGraph = useMutation({
    mutationFn: (fmt: string) =>
      get<{ filename: string; content_b64: string }>(`/api/kg/export?what=graph&fmt=${fmt}&workspace=${encodeURIComponent(workspace)}`),
    onSuccess: (r) => downloadB64(r.filename, r.content_b64),
    onError: (e: Error) => message.error(e.message),
  })

  const items = docsQ.data?.items ?? []
  const inflight = items.filter((d) => d.status === 'pending' || d.status === 'processing').length

  return (
    <div>
      {!embedded && <PageHeader title={t.pipeline.title} subtitle={t.pipeline.subtitle} />}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap size={12}>
          <Upload.Dragger {...uploadProps} style={{ width: isMobile ? '100%' : 420, padding: '12px 16px' }}>
            <p style={{ margin: 0 }}>
              <InboxOutlined style={{ fontSize: 26, color: 'var(--tf-primary)' }} />
            </p>
            <p style={{ margin: '6px 0 0', fontWeight: 500 }}>{t.pipeline.dropTitle}</p>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--tf-ink-3)' }}>{t.pipeline.dropHint}</p>
          </Upload.Dragger>
          <div style={{ display: 'grid', gap: 8, fontSize: 12.5, color: 'var(--tf-ink-2)', maxWidth: 380 }}>
            <div>⚠️ {t.pipeline.warnKey}</div>
            <Space wrap>
              <span>{t.pipeline.workspace}</span>
              <Select
                size="small"
                value={workspace}
                onChange={setWorkspace}
                style={{ minWidth: 140 }}
                options={Array.from(new Set(['default', ...(wsQ.data ?? [])])).map((w) => ({ value: w, label: w }))}
              />
            </Space>
            <Space wrap>
              <Button size="small" icon={<NodeIndexOutlined />} loading={buildCommunities.isPending} onClick={() => buildCommunities.mutate()}>
                {t.pipeline.buildCommunities}
              </Button>
              <Button size="small" icon={<DownloadOutlined />} onClick={() => exportGraph.mutate('zip')}>
                {t.pipeline.exportGraph}
              </Button>
            </Space>
          </div>
        </Space>
      </Card>

      <Card
        title={`${t.pipeline.listTitle}（${docsQ.data?.total ?? 0}）`}
        size="small"
        extra={
          <Space wrap>
            {inflight > 0 && <Tag color="processing">{t.pipeline.inflight.replace('{n}', String(inflight))}</Tag>}
            <Select
              size="small"
              value={statusFilter}
              onChange={setStatusFilter}
              style={{ width: 110 }}
              allowClear
              placeholder={t.pipeline.allStatus}
              options={Object.entries(STATUS_META).map(([k, v]) => ({ value: k, label: v[lang] }))}
            />
            <Input
              size="small"
              placeholder={t.pipeline.search}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 160 }}
              allowClear
            />
            <Button size="small" icon={<ReloadOutlined />} onClick={() => docsQ.refetch()} />
          </Space>
        }
      >
        <Table<KgDoc>
          rowKey="doc_key"
          size="small"
          loading={docsQ.isLoading}
          dataSource={items}
          locale={{ emptyText: <Empty description={t.pipeline.empty} image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          pagination={{ current: page, pageSize: 15, total: docsQ.data?.total ?? 0, onChange: setPage, size: 'small' }}
          columns={[
            {
              title: t.pipeline.docTitle,
              dataIndex: 'title',
              ellipsis: true,
              render: (v: string, r: KgDoc) => (
                <Space size={6}>
                  <FileTextOutlined style={{ color: 'var(--tf-ink-3)' }} />
                  <a style={{ fontWeight: 500 }} onClick={() => setChunkDoc(r)}>
                    {v || r.doc_key}
                  </a>
                  {r.filename && (
                    <Tooltip title={r.filename}>
                      <Tag style={{ fontSize: 11 }}>{r.filename.split('.').pop()?.toUpperCase()}</Tag>
                    </Tooltip>
                  )}
                </Space>
              ),
            },
            {
              title: t.pipeline.status,
              dataIndex: 'status',
              width: 96,
              render: (s: string) => <Tag color={STATUS_META[s]?.color}>{STATUS_META[s]?.[lang] ?? s}</Tag>,
            },
            {
              title: t.pipeline.progress,
              key: 'progress',
              width: 180,
              render: (_: unknown, r: KgDoc) =>
                r.status === 'processing' ? (
                  <Progress size="small" status="active" />
                ) : (
                  <span style={{ fontSize: 12, color: 'var(--tf-ink-2)' }}>
                    {r.chunks} chunks · {r.entities} ent · {r.relations} rel
                    {r.status === 'failed' && (
                      <Tooltip title={r.error}>
                        <Tag color="error" style={{ marginLeft: 6 }}>
                          err
                        </Tag>
                      </Tooltip>
                    )}
                  </span>
                ),
            },
            { title: 'Workspace', dataIndex: 'workspace', width: 110, ellipsis: true, render: (v: string) => <Tag>{v}</Tag> },
            {
              title: t.pipeline.updatedAt,
              dataIndex: 'updated_at',
              width: 130,
              render: (v: string | null) => (
                <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>{(v || '').slice(5, 16).replace('T', ' ')}</span>
              ),
            },
            {
              title: '',
              key: 'ops',
              width: 90,
              render: (_: unknown, r: KgDoc) => (
                <Space size={0}>
                  <Button size="small" type="text" icon={<FileTextOutlined />} onClick={() => setChunkDoc(r)} title={t.pipeline.viewChunks} />
                  <Popconfirm title={t.pipeline.delConfirm} onConfirm={() => delDoc.mutate(r.doc_key)}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Drawer
        title={`${t.pipeline.chunksTitle} — ${chunkDoc?.title ?? ''}`}
        width={isMobile ? '100%' : 620}
        open={!!chunkDoc}
        onClose={() => setChunkDoc(null)}
      >
        {chunksQ.isLoading ? null : (chunksQ.data?.length ?? 0) === 0 ? (
          <Empty description={t.pipeline.noChunks} />
        ) : (
          <div style={{ display: 'grid', gap: 10 }}>
            {chunksQ.data!.map((c) => (
              <Card key={c.doc_key} size="small" title={`#${c.index + 1} · ${c.chars} chars`} extra={<Tag>{c.strategy}</Tag>}>
                <ChunkBody docKey={c.doc_key} />
              </Card>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  )
}

function ChunkBody({ docKey }: { docKey: string }) {
  const [text, setText] = useState('')
  useEffect(() => {
    let alive = true
    // chunk 全文走文档详情接口（截断 1500 内联在列表；此处取完整 content）
    get<{ content: string }>(`/api/kg/chunk-content?doc_key=${encodeURIComponent(docKey)}`)
      .then((r) => alive && setText(r.content))
      .catch(() => alive && setText('（加载失败）'))
    return () => {
      alive = false
    }
  }, [docKey])
  return <div style={{ fontSize: 12.5, whiteSpace: 'pre-wrap', color: 'var(--tf-ink-2)', maxHeight: 220, overflowY: 'auto' }}>{text || '…'}</div>
}
