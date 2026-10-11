import { PageHeader } from '@/components/PageHeader'
import { Button, Card, Descriptions, Drawer, Empty, Input, message, Modal, Segmented, Select, Space, Table, Tabs, Tag, Timeline } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { get, getUsername, post } from '@/service'
import { Markdown } from '@/components/Markdown'

interface DefectRow {
  id: number
  code: string
  title: string
  origin_run: string
  case_codes: string[]
  req_code: string
  severity: string
  status: string
  resolution: string
  priority: string
  fix_version: string
  dupe_of: string
  assignee: string
  trace_id: string
  has_suggestion: boolean
  comment_count: number
  detail: string
  updated_at: string
  created_at: string
}

interface DefectDetailInfo {
  reason?: string
  module?: string
  failed?: number
  run?: string
  cases?: { code: string; title: string }[]
}

interface WorkflowEdge {
  from_status: string | null
  to_status: string
  require_comment: boolean
}

interface CommentRow {
  id: number
  who: string
  body: string
  type: string
  at: string
}

interface ActivityRow {
  actor: string
  field: string
  old: string
  new: string
  at: string
}

const SEV_COLOR: Record<string, string> = { 致命: 'red', 严重: 'orange', 一般: 'blue' }
const ST_COLOR: Record<string, string> = { 新建: 'default', 已确认: 'blue', 修复中: 'gold', 待回归: 'orange', 已关闭: 'green' }
const RES_COLOR: Record<string, string> = { 已修复: 'green', 非缺陷: 'default', 重复: 'purple', 无法复现: 'orange', 暂缓: 'default' }
const CTYPE_COLOR: Record<string, string> = { normal: 'blue', system: 'default', ai: 'geekblue', dupe: 'purple' }
const CTYPE_LABEL: Record<string, string> = { normal: '评论', system: '系统', ai: 'AI', dupe: '重复' }
const ST_ORDER = ['新建', '已确认', '修复中', '待回归', '已关闭']
const RESOLUTIONS = ['已修复', '非缺陷', '重复', '无法复现', '暂缓']
const PRIORITIES = ['P0', 'P1', 'P2', 'P3']

const fmtTime = (s: string) => (s ? s.slice(5, 16).replace('T', ' ') : '-')

export function Defects() {
  const qc = useQueryClient()
  const [view, setView] = useState<'table' | 'board'>('table')
  const [suggestFor, setSuggestFor] = useState<DefectRow | null>(null)
  const [detailFor, setDetailFor] = useState<DefectRow | null>(null)
  const [transitionFor, setTransitionFor] = useState<DefectRow | null>(null)
  const [transitionTarget, setTransitionTarget] = useState('')
  const [tResolution, setTResolution] = useState('')
  const [tDupe, setTDupe] = useState('')
  const [tPriority, setTPriority] = useState('')
  const [tFixVersion, setTFixVersion] = useState('')
  const [tAssignee, setTAssignee] = useState('')
  const [tComment, setTComment] = useState('')
  const [commentText, setCommentText] = useState('')

  const defects = useQuery({ queryKey: ['defects'], queryFn: () => get<DefectRow[]>('/api/defects'), refetchInterval: 8000 })
  const workflow = useQuery({ queryKey: ['defect-workflow'], queryFn: () => get<WorkflowEdge[]>('/api/defects/workflow') })
  const comments = useQuery({
    queryKey: ['defect-comments', detailFor?.id],
    queryFn: () => get<CommentRow[]>(`/api/defects/${detailFor?.id}/comments`),
    enabled: !!detailFor,
  })
  const history = useQuery({
    queryKey: ['defect-history', detailFor?.id],
    queryFn: () => get<ActivityRow[]>(`/api/defects/${detailFor?.id}/history`),
    enabled: !!detailFor,
  })
  const suggestion = useQuery({
    queryKey: ['suggestion', suggestFor?.id],
    queryFn: () => get<{ code: string; suggestion: string }>(`/api/defects/${suggestFor?.id}/suggestion`),
    enabled: !!suggestFor,
  })

  const detailInfo = useMemo<DefectDetailInfo | null>(() => {
    if (!detailFor?.detail) return null
    try {
      return JSON.parse(detailFor.detail) as DefectDetailInfo
    } catch {
      return null
    }
  }, [detailFor])

  const act = useMutation({
    mutationFn: ({ id, path }: { id: number; path: string }) => post(`/api/defects/${id}/${path}`, {}),
    onSuccess: (_r, v) => {
      message.success(v.path === 'regression' ? '回归完成（只重跑关联用例）' : v.path === 'suggest' ? 'AI 修复建议已生成' : '状态已更新')
      qc.invalidateQueries({ queryKey: ['defects'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const transition = useMutation({
    mutationFn: (payload: Record<string, unknown>) => post(`/api/defects/${transitionFor?.id}/status`, payload),
    onSuccess: () => {
      message.success('流转成功')
      setTransitionFor(null)
      qc.invalidateQueries({ queryKey: ['defects'] })
      qc.invalidateQueries({ queryKey: ['defect-history'] })
      qc.invalidateQueries({ queryKey: ['defect-comments'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const addComment = useMutation({
    mutationFn: (payload: { body: string }) => post(`/api/defects/${detailFor?.id}/comments`, { ...payload, actor: getUsername() || 'qa' }),
    onSuccess: () => {
      setCommentText('')
      qc.invalidateQueries({ queryKey: ['defect-comments', detailFor?.id] })
      qc.invalidateQueries({ queryKey: ['defects'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const openTransition = (d: DefectRow, target = '') => {
    if (target) {
      const legal = workflow.data?.some((e) => e.from_status === d.status && e.to_status === target)
      if (!legal) {
        message.error(`非法流转：${d.status} → ${target}`)
        return
      }
    }
    setTransitionFor(d)
    setTransitionTarget(target)
    setTResolution('')
    setTDupe('')
    setTPriority(d.priority || '')
    setTFixVersion(d.fix_version || '')
    setTAssignee(d.assignee || '')
    setTComment('')
  }

  const allowedNext = useMemo(
    () => workflow.data?.filter((e) => e.from_status === (transitionFor?.status ?? '')) ?? [],
    [workflow.data, transitionFor],
  )
  const edge = allowedNext.find((e) => e.to_status === transitionTarget)

  const submitTransition = () => {
    const closing = transitionTarget === '已关闭'
    if (closing && !tResolution) {
      message.error('关闭缺陷必须选择结案原因')
      return
    }
    if (tResolution === '重复' && !tDupe.trim()) {
      message.error('结案原因=重复 需填写目标缺陷编码（如 BUG-XXXXXX）')
      return
    }
    if (edge?.require_comment && !tComment.trim()) {
      message.error(`流转 ${transitionFor?.status} → ${transitionTarget} 必须附带评论`)
      return
    }
    transition.mutate({
      status: transitionTarget,
      resolution: tResolution,
      priority: tPriority,
      fix_version: tFixVersion,
      assignee: tAssignee,
      dupe_of: tDupe.trim(),
      comment: tComment.trim(),
      actor: getUsername() || 'qa',
      expected_updated_at: transitionFor?.updated_at ?? '',
    })
  }

  const tryDrop = (target: string, defectId: number) => {
    const row = defects.data?.find((x) => x.id === defectId)
    if (row) openTransition(row, target)
  }

  const actions = (d: DefectRow) => (
    <Space size={4} wrap>
      <Button size="small" onClick={() => setDetailFor(d)}>
        详情
      </Button>
      {d.status !== '已关闭' && (
        <Button size="small" type="primary" onClick={() => openTransition(d)}>
          流转
        </Button>
      )}
      {d.status !== '已关闭' && (
        <Button size="small" onClick={() => act.mutate({ id: d.id, path: 'regression' })} loading={act.isPending}>
          回归验证
        </Button>
      )}
      <Button
        size="small"
        onClick={() => {
          setSuggestFor(d)
          if (!d.has_suggestion) act.mutate({ id: d.id, path: 'suggest' })
        }}
      >
        AI 建议{d.has_suggestion ? '✓' : ''}
      </Button>
    </Space>
  )

  const columns = [
    { title: '编码', dataIndex: 'code', width: 110 },
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
      render: (v: string, d: DefectRow) => (
        <a onClick={() => setDetailFor(d)} style={{ textDecoration: 'none' }}>
          {v}
        </a>
      ),
    },
    { title: '严重度', dataIndex: 'severity', width: 80, render: (s: string) => <Tag color={SEV_COLOR[s]}>{s}</Tag> },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 76,
      render: (v: string) => (v ? <Tag color={v === 'P0' ? 'red' : v === 'P1' ? 'orange' : 'blue'}>{v}</Tag> : '-'),
    },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={ST_COLOR[s]}>{s}</Tag> },
    { title: '结案', dataIndex: 'resolution', width: 90, render: (v: string) => (v ? <Tag color={RES_COLOR[v]}>{v}</Tag> : '-') },
    {
      title: '重复→',
      dataIndex: 'dupe_of',
      width: 100,
      render: (v: string) => (v ? <Tag color="purple">{v}</Tag> : '-'),
    },
    { title: '指派', dataIndex: 'assignee', width: 90 },
    { title: '修复版本', dataIndex: 'fix_version', width: 90, render: (v: string) => v || '-' },
    { title: '需求', dataIndex: 'req_code', width: 90, render: (v: string) => v || '-' },
    { title: '来源 run', dataIndex: 'origin_run', width: 110, render: (v: string) => v || '-' },
    { title: '用例', dataIndex: 'case_codes', width: 60, render: (v: string[]) => v?.length ?? 0 },
    { title: '评论', dataIndex: 'comment_count', width: 60 },
    { title: '更新', dataIndex: 'updated_at', width: 110, render: fmtTime },
    { title: '操作', width: 300, render: (_: unknown, d: DefectRow) => actions(d) },
  ]

  return (
    <div>
      <PageHeader title="缺陷管理" subtitle="失败自动建缺陷 · 工作流驱动流转 · 字段级历史 · 看板视图" />
      <Card
        title={
          <Segmented
            value={view}
            onChange={(v) => setView(v as 'table' | 'board')}
            options={[
              { label: '表格', value: 'table' },
              { label: '看板', value: 'board' },
            ]}
          />
        }
      >
        {view === 'table' ? (
          <Table<DefectRow>
            rowKey="id"
            size="small"
            scroll={{ x: 1560 }}
            pagination={{ pageSize: 12 }}
            loading={defects.isLoading}
            dataSource={defects.data ?? []}
            columns={columns}
          />
        ) : (
          <div style={{ display: 'flex', gap: 8, overflowX: 'auto', alignItems: 'flex-start' }}>
            {ST_ORDER.map((st) => {
              const cards = (defects.data ?? []).filter((d) => d.status === st)
              return (
                <div
                  key={st}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault()
                    tryDrop(st, Number(e.dataTransfer.getData('text/plain')))
                  }}
                  style={{ flex: '0 0 230px', background: '#fafafa', borderRadius: 8, padding: 8, minHeight: 320 }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 8, color: '#555' }}>
                    {st}（{cards.length}）
                  </div>
                  {cards.map((d) => (
                    <Card
                      key={d.id}
                      size="small"
                      draggable
                      onDragStart={(e) => e.dataTransfer.setData('text/plain', String(d.id))}
                      onClick={() => setDetailFor(d)}
                      style={{ marginBottom: 8, cursor: 'grab' }}
                      styles={{ body: { padding: 8 } }}
                    >
                      <Space size={4} wrap style={{ marginBottom: 4 }}>
                        <Tag>{d.code}</Tag>
                        <Tag color={SEV_COLOR[d.severity]}>{d.severity}</Tag>
                        {d.priority && <Tag color={d.priority === 'P0' ? 'red' : d.priority === 'P1' ? 'orange' : 'blue'}>{d.priority}</Tag>}
                      </Space>
                      <div style={{ fontSize: 12, lineHeight: 1.4, minHeight: 32 }}>{d.title}</div>
                      <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                        {d.assignee || '未指派'} · 💬 {d.comment_count}
                        {d.resolution ? ` · ${d.resolution}` : ''}
                      </div>
                    </Card>
                  ))}
                  {!cards.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无" />}
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* 流转弹窗（Jira transition screen：状态 + 伴随字段 + 评论一次提交） */}
      <Modal
        title={`流转 · ${transitionFor?.code ?? ''}（当前：${transitionFor?.status ?? ''}）`}
        open={!!transitionFor}
        onOk={submitTransition}
        onCancel={() => setTransitionFor(null)}
        confirmLoading={transition.isPending}
        okText="提交"
        cancelText="取消"
        width={520}
      >
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          <div>
            <div style={{ marginBottom: 4 }}>目标状态{transitionTarget ? '（拖拽指定）' : ''}</div>
            <Select
              style={{ width: '100%' }}
              placeholder="不改状态，只更新字段"
              value={transitionTarget || undefined}
              onChange={setTransitionTarget}
              options={allowedNext.map((e) => ({
                label: `${e.to_status}${e.require_comment ? '（需评论）' : ''}`,
                value: e.to_status,
              }))}
            />
          </div>
          {(transitionTarget === '已关闭' || tResolution) && (
            <div>
              <div style={{ marginBottom: 4 }}>结案原因{transitionTarget === '已关闭' ? '（必填）' : ''}</div>
              <Select
                style={{ width: '100%' }}
                value={tResolution || undefined}
                onChange={setTResolution}
                options={RESOLUTIONS.map((r) => ({ label: r, value: r }))}
              />
            </div>
          )}
          {tResolution === '重复' && (
            <div>
              <div style={{ marginBottom: 4 }}>重复指向（自动沿重复链扁平化到最终目标）</div>
              <Input placeholder="目标缺陷编码，如 BUG-3F2A1C" value={tDupe} onChange={(e) => setTDupe(e.target.value)} />
            </div>
          )}
          <Space wrap>
            <div>
              <div style={{ marginBottom: 4 }}>优先级</div>
              <Select
                style={{ width: 120 }}
                allowClear
                placeholder="不修改"
                value={tPriority || undefined}
                onChange={setTPriority}
                options={PRIORITIES.map((p) => ({ label: p, value: p }))}
              />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>修复版本</div>
              <Input style={{ width: 160 }} placeholder="不修改" value={tFixVersion} onChange={(e) => setTFixVersion(e.target.value)} />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>指派</div>
              <Input style={{ width: 140 }} placeholder="不修改" value={tAssignee} onChange={(e) => setTAssignee(e.target.value)} />
            </div>
          </Space>
          <div>
            <div style={{ marginBottom: 4 }}>评论{edge?.require_comment ? '（此流转必须附带）' : ''}</div>
            <Input.TextArea rows={3} value={tComment} onChange={(e) => setTComment(e.target.value)} placeholder="变更说明 / 结案说明" />
          </div>
        </Space>
      </Modal>

      {/* 缺陷详情：信息 / 评论 / 字段级操作记录 */}
      <Drawer title={`缺陷详情 · ${detailFor?.code ?? ''}`} open={!!detailFor} onClose={() => setDetailFor(null)} width={620}>
        {detailFor && (
          <Tabs
            items={[
              {
                key: 'info',
                label: '信息',
                children: (
                  <div>
                    <Descriptions column={1} size="small" bordered>
                      <Descriptions.Item label="标题">{detailFor.title}</Descriptions.Item>
                      <Descriptions.Item label="状态">
                        <Space>
                          <Tag color={ST_COLOR[detailFor.status]}>{detailFor.status}</Tag>
                          {detailFor.resolution && <Tag color={RES_COLOR[detailFor.resolution]}>{detailFor.resolution}</Tag>}
                          {detailFor.dupe_of && <Tag color="purple">重复→ {detailFor.dupe_of}</Tag>}
                        </Space>
                      </Descriptions.Item>
                      <Descriptions.Item label="严重度 / 优先级">
                        {detailFor.severity} / {detailFor.priority || '未设置'}
                      </Descriptions.Item>
                      <Descriptions.Item label="指派">{detailFor.assignee || '未指派'}</Descriptions.Item>
                      <Descriptions.Item label="修复版本">{detailFor.fix_version || '-'}</Descriptions.Item>
                      <Descriptions.Item label="关联">
                        需求 {detailFor.req_code || '-'} · run {detailFor.origin_run || '-'}
                        {detailFor.trace_id ? ` · trace ${detailFor.trace_id}` : ''}
                      </Descriptions.Item>
                      {detailInfo && (
                        <Descriptions.Item label="失败详情">
                          <div style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>
                            {detailInfo.module && (
                              <div style={{ marginBottom: 4 }}>
                                失败目标：<b>{detailInfo.module}</b>
                              </div>
                            )}
                            {detailInfo.reason && <div style={{ marginBottom: 4 }}>原因：{detailInfo.reason}</div>}
                          </div>
                        </Descriptions.Item>
                      )}
                      <Descriptions.Item label={`失败用例（${detailFor.case_codes.length}）`}>
                        {detailInfo?.cases?.length ? (
                          <div>
                            {detailInfo.cases.slice(0, 10).map((c) => (
                              <div key={c.code} style={{ fontSize: 12, lineHeight: 1.6 }}>
                                <Tag style={{ marginRight: 4 }}>{c.code}</Tag>
                                {c.title}
                              </div>
                            ))}
                            {detailFor.case_codes.length > 10 && (
                              <div style={{ color: '#999', fontSize: 12 }}>…等共 {detailFor.case_codes.length} 条</div>
                            )}
                          </div>
                        ) : (
                          <Space size={4} wrap>
                            {detailFor.case_codes.slice(0, 10).map((c) => (
                              <Tag key={c}>{c}</Tag>
                            ))}
                            {detailFor.case_codes.length > 10 && <span style={{ color: '#999' }}>…等共 {detailFor.case_codes.length} 条</span>}
                          </Space>
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="创建 / 更新">
                        {fmtTime(detailFor.created_at)} / {fmtTime(detailFor.updated_at)}
                      </Descriptions.Item>
                    </Descriptions>
                    <Button
                      size="small"
                      style={{ marginTop: 12 }}
                      onClick={() => {
                        setSuggestFor(detailFor)
                        if (!detailFor.has_suggestion) act.mutate({ id: detailFor.id, path: 'suggest' })
                      }}
                    >
                      AI 修复建议{detailFor.has_suggestion ? '✓' : ''}
                    </Button>
                  </div>
                ),
              },
              {
                key: 'comments',
                label: `评论（${comments.data?.length ?? 0}）`,
                children: (
                  <div>
                    {(comments.data ?? []).map((c) => (
                      <div key={c.id} style={{ borderBottom: '1px solid #f0f0f0', padding: '8px 0' }}>
                        <Space size={6} style={{ marginBottom: 2 }}>
                          <Tag color={CTYPE_COLOR[c.type]}>{CTYPE_LABEL[c.type] || c.type}</Tag>
                          <span style={{ fontWeight: 500 }}>{c.who}</span>
                          <span style={{ color: '#999', fontSize: 12 }}>{fmtTime(c.at)}</span>
                        </Space>
                        <div style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>{c.body}</div>
                      </div>
                    ))}
                    {!comments.isLoading && !comments.data?.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无评论" />}
                    <Input.TextArea
                      rows={2}
                      style={{ marginTop: 12 }}
                      value={commentText}
                      onChange={(e) => setCommentText(e.target.value)}
                      placeholder="写下评论…"
                    />
                    <Button
                      type="primary"
                      size="small"
                      style={{ marginTop: 8 }}
                      loading={addComment.isPending}
                      disabled={!commentText.trim()}
                      onClick={() => addComment.mutate({ body: commentText.trim() })}
                    >
                      发布评论
                    </Button>
                  </div>
                ),
              },
              {
                key: 'history',
                label: `记录（${history.data?.length ?? 0}）`,
                children: (
                  <Timeline
                    items={(history.data ?? []).map((h, i) => ({
                      key: i,
                      children: (
                        <div style={{ fontSize: 13 }}>
                          <b>{h.field}</b>：{h.old || '∅'} → {h.new || '∅'}
                          <span style={{ color: '#999', marginLeft: 8 }}>
                            {h.actor} · {fmtTime(h.at)}
                          </span>
                        </div>
                      ),
                    }))}
                  />
                ),
              },
            ]}
          />
        )}
      </Drawer>

      <Drawer title={`AI 修复建议 · ${suggestFor?.code ?? ''}`} open={!!suggestFor} onClose={() => setSuggestFor(null)} width={520}>
        {suggestion.isFetching && !suggestion.data?.suggestion && <Tag color="processing">DeepSeek 分析中…</Tag>}
        {suggestion.data?.suggestion ? (
          <Markdown>{suggestion.data.suggestion}</Markdown>
        ) : (
          !suggestion.isFetching && <Tag>暂无建议，点「AI 建议」生成</Tag>
        )}
      </Drawer>
    </div>
  )
}
