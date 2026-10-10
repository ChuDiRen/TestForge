import { PageHeader } from '@/components/PageHeader'
import { Button, Card, Dropdown, message, Space, Table, Tag } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { downloadGeneratedTest, get, post } from '@/service'

interface RunRow {
  code: string
  target: string
  layer: string
  trigger: string
  sandbox_status: string
  pass_total: number
  pass_count: number
  coverage: number
  repair_rounds: number
  cost_s: number
  status: string
  trace_id: string
  req_code: string
  gen_id: string
  created_at: string
}

export function Runs() {
  const qc = useQueryClient()
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => get<RunRow[]>('/api/runs'), refetchInterval: 8000 })
  const rerun = useMutation({
    mutationFn: (code: string) => post(`/api/runs/${code}/rerun`),
    onSuccess: () => {
      message.success('重跑已发起')
      qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
  const exportRepo = useMutation({
    mutationFn: (genCode: string) => post<{ path: string }>(`/api/generations/${genCode}/export-to-repo`),
    onSuccess: (res) => message.success(`已写入仓库检出: ${res.path}`),
    onError: (e: any) => message.error(e.message),
  })

  const doExport = async (r: RunRow, mode: 'download' | 'repo') => {
    if (!r.gen_id) {
      message.warning('该 run 无关联生成产物')
      return
    }
    try {
      if (mode === 'download') {
        const name = await downloadGeneratedTest(r.gen_id)
        message.success(`已下载 ${name}`)
      } else {
        exportRepo.mutate(r.gen_id)
      }
    } catch (e: any) {
      message.error(e.message)
    }
  }

  return (
    <div>
      <PageHeader title="执行记录" subtitle="真实沙箱执行统一台账 · 产物可导出回写仓库" />
      <Card>
        <Table<RunRow>
          rowKey="code"
          size="small"
          scroll={{ x: 1060 }}
          pagination={{ pageSize: 12 }}
          loading={runs.isLoading}
          dataSource={runs.data ?? []}
          columns={[
            { title: 'Run', dataIndex: 'code', width: 130 },
            { title: '目标', dataIndex: 'target', ellipsis: true },
            { title: '触发', dataIndex: 'trigger', width: 80 },
            { title: '沙箱', dataIndex: 'sandbox_status', width: 80, render: (s: string) => <Tag>{s}</Tag> },
            {
              title: '通过',
              width: 90,
              render: (_, r) => (
                <Tag color={r.pass_count === r.pass_total ? 'green' : 'red'}>
                  {r.pass_count}/{r.pass_total}
                </Tag>
              ),
            },
            { title: '覆盖率', dataIndex: 'coverage', width: 80, render: (v: number) => `${v}%` },
            { title: '修复', dataIndex: 'repair_rounds', width: 60, render: (v: number) => `${v}轮` },
            { title: '耗时', dataIndex: 'cost_s', width: 70, render: (v: number) => `${v}s` },
            { title: '来源需求', dataIndex: 'req_code', width: 100, render: (v: string) => v || '-' },
            { title: 'trace', dataIndex: 'trace_id', width: 130, ellipsis: true },
            {
              title: '操作',
              width: 170,
              render: (_, r) => (
                <Space size={4}>
                  <Button size="small" onClick={() => rerun.mutate(r.code)}>
                    重跑
                  </Button>
                  {r.gen_id && (
                    <Dropdown
                      menu={{
                        items: [
                          { key: 'download', label: '下载测试文件' },
                          { key: 'repo', label: '写入仓库检出' },
                        ],
                        onClick: ({ key }) => doExport(r, key as 'download' | 'repo'),
                      }}
                    >
                      <Button size="small">导出 ▾</Button>
                    </Dropdown>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
