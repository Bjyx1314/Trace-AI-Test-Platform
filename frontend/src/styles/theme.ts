import type { ThemeConfig } from 'antd'

// ─── Trace AI 设计语言（claude design 重设计落地）──────────────────────────
// 主色：Anthropic 橙(clay)；中性背景 #F7F9FB；卡片白底精致描边 + 轻投影；数字用等宽字体。
export const PRIMARY_COLOR = '#D97757'        // 主色 brand-solid（橙·clay）
export const PRIMARY_DEEP = '#B5600A'         // brand-text：品牌色文字/链接/激活
export const PRIMARY_CYAN = '#E8930C'         // 渐变起点（琥珀）
export const SIDER_BG = '#FFFFFF'
export const SELECTED_BG = '#FEF3EE'          // brand-soft 选中态浅橙

// 品牌渐变 brand-grad（Logo / 强调按钮 / 头像）：琥珀 → 陶土
export const TECH_GRADIENT = 'linear-gradient(140deg, #E8930C 0%, #D97757 100%)'
export const TECH_GRADIENT_DEEP = 'linear-gradient(140deg, #D97757 0%, #B5600A 100%)'

// 发光投影（hover / 强调元素）
export const GLOW_SHADOW = '0 5px 14px -5px rgba(217,119,87,.5), 0 1px 4px rgba(217,119,87,.14)'

// 等宽数字字体栈：指标数字更有数据感（设计稿用 JetBrains Mono）
export const MONO_FONT =
  "'JetBrains Mono', 'SF Mono', 'Roboto Mono', ui-monospace, Menlo, Consolas, monospace"

// 语义状态色（与设计稿对齐）
export const SEMANTIC = {
  success: '#16A34A',
  warning: '#E8930C',
  error: '#EF4444',
  info: '#D97757',
}

export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: PRIMARY_COLOR,
    borderRadius: 9,
    colorBgLayout: '#F7F9FB',
    colorLink: PRIMARY_DEEP,
    colorLinkHover: PRIMARY_COLOR,
    colorText: '#1E293B',
    colorTextSecondary: '#64748B',
    fontSize: 14,
    fontFamily:
      "'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    colorBorderSecondary: '#ECEFF2',
  },
  components: {
    Layout: {
      siderBg: SIDER_BG,
      headerBg: 'rgba(247,249,251,.82)',
      bodyBg: '#F7F9FB',
    },
    Menu: {
      itemColor: '#64748B',
      itemHoverColor: '#1E293B',
      itemHoverBg: '#F3F6F8',
      itemSelectedColor: PRIMARY_DEEP,
      itemSelectedBg: SELECTED_BG,
      groupTitleColor: '#94A3B8',
      itemBorderRadius: 10,
      itemMarginInline: 12,
      itemHeight: 38,
    },
    Card: {
      borderRadiusLG: 14,
    },
    Table: {
      headerBg: '#FAFBFC',
      headerColor: '#64748B',
      headerSplitColor: 'transparent',
      rowHoverBg: '#FAFCFD',
      borderColor: '#F1F4F6',
      cellPaddingBlock: 14,
    },
    Button: {
      primaryShadow: '0 4px 12px -5px rgba(20,120,200,.55)',
      borderRadius: 9,
    },
    Input: { borderRadius: 9 },
    Select: { borderRadius: 9 },
  },
}

// 卡片面板统一样式：精致描边 + 轻投影（设计稿 #ECEFF2 + 极淡阴影）
export const PANEL_CARD_STYLE = {
  border: '1px solid #ECEFF2',
  boxShadow: '0 1px 2px rgba(16,24,40,.04)',
  borderRadius: 14,
}
