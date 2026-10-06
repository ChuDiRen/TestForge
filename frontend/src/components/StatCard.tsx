import type { ReactNode } from "react";
import { Card } from "antd";

const CHIP_STYLES: Record<string, string> = {
  indigo: "linear-gradient(135deg, #4f46e5, #6d28d9)",
  violet: "linear-gradient(135deg, #7c3aed, #a21caf)",
  cyan: "linear-gradient(135deg, #0891b2, #2563eb)",
  green: "linear-gradient(135deg, #059669, #16a34a)",
  amber: "linear-gradient(135deg, #d97706, #ea580c)",
  rose: "linear-gradient(135deg, #e11d48, #dc2626)",
};

/**
 * 高端统计卡：渐变图标芯片 + 大数字 + 标签/后缀，悬停轻浮起。
 * 覆盖 antd Statistic 的默认排版，视觉与仪表盘/列表页一致。
 */
export function StatCard({
  icon,
  label,
  value,
  suffix,
  tone = "indigo",
  footer,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  suffix?: string;
  tone?: keyof typeof CHIP_STYLES;
  footer?: ReactNode;
}) {
  return (
    <Card className="tf-stat-card tf-lift" styles={{ body: { padding: "18px 20px" } }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div className="tf-stat-chip" style={{ background: CHIP_STYLES[tone] ?? CHIP_STYLES.indigo }}>
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
      {footer && (
        <div style={{ marginTop: 10, fontSize: 12, color: "var(--tf-ink-2)" }}>{footer}</div>
      )}
    </Card>
  );
}
