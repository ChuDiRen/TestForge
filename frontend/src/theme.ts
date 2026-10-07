import type { ThemeConfig } from "antd";
import { theme as antdTheme } from "antd";

/**
 * TestForge 设计系统 · 暖灰杉青（Warm Paper Teal）
 *
 * 参照 Linear / Vercel 的品质基准：暖白纸面 + 米灰细线 + 深杉青单强调色，
 * 细线分层代替投影，无渐变无光晕。布局不动，风格集中在这一份 token + styles.css。
 *
 * 双主题：light 暖灰杉青（默认）/ dark 杉青夜航（同族暗色，炭底 + 亮杉青）。
 * 切换入口在顶栏（App.tsx 的主题按钮），选择持久化在 localStorage("tf_theme")，
 * 并同步到 html[data-theme]，styles.css 的 CSS 变量按它整站换肤。
 */

export type ThemeMode = "light" | "dark";

export const THEME_STORAGE_KEY = "tf_theme";

export function getInitialThemeMode(): ThemeMode {
  return localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
}

export const BRAND = {
  primary: "#0d7d72",
  primaryDeep: "#0b655c",
  primaryBright: "#2fb3a4",
  accSoft: "#eaf4f2",
};

/** 亮色 · 暖灰杉青（默认主题） */
export const lightTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorInfo: BRAND.primary,
    colorLink: BRAND.primary,
    colorSuccess: "#15803d",
    colorWarning: "#b45309",
    colorError: "#c93a2e",
    borderRadius: 10,
    borderRadiusLG: 12,
    colorBgLayout: "#f7f7f5",
    colorBgContainer: "#ffffff",
    colorTextBase: "#24272b",
    colorTextSecondary: "#6b6f76",
    colorBorder: "#dcd9d2",
    colorBorderSecondary: "#eeece7",
    fontFamily:
      '-apple-system, "Segoe UI", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif',
    boxShadow: "0 1px 2px rgba(28, 30, 33, 0.04)",
    boxShadowSecondary: "0 8px 24px rgba(28, 30, 33, 0.10)",
    fontSize: 13.5,
  },
  components: {
    Layout: {
      siderBg: "transparent",
      headerBg: "transparent",
      bodyBg: "transparent",
    },
    Menu: {
      // 侧栏是白纸面，选中态 = 杉青底 + 左缘 2px 青条（见 styles.css）
      itemBg: "transparent",
      itemColor: "#5d6167",
      itemHoverBg: "#f4f3f0",
      itemHoverColor: "#24272b",
      itemSelectedBg: "#eaf4f2",
      itemSelectedColor: "#0b655c",
      activeBarBorderWidth: 0,
      itemBorderRadius: 8,
      itemMarginInline: 10,
      itemHeight: 38,
      iconSize: 15,
      fontSize: 13.5,
      subMenuItemBg: "transparent",
      popupBg: "#ffffff",
    },
    Card: {
      borderRadiusLG: 12,
      paddingLG: 20,
    },
    Button: {
      controlHeight: 34,
      fontWeight: 500,
      primaryShadow: "0 1px 2px rgba(11, 101, 92, 0.22)",
    },
    Table: {
      headerBg: "#faf9f7",
      headerColor: "#5d6167",
      headerSplitColor: "transparent",
      rowHoverBg: "#f6f5f2",
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
      trackBg: "rgba(36, 39, 43, 0.05)",
    },
    Modal: {
      borderRadiusLG: 12,
    },
    Tooltip: {
      fontSize: 12,
    },
  },
};

/** 暗色 · 杉青夜航（同族暗色：炭底、发丝线、亮杉青） */
export const darkTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: BRAND.primaryBright,
    colorInfo: BRAND.primaryBright,
    colorLink: BRAND.primaryBright,
    colorSuccess: "#4cb782",
    colorWarning: "#dfa050",
    colorError: "#e5645a",
    borderRadius: 10,
    borderRadiusLG: 12,
    colorBgBase: "#141618",
    colorBgLayout: "#101113",
    colorBgContainer: "#1a1c1f",
    colorBgElevated: "#1f2225",
    colorTextBase: "#e4e6e8",
    colorTextSecondary: "#9aa0a6",
    colorBorder: "rgba(255, 255, 255, 0.13)",
    colorBorderSecondary: "rgba(255, 255, 255, 0.07)",
    fontFamily:
      '-apple-system, "Segoe UI", "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif',
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.35)",
    boxShadowSecondary: "0 10px 28px rgba(0, 0, 0, 0.5)",
    fontSize: 13.5,
  },
  components: {
    Layout: {
      siderBg: "transparent",
      headerBg: "transparent",
      bodyBg: "transparent",
    },
    Menu: {
      itemBg: "transparent",
      itemColor: "#9aa0a6",
      itemHoverBg: "rgba(255, 255, 255, 0.05)",
      itemHoverColor: "#e4e6e8",
      itemSelectedBg: "rgba(47, 179, 164, 0.15)",
      itemSelectedColor: "#5ad0c2",
      activeBarBorderWidth: 0,
      itemBorderRadius: 8,
      itemMarginInline: 10,
      itemHeight: 38,
      iconSize: 15,
      fontSize: 13.5,
      subMenuItemBg: "transparent",
      popupBg: "#1f2225",
    },
    Card: {
      borderRadiusLG: 12,
      paddingLG: 20,
    },
    Button: {
      controlHeight: 34,
      fontWeight: 500,
      primaryShadow: "none",
    },
    Table: {
      headerBg: "rgba(255, 255, 255, 0.04)",
      headerColor: "#9aa0a6",
      headerSplitColor: "transparent",
      rowHoverBg: "rgba(255, 255, 255, 0.04)",
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
      itemSelectedBg: "#2a2d31",
      trackBg: "rgba(255, 255, 255, 0.06)",
    },
    Modal: {
      borderRadiusLG: 12,
    },
    Tooltip: {
      fontSize: 12,
    },
  },
};

/** 兼容旧引用（App.tsx 等处按 mode 取用） */
export const themes: Record<ThemeMode, ThemeConfig> = { light: lightTheme, dark: darkTheme };
export const theme = lightTheme;
