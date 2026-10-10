import { useEffect, useState } from 'react'

/** 跟随 App 根组件写入 html[data-theme] 的亮暗模式（MutationObserver，切换即时生效）。
 *  Canvas/Sigma 画布吃不到 CSS 变量，需要解析成实际色值时配合 cssVar() 使用。 */
export function useThemeMode(): 'light' | 'dark' {
  const [mode, setMode] = useState<'light' | 'dark'>(() => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'))
  useEffect(() => {
    const ob = new MutationObserver(() => setMode(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'))
    ob.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => ob.disconnect()
  }, [])
  return mode
}
