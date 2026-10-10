import { useState } from 'react'
import { Alert, Button, Form, Input, Modal, Typography } from 'antd'
import { AimOutlined, LockOutlined, SafetyOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { post, setUsername, setToken } from '@/service'

const POINTS = [
  { icon: <ThunderboltOutlined />, title: '知识预编译 · 两阶段生成', desc: '六路上下文组装，覆盖守卫自动补齐用例类别' },
  { icon: <AimOutlined />, title: '沙箱真实验证闭环', desc: '期望值来自真实代码执行，失败自动修复 ≤3 轮' },
  { icon: <SafetyOutlined />, title: '全链路可回溯', desc: 'traceID 账本 + 生成依据逐条引用溯源' },
]

const DEMO_USERNAME = 'admin'
const DEMO_PASSWORD = 'testforge-admin' // 仅本地开发/演示：backend/.env 可改 ADMIN_PASSWORD

interface LoginResult {
  token: string
  username: string
  role: string
  must_change_password?: boolean
}

export function Login({ onLogin }: { onLogin: () => void }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [form] = Form.useForm<{ username: string; password: string }>()
  // 默认口令登录 → 强制修改密码后才能进入系统
  const [forceChange, setForceChange] = useState<null | { username: string; password: string }>(null)
  const [changeLoading, setChangeLoading] = useState(false)
  const [changeError, setChangeError] = useState('')
  const [changeForm] = Form.useForm<{ new_password: string; confirm: string }>()

  const doLogin = async (values: { username: string; password: string }) => {
    setLoading(true)
    setError('')
    try {
      const res = await post<LoginResult>('/api/auth/login', values)
      if (res.must_change_password) {
        // 默认口令：先不落地 token，改完密码再进系统
        setForceChange({ username: values.username, password: values.password })
        return
      }
      setToken(res.token)
      setUsername(res.username)
      onLogin()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const oneClickLogin = () => {
    setError('')
    form.setFieldsValue({ username: DEMO_USERNAME, password: DEMO_PASSWORD })
    void doLogin({ username: DEMO_USERNAME, password: DEMO_PASSWORD })
  }

  const doForceChange = async () => {
    if (!forceChange) return
    setChangeError('')
    try {
      const v = await changeForm.validateFields()
      setChangeLoading(true)
      // 用临时凭据完成改密：直接以默认口令登录换 token → 调自助改密接口
      const res = await post<LoginResult>('/api/auth/login', { username: forceChange.username, password: forceChange.password })
      setToken(res.token)
      await post('/api/auth/change-password', { old_password: forceChange.password, new_password: v.new_password })
      setToken('')
      setForceChange(null)
      Modal.success({ title: '密码已更新', content: '请使用新密码重新登录', okText: '去登录' })
    } catch (e) {
      if (e instanceof Error && e.message !== 'validate failed') setChangeError(e.message)
    } finally {
      setChangeLoading(false)
    }
  }

  return (
    <div className="tf-login">
      <div className="tf-login-hero">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div className="tf-brand-mark" style={{ width: 42, height: 42, fontSize: 17 }}>
            TF
          </div>
          <div>
            <div style={{ fontWeight: 800, fontSize: 18, letterSpacing: 0.4 }}>TestForge</div>
            <div style={{ fontSize: 12, color: 'rgba(226,229,245,0.6)' }}>AI 测试用例生成平台</div>
          </div>
        </div>
        <h1>
          需求是源头，知识预编译，
          <br />
          AI 生成用例，沙箱验证闭环
        </h1>
        <p>从需求录入到准出报告的全链路测试工程平台：仓库索引、契约管理、用例生成、真实执行、缺陷回归，一个平台闭环。</p>
        <div style={{ display: 'grid', gap: 14, marginTop: 6 }}>
          {POINTS.map((p) => (
            <div key={p.title} className="tf-login-point">
              {p.icon}
              <div>
                <div style={{ fontWeight: 600, marginBottom: 2 }}>{p.title}</div>
                <div style={{ color: 'rgba(226,229,245,0.6)', fontSize: 12 }}>{p.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="tf-login-panel">
        <div className="tf-login-card" style={{ padding: '34px 32px 26px' }}>
          <div style={{ textAlign: 'center', marginBottom: 22 }}>
            <div
              style={{
                width: 44,
                height: 44,
                margin: '0 auto 12px',
                display: 'grid',
                placeItems: 'center',
                borderRadius: 12,
                background: 'var(--tf-primary)',
                color: '#fff',
                fontWeight: 800,
                fontSize: 17,
                boxShadow: '0 8px 22px rgba(11,101,92,0.35)',
              }}
            >
              TF
            </div>
            <Typography.Title level={4} style={{ marginBottom: 2 }}>
              登录 TestForge
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
              AI 测试用例生成平台
            </Typography.Text>
          </div>
          {error && (
            <Alert
              type="error"
              message={error}
              description={error.includes('用户名或密码') ? '若默认口令已修改过，请手动输入新口令。' : undefined}
              showIcon
              style={{ marginBottom: 16 }}
            />
          )}
          <Form form={form} onFinish={doLogin} layout="vertical" requiredMark={false}>
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input placeholder="用户名" autoComplete="username" size="large" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password placeholder="密码" autoComplete="current-password" size="large" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading} size="large" style={{ marginTop: 4 }}>
              登 录
            </Button>
            <Button type="dashed" block icon={<ThunderboltOutlined />} onClick={oneClickLogin} disabled={loading} style={{ marginTop: 10 }}>
              一键登录（演示管理员）
            </Button>
            <Typography.Text type="secondary" style={{ display: 'block', textAlign: 'center', marginTop: 14, fontSize: 12 }}>
              首次登录将要求修改默认口令；改过口令后请手动输入
            </Typography.Text>
          </Form>
        </div>
      </div>

      <Modal
        title={
          <span>
            <LockOutlined style={{ color: 'var(--tf-primary, #0d7d72)', marginRight: 8 }} />
            检测到默认口令，请先修改密码
          </span>
        }
        open={forceChange !== null}
        okText="修改并重新登录"
        cancelText="取消"
        confirmLoading={changeLoading}
        onOk={doForceChange}
        onCancel={() => setForceChange(null)}
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="当前仍在使用首启默认口令，为避免未授权访问，必须修改密码后才能使用系统。"
        />
        {changeError && <Alert type="error" message={changeError} showIcon style={{ marginBottom: 16 }} />}
        <Form form={changeForm} layout="vertical" requiredMark={false}>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '密码至少 8 位' },
              {
                validator: (_: unknown, v: string) =>
                  !v || /[A-Za-z]/.test(v) === false || /\d/.test(v) === false
                    ? Promise.reject(new Error('必须同时包含字母和数字'))
                    : Promise.resolve(),
              },
            ]}
          >
            <Input.Password placeholder="至少 8 位，含字母和数字" autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请再次输入新密码' },
              ({ getFieldValue }) => ({
                validator(_: unknown, v: string) {
                  return !v || v === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('两次输入不一致'))
                },
              }),
            ]}
          >
            <Input.Password placeholder="再次输入新密码" autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
