import { Button, Card, Col, Row, Space, Table, Tag, Typography } from 'antd'
import { ApartmentOutlined, BookOutlined, CheckCircleOutlined, DatabaseOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { get } from '@/service'
import { PageHeader } from '@/components/PageHeader'
import { StatCard } from '@/components/StatCard'

interface ServiceStatus {
  name: string
  port: number
  ok: boolean
  db_ok: boolean
  version?: string
  error?: string
}

interface ServicesResp {
  services: ServiceStatus[]
  all_green: boolean
  mode: 'mono' | 'micro'
}

interface Stats {
  repos: number
  cases_total: number
  cases_by_layer: Record<string, number>
  runs_total: number
  runs_pass_rate: number
  requirements_total: number
  requirements_pending: number
  jobs_active: number
  cases_stale: number
  pending_reviews: number
  defects_open: number
  wiki_pages: number
  wiki_stale: number
  contracts: number
}

export function Dashboard() {
  const services = useQuery({
    queryKey: ['services'],
    queryFn: () => get<ServicesResp>('/api/system/services'),
    refetchInterval: 15000,
  })
  const stats = useQuery({ queryKey: ['stats'], queryFn: () => get<Stats>('/api/stats/summary') })

  const s = stats.data
  const wikiOk = (s?.wiki_pages ?? 0) - (s?.wiki_stale ?? 0)

  const nav = (view: string) => window.dispatchEvent(new CustomEvent('tf-navigate', { detail: view }))
  const empty = (s?.repos ?? 0) === 0
  const todos = [
    { label: '待人审用例', count: s?.pending_reviews ?? 0, view: 'cases', desc: '草稿用例确认入库' },
    { label: '需求待处理', count: s?.requirements_pending ?? 0, view: 'requirements', desc: '待人审 / 规则冲突' },
    { label: '运行中任务', count: s?.jobs_active ?? 0, view: 'jobs', desc: '生成与回归进度' },
    { label: '待回归用例', count: s?.cases_stale ?? 0, view: 'cases', desc: '源码变更已失效' },
    { label: '未关闭缺陷', count: s?.defects_open ?? 0, view: 'defects', desc: '跟进修复' },
  ]
  const activeTodos = todos.filter((t) => t.count > 0)
  const QUICK_STEPS = [
    { step: 1, title: '接入仓库', desc: '填远程 Git URL，自动克隆 + 索引 + 编译 Wiki', view: 'repo-add' },
    { step: 2, title: '录入需求', desc: '用户故事 + 验收条件，AI 评分可测性', view: 'requirements' },
    { step: 3, title: '生成用例', desc: '选函数生成单测，或按模块批量入队', view: 'workbench' },
    { step: 4, title: '执行与回归', desc: '真实沙箱执行，失败自动建缺陷', view: 'runs' },
  ]

  return (
    <div>
      <PageHeader title="仪表盘" subtitle="平台运行全景 · 数据全部来自真实接口，15s 自动刷新服务状态" />
      {empty ? (
        <Card style={{ marginBlockEnd: 16 }}>
          <Typography.Title level={4} style={{ marginBlockEnd: 4 }}>
            四步开始你的第一条测试用例
          </Typography.Title>
          <Typography.Text type="secondary">点击任意步骤直达对应页面；整个流程串起来就是：接入仓库 → 录需求 → 生成 → 执行</Typography.Text>
          <Row gutter={[12, 12]} style={{ marginBlockStart: 16 }}>
            {QUICK_STEPS.map((q) => (
              <Col xs={24} sm={12} xl={6} key={q.step}>
                <div
                  onClick={() => nav(q.view)}
                  style={{
                    cursor: 'pointer',
                    padding: '14px 16px',
                    borderRadius: 12,
                    border: '1px solid var(--tf-line, #e5e7f0)',
                    transition: 'box-shadow .18s ease',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.boxShadow = '0 6px 18px rgba(11,101,92,.16)')}
                  onMouseLeave={(e) => (e.currentTarget.style.boxShadow = 'none')}
                >
                  <Tag color="cyan">第 {q.step} 步</Tag>
                  <div style={{ fontWeight: 700, marginBlockStart: 6 }}>{q.title}</div>
                  <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
                    {q.desc}
                  </Typography.Text>
                </div>
              </Col>
            ))}
          </Row>
        </Card>
      ) : (
        activeTodos.length > 0 && (
          <Card
            size="small"
            style={{ marginBlockEnd: 16 }}
            title="待办"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                点击直达对应页面
              </Typography.Text>
            }
          >
            <Space wrap size={8}>
              {activeTodos.map((t) => (
                <Button key={t.label} onClick={() => nav(t.view)} style={{ height: 'auto', padding: '6px 14px' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Tag color={t.count > 5 ? 'volcano' : 'cyan'} style={{ marginInlineEnd: 0 }}>
                      {t.count}
                    </Tag>
                    <span style={{ fontSize: 13 }}>{t.label}</span>
                    <span style={{ fontSize: 11.5, color: 'var(--tf-ink-2, #8a8fa8)' }}>{t.desc}</span>
                  </span>
                </Button>
              ))}
            </Space>
          </Card>
        )
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}>
          <StatCard icon={<ApartmentOutlined />} label="接入仓库" value={s?.repos ?? 0} suffix="个" tone="indigo" />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <StatCard
            icon={<DatabaseOutlined />}
            label="用例总数"
            value={s?.cases_total ?? 0}
            suffix="条"
            tone="violet"
            footer={`单测/接口/E2E 分层管理${s?.cases_total ? ` · 当前 ${(s?.cases_by_layer?.ut ?? 0) + (s?.cases_by_layer?.api ?? 0) + (s?.cases_by_layer?.e2e ?? 0)} 条在库` : ''}`}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <StatCard
            icon={<CheckCircleOutlined />}
            label="执行通过率"
            value={s?.runs_pass_rate ?? 0}
            suffix="%"
            tone={(s?.runs_pass_rate ?? 0) >= 90 ? 'green' : 'amber'}
            footer={`累计执行 ${s?.runs_total ?? 0} 次（真实沙箱）`}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <StatCard
            icon={<BookOutlined />}
            label="Wiki 健康"
            value={wikiOk}
            suffix={`/ ${s?.wiki_pages ?? 0} 页`}
            tone="cyan"
            footer={(s?.wiki_stale ?? 0) > 0 ? `${s?.wiki_stale} 页待重建（源码已变更）` : '知识全部新鲜'}
          />
        </Col>
      </Row>

      <Card
        title={
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            服务状态
            <Tag color={services.data?.mode === 'mono' ? 'blue' : 'orange'} style={{ fontWeight: 500 }}>
              {services.data?.mode === 'micro' ? '微服务模式' : '单体模式 · 进程内模块'}
            </Tag>
          </span>
        }
        style={{ marginTop: 16 }}
        extra={
          services.data ? (
            <Tag color={services.data.all_green ? 'green' : 'red'} style={{ fontWeight: 500 }}>
              {services.data.all_green ? '全部 GREEN' : '存在 FAIL'}
            </Tag>
          ) : null
        }
      >
        <Table<ServiceStatus>
          rowKey="name"
          size="small"
          scroll={{ x: 620 }}
          pagination={false}
          loading={services.isLoading}
          dataSource={services.data?.services ?? []}
          columns={[
            { title: '模块', dataIndex: 'name', render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
            {
              title: services.data?.mode === 'micro' ? '端口' : '部署形态',
              dataIndex: 'port',
              render: (port: number) => (services.data?.mode === 'micro' ? port : <Tag>同进程直调</Tag>),
            },
            {
              title: '状态',
              dataIndex: 'ok',
              render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? 'UP' : 'DOWN'}</Tag>,
            },
            {
              title: 'DB',
              dataIndex: 'db_ok',
              render: (v: boolean) => <Tag color={v ? 'green' : 'red'}>{v ? 'OK' : 'FAIL'}</Tag>,
            },
            { title: '版本', dataIndex: 'version' },
            { title: '错误', dataIndex: 'error', render: (e?: string) => e ?? '-' },
          ]}
        />
      </Card>
    </div>
  )
}
