import { PageHeader } from '@/components/PageHeader'
import { Card, Progress, Table, Tag } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { get } from '@/service'

interface QualityRow {
  code: string
  title: string
  status: string
  gates: Record<string, { ok: boolean; detail: string }>
  gates_passed: number
  quality_score: number
  manual_interventions: number
}

const GATES = Object.keys({
  'G0 可测性': 1,
  'G1 知识就绪': 1,
  'G2 用例覆盖': 1,
  'G3 执行验证': 1,
  'G4 缺陷清零': 1,
  'G5 准出报告': 1,
})

export function Quality() {
  const q = useQuery({ queryKey: ['quality'], queryFn: () => get<QualityRow[]>('/api/quality/requirements'), refetchInterval: 10000 })

  return (
    <div>
      <PageHeader title="需求质量流水线" subtitle="G0~G5 六道关卡 · AI 自动判定推进" />
      <Card>
        <Table<QualityRow>
          rowKey="code"
          size="small"
          scroll={{ x: 860 }}
          pagination={false}
          loading={q.isLoading}
          dataSource={q.data ?? []}
          columns={[
            { title: '需求', dataIndex: 'code', width: 90 },
            { title: '标题', dataIndex: 'title', ellipsis: true },
            {
              ...GATE_COLUMN(),
              render: (_, r) => <Progress percent={r.quality_score} size="small" strokeColor={r.quality_score >= 80 ? '#52c41a' : '#faad14'} />,
            },
            {
              title: '关卡',
              width: 260,
              render: (_, r) => (
                <span>
                  {GATES.map((g) => (
                    <Tag key={g} style={{ marginBottom: 2 }} color={r.gates[g]?.ok ? 'green' : 'default'}>
                      {g.split(' ')[0]}
                    </Tag>
                  ))}
                </span>
              ),
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 110,
              render: (s: string) => <Tag color={s === '已生效' ? 'green' : s === '已打回' ? 'red' : 'orange'}>{s}</Tag>,
            },
            { title: '人工介入', dataIndex: 'manual_interventions', width: 80, render: (v: number) => `${v} 次` },
          ]}
        />
      </Card>
    </div>
  )
}

function GATE_COLUMN() {
  return { title: '质量分', width: 140 }
}
