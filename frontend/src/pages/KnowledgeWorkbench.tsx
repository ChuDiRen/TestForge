import { PageHeader } from '@/components/PageHeader'
import { useState } from 'react'
import { Alert, Button, Card, Empty, Input, Popconfirm, Select, Table, Tag, Tooltip } from 'antd'
import { DownloadOutlined, FileTextOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post } from '@/service'
import { repoName } from '@/utils'
import { useIsMobile } from '@/hooks'

interface WorkbenchData {
  summary: Record<string, number>
  kg: { entities: number; relations: number; communities: number; dirty: boolean }
  pipeline: {
    by_status: Record<string, number>
    recent: { title: string; status: string; workspace: string; updated_at: string | null }[]
  }
  recent_qa: { content: string; session_id: number; created_at: string | null }[]
}

interface WorkbenchDoc {
  doc_key: string
  repo_id: number
  title: string
  kind: string
  chars: number
  source: string
  object_key: string
  updated_at: string | null
}

const KIND_TAG: Record<string, { label: string; color: string }> = {
  user_doc: { label: '技术文档', color: 'cyan' },
  req: { label: '需求文档', color: 'purple' },
  plan: { label: '测试方案', color: 'gold' },
  defect: { label: '缺陷摘要', color: 'red' },
  wiki: { label: 'Wiki 页', color: 'geekblue' },
  case: { label: '测试用例', color: 'green' },
  lesson: { label: '教训沉淀', color: 'orange' },
  kg_doc: { label: '图谱文档', color: 'blue' },
}
/** 行内托管控：wiki 由 Wiki 页托管、case 由用例库托管、defect 由缺陷页托管、kg_doc 走图谱运维 */
const MANAGED_BY: Partial<Record<string, string>> = {
  wiki: 'Wiki 页托管——删除请到「Wiki 知识库」',
  case: '用例库托管——删除请到「用例库」',
  defect: '缺陷页托管——删除请到「缺陷管理」',
  kg_doc: '图谱文档——删除请到「知识资产 → 图谱运维」',
}

const PIPELINE_TAG: Record<string, { color: string; zh: string }> = {
  pending: { color: 'default', zh: '排队' },
  processing: { color: 'processing', zh: '处理中' },
  ok: { color: 'success', zh: '完成' },
  failed: { color: 'error', zh: '失败' },
}

/** 知识工作台（功能中心首屏，对齐参考产品 IA）：闭环总览 + 图谱规模 + 管线状态 + 全量知识条目表。 */
export function KnowledgeWorkbench() {
  const qc = useQueryClient()
  const isMobile = useIsMobile()
  const [kind, setKind] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)

  const wbQ = useQuery({ queryKey: ['kb-workbench'], queryFn: () => get<WorkbenchData>('/api/knowledge/workbench') })
  const repos = useQuery({ queryKey: ['repos'], queryFn: () => get<{ id: number; url: string }[]>('/api/repos') })
  const docsQ = useQuery({
    queryKey: ['kb-docs', kind, q, page],
    queryFn: () =>
      get<{ items: WorkbenchDoc[]; total: number }>(
        `/api/knowledge/documents/paged?kind=${kind}&q=${encodeURIComponent(q)}&page=${page}&page_size=15`,
      ),
  })
  const delDoc = useMutation({
    mutationFn: (docKey: string) => post(`/api/knowledge/documents/delete?doc_key=${encodeURIComponent(docKey)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kb-docs'] })
      qc.invalidateQueries({ queryKey: ['kb-workbench'] })
    },
  })

  const s = wbQ.data?.summary
  const kg = wbQ.data?.kg
  const inflight = (wbQ.data?.pipeline.by_status.pending ?? 0) + (wbQ.data?.pipeline.by_status.processing ?? 0)
  const failed = wbQ.data?.pipeline.by_status.failed ?? 0
  const stat = (v: number | undefined, label: string, color?: string) => (
    <div key={label} style={{ border: '1px solid var(--tf-line)', borderRadius: 8, padding: '10px 4px', textAlign: 'center' }}>
      <b style={{ fontSize: 20, color }}>{v ?? '-'}</b>
      <div style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>{label}</div>
    </div>
  )
  const download = (objectKey: string) => {
    window.open(`/api/knowledge/assets/download?object_key=${encodeURIComponent(objectKey)}`, '_blank')
  }

  return (
    <div>
      <PageHeader title="知识工作台" subtitle="知识闭环驾驶舱：总览 · 图谱规模 · 管线状态 · 全量知识条目" />

      {(inflight > 0 || failed > 0 || kg?.dirty) && (
        <Alert
          type={failed > 0 ? 'warning' : 'info'}
          showIcon
          style={{ marginBottom: 14 }}
          message={
            <span>
              {inflight > 0 && `图谱管线 ${inflight} 篇在途处理中；`}
              {failed > 0 && `图谱管线 ${failed} 篇失败（原因见图谱运维）；`}
              {kg?.dirty && '图谱已更新、社区报告未重建；'}
              <Button type="link" size="small" onClick={() => window.dispatchEvent(new CustomEvent('tf-navigate', { detail: 'knowledge-docs' }))}>
                去图谱运维处理
              </Button>
            </span>
          }
        />
      )}

      <Card title="知识闭环总览" size="small" style={{ marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(96px, 1fr))', gap: 10 }}>
          {stat(s?.req, '需求文档', '#7c3aed')}
          {stat(s?.user_doc, '技术文档', '#0891b2')}
          {stat(s?.plan, '测试方案', '#b45309')}
          {stat(s?.defects_total, '缺陷', '#c93a2e')}
          {stat(s?.cases, '测试用例', '#15803d')}
          {stat(s?.wiki_pages, 'Wiki 页', '#0d7d72')}
          {stat(s?.functions, '索引函数')}
          {stat(kg?.entities, '图谱实体', '#2563eb')}
          {stat(kg?.relations, '图谱关系', '#7c3aed')}
          {stat(kg?.communities, '社区报告', '#0d9488')}
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 320px', gap: 14, alignItems: 'start' }}>
        <Card
          title={`全量知识条目（${docsQ.data?.total ?? 0}）`}
          size="small"
          extra={
            <span>
              <Select
                size="small"
                value={kind}
                onChange={(v) => {
                  setKind(v)
                  setPage(1)
                }}
                style={{ width: 110, marginRight: 8 }}
                allowClear
                placeholder="全部类型"
                options={Object.entries(KIND_TAG).map(([k, v]) => ({ value: k, label: v.label }))}
              />
              <Input
                size="small"
                placeholder="搜索标题"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value)
                  setPage(1)
                }}
                style={{ width: 140, marginRight: 8 }}
                allowClear
              />
              <Button size="small" icon={<ReloadOutlined />} onClick={() => docsQ.refetch()} />
            </span>
          }
        >
          <Table<WorkbenchDoc>
            rowKey="doc_key"
            size="small"
            loading={docsQ.isLoading}
            dataSource={docsQ.data?.items ?? []}
            locale={{ emptyText: <Empty description="还没有知识条目——到「文档管理」上传" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            pagination={{ current: page, pageSize: 15, total: docsQ.data?.total ?? 0, onChange: setPage, size: 'small' }}
            columns={[
              {
                title: '条目',
                dataIndex: 'title',
                ellipsis: true,
                render: (v: string, r: WorkbenchDoc) => (
                  <span>
                    <FileTextOutlined style={{ color: 'var(--tf-ink-3)', marginRight: 6 }} />
                    <span style={{ fontWeight: 500 }}>{v || r.doc_key}</span>
                  </span>
                ),
              },
              {
                title: '类型',
                dataIndex: 'kind',
                width: 92,
                render: (k: string) => {
                  const t = KIND_TAG[k] ?? { label: k, color: 'default' }
                  return <Tag color={t.color}>{t.label}</Tag>
                },
              },
              {
                title: '来源',
                dataIndex: 'source',
                width: 96,
                render: (v: string) => <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>{v || '-'}</span>,
              },
              {
                title: '归属',
                dataIndex: 'repo_id',
                width: 100,
                ellipsis: true,
                render: (rid: number) => (
                  <span style={{ fontSize: 12 }}>{rid ? repoName(repos.data?.find((x) => x.id === rid)?.url || '') || `#${rid}` : '全局'}</span>
                ),
              },
              { title: '字数', dataIndex: 'chars', width: 70, render: (v: number) => <span style={{ fontSize: 12 }}>{v}</span> },
              {
                title: '更新',
                dataIndex: 'updated_at',
                width: 100,
                render: (v: string | null) => (
                  <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>{(v || '').slice(5, 16).replace('T', ' ')}</span>
                ),
              },
              {
                title: '',
                key: 'ops',
                width: 80,
                render: (_: unknown, r: WorkbenchDoc) => {
                  const managed = MANAGED_BY[r.kind]
                  const deletable = r.kind === 'user_doc' || r.kind === 'plan'
                  return (
                    <span>
                      {r.object_key && (
                        <Tooltip title="下载原件">
                          <Button size="small" type="text" icon={<DownloadOutlined />} onClick={() => download(r.object_key)} />
                        </Tooltip>
                      )}
                      {deletable ? (
                        <Popconfirm title="删除该条目？检索行与图谱贡献一并撤除" onConfirm={() => delDoc.mutate(r.doc_key)}>
                          <Button size="small" type="text" danger loading={delDoc.isPending && delDoc.variables === r.doc_key}>
                            删
                          </Button>
                        </Popconfirm>
                      ) : (
                        <Tooltip title={managed}>
                          <Button size="small" type="text" disabled>
                            删
                          </Button>
                        </Tooltip>
                      )}
                    </span>
                  )
                },
              },
            ]}
          />
        </Card>

        <div style={{ display: 'grid', gap: 14 }}>
          <Card title="图谱管线" size="small">
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
              {Object.entries(wbQ.data?.pipeline.by_status ?? {}).map(([st, n]) => (
                <Tag key={st} color={PIPELINE_TAG[st]?.color ?? 'default'}>
                  {PIPELINE_TAG[st]?.zh ?? st} {n}
                </Tag>
              ))}
              {Object.keys(wbQ.data?.pipeline.by_status ?? {}).length === 0 && (
                <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>暂无管线文档</span>
              )}
            </div>
            {(wbQ.data?.pipeline.recent ?? []).map((p, i) => (
              <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, padding: '3px 0' }}>
                <Tag color={PIPELINE_TAG[p.status]?.color} style={{ fontSize: 10, flexShrink: 0 }}>
                  {PIPELINE_TAG[p.status]?.zh ?? p.status}
                </Tag>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.title}</span>
              </div>
            ))}
          </Card>
          <Card title="最近知识问答" size="small">
            {(wbQ.data?.recent_qa ?? []).length === 0 ? (
              <span style={{ fontSize: 12, color: 'var(--tf-ink-3)' }}>还没有提问——去「Wiki 知识库」开问</span>
            ) : (
              (wbQ.data?.recent_qa ?? []).map((m, i) => (
                <Tooltip key={i} title={m.content}>
                  <div
                    onClick={() => window.dispatchEvent(new CustomEvent('tf-navigate', { detail: 'wiki' }))}
                    style={{
                      fontSize: 12,
                      padding: '4px 0',
                      borderBottom: '1px dashed var(--tf-line)',
                      cursor: 'pointer',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {m.content}
                  </div>
                </Tooltip>
              ))
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
