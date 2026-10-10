import { useState } from 'react'
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import { DeleteOutlined, KeyOutlined, PlusOutlined, StopOutlined, UserSwitchOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post, put } from '@/service'
import { PageHeader } from '@/components/PageHeader'

interface UserRow {
  username: string
  role: 'admin' | 'viewer'
  disabled: boolean
  created_at: string
}

/** 用户管理（仅 admin 可见可操作）：创建 / 改角色 / 停用启用 / 删除 / 重置密码。 */
export function Users() {
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['auth-users'], queryFn: () => get<UserRow[]>('/api/auth/users') })
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<null | { kind: 'reset' | 'create'; username?: string }>(null)
  const [form] = Form.useForm<{ username?: string; password?: string; role?: 'admin' | 'viewer' }>()
  const [me] = useState(() => localStorage.getItem('tf_username') || 'admin')

  const refresh = () => qc.invalidateQueries({ queryKey: ['auth-users'] })

  const onCreate = async () => {
    const v = await form.validateFields()
    try {
      await post('/api/auth/users/create', v)
      message.success(`账号 ${v.username} 已创建`)
      setCreateOpen(false)
      form.resetFields()
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const onReset = async () => {
    if (!editing?.username) return
    const v = await form.validateFields()
    try {
      await post(`/api/auth/users/${editing.username}/reset-password`, { new_password: v.password })
      message.success(`账号 ${editing.username} 密码已重置`)
      setEditing(null)
      form.resetFields()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const toggleDisabled = async (row: UserRow) => {
    try {
      await put(`/api/auth/users/${row.username}`, { disabled: !row.disabled })
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const changeRole = async (row: UserRow, role: string) => {
    try {
      await put(`/api/auth/users/${row.username}`, { role })
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const remove = async (row: UserRow) => {
    try {
      await post(`/api/auth/users/${row.username}/delete`)
      message.success(`账号 ${row.username} 已删除`)
      refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div>
      <PageHeader
        title="用户管理"
        subtitle="平台账号：admin 全权 / viewer 只读；停用即时生效，系统至少保留一个可用管理员"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            创建账号
          </Button>
        }
      />
      <Card>
        <Table<UserRow>
          rowKey="username"
          size="small"
          pagination={false}
          loading={users.isLoading}
          dataSource={users.data ?? []}
          columns={[
            {
              title: '账号',
              dataIndex: 'username',
              render: (v: string, r) => (
                <Space>
                  <span style={{ fontWeight: 600 }}>{v}</span>
                  {v === me && <Tag color="blue">当前登录</Tag>}
                  {r.disabled && <Tag color="red">已停用</Tag>}
                </Space>
              ),
            },
            {
              title: '角色',
              dataIndex: 'role',
              width: 200,
              render: (role: string, r) =>
                r.username === me ? (
                  <Tag color={role === 'admin' ? 'purple' : 'default'}>{role === 'admin' ? 'admin' : 'viewer'}</Tag>
                ) : (
                  <Select
                    size="small"
                    value={role}
                    style={{ width: 110 }}
                    onChange={(v) => changeRole(r, v)}
                    options={[
                      { value: 'admin', label: 'admin' },
                      { value: 'viewer', label: 'viewer' },
                    ]}
                  />
                ),
            },
            { title: '创建时间', dataIndex: 'created_at', width: 180, render: (v: string) => v.slice(0, 19).replace('T', ' ') },
            {
              title: '操作',
              key: 'ops',
              width: 280,
              render: (_: unknown, r) => {
                const self = r.username === me
                return (
                  <Space>
                    <Button
                      size="small"
                      icon={<KeyOutlined />}
                      onClick={() => {
                        setEditing({ kind: 'reset', username: r.username })
                        form.resetFields()
                      }}
                    >
                      重置密码
                    </Button>
                    <Button size="small" icon={<UserSwitchOutlined />} disabled={self} onClick={() => toggleDisabled(r)}>
                      {r.disabled ? '启用' : '停用'}
                    </Button>
                    <Popconfirm title={`确认删除账号 ${r.username}？`} onConfirm={() => remove(r)} disabled={self}>
                      <Button size="small" danger icon={<DeleteOutlined />} disabled={self}>
                        删除
                      </Button>
                    </Popconfirm>
                  </Space>
                )
              },
            },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          登录 / 停用 / 删除等全部认证事件已写入「日志 / 追溯」台账（type=认证）。
        </Typography.Text>
      </Card>

      {/* 创建账号 */}
      <Modal title="创建账号" open={createOpen} onOk={onCreate} onCancel={() => setCreateOpen(false)} okText="创建" destroyOnClose>
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="用户名" autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            rules={[
              { required: true, message: '请输入初始密码' },
              { min: 8, message: '密码至少 8 位' },
              {
                validator: (_: unknown, v: string) =>
                  v && (!/[A-Za-z]/.test(v) || !/\d/.test(v)) ? Promise.reject(new Error('必须同时包含字母和数字')) : Promise.resolve(),
              },
            ]}
          >
            <Input.Password placeholder="至少 8 位，含字母和数字" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="viewer" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'viewer', label: 'viewer（只读）' },
                { value: 'admin', label: 'admin（全权）' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 重置密码 */}
      <Modal
        title={editing ? `重置密码：${editing.username}` : ''}
        open={editing?.kind === 'reset'}
        onOk={onReset}
        onCancel={() => setEditing(null)}
        okText="重置"
        destroyOnClose
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            name="password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '密码至少 8 位' },
              {
                validator: (_: unknown, v: string) =>
                  v && (!/[A-Za-z]/.test(v) || !/\d/.test(v)) ? Promise.reject(new Error('必须同时包含字母和数字')) : Promise.resolve(),
              },
            ]}
          >
            <Input.Password placeholder="至少 8 位，含字母和数字" autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
