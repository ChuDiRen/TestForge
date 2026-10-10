import { PageHeader } from '@/components/PageHeader'
import { useState } from 'react'
import { Button, Card, Input, Select, Table, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { get } from '@/service'

interface TraceEvent {
  ts: string
  trace_id: string
  type: string
  actor: string
  summary: string
  req_code: string
}

const TYPE_COLOR: Record<string, string> = {
  需求: 'purple',
  生成: 'blue',
  执行: 'green',
  仓库: 'cyan',
  契约: 'cyan',
  缺陷: 'red',
  计划: 'orange',
  认证: 'magenta',
}

const TRACE_TYPES = Object.keys(TYPE_COLOR)

export function Logs() {
  const [tid, setTid] = useState('')
  const [searchTid, setSearchTid] = useState('')
  const [typeFilter, setTypeFilter] = useState<string>('')

  const list = useQuery({
    queryKey: ['traces', typeFilter],
    queryFn: () =>
      get<{ count: number; events: TraceEvent[] }>(`/api/traces?limit=200${typeFilter ? `&type=${encodeURIComponent(typeFilter)}` : ''}`),
    refetchInterval: 15000,
  })
  const tr = useQuery({
    queryKey: ['trace', searchTid],
    queryFn: () => get<{ count: number; events: TraceEvent[] }>(`/api/traces/${searchTid}`),
    enabled: searchTid.startsWith('tr_'),
  })

  const submitSearch = () => {
    if (tid.startsWith('tr_')) setSearchTid(tid.trim())
  }

  const eventColumns = [
    { title: '时间', dataIndex: 'ts', width: 170, render: (v: string) => v?.replace('T', ' ').slice(0, 19) },
    { title: '类型', dataIndex: 'type', width: 80, render: (t: string) => <Tag color={TYPE_COLOR[t]}>{t}</Tag> },
    { title: '操作者', dataIndex: 'actor', width: 120 },
    { title: '摘要', dataIndex: 'summary', ellipsis: true },
    {
      title: 'traceID',
      dataIndex: 'trace_id',
      width: 210,
      render: (v: string) => (
        <Typography.Text
          code
          style={{ fontSize: 12, cursor: 'pointer' }}
          onClick={() => {
            setTid(v)
            setSearchTid(v)
          }}
        >
          {v}
        </Typography.Text>
      ),
    },
    { title: '需求', dataIndex: 'req_code', width: 100, render: (v: string) => v || '-' },
  ]

  return (
    <div>
      <PageHeader title="日志 / 追溯" subtitle="写操作全量留痕台账 · 点击 traceID 回溯全链路" />
      <Card
        title="链路查询"
        extra={
          <span style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <Input
              style={{ width: 280, maxWidth: '100%' }}
              placeholder="输入 traceID（tr_xxx）回溯全链路"
              value={tid}
              onChange={(e) => setTid(e.target.value)}
              onPressEnter={submitSearch}
              allowClear
            />
            <Button type="primary" disabled={!tid.startsWith('tr_')} onClick={submitSearch}>
              查询
            </Button>
          </span>
        }
        style={{ marginBottom: 16 }}
      >
        {searchTid.startsWith('tr_') ? (
          <>
            <div style={{ marginBottom: 12, fontFamily: 'monospace' }}>
              {`链路 ${searchTid}`}
              {(tr.data?.events ?? []).length > 0 ? ` · ${tr.data!.events.length} 个事件` : ' · 无事件'}
            </div>
            <Table<TraceEvent>
              rowKey={(_, i) => String(i)}
              size="small"
              scroll={{ x: 640 }}
              pagination={false}
              loading={tr.isFetching}
              dataSource={tr.data?.events ?? []}
              columns={eventColumns}
            />
          </>
        ) : (
          <div style={{ color: '#999' }}>提示：在下方台账点击任意 traceID，或输入 traceID 回溯「需求录入 → 生成 → 沙箱执行」全链路。</div>
        )}
      </Card>
      <Card
        title="事件台账"
        extra={
          <Select
            style={{ width: 140 }}
            allowClear
            placeholder="全部类型"
            value={typeFilter || undefined}
            onChange={(v) => setTypeFilter(v || '')}
            options={TRACE_TYPES.map((t) => ({ value: t, label: t }))}
          />
        }
      >
        <Table<TraceEvent>
          rowKey={(_, i) => String(i)}
          size="small"
          scroll={{ x: 900 }}
          loading={list.isLoading}
          dataSource={list.data?.events ?? []}
          pagination={{ pageSize: 15, showSizeChanger: false, showTotal: (n) => `共 ${n} 条` }}
          columns={eventColumns}
          locale={{ emptyText: '暂无事件——登录、生成、执行等写操作都会在这里留痕' }}
        />
      </Card>
    </div>
  )
}
