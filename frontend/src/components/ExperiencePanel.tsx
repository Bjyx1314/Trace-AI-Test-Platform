import { useEffect, useState } from 'react'
import { Card, Tag, Button, Space, Modal, Input, message, Tooltip } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import type { ExperienceHit } from '../types/api'
import { experiencesApi } from '../api'

interface Props {
  requirementId?: string
  impactId?: string
  title?: string
}

/** 经验命中面板（方案 6.2.3）：标签+语义召回历史经验，带命中原因 + 采纳/忽略/不适用。 */
export default function ExperiencePanel({ requirementId, impactId, title = '历史经验召回' }: Props) {
  const [hits, setHits] = useState<ExperienceHit[]>([])
  const [loading, setLoading] = useState(false)
  const [acted, setActed] = useState<Record<string, string>>({})

  const load = () => {
    if (!requirementId && !impactId) return
    setLoading(true)
    experiencesApi.recall({ requirement_id: requirementId, impact_id: impactId, top_n: 5 })
      .then((r) => setHits(r.data.hits))
      .finally(() => setLoading(false))
  }
  useEffect(load, [requirementId, impactId])

  const doAdopt = async (h: ExperienceHit) => {
    await experiencesApi.adopt(h.experience_id, { requirement_id: requirementId })
    setActed((s) => ({ ...s, [h.experience_id]: '已采纳' })); message.success('已采纳')
  }
  const doIgnore = async (h: ExperienceHit) => {
    await experiencesApi.ignore(h.experience_id, { requirement_id: requirementId })
    setActed((s) => ({ ...s, [h.experience_id]: '已忽略' }))
  }
  const doNotApplicable = (h: ExperienceHit) => {
    let reason = ''
    Modal.confirm({
      title: '本次不适用（负反馈，必填原因）',
      content: <Input.TextArea rows={3} placeholder="例如：本次只改 UI 展示，不涉及退款逻辑" onChange={(e) => (reason = e.target.value)} />,
      onOk: async () => {
        if (!reason.trim()) { message.warning('请填写原因'); throw new Error('need reason') }
        await experiencesApi.notApplicable(h.experience_id, { requirement_id: requirementId, reason })
        setActed((s) => ({ ...s, [h.experience_id]: '本次不适用' })); message.success('已记录')
      },
    })
  }

  if (!requirementId && !impactId) return null
  // 无命中（或仍在首次加载）时整块隐藏，不再展示空态卡片
  if (hits.length === 0) return null

  return (
    <Card size="small" style={{ marginBottom: 16 }} title={<span><BulbOutlined /> {title} <Tag color="gold">{hits.length}</Tag></span>} loading={loading}>
      {(
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          {hits.map((h) => (
            <div key={h.experience_id} style={{ borderBottom: '1px solid #f0f0f0', paddingBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <b>{h.title}</b>
                <Tag color="blue">置信 {h.confidence}</Tag>
                <Tag>{h.channel === 'tag+semantic' ? '标签+语义' : h.channel === 'tag' ? '标签' : '语义'}</Tag>
                {h.found_bug && <Tag color="red">曾发现 Bug</Tag>}
                <Tooltip title={`采纳${h.stats?.adopt_count ?? 0} / 忽略${h.stats?.reject_count ?? 0}`}><Tag color="default">score {h.score}</Tag></Tooltip>
              </div>
              <div style={{ fontSize: 12, color: '#888', margin: '2px 0' }}>命中原因：{h.hit_reason}</div>
              {h.suggested_covered_items.length > 0 && (
                <div style={{ fontSize: 12 }}>建议加入覆盖项：{h.suggested_covered_items.map((s) => <Tag key={s} color="green">{s}</Tag>)}</div>
              )}
              <div style={{ marginTop: 6 }}>
                {acted[h.experience_id]
                  ? <Tag color="processing">{acted[h.experience_id]}</Tag>
                  : (
                    <Space>
                      <Button size="small" type="primary" onClick={() => doAdopt(h)}>采纳</Button>
                      <Button size="small" onClick={() => doIgnore(h)}>忽略</Button>
                      <Button size="small" onClick={() => doNotApplicable(h)}>本次不适用</Button>
                    </Space>
                  )}
              </div>
            </div>
          ))}
        </Space>
      )}
    </Card>
  )
}
