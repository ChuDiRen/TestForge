import type { ReactNode } from 'react'
import { Card } from 'antd'

// 平涂芯片：底色/前景色走 styles.css 的变量，亮暗主题自动跟随
const CHIP_STYLES: Record<string, { bg: string; fg: string }> = {
  indigo: { bg: 'var(--tf-chip-teal-bg)', fg: 'var(--tf-chip-teal-fg)' },
  violet: { bg: 'var(--tf-chip-slate-bg)', fg: 'var(--tf-chip-slate-fg)' },
  cyan: { bg: 'var(--tf-chip-blue-bg)', fg: 'var(--tf-chip-blue-fg)' },
  green: { bg: 'var(--tf-chip-green-bg)', fg: 'var(--tf-chip-green-fg)' },
  amber: { bg: 'var(--tf-chip-amber-bg)', fg: 'var(--tf-chip-amber-fg)' },
  rose: { bg: 'var(--tf-chip-rose-bg)', fg: 'var(--tf-chip-rose-fg)' },
}

/**
 * 统计卡：平涂图标芯片 + 大数字 + 标签/后缀，悬停轻浮起。
 * 覆盖 antd Statistic 的默认排版，视觉与仪表盘/列表页一致。
 */
export function StatCard({
  icon,
  label,
  value,
  suffix,
  tone = 'indigo',
  footer,
}: {
  icon: ReactNode
  label: string
  value: ReactNode
  suffix?: string
  tone?: keyof typeof CHIP_STYLES
  footer?: ReactNode
}) {
  return (
    <Card className="tf-stat-card tf-lift" styles={{ body: { padding: '18px 20px' } }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div
          className="tf-stat-chip"
          style={{
            background: (CHIP_STYLES[tone] ?? CHIP_STYLES.indigo).bg,
            color: (CHIP_STYLES[tone] ?? CHIP_STYLES.indigo).fg,
          }}
        >
          {icon}
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="tf-stat-label">{label}</div>
          <div className="tf-stat-value">
            {value}
            {suffix && <span className="tf-stat-suffix">{suffix}</span>}
          </div>
        </div>
      </div>
      {footer && <div style={{ marginTop: 10, fontSize: 12, color: 'var(--tf-ink-2)' }}>{footer}</div>}
    </Card>
  )
}
