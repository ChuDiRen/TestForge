import type { ThemeConfig } from "antd";

/**
 * TestForge 设计系统 · 深空靛蓝（Deep Space Indigo）
 *
 * 品牌主色 indigo #4f46e5 + 紫罗兰 #7c3aed 渐变，内容区为精修亮色，
 * 侧边栏为深空渐变（见 styles.css 的 .tf-sider）。
 * 所有页面共用这一份 AntD v5 token，改风格只动这里 + styles.css。
 */

export const BRAND = {
  primary: "#4f46e5",
  violet: "#7c3aed",
  cyan: "#0891b2",
  gradient: "linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)",
  gradientSoft: "linear-gradient(135deg, rgba(79,70,229,0.10) 0%, rgba(124,58,237,0.10) 100%)",
  siderFrom: "#12142b",
  siderTo: "#1d1f3f",
};

export const theme: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorInfo: BRAND.primary,
    colorLink: BRAND.primary,
    colorSuccess: "#16a34a",
    colorWarning: "#d97706",
    colorError: "#dc2626",
    borderRadius: 10,
    borderRadiusLG: 14,
    colorBgLayout: "#f3f5fb",
    colorTextBase: "#181c2a",
    colorTextSecondary: "#5a6072",
    colorBorder: "#d9dded",
    colorBorderSecondary: "#e8eaf4",
    fontFamily:
      '-apple-system, "Segoe UI", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif',
    boxShadow:
      "0 1px 2px rgba(24,28,42,0.04), 0 4px 16px rgba(24,28,42,0.06)",
    boxShadowSecondary:
      "0 2px 6px rgba(24,28,42,0.06), 0 12px 32px rgba(37,42,80,0.12)",
    fontSize: 13.5,
  },
  components: {
    Layout: {
      siderBg: "transparent",
      headerBg: "transparent",
      bodyBg: "transparent",
    },
    Menu: {
      darkItemBg: "transparent",
      darkSubMenuItemBg: "transparent",
      darkItemSelectedBg: "rgba(124,120,255,0.22)",
      darkItemSelectedColor: "#ffffff",
      darkItemColor: "rgba(226,229,245,0.72)",
      darkItemHoverColor: "#ffffff",
      darkItemHoverBg: "rgba(255,255,255,0.06)",
      itemBorderRadius: 9,
      itemMarginInline: 10,
      itemHeight: 40,
      iconSize: 15,
      fontSize: 13.5,
    },
    Card: {
      borderRadiusLG: 14,
      paddingLG: 20,
    },
    Button: {
      controlHeight: 34,
      fontWeight: 500,
      primaryShadow: "0 4px 14px rgba(79,70,229,0.35)",
    },
    Table: {
      headerBg: "rgba(79,70,229,0.04)",
      headerColor: "#414862",
      headerSplitColor: "transparent",
      rowHoverBg: "rgba(79,70,229,0.045)",
      borderRadius: 10,
      cellPaddingBlockSM: 9,
      fontSize: 13,
    },
    Statistic: {
      titleFontSize: 12.5,
      contentFontSize: 26,
    },
    Tag: {
      borderRadiusSM: 6,
    },
    Segmented: {
      itemSelectedBg: "#ffffff",
      trackBg: "rgba(24,28,42,0.05)",
    },
    Modal: {
      borderRadiusLG: 14,
    },
    Tooltip: {
      fontSize: 12,
    },
  },
};
