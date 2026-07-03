import type { CSSProperties } from 'react'

// ─── 枚举标签配色系统 ────────────────────────────────────────────────
// 8 色调色板循环 + 端固定映射 + 优先级状态色 + 缺陷等级色。不再用固定灰色。

export const TAG_PALETTE = [
  { bg: '#EAF4FB', c: '#1577C2', bd: '#C6E1F4' }, // 0 蓝
  { bg: '#E6F6F4', c: '#0B8276', bd: '#BFE7E1' }, // 1 青
  { bg: '#E9F8EF', c: '#128A43', bd: '#B5E0C8' }, // 2 绿
  { bg: '#F0EDFD', c: '#6B4FD6', bd: '#DAD0FA' }, // 3 紫
  { bg: '#FEF3EE', c: '#C96B44', bd: '#F5D6C8' }, // 4 橙
  { bg: '#FEF0F4', c: '#B83368', bd: '#F5C4D6' }, // 5 玫
  { bg: '#EEF2FF', c: '#4150B0', bd: '#C7CBF0' }, // 6 靛
  { bg: '#FFF8EE', c: '#A16207', bd: '#FDE68A' }, // 7 暖黄
]
export const TAG_GRAY = { bg: '#F2F5F8', c: '#475569', bd: '#E3E8ED' } // 灰(接口/无色)
const RED = { bg: '#FEECEC', c: '#C9332B', bd: '#F6CFCB' }

// 标签通用基础样式
export const TAG_BASE: CSSProperties = {
  display: 'inline-flex', alignItems: 'center',
  fontSize: 12, fontWeight: 500, padding: '3px 10px', borderRadius: 7, lineHeight: 1.4,
}

const styleOf = (p: { bg: string; c: string; bd: string }): CSSProperties =>
  ({ background: p.bg, color: p.c, border: `1px solid ${p.bd}` })

export const grayTagStyle = (): CSSProperties => styleOf(TAG_GRAY)
export const paletteTagStyle = (i: number): CSSProperties => styleOf(TAG_PALETTE[((i % 8) + 8) % 8])

// 端：固定调色板映射；接口/api 用灰
const PLATFORM_PALETTE_INDEX: Record<string, number> = {
  'web-admin': 0, 'web-portal': 1, 'android-app': 2, 'ios-app': 3, 'mini-app': 4,
}
export function platformTagStyle(key: string): CSSProperties {
  if (key === 'api' || key === '接口') return grayTagStyle()
  const idx = PLATFORM_PALETTE_INDEX[key]
  return idx == null ? grayTagStyle() : paletteTagStyle(idx)
}

// 模块/场景类型：优先按列表索引循环；无 index 时按字符串 hash 稳定取色
function hashIndex(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return h % 8
}
export function indexTagStyle(key: string, index?: number): CSSProperties {
  return paletteTagStyle(index != null && index >= 0 ? index : hashIndex(key))
}

// 优先级：P0 红 / P1 橙 / P2 蓝 / P3 灰
const PRIORITY_TAG: Record<string, { bg: string; c: string; bd: string }> = {
  P0: RED, P1: TAG_PALETTE[4], P2: TAG_PALETTE[0], P3: TAG_GRAY,
}
export const priorityTagStyle = (p: string): CSSProperties => styleOf(PRIORITY_TAG[p] || TAG_GRAY)

// 缺陷等级：1级红 / 2级玫 / 3级橙 / 4级灰
export function severityTagStyle(sev: string): CSSProperties {
  const s = sev || ''
  if (s.startsWith('1级')) return styleOf(RED)
  if (s.startsWith('2级')) return paletteTagStyle(5)
  if (s.startsWith('3级')) return paletteTagStyle(4)
  return grayTagStyle()
}
