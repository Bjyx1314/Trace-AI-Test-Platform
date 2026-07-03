/**
 * 门禁/可发布状态标签 —— 质量看板 & 执行历史共用，三态由数据驱动（勿硬编码红色）。
 *  pass  可发布   check_circle  #128A43 / #E9F8EF / #B5E0C8
 *  block 不可发布 block         #C9332B / #FDECEC / #F7C9C9
 *  none  无数据   —             #CBD5E1
 */
export type GateState = 'pass' | 'block' | 'none'

const BASE: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 4,
  fontSize: 12, fontWeight: 500, padding: '3px 9px', borderRadius: 7, whiteSpace: 'nowrap',
}

export default function GateTag({ state }: { state: GateState }) {
  if (state === 'block') {
    return <span style={{ ...BASE, color: '#C9332B', background: '#FDECEC', border: '1px solid #F7C9C9' }}>
      <span className="ms" style={{ fontSize: 14 }}>block</span>不可发布
    </span>
  }
  if (state === 'pass') {
    return <span style={{ ...BASE, color: '#128A43', background: '#E9F8EF', border: '1px solid #B5E0C8' }}>
      <span className="ms" style={{ fontSize: 14 }}>check_circle</span>可发布
    </span>
  }
  return <span style={{ color: '#CBD5E1' }}>—</span>
}

/** 后端 releasability(pass/warn/block/not_started) → 门禁三态 */
export function toGateState(releasability?: string): GateState {
  if (releasability === 'block' || releasability === 'warn') return 'block'
  if (releasability === 'pass') return 'pass'
  return 'none'
}
