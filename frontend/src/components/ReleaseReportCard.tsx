import { useState } from 'react'
import { Card, Button, Tag, List } from 'antd'
import { releaseApi } from '../api'

const SUG_COLOR: Record<string, string> = { pass: 'success', warn: 'warning', block: 'error' }
const SUG_LABEL: Record<string, string> = { pass: '建议发布', warn: '发布需人工确认', block: '不建议发布' }

/** 发布建议卡：由覆盖矩阵 + 命中规则 + 剩余风险生成 release_suggestion（证据先行）。 */
export default function ReleaseReportCard({ requirementId }: { requirementId: string }) {
  const [rep, setRep] = useState<{ release_suggestion: string; reasons: string[]; summary: any } | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try { setRep((await releaseApi.report(requirementId)).data) } finally { setLoading(false) }
  }

  return (
    <Card size="small" title="发布建议" style={{ marginBottom: 16 }}
      extra={<Button size="small" loading={loading} onClick={load}>{rep ? '刷新' : '生成发布报告'}</Button>}>
      {!rep ? <span style={{ color: '#999' }}>点击「生成发布报告」评估本需求是否适合发布</span> : (
        <div>
          <Tag color={SUG_COLOR[rep.release_suggestion]} style={{ fontSize: 14, padding: '2px 10px' }}>
            {SUG_LABEL[rep.release_suggestion] || rep.release_suggestion}
          </Tag>
          {rep.summary && (
            <span style={{ marginLeft: 12, fontSize: 12, color: '#888' }}>
              覆盖项 {rep.summary.covered ?? 0}/{rep.summary.total ?? 0} 已验证，未覆盖 {rep.summary.not_covered ?? 0}，失败 {rep.summary.failed ?? 0}
            </span>
          )}
          {rep.reasons.length > 0 && (
            <List size="small" style={{ marginTop: 8 }} dataSource={rep.reasons}
              renderItem={(r) => <List.Item style={{ color: '#c9332b', fontSize: 13 }}>⚠ {r}</List.Item>} />
          )}
        </div>
      )}
    </Card>
  )
}
