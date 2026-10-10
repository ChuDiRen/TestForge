import { useEffect, useMemo, useState } from 'react'
import { Avatar, Button, ConfigProvider, Dropdown, Drawer, Grid, Layout, Menu, Typography } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import { LogoutOutlined, MenuUnfoldOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import type { ThemeConfig } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { get, setToken } from '@/service'
import { getLang, getInitialThemeMode, themes, THEME_STORAGE_KEY, useLang, type ThemeMode } from '@/store'
import { buildMenuItems, currentView, defaultOpenKeysFor, VIEW_COMPONENTS, VIEWS, type ViewKey } from '@/routes'
import { Login } from '@/pages/Login'

const { Sider, Header, Content } = Layout

function Brand({ compact }: { compact?: boolean }) {
  return (
    <div className="tf-brand">
      <div className="tf-brand-mark">TF</div>
      <div>
        <div className="tf-brand-name">TestForge</div>
        {!compact && <div className="tf-brand-sub">AI 测试用例生成平台</div>}
      </div>
    </div>
  )
}

interface Me {
  username: string
  role: string
}

function Shell({ user, mode, onToggleTheme }: { user: Me; mode: ThemeMode; onToggleTheme: () => void }) {
  // 视图切换用 React 状态（SPA），URL 仅作刷新恢复用——绝不全页 reload
  const [view, setView] = useState<ViewKey>(currentView)
  const screens = Grid.useBreakpoint()
  const isMobile = !screens.md
  const [navOpen, setNavOpen] = useState(false)
  const current = VIEWS.find((v) => v.key === view)

  useEffect(() => {
    // AI 回答里的引用标签（[[Function:x]] 等）通过 tf-navigate 事件请求跳转
    const onNav = (e: Event) => {
      const key = (e as CustomEvent<string>).detail as ViewKey
      if (VIEWS.some((v) => v.key === key)) {
        setView(key)
        window.history.replaceState(null, '', `?view=${key}`)
      }
    }
    window.addEventListener('tf-navigate', onNav)
    return () => window.removeEventListener('tf-navigate', onNav)
  }, [])

  const body = useMemo(() => {
    const C = VIEW_COMPONENTS[view]
    return <C />
  }, [view])

  const menuItems = buildMenuItems(user.role)
  const [defaultOpenKeys] = useState(() => defaultOpenKeysFor(view))

  const onMenuClick = (e: { key: string }) => {
    const key = e.key as ViewKey
    setNavOpen(false)
    setView(key)
    window.history.replaceState(null, '', `?view=${key}`)
  }
  const logout = () => {
    setToken('')
    window.location.reload()
  }
  const userMenu = {
    items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录' }],
    onClick: logout,
  }

  return (
    <ConfigProvider locale={getLang() === 'en' ? enUS : zhCN} theme={themes[mode] as ThemeConfig}>
      <Layout style={{ minHeight: '100vh' }}>
        {!isMobile && (
          <Sider width={220} className="tf-sider" style={{ overflow: 'hidden' }}>
            <Brand />
            <Menu
              theme="light"
              mode="inline"
              selectedKeys={[view]}
              defaultOpenKeys={defaultOpenKeys}
              items={menuItems}
              onClick={onMenuClick}
              style={{ height: 'calc(100vh - 72px)', overflowY: 'auto', paddingBlock: 6 }}
            />
          </Sider>
        )}
        <Layout>
          <Header className="tf-header">
            <div className="tf-header-view">
              {isMobile && (
                <Button
                  type="text"
                  aria-label="打开导航菜单"
                  icon={<MenuUnfoldOutlined style={{ fontSize: 16 }} />}
                  onClick={() => setNavOpen(true)}
                />
              )}
              {!isMobile && <span className="tf-header-icon">{current?.icon}</span>}
              <Typography.Text strong style={{ fontSize: 15.5 }} ellipsis>
                {isMobile ? 'TestForge · ' : ''}
                {current?.label}
              </Typography.Text>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Button
                className="tf-theme-toggle"
                type="text"
                aria-label={mode === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}
                title={mode === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}
                icon={mode === 'dark' ? <SunOutlined style={{ fontSize: 15 }} /> : <MoonOutlined style={{ fontSize: 15 }} />}
                onClick={onToggleTheme}
              />
              <Dropdown menu={userMenu} placement="bottomRight">
                <div className="tf-user-chip">
                  <Avatar size={26} style={{ background: 'var(--tf-primary)', flexShrink: 0 }}>
                    {(user.username || 'U').slice(0, 1).toUpperCase()}
                  </Avatar>
                  {!isMobile && (
                    <Typography.Text style={{ fontSize: 12.5 }}>
                      {user.username}
                      <span style={{ color: 'var(--tf-ink-2)', marginLeft: 6, fontSize: 11.5 }}>{user.role === 'admin' ? '管理员' : '只读'}</span>
                    </Typography.Text>
                  )}
                </div>
              </Dropdown>
            </div>
          </Header>
          <Content className="tf-content">
            <div className="tf-page">{body}</div>
          </Content>
        </Layout>
        <Drawer
          title={<Brand compact />}
          placement="left"
          width={280}
          open={navOpen}
          onClose={() => setNavOpen(false)}
          styles={{ body: { padding: 0 } }}
        >
          <Menu
            mode="inline"
            selectedKeys={[view]}
            defaultOpenKeys={defaultOpenKeys}
            items={menuItems}
            onClick={onMenuClick}
            style={{ borderInlineEnd: 'none' }}
          />
        </Drawer>
      </Layout>
    </ConfigProvider>
  )
}

export function App() {
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => get<Me>('/api/auth/me'),
    retry: false,
    staleTime: Infinity,
  })
  // 语言：订阅 i18n store，切换时触发 ConfigProvider locale 联动（antd 组件文案同步）
  useLang()

  // 主题模式：亮色「暖灰杉青」/ 暗色「杉青夜航」，选择持久化并同步 html[data-theme]
  const [mode, setMode] = useState<ThemeMode>(getInitialThemeMode)
  useEffect(() => {
    document.documentElement.dataset.theme = mode
    localStorage.setItem(THEME_STORAGE_KEY, mode)
  }, [mode])
  const toggleTheme = () => setMode((m) => (m === 'dark' ? 'light' : 'dark'))

  if (me.isLoading) {
    // 认证校验通常 <100ms：渲染空白而不是全屏 Spin，避免刷新时的转圈动画
    return null
  }
  if (me.isError || !me.data) {
    return (
      <ConfigProvider locale={zhCN} theme={themes[mode] as ThemeConfig}>
        <Login onLogin={() => me.refetch()} />
      </ConfigProvider>
    )
  }
  return <Shell user={me.data} mode={mode} onToggleTheme={toggleTheme} />
}
