import { PageHeader } from '@/components/PageHeader'
import { NextStep } from '@/components/NextStep'
import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Table, Tabs, Tag, Tooltip, Upload, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post, uploadRepoZip } from '@/service'

interface RepoRow {
  id: number
  url: string
  branch: string
  status: string
  last_pull: string | null
  head_rev: string
}

/** 远程 Git URL 校验：file:// 与本地/盘符路径一律拒绝 */
const REMOTE_URL_RE = /^(https?:\/\/|git:\/\/|ssh:\/\/|git@[\w.-]+:)\S+/

function validateRemoteUrl(_rule: unknown, value: string): Promise<void> {
  const u = (value || '').trim()
  if (!u) return Promise.resolve() // required 规则负责必填
  const lowered = u.toLowerCase()
  const isLocal =
    lowered.startsWith('file:') || lowered.startsWith('\\\\') || u.startsWith('/') || u.startsWith('\\') || u.startsWith('~') || /^[a-zA-Z]:/.test(u)
  if (isLocal || !REMOTE_URL_RE.test(u)) {
    return Promise.reject('仅支持远程 Git URL（https://… 或 git@host:repo.git），不允许本地路径')
  }
  return Promise.resolve()
}

export function RepoAdd() {
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const repos = useQuery({ queryKey: ['repos'], queryFn: () => get<RepoRow[]>('/api/repos'), refetchInterval: 8000 })
  const [steps, setSteps] = useState<Record<number, string[]>>({})
  const [nextStep, setNextStep] = useState<string | null>(null)
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [zipName, setZipName] = useState('')

  const afterRegistered = (id: number, stepsList: string[]) => {
    qc.invalidateQueries({ queryKey: ['repos'] })
    setSteps((s) => ({ ...s, [id]: stepsList }))
    setNextStep(`仓库 #${id} 已接入——索引与 Wiki 编译在后台进行，任务完成后即可生成用例`)
  }

  const add = useMutation({
    mutationFn: (v: { url: string; branch: string }) => post<any>('/api/repos', v),
    onSuccess: (r) => {
      message.success(`仓库 #${r.id} 已接入`)
      form.resetFields()
      afterRegistered(r.id, r.steps ?? [])
    },
  })
  const uploadZip = useMutation({
    mutationFn: () => uploadRepoZip(zipFile!, zipName),
    onSuccess: (r) => {
      message.success(`代码包已接入：${r.functions} 函数 / ${r.call_edges} 调用边 / Wiki ${r.wiki_pages} 页`)
      setZipFile(null)
      setZipName('')
      afterRegistered(r.id, r.steps ?? [])
    },
    onError: (e: any) => message.error(e.message),
  })
  const pull = useMutation({
    mutationFn: (id: number) => post<any>(`/api/repos/${id}/pull`),
    onSuccess: (r) => {
      message.success('拉取完成')
      qc.invalidateQueries({ queryKey: ['repos'] })
      setSteps((s) => ({ ...s, [r.id!]: r.steps ?? [] }))
    },
    onError: (e: any) => message.error(e.message),
  })

  return (
    <div>
      <PageHeader title="仓库接入" subtitle="远程 Git URL 或上传代码压缩包：安全解压 → tree-sitter 索引 → 调用图谱 → Wiki 编译" />
      {nextStep && (
        <NextStep
          title={nextStep}
          actions={[
            { label: '查看代码图谱', view: 'graph' },
            { label: '查看代码库 / Wiki', view: 'wiki' },
            { label: '去录入需求', view: 'requirements' },
            { label: '直接生成用例', view: 'workbench' },
          ]}
        />
      )}
      <Card title="接入仓库" style={{ marginBottom: 16 }}>
        <Tabs
          defaultActiveKey="git"
          items={[
            {
              key: 'git',
              label: '远程 Git URL',
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="仅支持远程 Git 仓库（https://… 或 git@host:repo.git）"
                    description="出于安全考虑，平台不接受本地路径 / file:// 上传——网关不会读取服务器本地任意目录。本地代码请打包成 zip 走「上传代码包」。"
                  />
                  <Form form={form} layout="vertical" onFinish={(v) => add.mutate(v)}>
                    <Form.Item
                      name="url"
                      rules={[{ required: true, message: '仓库地址必填' }, { validator: validateRemoteUrl }]}
                      style={{ marginBottom: 8 }}
                    >
                      <Input style={{ width: 420, maxWidth: '100%' }} placeholder="https://github.com/user/repo.git 或 git@host:user/repo.git" />
                    </Form.Item>
                    <Form.Item name="branch" initialValue="main" style={{ marginBottom: 8 }}>
                      <Input style={{ width: 120, maxWidth: '100%' }} placeholder="分支" />
                    </Form.Item>
                    <Button type="primary" htmlType="submit" loading={add.isPending}>
                      接入（克隆→索引→Wiki 编译）
                    </Button>
                  </Form>
                </>
              ),
            },
            {
              key: 'zip',
              label: '上传代码包（zip）',
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="上传 zip 压缩包：服务端安全解压后自动产出函数索引、调用图谱、影响面与 Wiki——与 Git 接入完全同构"
                    description="安全栏：路径穿越防护 · 单包 ≤200MB · 解压后 ≤20000 文件 · 自动排除 .git / node_modules / 构建产物。同名包重新上传 = 更新图谱。"
                  />
                  <Upload.Dragger
                    accept=".zip"
                    maxCount={1}
                    beforeUpload={(file) => {
                      setZipFile(file)
                      if (!zipName) setZipName(file.name.replace(/\.zip$/i, ''))
                      return false
                    }}
                    onRemove={() => {
                      setZipFile(null)
                      return true
                    }}
                    fileList={zipFile ? [{ uid: 'zip', name: zipFile.name, status: 'done' }] : []}
                    style={{ marginBottom: 12 }}
                  >
                    <p className="ant-upload-drag-icon">
                      <InboxOutlined />
                    </p>
                    <p className="ant-upload-text">点击或拖拽 zip 代码包到此处</p>
                    <p className="ant-upload-hint">上传后自动生成知识图谱：函数 / 调用边 / 聚类 / 执行流 / Wiki</p>
                  </Upload.Dragger>
                  <Input
                    style={{ width: 280, marginBottom: 8 }}
                    placeholder="仓库名称（默认取文件名）"
                    value={zipName}
                    onChange={(e) => setZipName(e.target.value)}
                  />
                  <Button type="primary" loading={uploadZip.isPending} disabled={!zipFile} onClick={() => uploadZip.mutate()}>
                    上传并生成知识图谱
                  </Button>
                </>
              ),
            },
          ]}
        />
      </Card>
      <Card title="仓库列表">
        <Table<RepoRow>
          rowKey="id"
          size="small"
          scroll={{ x: 720 }}
          pagination={false}
          loading={repos.isLoading}
          dataSource={repos.data ?? []}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            {
              title: 'URL',
              dataIndex: 'url',
              ellipsis: true,
              render: (v: string) => (
                <span>
                  {v} {v?.startsWith('upload://') && <Tag color="orange">代码包</Tag>}
                </span>
              ),
            },
            { title: '分支', dataIndex: 'branch', width: 80 },
            { title: 'HEAD', dataIndex: 'head_rev', width: 100, render: (v: string) => v?.slice(0, 8) },
            {
              title: '状态',
              dataIndex: 'status',
              width: 100,
              render: (s: string) => <Tag color={s === '已接入' ? 'green' : 'red'}>{s}</Tag>,
            },
            { title: '最近拉取', dataIndex: 'last_pull', width: 160, render: (v: string | null) => v?.replace('T', ' ').slice(0, 19) ?? '-' },
            {
              title: '操作',
              width: 110,
              render: (_, r) =>
                r.url?.startsWith('upload://') ? (
                  <Tooltip title="代码包来源没有远端——重新上传同名 zip 即可更新图谱">
                    <Button size="small" disabled>
                      拉取
                    </Button>
                  </Tooltip>
                ) : (
                  <Button size="small" onClick={() => pull.mutate(r.id)} loading={pull.isPending}>
                    拉取
                  </Button>
                ),
            },
          ]}
        />
        {Object.entries(steps).map(([id, st]) =>
          st.length ? (
            <Card key={id} size="small" title={`仓库 #${id} 流水线`} style={{ marginTop: 12 }}>
              {st.map((s, i) => (
                <Tag key={i} style={{ marginBottom: 4 }}>
                  {i + 1}. {s}
                </Tag>
              ))}
            </Card>
          ) : null,
        )}
      </Card>
    </div>
  )
}
