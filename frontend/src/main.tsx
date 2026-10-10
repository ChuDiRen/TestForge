import '@/assets/css/styles.css'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getInitialThemeMode } from '@/store'
import { App } from '@/App'

// 首帧前同步主题，避免暗色用户刷新时闪一下亮色底
document.documentElement.dataset.theme = getInitialThemeMode()

// 标题兜底：任何运行时干扰后仍保持品牌名（正常情况下 index.html 的 <title> 已生效）
document.title = 'TestForge · AI 测试用例生成平台'

// DEV 错误探针：React 卸树只走 console.error，页面白屏却抓不到报错——全量收集到 window.__tfErrors
if (import.meta.env.DEV) {
  ;(window as any).__tfErrors = []
  const origError = console.error.bind(console)
  console.error = (...args: unknown[]) => {
    const err = args.find((a) => a instanceof Error) as Error | undefined
    ;(window as any).__tfErrors.push(err?.stack ?? String(args[0]))
    origError(...args)
  }
}

const qc = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)
