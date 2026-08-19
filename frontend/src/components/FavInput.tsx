import { useEffect, useState } from 'react'
import { Input, Button, Popover, Tooltip, message } from 'antd'
import { favPhonesApi } from '../api'

/**
 * 带「常用项」的输入框（PC/App 通用、按用户隔离）。
 * - 输入框：输入未收藏的新值时，右侧【立即出现「＋收藏」提示】(不用点常用就能看到)；已收藏显示「已收藏」。
 * - 右侧「常用 ▾」按钮：点开浮层，列出我的常用项，点一项填入、每项 × 删除。
 * kind: phone 常用号码 / tenant 常用租户（账号也复用 phone 池）。
 */
export default function FavInput({
  value, onChange, placeholder, kind = 'phone', size = 'middle', style,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  kind?: 'phone' | 'tenant'
  size?: 'small' | 'middle' | 'large'
  style?: React.CSSProperties
}) {
  const [favs, setFavs] = useState<{ id: string; phone: string }[]>([])
  const [open, setOpen] = useState(false)
  const load = async () => { try { const r = await favPhonesApi.list(kind); setFavs(r.data || []) } catch { /* 忽略 */ } }
  useEffect(() => { load() }, [kind])

  const cur = (value || '').trim()
  const alreadyFav = favs.some((f) => f.phone === cur)
  const label = kind === 'tenant' ? '租户' : '号码'

  const add = async () => {
    if (!cur) return
    try { await favPhonesApi.add(cur, kind); await load(); message.success(`已加入常用${label}`) }
    catch { message.error('加入失败') }
  }
  const del = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    try { await favPhonesApi.remove(id); setFavs((prev) => prev.filter((f) => f.id !== id)) } catch { /* 忽略 */ }
  }

  // 输入框内右侧：输入了未收藏的新值 → 立即出现「＋ 常用」(点即加入常用)；已收藏则不显示任何东西
  const suffix = cur && !alreadyFav
    ? <span onMouseDown={(e) => e.preventDefault()} onClick={add}
        style={{ color: '#2563EB', fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap', fontWeight: 500 }}>＋ 常用</span>
    : <span />

  const rowBase: React.CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14,
    padding: '8px 10px', borderRadius: 7, cursor: 'pointer', fontSize: 13,
  }
  const content = (
    <div style={{ minWidth: 200, maxHeight: 300, overflowY: 'auto' }}>
      <div style={{ fontSize: 11.5, color: '#94A3B8', padding: '2px 10px 6px' }}>我的常用{label}</div>
      {favs.map((f) => (
        <div key={f.id} style={rowBase}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => { onChange(f.phone); setOpen(false) }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#F2F5F9' }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.phone}</span>
          <Tooltip title="删除"><span onClick={(e) => del(e, f.id)}
            style={{ flex: 'none', color: '#B0BAC4', fontSize: 17, lineHeight: 1, padding: '0 2px', cursor: 'pointer' }}>×</span></Tooltip>
        </div>
      ))}
      {favs.length === 0 && (
        <div style={{ color: '#94A3B8', fontSize: 12, padding: '8px 10px' }}>还没有常用{label}，在输入框输入后点「＋收藏」</div>
      )}
    </div>
  )

  return (
    <div style={{ display: 'flex', width: '100%', ...style }}>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        size={size}
        suffix={suffix}
        style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
      />
      <Popover open={open} onOpenChange={setOpen} trigger="click" placement="bottomRight"
        content={content} overlayInnerStyle={{ padding: 4 }} zIndex={1400}>
        <Tooltip title={`我的常用${label}`}>
          <Button size={size} icon={<span className="ms" style={{ fontSize: 17 }}>bookmarks</span>}
            style={{ flex: 'none', color: '#5B6472', borderTopLeftRadius: 0, borderBottomLeftRadius: 0, paddingInline: 8 }} />
        </Tooltip>
      </Popover>
    </div>
  )
}
