import { createRoot } from 'react-dom/client'

export type ConfirmOptions = {
  title: string
  desc?: string
  ok?: string
  cancel?: string
  danger?: boolean
}

const BRAND_GRAD = 'linear-gradient(135deg,#E8916B 0%,#D97757 100%)'
const DANGER_GRAD = 'linear-gradient(140deg,#F87171 0%,#EF4444 100%)'

function ConfirmCard({ title, desc, ok, cancel, danger, onOk, onCancel }: ConfirmOptions & { onOk: () => void; onCancel: () => void }) {
  return (
    <div
      onClick={onCancel}
      style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(15,23,42,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', animation: 'cfmFadeBg .2s ease both' }}
    >
      <style>{`
        @keyframes cfmFadeBg{from{opacity:0}to{opacity:1}}
        @keyframes cfmFadeUp{from{opacity:0;transform:translateY(12px) scale(.97)}to{opacity:1;transform:translateY(0) scale(1)}}
        .cfm-cancel:hover{background:#F7F9FB!important;border-color:#D5DDE4!important}
        .cfm-ok:hover{opacity:.9}
        .cfm-close:hover{color:#94A3B8!important}
      `}</style>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 400, background: '#fff', borderRadius: 18, boxShadow: '0 24px 64px -16px rgba(15,23,42,.28)', overflow: 'hidden', animation: 'cfmFadeUp .28s cubic-bezier(.22,1,.36,1) both' }}
      >
        <div style={{ padding: '24px 24px 0', display: 'flex', alignItems: 'flex-start', gap: 14 }}>
          <div style={{ width: 40, height: 40, flex: 'none', borderRadius: 12, background: danger ? '#FDECEC' : '#FEF6E7', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span className="ms" style={{ fontSize: 22, color: danger ? '#EF4444' : '#E8930C', fontVariationSettings: "'FILL' 1" }}>warning</span>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#0F172A', marginBottom: 8 }}>{title}</div>
            {desc && <div style={{ fontSize: 13.5, color: '#64748B', lineHeight: 1.7, whiteSpace: 'pre-wrap', maxHeight: 260, overflowY: 'auto' }}>{desc}</div>}
          </div>
          <span className="ms cfm-close" onClick={onCancel} style={{ fontSize: 20, color: '#CBD5E1', cursor: 'pointer', flex: 'none' }}>close</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 10, padding: '20px 24px 22px' }}>
          <button className="cfm-cancel" onClick={onCancel} style={{ height: 38, padding: '0 20px', background: '#fff', border: '1.5px solid #E7ECF0', borderRadius: 10, fontSize: 13.5, color: '#64748B', cursor: 'pointer', transition: 'all .15s' }}>
            {cancel || '取消'}
          </button>
          <button className="cfm-ok" onClick={onOk} style={{ height: 38, padding: '0 20px', border: 'none', borderRadius: 10, fontSize: 13.5, fontWeight: 600, color: '#fff', cursor: 'pointer', background: danger ? DANGER_GRAD : BRAND_GRAD, boxShadow: danger ? '0 4px 12px -5px rgba(239,68,68,.45)' : '0 4px 12px -5px rgba(217,119,87,.45)' }}>
            {ok || '确认'}
          </button>
        </div>
      </div>
    </div>
  )
}

/** 命令式通用确认弹框，返回 Promise<boolean>（确认=true / 取消/遮罩/关闭=false）。 */
export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    let done = false
    const close = (v: boolean) => {
      if (done) return
      done = true
      root.unmount()
      host.remove()
      resolve(v)
    }
    root.render(<ConfirmCard {...opts} onOk={() => close(true)} onCancel={() => close(false)} />)
  })
}
