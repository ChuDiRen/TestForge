import type { ReactNode } from 'react'

/**
 * 统一页头：渐变竖条 + 标题 + 副标题 + 右侧操作区。
 * 所有视图顶部都用它建立一致的视觉层级（页面级标题 → 卡片级分节）。
 */
export function PageHeader({ title, subtitle, extra }: { title: string; subtitle?: string; extra?: ReactNode }) {
  return (
    <div className="tf-page-head">
      <div>
        <div className="tf-page-head-title">{title}</div>
        {subtitle && <div className="tf-page-head-sub">{subtitle}</div>}
      </div>
      {extra && <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{extra}</div>}
    </div>
  )
}
