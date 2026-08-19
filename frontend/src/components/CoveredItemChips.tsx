import { Tag, Tooltip } from 'antd'
import type { CoveredItem } from '../types/api'
import { sourceLabel, sourceColor, COVERAGE_STATUS_COLOR, COVERAGE_STATUS_LABEL } from '../constants/coveredItem'

const PRIORITY_COLOR: Record<string, string> = { P0: '#f5222d', P1: '#fa8c16', P2: '#8c8c8c' }

interface Props {
  items?: CoveredItem[] | null
  max?: number           // 超出折叠为 +N
  showStatus?: boolean   // 是否显示覆盖状态点
  onClickItem?: (item: CoveredItem) => void
}

/** 覆盖项 chips：名称 + 来源角标 + 优先级点 + 覆盖状态 + risk_tags。四处复用。 */
export default function CoveredItemChips({ items, max, showStatus = true, onClickItem }: Props) {
  const list = items || []
  if (list.length === 0) return <span style={{ color: '#bbb', fontSize: 12 }}>暂无覆盖项</span>

  const shown = max && list.length > max ? list.slice(0, max) : list
  const rest = max && list.length > max ? list.length - max : 0

  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
      {shown.map((ci, idx) => {
        const srcs = ci.sources || []
        const status = ci.coverage_status || 'not_covered'
        const tip = (
          <div style={{ maxWidth: 320 }}>
            <div><b>{ci.name}</b></div>
            {ci.expected && <div style={{ color: '#ccc' }}>预期：{ci.expected}</div>}
            {ci.reason && <div style={{ color: '#ccc' }}>原因：{ci.reason}</div>}
            {(ci.matched_rules?.length ?? 0) > 0 && <div>命中规则：{ci.matched_rules!.join('、')}</div>}
            <div>来源：{srcs.map(sourceLabel).join('、') || '—'}</div>
          </div>
        )
        return (
          <Tooltip key={ci.item_id || idx} title={tip}>
            <Tag
              onClick={onClickItem ? () => onClickItem(ci) : undefined}
              style={{ margin: 0, cursor: onClickItem ? 'pointer' : 'default', display: 'inline-flex', alignItems: 'center', gap: 4 }}
            >
              {ci.priority && (
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: PRIORITY_COLOR[ci.priority] || '#8c8c8c', display: 'inline-block' }} />
              )}
              <span>{ci.name}</span>
              {srcs.slice(0, 2).map((s) => (
                <Tag key={s} color={sourceColor(s)} style={{ margin: 0, transform: 'scale(0.82)', lineHeight: '16px' }}>
                  {sourceLabel(s)}
                </Tag>
              ))}
              {showStatus && (
                <Tag color={COVERAGE_STATUS_COLOR[status]} style={{ margin: 0, transform: 'scale(0.82)', lineHeight: '16px' }}>
                  {COVERAGE_STATUS_LABEL[status]}
                </Tag>
              )}
            </Tag>
          </Tooltip>
        )
      })}
      {rest > 0 && <Tag style={{ margin: 0 }}>+{rest}</Tag>}
    </span>
  )
}
