import { Grid } from 'antd'

/** 中屏（md）以下按手机布局渲染：抽屉导航、全宽抽屉、纵向表单 */
export function useIsMobile(): boolean {
  const screens = Grid.useBreakpoint()
  return !screens.md
}
