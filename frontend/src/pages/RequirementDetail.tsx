import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import {
  Card, Button, Table, Tag, Space, Typography, Descriptions, Empty, Badge, Progress,
  Input, List, message, Image, Modal, Alert, Checkbox, Drawer, Form, Select, Dropdown, Tooltip,
} from 'antd'
import { requirementsApi, testCasesApi, defectsApi, executionsApi, pipelineApi, enumsApi, slicesApi } from '../api'
import ExecConfigModal, { categorizeCaseByPlatform, isAutoExecutable } from '../components/ExecConfigModal'
import { confirmDialog } from '../components/ConfirmModal'
import DefectReviewTable from '../components/DefectReviewTable'
import type { CheckboxChangeEvent } from 'antd/es/checkbox'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useAuthStore } from '../store/authStore'
import { PANEL_CARD_STYLE, MONO_FONT, TECH_GRADIENT, PRIMARY_DEEP, SELECTED_BG } from '../styles/theme'
import { TAG_BASE, priorityTagStyle, indexTagStyle, platformTagStyle, grayTagStyle } from '../styles/tagColors'
import dayjs from 'dayjs'

const STATUS_COLOR: Record<string, string> = {
  pending_analysis: 'default',
  analyzing: 'processing',
  pending_case_generation: 'cyan',
  generating_cases: 'processing',
  pending_test: 'blue',
  testing: 'warning',
  done: 'success',
}
const STATUS_LABEL: Record<string, string> = {
  pending_analysis: '待需求分析',
  analyzing: '分析中',
  pending_case_generation: '待生成用例',
  generating_cases: '生成用例中',
  pending_test: '待测试',
  testing: '测试中',
  done: '已完成',
}
const PRIORITY_COLOR: Record<string, string> = { P0: 'red', P1: 'orange', P2: 'blue' }
// 最近结果：通过 / 失败 / 手动测试通过 / 手动测试失败 / 未执行
const LAST_STATUS_COLOR: Record<string, string> = {
  passed: 'success', skipped: 'success', manual_passed: 'success',
  failed: 'error', error: 'error', manual_failed: 'error', not_run: 'default',
}
const RESULT_COLOR: Record<string, string> = {
  passed: 'success', skipped: 'success', manual_passed: 'success',
  failed: 'error', error: 'error', manual_failed: 'error',
}
const CASE_STATUS_LABEL: Record<string, string> = {
  passed: '通过', skipped: '通过', manual_passed: '手动测试通过',
  failed: '失败', error: '失败', manual_failed: '手动测试失败', not_run: '未执行',
}
// 「最近结果」筛选项：按显示文案去重(passed/skipped 都叫「通过」、failed/error 都叫「失败」)，
// value 用 label，onFilter 按 label 分组匹配，避免下拉里「通过/失败」重复出现。
const CASE_STATUS_FILTER_OPTIONS: { value: string; label: string }[] = (() => {
  const seen = new Set<string>()
  const opts: { value: string; label: string }[] = []
  for (const k of Object.keys(CASE_STATUS_LABEL)) {
    const label = CASE_STATUS_LABEL[k]
    if (seen.has(label)) continue
    seen.add(label)
    opts.push({ value: label, label })
  }
  return opts
})()
const EXEC_STATUS_COLOR: Record<string, string> = {
  pending: 'default', running: 'processing', done: 'success', failed: 'error',
}
const DEFECT_STATUS_COLOR: Record<string, string> = {
  draft: 'default', ticket_created: 'processing', confirmed: 'red', ignored: 'default', duplicate: 'purple', fixed: 'success',
}
const DEFECT_STATUS_LABEL: Record<string, string> = {
  draft: '待处理', ticket_created: '已建单', confirmed: '已确认', ignored: '已忽略', duplicate: '重复', fixed: '已解决',
}

const IN_PROGRESS_STATUSES = ['analyzing', 'generating_cases']

const FILTER_BRAND = '#D97757'

// 通用描边按钮(需求详情标题行)
const GHOST_BTN: CSSProperties = {
  height: 34, padding: '0 13px', borderRadius: 9, background: '#fff', border: '1px solid #E7ECF0',
  fontSize: 12.5, color: '#64748B', cursor: 'pointer', whiteSpace: 'nowrap',
  display: 'inline-flex', alignItems: 'center', gap: 5, transition: 'all .15s',
}

// 自定义列头筛选面板(多选 + 重置/确定)，配合 antd Table 的 filterDropdown 使用
function ColFilter({ title, options, scroll, setSelectedKeys, selectedKeys, confirm, clearFilters }: {
  title: string
  options: { value: any; label: string }[]
  scroll?: boolean
  setSelectedKeys: (keys: any[]) => void
  selectedKeys: any[]
  confirm: () => void
  clearFilters?: () => void
}) {
  const sel: any[] = selectedKeys || []
  const toggle = (v: any) => setSelectedKeys(sel.includes(v) ? sel.filter((x) => x !== v) : [...sel, v])
  return (
    <div style={{ background: '#fff', borderRadius: 11, padding: '8px 6px', minWidth: scroll ? 160 : 140 }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: '#94A3B8', letterSpacing: '.4px', padding: '2px 8px 6px' }}>{title}</div>
      <div style={{ maxHeight: scroll ? 180 : undefined, overflowY: scroll ? 'auto' : 'visible' }}>
        {options.map((o) => {
          const on = sel.includes(o.value)
          return (
            <div key={String(o.value)} onClick={() => toggle(o.value)}
              style={{ padding: '7px 10px', borderRadius: 7, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 7, fontSize: 13 }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#F7F9FB')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
              <span style={{ width: 14, height: 14, flex: 'none', borderRadius: 4, border: `1.5px solid ${on ? FILTER_BRAND : '#D1D5DB'}`, background: on ? FILTER_BRAND : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {on && <span className="ms" style={{ fontSize: 10, color: '#fff', fontVariationSettings: "'FILL' 1" }}>check</span>}
              </span>
              <span>{o.label}</span>
            </div>
          )
        })}
      </div>
      <div style={{ height: 1, background: '#F1F4F6', margin: '6px 4px' }} />
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 7, padding: '4px 4px 2px' }}>
        <span onClick={() => { clearFilters && clearFilters(); confirm() }}
          style={{ fontSize: 12, color: '#94A3B8', padding: '4px 10px', borderRadius: 6, cursor: 'pointer' }}>重置</span>
        <span onClick={() => confirm()}
          style={{ fontSize: 12, color: '#fff', background: FILTER_BRAND, padding: '4px 12px', borderRadius: 6, fontWeight: 500, cursor: 'pointer' }}>确定</span>
      </div>
    </div>
  )
}

const filterIcon = (filtered: boolean) => (
  <span className="ms" style={{ fontSize: 14, color: filtered ? FILTER_BRAND : '#B0BAC4' }}>filter_list</span>
)

export default function RequirementDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()

  const [req, setReq] = useState<any>(null)
  const [cases, setCases] = useState<any[]>([])
  const [defectsData, setDefectsData] = useState<any[]>([])
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [generating, setGenerating] = useState(false)
  // 多执行并发追踪：记录每个在跑执行的用例集与是否含App，用于"只置灰在跑用例 + App串行"
  const [activeExecs, setActiveExecs] = useState<{ id: string; caseIds: string[]; isApp: boolean }[]>([])
  // 各 PC 端是否可登录(框架覆盖)。无法登录的 web 端用例将禁止执行。
  const [webCoverage, setWebCoverage] = useState<Record<string, boolean>>({})
  const [pollTick, setPollTick] = useState(0)
  const [analyzeTick, setAnalyzeTick] = useState(0)  // 分析/生成轮询续轮计数(与 req 解耦，抗瞬时请求失败)
  const [dragStepIdx, setDragStepIdx] = useState<number | null>(null)  // 测试步骤拖动排序
  const [coverageOpen, setCoverageOpen] = useState(false)
  const [coverageLoading, setCoverageLoading] = useState(false)
  const [coverageData, setCoverageData] = useState<any>(null)
  const isAdmin = useAuthStore((s) => s.isAdmin)  // 覆盖分析暂仅管理员可见
  const myName = useAuthStore((s) => s.user?.name)  // 当前登录人姓名，用于「加入我的范围」判断
  const [lastResultCase, setLastResultCase] = useState<any>(null)
  const [lastResultData, setLastResultData] = useState<any[]>([])
  const [lastResultLoading, setLastResultLoading] = useState(false)
  const [execModalOpen, setExecModalOpen] = useState(false)
  const [pendingCaseIds, setPendingCaseIds] = useState<string[]>([])
  const [execApiBaseUrl, setExecApiBaseUrl] = useState('')
  const [shouldOpenExecModal, setShouldOpenExecModal] = useState(false)
  const [selectedPointIds, setSelectedPointIds] = useState<string[]>([])
  const [batchConfirming, setBatchConfirming] = useState(false)
  const [reviewSelectedIds, setReviewSelectedIds] = useState<string[]>([])
  const [reviewingBatch, setReviewingBatch] = useState(false)
  const [batchManualPassing, setBatchManualPassing] = useState(false)
  const [sliceModalOpen, setSliceModalOpen] = useState(false)
  const [sliceLabel, setSliceLabel] = useState('')
  const [modeModal, setModeModal] = useState<{ open: boolean; kind: 'analyze' | 'generate' }>({ open: false, kind: 'analyze' })
  const [platformDraft, setPlatformDraft] = useState<string[] | null>(null)  // 涉及端编辑草稿(null=用已存的)
  const [platformSaving, setPlatformSaving] = useState(false)
  const [editingPlatforms, setEditingPlatforms] = useState(false)  // 已确认态点「修改」回到编辑态

  const [categoryOptions, setCategoryOptions] = useState<any[]>([])
  const [moduleOptions, setModuleOptions] = useState<any[]>([])
  const [platformOptions, setPlatformOptions] = useState<any[]>([])
  const [severityOptions, setSeverityOptions] = useState<any[]>([])

  // 用例查看/编辑
  const [caseDetail, setCaseDetail] = useState<any>(null)
  const [caseEditMode, setCaseEditMode] = useState(false)
  const [caseSaving, setCaseSaving] = useState(false)
  const [caseForm] = Form.useForm()

  const loadCases = () => testCasesApi.list({ requirement_id: id }).then((r) => setCases(r.data))
  const loadDefects = () => defectsApi.list({ requirement_id: id }).then((r) => setDefectsData(r.data))
  const reloadReq = () => requirementsApi.get(id!).then((r) => setReq(r.data))

  // 需求切片(负责范围)：默认「全文」切片 = 需求级数据视图；非默认切片各自分析/生成
  const [slices, setSlices] = useState<any[]>([])
  const [activeSliceId, setActiveSliceId] = useState<string | undefined>(undefined)
  const loadSlices = () => slicesApi.list(id!).then((r) => {
    setSlices(r.data)
    setActiveSliceId((cur) =>
      cur && r.data.some((s) => s.id === cur) ? cur : (r.data.find((s) => s.is_default)?.id ?? r.data[0]?.id))
  })
  const activeSlice = slices.find((s) => s.id === activeSliceId) || slices.find((s) => s.is_default) || null
  const onDefaultSlice = !activeSlice || activeSlice.is_default
  const sliceParam = onDefaultSlice ? undefined : activeSlice!.id
  // 分析数据/状态：默认切片取需求本身(零回归)，非默认切片取切片自身
  const analysis = onDefaultSlice ? req?.analysis_result : activeSlice?.analysis_result
  const aStatus = onDefaultSlice ? req?.status : activeSlice?.status
  // 增量判定(仅非默认切片)：有未分析的新追加范围 / 有尚未生成用例的新问题点
  const myExistingSlice = slices.find((s) => !s.is_default && s.owner_name && s.owner_name === myName)
  const sliceCases = activeSlice ? cases.filter((c) => c.slice_id === activeSlice.id) : []
  const coveredIssueIds = new Set(sliceCases.map((c) => c.source_issue_point))
  const hasPendingAnalysis = !onDefaultSlice && !!analysis && !!activeSlice?.has_pending
  const hasNewIssues = !onDefaultSlice && !!analysis && (analysis.issue_points || []).some((ip: any) => !coveredIssueIds.has(ip.issue_id))

  // 用例变化时查一次各 PC 端可登录性(框架覆盖)，用于禁止无法登录的 web 用例执行
  useEffect(() => {
    const webPlats = Array.from(new Set(
      cases.filter((c) => categorizeCaseByPlatform(c) === 'web').flatMap((c) => c.platforms || [])
    )) as string[]
    if (!webPlats.length) return
    executionsApi.webAccounts(webPlats)
      .then((r) => {
        const cov: Record<string, boolean> = {}
        Object.entries(r.data || {}).forEach(([p, info]) => { cov[p] = !!info.covered })
        setWebCoverage(cov)
      })
      .catch(() => {})
  }, [cases])

  // web 用例的端均未接入框架(无法登录) → 不可执行
  const isLoginBlocked = (c: any) =>
    categorizeCaseByPlatform(c) === 'web'
    && (c.platforms || []).length > 0
    && (c.platforms || []).every((p: string) => webCoverage[p] === false)

  const openCaseDetail = (row: any) => {
    setCaseDetail(row)
    setCaseEditMode(false)
  }

  const startCaseEdit = () => {
    caseForm.setFieldsValue({
      title: caseDetail.title,
      priority: caseDetail.priority,
      case_type: caseDetail.case_type,
      modules: caseDetail.modules || [],
      platforms: caseDetail.platforms || [],
      expected_result: caseDetail.expected_result || '',
      steps: (caseDetail.steps || []).map((s: any) => ({ action: s.action || '', expected: s.expected || '', check_points: s.check_points || [] })),
    })
    setCaseEditMode(true)
  }

  const handleCaseSave = async () => {
    try {
      const values = await caseForm.validateFields()
      setCaseSaving(true)
      // 步骤按当前顺序重排 seq
      const steps = (values.steps || []).map((s: any, i: number) => ({
        seq: i + 1, action: s?.action || '', expected: s?.expected || '',
        check_points: s?.check_points || [],
      }))
      await testCasesApi.update(caseDetail.id, {
        project_id: caseDetail.project_id,
        requirement_id: caseDetail.requirement_id,
        product_line: caseDetail.product_line,
        preconditions: caseDetail.preconditions,
        ...values,
        steps,
      })
      message.success('保存成功')
      setCaseDetail({ ...caseDetail, ...values, steps })
      setCaseEditMode(false)
      loadCases()
    } catch (err: any) {
      if (err?.errorFields) return
      message.error('保存失败')
    } finally {
      setCaseSaving(false)
    }
  }

  const handleManualPass = (row: any) => {
    testCasesApi.manualPass(row.id).then(() => {
      message.success('已标记手动测试通过')
      loadCases()
    })
  }

  const handleCoverage = async () => {
    setCoverageOpen(true); setCoverageLoading(true); setCoverageData(null)
    try {
      const r = await requirementsApi.coverage(id!)
      setCoverageData(r.data)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '覆盖分析失败，请稍后重试')
      setCoverageOpen(false)
    } finally {
      setCoverageLoading(false)
    }
  }

  const dropUncovered = (point: string) =>
    setCoverageData((d: any) => d ? { ...d, uncovered_points: (d.uncovered_points || []).filter((p: string) => p !== point) } : d)

  const genForUncovered = async (point: string) => {
    try {
      await pipelineApi.generateCases(id!, false, point, undefined, sliceParam)
      message.success('已针对该功能点启动用例生成')
      setAnalyzing(true); setGenerating(true)
      dropUncovered(point)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '生成启动失败，请稍后重试')
    }
  }

  const handleManualFail = (row: any) => {
    testCasesApi.manualFail(row.id).then(() => {
      message.success('已标记手动测试失败，已生成待复核缺陷')
      loadCases(); loadDefects()
    })
  }

  const openLastResult = async (row: any) => {
    if (row.last_status === 'not_run') return
    setLastResultCase(row)
    setLastResultData([])
    setLastResultLoading(true)
    try {
      const r = await testCasesApi.results(row.id)
      setLastResultData(r.data)
    } finally {
      setLastResultLoading(false)
    }
  }

  const handleBatchManualPass = async () => {
    if (!selectedRowKeys.length) return
    setBatchManualPassing(true)
    try {
      await Promise.all(selectedRowKeys.map((cid) => testCasesApi.manualPass(cid)))
      message.success(`已批量手动通过 ${selectedRowKeys.length} 条用例`)
      setSelectedRowKeys([])
      loadCases()
    } finally {
      setBatchManualPassing(false)
    }
  }

  const handleBatchDeleteCases = async () => {
    if (!selectedRowKeys.length) return
    if (!(await confirmDialog({ title: '批量删除用例', desc: `确认删除选中的 ${selectedRowKeys.length} 条用例？删除后进入用例库回收站。`, ok: '删除', danger: true }))) return
    await Promise.all(selectedRowKeys.map((cid) => testCasesApi.delete(cid)))
    message.success(`已删除 ${selectedRowKeys.length} 条用例`)
    setSelectedRowKeys([])
    loadCases()
  }

  const handleDeleteCase = (row: any) => {
    testCasesApi.delete(row.id).then(() => {
      message.success('已删除，可在用例库回收站找回')
      loadCases()
    })
  }

  useEffect(() => {
    requirementsApi.get(id!).then((r) => {
      setReq(r.data)
      // 从列表页跳转过来时分析/生成可能已在后台运行，自动接管轮询
      if (IN_PROGRESS_STATUSES.includes(r.data.status)) {
        setAnalyzing(true)
        if (r.data.status === 'generating_cases') setGenerating(true)
      }
    })
    loadCases(); loadDefects(); loadSlices()
    enumsApi.list('category').then((r) => setCategoryOptions(r.data))
    enumsApi.list('severity').then((r) => setSeverityOptions(r.data))
    enumsApi.list('module').then((r) => setModuleOptions(r.data))
    enumsApi.list('platform').then((r) => setPlatformOptions(r.data))
    const st = location.state as any
    if (st?.openExecModal) setShouldOpenExecModal(true)
    if (st?.activeSliceId) setActiveSliceId(st.activeSliceId)  // 从列表子范围行跳转：激活该范围
  }, [id])

  useEffect(() => {
    if (shouldOpenExecModal && cases.length > 0) {
      // 从子范围行跳转来：只执行该范围的用例；否则执行整条需求的用例
      const execCases = (activeSlice && !activeSlice.is_default)
        ? cases.filter((c) => c.slice_id === activeSlice.id)
        : cases
      if (execCases.length === 0) {
        message.warning('该范围还没有用例，请先生成用例')
        setShouldOpenExecModal(false)
        return
      }
      setPendingCaseIds(execCases.map((c) => c.id))
      setExecModalOpen(true)
      setShouldOpenExecModal(false)
    }
  }, [shouldOpenExecModal, cases, activeSliceId])

  useEffect(() => {
    if (!location.hash) return
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: 'smooth' })
  }, [location.hash, req])

  useEffect(() => { setPlatformDraft(null); setEditingPlatforms(false) }, [activeSliceId])  // 切范围时清空涉及端草稿

  // Polling: stop based on whether we're waiting for just analysis or also case generation。
  // 用 analyzeTick 续轮（与 req 解耦）+ try/catch：瞬时请求失败（如后端重启）也不会让轮询永久停摆。
  useEffect(() => {
    if (!analyzing && !generating) return
    const t = setTimeout(async () => {
      try {
        // 按当前激活范围轮询(默认切片=需求级；非默认=该切片)，并刷新需求与切片数据
        const r = await pipelineApi.status(id!, sliceParam)
        const status = r.data.status
        reloadReq(); loadSlices()
        // 后台失败(不再是独立状态，靠 failed 标志)：展示原因并停轮询
        if (r.data.failed) {
          message.error(r.data.message || '执行失败，请重试')
          setAnalyzing(false); setGenerating(false)
          return
        }
        const stillInProgress = generating
          ? IN_PROGRESS_STATUSES.includes(status)
          : status === 'analyzing'
        if (!stillInProgress) {
          setAnalyzing(false)
          if (generating) { setGenerating(false); loadCases() }
          return  // 完成：停止轮询
        }
      } catch {
        // 后端瞬时不可用（如重启），忽略本轮，继续轮询
      }
      setAnalyzeTick((n) => n + 1)  // 续下一轮
    }, 1500)
    return () => clearTimeout(t)
  }, [analyzing, generating, analyzeTick])

  useEffect(() => {
    if (!activeExecs.length) return
    const t = setTimeout(async () => {
      const done: string[] = []
      for (const e of activeExecs) {
        try {
          const r = await executionsApi.get(e.id)
          if (r.data.status === 'done' || r.data.status === 'failed') {
            done.push(e.id)
            if (r.data.status === 'failed') {
              message.error(r.data.error_message || '执行失败，请重试')
            }
          }
        } catch { /* 忽略，下轮重试 */ }
      }
      if (done.length) {
        setActiveExecs((prev) => prev.filter((x) => !done.includes(x.id)))
        loadCases(); loadDefects(); reloadReq()
      } else {
        setPollTick((n) => n + 1)
      }
    }, 1500)
    return () => clearTimeout(t)
  }, [activeExecs, pollTick])

  // 覆盖前二次确认
  const confirmOverwrite = (content: string) =>
    confirmDialog({ title: '确认操作', desc: content, ok: '确认覆盖', danger: true })
  const hasAnalysis = () => !!analysis && (analysis.issue_points || []).length > 0

  const runAnalyze = async (mode: 'full' | 'incremental') => {
    setModeModal((m) => ({ ...m, open: false }))
    try {
      await pipelineApi.analyze(id!, undefined, undefined, sliceParam, mode)
      message.info(mode === 'incremental' ? '增量需求分析已启动' : '需求分析已启动')
      setAnalyzing(true)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '需求分析启动失败，请稍后重试')
    }
  }

  const runGenerate = async (mode: 'full' | 'incremental', regenerate: boolean) => {
    setModeModal((m) => ({ ...m, open: false }))
    try {
      await pipelineApi.generateCases(id!, regenerate, undefined, undefined, sliceParam, mode)
      message.info(mode === 'incremental' ? '增量用例生成已启动' : '测试用例生成已启动')
      setAnalyzing(true)
      setGenerating(true)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '用例生成启动失败，请稍后重试')
    }
  }

  const handleAnalyze = async () => {
    // 非默认范围 + 已有分析 + 有新追加未分析范围 → 让用户选全量/增量
    if (hasPendingAnalysis) { setModeModal({ open: true, kind: 'analyze' }); return }
    if (hasAnalysis() && !(await confirmOverwrite('已存在需求分析结果（含已确认的点），重新分析将【覆盖现有分析及确认记录】，是否继续？'))) return
    runAnalyze('full')
  }

  const handleGenerateCases = async () => {
    if (!analysis) {
      message.warning('请先完成需求分析')
      return
    }
    if (!analysis.platforms_confirmed) {
      message.warning('请先确认「涉及端」')
      return
    }
    const allConfirmed = (analysis.issue_points || []).every((ip: any) =>
      (ip.confirmation_points || []).every((cp: any) => cp.status === 'confirmed')
    )
    if (!allConfirmed) {
      message.warning('请确认所有待确认点后再生成用例')
      return
    }
    // 非默认范围 + 已有用例 + 有未生成用例的新问题点 → 让用户选全量/增量
    if (!onDefaultSlice && sliceCases.length > 0 && hasNewIssues) { setModeModal({ open: true, kind: 'generate' }); return }
    if (cases.length > 0 && !(await confirmOverwrite('已存在测试用例，继续生成会新增用例（与库中相似的会进入去重复核），是否继续？'))) return
    runGenerate('full', false)
  }

  const getSelectedText = () => (window.getSelection?.()?.toString() || '').trim()

  // 从选区 DOM 范围里提取被选中的图片 token(只发选中段附近/包含的图)
  const getSelectedImageTokens = (): string[] => {
    const sel = window.getSelection?.()
    if (!sel || sel.rangeCount === 0) return []
    const tokens: string[] = []
    try {
      const frag = sel.getRangeAt(0).cloneContents()
      frag.querySelectorAll('img').forEach((img) => {
        const m = (img.getAttribute('src') || '').match(/\/api\/requirements\/media\/([A-Za-z0-9]+)/)
        if (m && !tokens.includes(m[1])) tokens.push(m[1])
      })
    } catch { /* ignore */ }
    return tokens
  }

  const handleRegenerateCases = async () => {
    if (cases.length > 0 && !(await confirmOverwrite('重新生成会【清除现有未执行用例】并重新生成，是否继续？'))) return
    try {
      await pipelineApi.generateCases(id!, true, undefined, undefined, sliceParam)
      message.info('用例重新生成已启动')
      setAnalyzing(true)
      setGenerating(true)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '重新生成失败，请稍后重试')
    }
  }

  const handleCompleteTest = async () => {
    try {
      await requirementsApi.complete(id!)
      message.success('需求已标记为已完成')
      reloadReq()
    } catch {
      message.error('操作失败，请稍后重试')
    }
  }

  const handleConfirmPlatforms = async () => {
    const val = platformDraft ?? (analysis?.platforms || [])
    if (!val.length) { message.warning('请至少选择一个涉及端'); return }
    setPlatformSaving(true)
    try {
      await pipelineApi.confirmPlatforms(id!, val, sliceParam)
      message.success('涉及端已确认')
      setEditingPlatforms(false)
      reloadReq(); loadSlices()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '确认失败')
    } finally {
      setPlatformSaving(false)
    }
  }

  const handleBatchNoConfirm = async () => {
    if (!selectedPointIds.length) return
    setBatchConfirming(true)
    try {
      await requirementsApi.batchNoConfirm(id!, selectedPointIds, sliceParam)
      message.success(`已批量确认 ${selectedPointIds.length} 个待确认点`)
      setSelectedPointIds([])
      reloadReq(); loadSlices()
    } finally {
      setBatchConfirming(false)
    }
  }

  // 负责范围(切片)：新建(可圈选原文作范围)、删除
  const sliceScopeRef = useRef<{ text: string; tokens: string[] }>({ text: '', tokens: [] })
  const handleCreateSlice = async () => {
    const text = getSelectedText()
    if (!text) { message.warning('请先在下方需求文档中选中你负责的范围内容，再点该按钮'); return }
    // 已有我的范围 → 直接并入(累加 + 记为待分析增量)，不再问范围名
    if (myExistingSlice) {
      try {
        const r = await slicesApi.create(id!, { scope_text: text, scope_image_tokens: getSelectedImageTokens() })
        if (r.data.appended === false) {
          message.info('这段内容已在你的范围里，已跳过（不会重复分析/生成）')
        } else {
          message.success('已加入我的范围；下次「需求分析/生成用例」可选全量或增量')
        }
        await loadSlices()
        setActiveSliceId(myExistingSlice.id)
      } catch (e: any) {
        message.error(e?.response?.data?.detail || '加入失败')
      }
      return
    }
    sliceScopeRef.current = { text, tokens: getSelectedImageTokens() }
    setSliceLabel(text.replace(/\s+/g, ' ').slice(0, 16))
    setSliceModalOpen(true)
  }
  const submitCreateSlice = async () => {
    const label = sliceLabel.trim()
    if (!label) { message.warning('请填写范围名'); return }
    try {
      const r = await slicesApi.create(id!, {
        scope_label: label,
        scope_text: sliceScopeRef.current.text || undefined,
        scope_image_tokens: sliceScopeRef.current.tokens.length ? sliceScopeRef.current.tokens : undefined,
      })
      message.success(`已新建范围「${label}」`)
      setSliceModalOpen(false); setSliceLabel('')
      await loadSlices()
      setActiveSliceId(r.data.id)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '新建范围失败')
    }
  }
  const handleDeleteSlice = async (sl: any) => {
    if (sl.is_default) { message.warning('默认「全文」范围不可删除'); return }
    if (!(await confirmDialog({ title: '删除范围', desc: `确认删除范围「${sl.scope_label}」？其下用例将解绑(仍保留在需求中)。`, ok: '删除', danger: true }))) return
    try {
      await slicesApi.remove(sl.id)
      message.success('已删除范围')
      if (activeSliceId === sl.id) setActiveSliceId(undefined)
      loadSlices(); loadCases()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
  }

  const handleBatchReview = async (action: string) => {
    if (!reviewSelectedIds.length) return
    setReviewingBatch(true)
    try {
      const r = await testCasesApi.batchReview(reviewSelectedIds, action)
      message.success(action === 'keep'
        ? `已复用 ${r.data.count} 条老用例纳入本次测试`
        : `已更新用例库并纳入本次测试 ${r.data.count} 条`)
      setReviewSelectedIds([])
      loadCases()
    } finally {
      setReviewingBatch(false)
    }
  }

  const categorizeCase = categorizeCaseByPlatform
  // 正在执行的用例集合(用于只置灰在跑用例) + 是否有App在跑(用于App串行)
  const runningCaseIds = new Set(activeExecs.flatMap((e) => e.caseIds))
  const appBusy = activeExecs.some((e) => e.isApp)

  const runExecution = async (caseIds: string[], runMode: string = 'fresh', accountOverrides?: Record<string, any>, targetDevice?: string | null, env?: string, packageOverrides?: Record<string, string>) => {
    if (!req || !caseIds.length) return
    const isApp = cases.some((c) => caseIds.includes(c.id) && categorizeCase(c) === 'mobile')
    try {
      const r = await executionsApi.create({
        project_id: req.project_id,
        name: `${req.title} - 执行测试`,
        case_ids: caseIds,
        run_mode: runMode,
        account_overrides: accountOverrides && Object.keys(accountOverrides).length ? accountOverrides : undefined,
        target_device: targetDevice ?? undefined,
        env: env || undefined,
        package_overrides: packageOverrides,
        reorder: true, // 需求详情批量执行：按功能块排序，操作先于查询
      })
      setActiveExecs((prev) => [...prev, { id: r.data.id, caseIds, isApp }])
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '执行测试启动失败，请稍后重试')
    }
  }

  const openExecModal = (caseIds: string[]) => {
    setPendingCaseIds(caseIds)
    setExecModalOpen(true)
  }

  const handleExecuteConfirm = async (runMode: string, accountOverrides?: Record<string, any>, targetDevice?: string | null, env?: string, packageOverrides?: Record<string, string>) => {
    // 排除正在执行中的用例(避免重复触发)
    const targetCases = cases.filter((c) => pendingCaseIds.includes(c.id) && !runningCaseIds.has(c.id))
    // 排除无法登录的 web 用例(端未接入框架)
    const blockedCount = targetCases.filter((c) => isLoginBlocked(c)).length
    if (blockedCount) message.warning(`${blockedCount} 条用例所属端无法登录(未接入自动化框架)，已跳过`)
    let executableCases = targetCases.filter((c) => isAutoExecutable(categorizeCase(c)) && !isLoginBlocked(c)) // web/api 可并发
    const mobileCases = targetCases.filter((c) => categorizeCase(c) === 'mobile')
    if (mobileCases.length) {
      try {
        const dev = await executionsApi.devices()
        if (dev.data.devices?.length || dev.data.sonic_devices?.length) {
          executableCases = [...executableCases, ...mobileCases]
          if (appBusy) message.warning('移动端测试需等待前一个测试完成，已加入排队，完成后自动执行')
        } else {
          message.warning('未检测到真机（本地/远程均无），移动端用例已跳过')
        }
      } catch { message.warning('真机探测失败，移动端用例已跳过') }
    }
    if (executableCases.length === 0) {
      message.warning('没有可执行的用例(可能都在执行中，或移动端需等待/连真机)')
      return
    }
    setExecModalOpen(false)
    await runExecution(executableCases.map((c) => c.id), runMode, accountOverrides, targetDevice, env, packageOverrides)
  }

  const caseColumns = [
    { title: '用例ID', dataIndex: 'case_id', key: 'case_id', width: 110 },
    {
      title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (v: string, row: any) => (
        <a className="row-title" onClick={() => openCaseDetail(row)}>{v}</a>
      ),
    },
    {
      title: '模块', dataIndex: 'modules', key: 'modules', width: 130, align: 'center' as const,
      filterIcon,
      filterDropdown: (p: any) => <ColFilter title="按模块筛选" scroll options={moduleOptions.map((o) => ({ value: o.key, label: o.label }))} {...p} />,
      onFilter: (value: any, row: any) => (row.modules || []).includes(value),
      render: (v: string[]) => (v || []).map((m) => <span key={m} style={{ ...TAG_BASE, ...indexTagStyle(m, moduleOptions.findIndex((o) => o.key === m)), marginRight: 4 }}>{moduleOptions.find((o) => o.key === m)?.label || m}</span>),
    },
    {
      title: '端', dataIndex: 'platforms', key: 'platforms', width: 130, align: 'center' as const,
      filterIcon,
      filterDropdown: (p: any) => <ColFilter title="按端筛选" scroll options={platformOptions.map((o) => ({ value: o.key, label: o.label }))} {...p} />,
      onFilter: (value: any, row: any) => (row.platforms || []).includes(value),
      render: (v: string[]) => (v || []).map((p) => <span key={p} style={{ ...TAG_BASE, ...platformTagStyle(p), marginRight: 4 }}>{platformOptions.find((o) => o.key === p)?.label || p}</span>),
    },
    {
      title: '场景类型', dataIndex: 'case_type', key: 'case_type', width: 110, align: 'center' as const,
      filterIcon,
      filterDropdown: (p: any) => <ColFilter title="按场景类型筛选" options={categoryOptions.map((o) => ({ value: o.key, label: o.label }))} {...p} />,
      onFilter: (value: any, row: any) => row.case_type === value,
      render: (v: string) => v ? <span style={{ ...TAG_BASE, ...indexTagStyle(v, categoryOptions.findIndex((o) => o.key === v)), whiteSpace: 'nowrap' }}>{categoryOptions.find((o) => o.key === v)?.label || v}</span> : '-',
    },
    {
      title: '优先级', dataIndex: 'priority', key: 'priority', width: 90, align: 'center' as const,
      filterIcon,
      filterDropdown: (p: any) => <ColFilter title="按优先级筛选" options={Array.from(new Set(cases.map((c) => c.priority).filter(Boolean))).sort().map((pr) => ({ value: pr, label: pr as string }))} {...p} />,
      onFilter: (value: any, row: any) => row.priority === value,
      render: (v: string) => <span style={{ ...TAG_BASE, ...priorityTagStyle(v), borderRadius: 999 }}>{v}</span>,
    },
    {
      title: '自动化', dataIndex: 'is_automated', key: 'is_automated', width: 100, align: 'center' as const,
      filterIcon,
      filterDropdown: (p: any) => <ColFilter title="按自动化筛选" options={[{ value: 'yes', label: '已生成' }, { value: 'no', label: '未生成' }]} {...p} />,
      onFilter: (value: any, row: any) => (row.is_automated ? 'yes' : 'no') === value,
      render: (v: boolean) => <Tag color={v ? 'success' : 'default'}>{v ? '已生成' : '未生成'}</Tag>,
    },
    {
      title: '最近结果', dataIndex: 'last_status', key: 'last_status', width: 130, align: 'center' as const,
      filterIcon,
      filterDropdown: (p: any) => <ColFilter title="按最近结果筛选" options={CASE_STATUS_FILTER_OPTIONS} {...p} />,
      onFilter: (value: any, row: any) => (CASE_STATUS_LABEL[row.last_status || 'not_run'] || '未执行') === value,
      render: (v: string, row: any) => (
        <Tag
          color={LAST_STATUS_COLOR[v] || 'default'}
          style={v !== 'not_run' ? { cursor: 'pointer' } : undefined}
          onClick={() => v !== 'not_run' && openLastResult(row)}
        >
          {CASE_STATUS_LABEL[v] || '未执行'}
        </Tag>
      ),
    },
    {
      title: '操作', key: 'action', width: 270, fixed: 'right' as const, align: 'center' as const,
      render: (_: any, row: any) => {
        const manual = row.last_status === 'manual_passed' ? 'pass' : row.last_status === 'manual_failed' ? 'fail' : null
        const manualLabel = manual === 'pass' ? '通过' : manual === 'fail' ? '失败' : '结果'
        const rightStyle = manual === 'pass'
          ? { color: '#128A43', background: '#F0FBF4' }
          : manual === 'fail'
          ? { color: '#C9332B', background: '#FEF5F5' }
          : { color: '#94A3B8', background: '#fff' }
        return (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, flexWrap: 'nowrap' }}>
            <Tooltip title={isLoginBlocked(row) ? `${(row.platforms || []).join('、')} 未接入自动化框架(地址无效/无法登录)，无法执行` : ''}>
              <Button type="link" size="small" style={{ padding: 0, fontSize: 12, whiteSpace: 'nowrap' }}
                disabled={runningCaseIds.has(row.id) || isLoginBlocked(row)}
                onClick={() => openExecModal([row.id])}>
                {runningCaseIds.has(row.id) ? '测试中…' : '执行测试'}
              </Button>
            </Tooltip>
            <Dropdown menu={{ items: [
              { key: 'pass', label: <span style={{ color: '#128A43', fontSize: 12 }}>手动测试通过</span>, onClick: () => handleManualPass(row) },
              { key: 'fail', label: <span style={{ color: '#C9332B', fontSize: 12 }}>手动测试失败（生成缺陷）</span>, onClick: () => handleManualFail(row) },
            ] }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', border: '1px solid #E7ECF0', borderRadius: 8, overflow: 'hidden', height: 28, cursor: 'pointer' }}>
                <span style={{ padding: '0 10px', fontSize: 11.5, fontWeight: 600, color: '#64748B', background: '#F7F9FB', borderRight: '1px solid #E7ECF0', whiteSpace: 'nowrap', lineHeight: '28px' }}>手动测试</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '0 9px', height: '100%', fontSize: 11.5, fontWeight: 600, ...rightStyle }}>
                  {manualLabel}<span className="ms" style={{ fontSize: 13 }}>expand_more</span>
                </span>
              </span>
            </Dropdown>
            <Button type="link" size="small" danger style={{ padding: 0, fontSize: 12, whiteSpace: 'nowrap' }}
              onClick={async () => { if (await confirmDialog({ title: '删除用例', desc: '删除后将进入用例库回收站，确认删除？', ok: '删除', danger: true })) handleDeleteCase(row) }}>删除</Button>
          </div>
        )
      },
    },
  ]

  const canCompleteTest = req && ['testing', 'pending_test'].includes(req.status)

  const analysisIssuePoints: any[] = analysis?.issue_points || []
  const allConfirmed = !!analysis?.platforms_confirmed && analysisIssuePoints.length > 0 && analysisIssuePoints.every(
    (ip: any) => (ip.confirmation_points || []).every((cp: any) => cp.status === 'confirmed')
  )

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }} align="center">
        <Button onClick={() => navigate('/requirements')}>返回需求列表</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>{req?.title || '需求详情'}</Typography.Title>
        {req && (
          <Tag color={STATUS_COLOR[req.status] || 'default'}>
            {STATUS_LABEL[req.status] || req.status}
          </Tag>
        )}
        {canCompleteTest && (
          <Button type="primary" onClick={handleCompleteTest}>完成测试</Button>
        )}
      </Space>

      {/* 需求文档 */}
      <Card
        id="document"
        title="需求文档"
        bordered={false}
        style={{ ...PANEL_CARD_STYLE, marginBottom: 16 }}
        extra={!req?.attachment_path && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <style>{`.rd-brand-btn:hover:not(:disabled){opacity:.88}`}</style>
            <span style={{ fontSize: 12, color: '#94A3B8' }}>选中下方内容可圈定负责范围：</span>
            <button className="rd-brand-btn" onClick={handleCreateSlice}
              style={{ height: 32, padding: '0 12px', borderRadius: 8, background: '#FBEEE6', border: '1px solid #EFD6C8', fontSize: 12.5, color: '#C25E3F', fontWeight: 500, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <span className="ms" style={{ fontSize: 16 }}>auto_awesome</span>{myExistingSlice ? '加入我的范围' : '新建负责范围'}
            </button>
          </div>
        )}
      >
        {req ? (
          <>
            <Descriptions column={1} size="small" style={{ marginBottom: 12 }}>
              <Descriptions.Item label="创建时间">{dayjs(req.created_at).format('YYYY-MM-DD HH:mm')}</Descriptions.Item>
            </Descriptions>
            {req.attachment_path ? (
              <Image src={requirementsApi.getAttachmentUrl(req.id)} style={{ maxWidth: '100%' }} alt={req.title} />
            ) : (
              <div className="md-content" style={{ background: '#fafafa', padding: '12px 16px', borderRadius: 6, border: '1px solid #f0f0f0', maxHeight: 520, overflow: 'auto', fontSize: 13, lineHeight: 1.7, color: '#334155' }}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    img: (p: any) => (
                      <Image
                        src={p.src}
                        height={110}
                        style={{ borderRadius: 6, border: '1px solid #eee', margin: '4px 6px 4px 0', objectFit: 'cover', cursor: 'zoom-in' }}
                      />
                    ),
                    // 标题压到接近正文(markdown 默认 h1≈2em 太大)
                    h1: (p: any) => <div {...p} style={{ fontSize: 15, fontWeight: 600, color: '#0F172A', margin: '12px 0 6px' }} />,
                    h2: (p: any) => <div {...p} style={{ fontSize: 14, fontWeight: 600, color: '#0F172A', margin: '10px 0 6px' }} />,
                    h3: (p: any) => <div {...p} style={{ fontSize: 13.5, fontWeight: 600, color: '#0F172A', margin: '10px 0 4px' }} />,
                    h4: (p: any) => <div {...p} style={{ fontSize: 13, fontWeight: 600, color: '#334155', margin: '8px 0 4px' }} />,
                    h5: (p: any) => <div {...p} style={{ fontSize: 13, fontWeight: 600, color: '#334155', margin: '8px 0 4px' }} />,
                    h6: (p: any) => <div {...p} style={{ fontSize: 13, fontWeight: 600, color: '#64748B', margin: '8px 0 4px' }} />,
                    table: (p: any) => <table {...p} style={{ borderCollapse: 'collapse', width: '100%', margin: '8px 0' }} />,
                    th: (p: any) => <th {...p} style={{ border: '1px solid #e7ecf0', padding: '6px 10px', background: '#f7f9fb', textAlign: 'left', fontWeight: 600 }} />,
                    td: (p: any) => <td {...p} style={{ border: '1px solid #f0f0f0', padding: '6px 10px', verticalAlign: 'top' }} />,
                  }}
                >
                  {(req.content || '').split('\n').filter((l: string) => !l.startsWith('原始链接:')).join('\n').trim() || '（无文档内容）'}
                </ReactMarkdown>
              </div>
            )}
          </>
        ) : (
          <Empty description="加载中..." />
        )}
      </Card>

      {/* 需求分析 */}
      <Card
        id="analysis"
        bordered={false}
        style={{ ...PANEL_CARD_STYLE, marginBottom: 16 }}
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 16, fontWeight: 600, color: '#0F172A' }}>需求分析</span>
            {allConfirmed && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, fontWeight: 500,
                padding: '2px 9px', borderRadius: 999, background: '#E9F8EF', color: '#128A43', border: '1px solid #B5E0C8',
              }}>
                <span className="ms" style={{ fontSize: 13 }}>check_circle</span>已全部确认
              </span>
            )}
          </span>
        }
        extra={
          <Space>
            {selectedPointIds.length > 0 && (
              <Button size="small" loading={batchConfirming} onClick={handleBatchNoConfirm}>
                批量无需确认（{selectedPointIds.length}）
              </Button>
            )}
            <Button
              onClick={handleAnalyze}
              loading={analyzing && !generating}
              disabled={analyzing || IN_PROGRESS_STATUSES.includes(aStatus)}
              style={{ height: 32, padding: '0 13px', borderRadius: 9, fontSize: 12, color: '#94A3B8', borderColor: '#E7ECF0' }}
            >
              {analysis ? '重新分析' : '开始分析'}
            </Button>
          </Space>
        }
      >
        <SliceBar
          slices={slices}
          activeSliceId={activeSlice?.id}
          onSwitch={setActiveSliceId}
          onDelete={handleDeleteSlice}
        />
        {!analysis ? (
          <Empty description={onDefaultSlice ? '尚未分析，点击右上角按钮开始' : `范围「${activeSlice?.scope_label}」尚未分析，点击右上角「开始分析」`} />
        ) : (
          <>
          {analysis?.vision_warning && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 14 }}
              message={`⚠️ 图片未完整识别（${analysis.vision_warning.image_count} 张）`}
              description={analysis.vision_warning.message}
            />
          )}
          {/* 涉及端确认（确认列表第一条）：确认后生成只能用这些端 */}
          <style>{`.rd-plat-chip:hover{opacity:.85}
            .rd-plat-confirm:hover:not(:disabled){opacity:.92}
            .rd-plat-edit:hover{color:#B5600A !important}`}</style>
          {(() => {
            // 旧分析没存 platforms 时，用各问题点端的并集预填
            const unionPlats = Array.from(new Set((analysis?.issue_points || []).flatMap((ip: any) => ip.platforms || []))) as string[]
            const val = platformDraft ?? (analysis?.platforms ?? unionPlats)
            const confirmed = !!analysis?.platforms_confirmed
            const labelOf = (k: string) => (platformOptions.find((p: any) => p.key === k)?.label) || k
            // 设计令牌
            const BRAND_SOLID = '#D97757', BRAND_TEXT = '#B5600A', BRAND_SOFT = '#FEF3EE', BRAND_BORDER = '#F0D2C0'
            const BRAND_GRAD = TECH_GRADIENT
            const toggle = (k: string) => setPlatformDraft(val.includes(k) ? val.filter((x: string) => x !== k) : [...val, k])

            // 已确认态：收缩为单行
            if (confirmed && !editingPlatforms) {
              const CHIP_COLORS = [
                { bg: '#FEF3EE', fg: '#B5600A' }, // 橙
                { bg: '#E6F7F6', fg: '#0E8B86' }, // 青
                { bg: '#EAF1FE', fg: '#2563EB' }, // 蓝
                { bg: '#F1ECFB', fg: '#7C3AED' }, // 紫
                { bg: '#E9F8EF', fg: '#128A43' }, // 绿
              ]
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: '#F7F9FB', border: '1px solid #F1F4F6', borderRadius: 10, flexWrap: 'wrap', marginBottom: 18 }}>
                  <span className="ms" style={{ fontSize: 15, color: '#128A43', fontVariationSettings: "'FILL' 1" }}>check_circle</span>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: '#334155' }}>涉及端已确认</span>
                  <div style={{ flex: 1, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {val.map((k: string, i: number) => {
                      const c = CHIP_COLORS[i % CHIP_COLORS.length]
                      return <span key={k} style={{ fontSize: 12, fontWeight: 500, padding: '2px 10px', borderRadius: 999, background: c.bg, color: c.fg }}>{labelOf(k)}</span>
                    })}
                  </div>
                  <span className="rd-plat-edit" onClick={() => { setPlatformDraft(val); setEditingPlatforms(true) }}
                    style={{ fontSize: 12, color: '#94A3B8', cursor: 'pointer' }}>修改</span>
                </div>
              )
            }

            // 未确认态（或点了「修改」）
            return (
              <div style={{ marginBottom: 18, padding: '14px 16px', borderRadius: 12, border: `1.5px solid ${BRAND_BORDER}`, background: BRAND_SOFT }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                  <span className="ms" style={{ fontSize: 15, color: BRAND_SOLID, fontVariationSettings: "'FILL' 1" }}>auto_awesome</span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#0F172A' }}>涉及端（请确认）</span>
                  <span style={{ fontSize: 12, color: '#94A3B8' }}>确认后用例生成只会用这些端</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
                  {platformOptions.map((p: any) => {
                    const on = val.includes(p.key)
                    return (
                      <span key={p.key} className="rd-plat-chip" onClick={() => toggle(p.key)}
                        style={{
                          display: 'inline-flex', alignItems: 'center', gap: 5, height: 32, padding: '0 13px', borderRadius: 999,
                          fontSize: 13, cursor: 'pointer', transition: 'all .15s',
                          background: on ? BRAND_SOFT : '#F7F9FB',
                          border: `1px solid ${on ? BRAND_BORDER : '#E7ECF0'}`,
                          color: on ? BRAND_TEXT : '#64748B', fontWeight: on ? 600 : 400,
                        }}>
                        <span className="ms" style={{ fontSize: 14, color: on ? BRAND_SOLID : '#CBD5E1', fontVariationSettings: on ? "'FILL' 1" : "'FILL' 0" }}>
                          {on ? 'check_circle' : 'radio_button_unchecked'}
                        </span>
                        {p.label}
                      </span>
                    )
                  })}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 12.5, color: '#64748B' }}>
                    已选 <span style={{ fontFamily: MONO_FONT, fontWeight: 700, color: BRAND_TEXT }}>{val.length}</span> 个端
                  </span>
                  <button className="rd-plat-confirm" disabled={platformSaving} onClick={handleConfirmPlatforms}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 5, height: 34, padding: '0 18px', borderRadius: 8,
                      background: BRAND_GRAD, color: '#fff', border: 'none', fontSize: 13, fontWeight: 600,
                      cursor: platformSaving ? 'not-allowed' : 'pointer', boxShadow: '0 4px 12px -5px rgba(217,119,87,.4)',
                    }}>
                    <span className="ms" style={{ fontSize: 16 }}>check</span>{confirmed ? '更新确认' : '确认'}
                  </button>
                </div>
              </div>
            )
          })()}
          <div style={{ maxHeight: 560, overflowY: 'auto', paddingRight: 6 }}>
          {analysisIssuePoints.map((ip: any, idx: number) => (
            <div key={ip.issue_id} style={{ marginBottom: idx === analysisIssuePoints.length - 1 ? 0 : 22 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <span style={{
                  width: 20, height: 20, flex: 'none', borderRadius: '50%', background: '#FEF3EE', color: '#B5600A',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, fontFamily: MONO_FONT,
                }}>{idx + 1}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: '#0F172A' }}>{ip.description}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {(ip.confirmation_points || []).map((cp: any) => (
                  <ConfirmationPointCard
                    key={cp.point_id}
                    reqId={id!}
                    sliceId={sliceParam}
                    point={cp}
                    onDone={() => { reloadReq(); loadSlices() }}
                    isSelected={selectedPointIds.includes(cp.point_id)}
                    onToggleSelect={(pointId) =>
                      setSelectedPointIds((prev) =>
                        prev.includes(pointId) ? prev.filter((x) => x !== pointId) : [...prev, pointId]
                      )
                    }
                  />
                ))}
              </div>
            </div>
          ))}
          </div>
          </>
        )}
      </Card>

      {/* 测试用例 */}
      <Card
        id="testcases"
        title="测试用例"
        bordered={false}
        style={{ ...PANEL_CARD_STYLE, marginBottom: 16 }}
        extra={
          cases.length > 0 ? (
            <Space size={8}>
              {isAdmin && (
                <button className="rd-ghost-btn" onClick={handleCoverage} style={GHOST_BTN}>
                  <span className="ms" style={{ fontSize: 17 }}>analytics</span>覆盖分析
                </button>
              )}
              <Dropdown menu={{ items: selectedRowKeys.length > 0
                ? [
                  { key: 'xlsx', label: `导出选中（${selectedRowKeys.length}）为表格`, onClick: () => window.open(testCasesApi.exportUrl('xlsx', { ids: selectedRowKeys })) },
                  { key: 'md', label: `导出选中（${selectedRowKeys.length}）为 Markdown`, onClick: () => window.open(testCasesApi.exportUrl('md', { ids: selectedRowKeys })) },
                ]
                : [
                  { key: 'xlsx', label: '导出为表格(Excel)', onClick: () => window.open(testCasesApi.exportUrl('xlsx', { requirementId: id! })) },
                  { key: 'md', label: '导出为 Markdown', onClick: () => window.open(testCasesApi.exportUrl('md', { requirementId: id! })) },
                ] }}>
                <button className="rd-ghost-btn" style={GHOST_BTN}>
                  <span className="ms" style={{ fontSize: 17 }}>download</span>导出{selectedRowKeys.length ? `选中（${selectedRowKeys.length}）` : ''}
                  <span className="ms" style={{ fontSize: 16, color: '#B0BAC4' }}>expand_more</span>
                </button>
              </Dropdown>
              <button className="rd-ghost-btn" onClick={handleRegenerateCases}
                disabled={generating || IN_PROGRESS_STATUSES.includes(aStatus)}
                style={{ ...GHOST_BTN, opacity: generating || IN_PROGRESS_STATUSES.includes(aStatus) ? 0.6 : 1 }}>
                <span className="ms" style={{ fontSize: 17 }}>refresh</span>{generating ? '生成中…' : '重新生成用例'}
              </button>
              {selectedRowKeys.length > 0 && (
                <>
                  <Button loading={batchManualPassing} onClick={handleBatchManualPass}>
                    批量手动通过（{selectedRowKeys.length}条）
                  </Button>
                  <Button danger onClick={handleBatchDeleteCases}>
                    批量删除（{selectedRowKeys.length}）
                  </Button>
                </>
              )}
              <Button
                type="primary"
                onClick={() => openExecModal(selectedRowKeys.length ? selectedRowKeys : cases.map((c) => c.id))}
              >
                执行测试{selectedRowKeys.length ? `（${selectedRowKeys.length}条）` : '（全量）'}
                {activeExecs.length > 0 ? `· ${runningCaseIds.size} 在跑` : ''}
              </Button>
            </Space>
          ) : (
            analysis && (
              <Button onClick={handleGenerateCases} loading={generating} type="primary">
                生成用例
              </Button>
            )
          )
        }
      >
        {analysis?.generation_vision_warning && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message={`⚠️ 用例生成时图片未完整识别（${analysis.generation_vision_warning.image_count} 张）`}
            description={analysis.generation_vision_warning.message}
          />
        )}
        {cases.length === 0 ? (
          <Empty description="尚未生成用例" />
        ) : (
          <>
            {/* 用例去重确认面板 */}
            {cases.some((c) => c.review_status === 'pending_review') && (
              <Card
                size="small"
                style={{ marginBottom: 16, border: '1px solid #faad14', background: '#fffbe6' }}
                title={
                  <Space>
                    <Tag color="warning">待确认</Tag>
                    <Typography.Text>以下生成的用例与用例库已有用例相似：「纳入本次测试」直接复用老用例，「更新用例库」用新内容更新老用例，二者都会把老用例纳入本需求测试范围并丢弃新草稿</Typography.Text>
                  </Space>
                }
                extra={
                  reviewSelectedIds.length > 0 && (
                    <Space>
                      <Button
                        size="small" loading={reviewingBatch}
                        onClick={() => handleBatchReview('keep')}
                      >批量纳入本次测试（{reviewSelectedIds.length}）</Button>
                      <Button
                        size="small" loading={reviewingBatch}
                        onClick={() => handleBatchReview('update_existing')}
                      >批量更新用例库（{reviewSelectedIds.length}）</Button>
                    </Space>
                  )
                }
              >
                <Table
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={cases.filter((c) => c.review_status === 'pending_review')}
                  rowSelection={{
                    selectedRowKeys: reviewSelectedIds,
                    onChange: (keys) => setReviewSelectedIds(keys as string[]),
                  }}
                  columns={[
                    { title: '新生成用例', dataIndex: 'title', key: 'title', ellipsis: true },
                    {
                      title: '相似已有用例', key: 'similar', width: 280,
                      render: (_: any, row: any) => row.similar_case_case_id ? (
                        <Typography.Text type="secondary">{row.similar_case_case_id} · {row.similar_case_title}</Typography.Text>
                      ) : '-',
                    },
                    {
                      title: '操作', key: 'action', width: 200,
                      render: (_: any, row: any) => (
                        <Space>
                          <Button size="small" type="primary" onClick={async () => {
                            await testCasesApi.review(row.id, 'keep')
                            message.success('已复用老用例纳入本次测试')
                            loadCases()
                          }}>纳入本次测试</Button>
                          <Button size="small" onClick={async () => {
                            await testCasesApi.review(row.id, 'update_existing')
                            message.success('已更新用例库并纳入本次测试')
                            loadCases()
                          }}>更新用例库</Button>
                        </Space>
                      ),
                    },
                  ]}
                />
              </Card>
            )}

            <style>{`.case-list-table .ant-table-thead > tr > th{white-space:nowrap}
              .case-list-table .ant-table-tbody > tr > td{vertical-align:middle}
              .rd-ghost-btn:hover:not(:disabled){background:#F3F6F8;border-color:#D5DDE4}
              .rd-brand-btn:hover:not(:disabled){opacity:.88}`}</style>
            <Table
              className="case-list-table"
              rowKey="id"
              dataSource={cases.filter((c) => c.review_status !== 'pending_review')}
              columns={caseColumns}
              rowSelection={{ selectedRowKeys, onChange: (keys) => setSelectedRowKeys(keys as string[]) }}
              scroll={{ x: 1200 }}
              pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (total) => `共 ${total} 条` }}
            />

          </>
        )}
      </Card>

      {/* 缺陷复核 */}
      <Card id="defects" title="缺陷复核" bordered={false} style={PANEL_CARD_STYLE}>
        {defectsData.length === 0 ? (
          <Empty description="暂无缺陷" />
        ) : (
          <DefectReviewTable
            defects={defectsData}
            severityOptions={severityOptions}
            onChanged={() => { loadDefects(); reloadReq() }}
          />
        )}
      </Card>

      {/* 最近执行结果 Drawer */}
      <Drawer
        title={lastResultCase ? `${lastResultCase.case_id} — 最近执行结果` : ''}
        open={!!lastResultCase}
        onClose={() => setLastResultCase(null)}
        width={480}
      >
        {lastResultLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Typography.Text type="secondary">加载中…</Typography.Text></div>
        ) : lastResultData.length === 0 ? (
          <Empty description="暂无执行记录" />
        ) : (() => {
          const r = lastResultData[0]
          return (
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="结果">
                <Tag color={RESULT_COLOR[r.status]}>{CASE_STATUS_LABEL[r.status] || r.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="耗时">{r.duration_ms != null ? `${r.duration_ms} ms` : '—'}</Descriptions.Item>
              <Descriptions.Item label="执行时间">{r.created_at ? dayjs(r.created_at).format('YYYY-MM-DD HH:mm:ss') : '—'}</Descriptions.Item>
              {Array.isArray(r.ui_trace) && r.ui_trace.length > 0 && (
                <Descriptions.Item label="分步结果">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <Image.PreviewGroup>
                      {r.ui_trace.map((st: any) => (
                        <div key={st.seq} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', paddingBottom: 12, borderBottom: '1px solid #F1F4F6' }}>
                          <div style={{ width: 22, height: 22, flex: 'none', borderRadius: '50%', background: SELECTED_BG, color: PRIMARY_DEEP, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, fontFamily: MONO_FONT }}>{st.seq || '·'}</div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                              <span style={{ fontSize: 13, fontWeight: 600, color: '#0F172A' }}>{st.action || '操作'}</span>
                              {st.verdict && (
                                <Tag color={st.verdict === 'pass' ? 'success' : st.verdict === 'fail' ? 'error' : 'warning'} style={{ marginInlineEnd: 0 }}>
                                  {st.verdict_cn || (st.verdict === 'pass' ? '通过' : st.verdict === 'fail' ? '不符' : '无法验证')}
                                </Tag>
                              )}
                            </div>
                            {st.expected && <div style={{ fontSize: 12, color: '#94A3B8', marginTop: 2 }}>预期：{st.expected}</div>}
                            {Array.isArray(st.checks) && st.checks.length > 0 && (
                              <div style={{ marginTop: 4 }}>
                                {st.checks.map((c: any, ci: number) => (
                                  <div key={ci} style={{ fontSize: 12, color: c.ok ? '#128A43' : '#C9332B', lineHeight: 1.6 }}>
                                    {c.ok ? '✓' : '✗'} {c.point}
                                  </div>
                                ))}
                              </div>
                            )}
                            {st.reason && <div style={{ fontSize: 12, color: st.verdict === 'pass' ? '#64748B' : '#C9332B', marginTop: 2 }}>判定：{st.reason}</div>}
                          </div>
                          {st.shot
                            ? <Image src={st.shot} width={64} height={136} style={{ flex: 'none', objectFit: 'cover', borderRadius: 6, border: '1px solid #ECEFF2', cursor: 'pointer' }} />
                            : <Typography.Text type="secondary" style={{ flex: 'none', fontSize: 12 }}>无图</Typography.Text>}
                        </div>
                      ))}
                    </Image.PreviewGroup>
                  </div>
                </Descriptions.Item>
              )}
              {r.api_trace && (
                <>
                  <Descriptions.Item label="x-hubble-trace-id">
                    <Typography.Text copyable style={{ fontSize: 12, fontFamily: MONO_FONT }}>{r.api_trace.trace_id || '—'}</Typography.Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="接口请求">
                    <div style={{ fontSize: 12, marginBottom: 4 }}>
                      <Tag color="blue">{r.api_trace.request?.method || 'GET'}</Tag>
                      <Typography.Text style={{ fontFamily: MONO_FONT }}>{r.api_trace.request?.url}</Typography.Text>
                    </div>
                    <pre style={{ margin: 0, padding: 10, background: '#0F172A', color: '#CBD5E1', borderRadius: 8, fontSize: 11.5, overflowX: 'auto', maxHeight: 200 }}>
{JSON.stringify(r.api_trace.request?.body ?? {}, null, 2)}</pre>
                  </Descriptions.Item>
                  <Descriptions.Item label="接口返回">
                    <div style={{ fontSize: 12, marginBottom: 4 }}>
                      <Tag color={(r.api_trace.response?.status ?? 0) < 400 ? 'success' : 'error'}>HTTP {r.api_trace.response?.status ?? '—'}</Tag>
                    </div>
                    <pre style={{ margin: 0, padding: 10, background: '#0F172A', color: '#CBD5E1', borderRadius: 8, fontSize: 11.5, overflowX: 'auto', maxHeight: 240 }}>
{JSON.stringify(r.api_trace.response?.body ?? {}, null, 2)}</pre>
                  </Descriptions.Item>
                </>
              )}
              <Descriptions.Item label="执行 ID">
                <Typography.Text copyable style={{ fontSize: 12 }}>{r.execution_id}</Typography.Text>
              </Descriptions.Item>
            </Descriptions>
          )
        })()}
      </Drawer>

      {/* 执行测试配置弹窗 */}
      <ExecConfigModal
        open={execModalOpen}
        cases={cases.filter((c) => pendingCaseIds.includes(c.id))}
        categorizeCase={categorizeCase}
        execApiBaseUrl={execApiBaseUrl}
        setExecApiBaseUrl={setExecApiBaseUrl}
        onCancel={() => setExecModalOpen(false)}
        onConfirm={handleExecuteConfirm}
      />

      {/* 新建负责范围(切片) */}
      <Modal
        title="新建负责范围"
        open={sliceModalOpen}
        onCancel={() => setSliceModalOpen(false)}
        onOk={submitCreateSlice}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <div style={{ fontSize: 12.5, color: '#94A3B8', marginBottom: 10 }}>
          已圈选的内容将作为该范围；该范围独立分析/生成，归属人默认为你。
        </div>
        <Input
          placeholder="范围名，如 支付模块 / 退款流程"
          value={sliceLabel}
          onChange={(e) => setSliceLabel(e.target.value)}
          onPressEnter={submitCreateSlice}
          maxLength={40}
        />
      </Modal>

      {/* 全量 / 增量 选择 */}
      <Modal
        title={modeModal.kind === 'analyze' ? '需求分析' : '生成用例'}
        open={modeModal.open}
        onCancel={() => setModeModal((m) => ({ ...m, open: false }))}
        footer={null}
        destroyOnClose
      >
        <div style={{ fontSize: 13, color: '#475569', marginBottom: 16 }}>
          范围「{activeSlice?.scope_label}」检测到新追加、尚未处理的内容。请选择：
          {modeModal.kind === 'analyze'
            ? '增量分析只分析新加的那部分并把新问题点追加进现有分析；全量分析重新分析整段范围（会覆盖现有分析及确认）。'
            : '增量生成只对尚未生成用例的新问题点生成并追加；全量生成会清掉未执行用例、按整段范围重新生成。'}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <Button onClick={() => setModeModal((m) => ({ ...m, open: false }))}>取消</Button>
          {modeModal.kind === 'analyze' ? (
            <>
              <Button onClick={() => runAnalyze('full')}>全量分析</Button>
              <Button type="primary" onClick={() => runAnalyze('incremental')}>增量分析</Button>
            </>
          ) : (
            <>
              <Button onClick={() => runGenerate('full', true)}>全量生成</Button>
              <Button type="primary" onClick={() => runGenerate('incremental', false)}>增量生成</Button>
            </>
          )}
        </div>
      </Modal>

      {/* 用例查看/编辑 */}
      <Drawer
        title={caseDetail ? `${caseDetail.case_id} — ${caseEditMode ? '编辑用例' : '用例详情'}` : ''}
        open={!!caseDetail}
        onClose={() => { setCaseDetail(null); setCaseEditMode(false) }}
        width={560}
        extra={
          caseDetail && !caseEditMode ? (
            <Button type="primary" onClick={startCaseEdit}>编辑</Button>
          ) : caseDetail ? (
            <Space>
              <Button onClick={() => setCaseEditMode(false)}>取消</Button>
              <Button type="primary" loading={caseSaving} onClick={handleCaseSave}>保存</Button>
            </Space>
          ) : null
        }
      >
        {caseDetail && !caseEditMode && (() => {
          const mLabel = (k: string) => moduleOptions.find((o) => o.key === k)?.label || k
          const pLabel = (k: string) => platformOptions.find((o) => o.key === k)?.label || k
          const ctLabel = categoryOptions.find((o) => o.key === caseDetail.case_type)?.label || caseDetail.case_type
          const rows: Array<{ label: string; node: React.ReactNode }> = [
            { label: '标题', node: <span style={{ fontSize: 13.5, fontWeight: 500, color: '#0F172A', lineHeight: 1.55 }}>{caseDetail.title}</span> },
            { label: '优先级', node: <span style={{ ...TAG_BASE, ...priorityTagStyle(caseDetail.priority), borderRadius: 999 }}>{caseDetail.priority}</span> },
            { label: '场景类型', node: caseDetail.case_type ? <span style={{ ...TAG_BASE, ...indexTagStyle(caseDetail.case_type, categoryOptions.findIndex((o) => o.key === caseDetail.case_type)) }}>{ctLabel}</span> : '—' },
            { label: '模块', node: <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{(caseDetail.modules || []).map((m: string) => <span key={m} style={{ ...TAG_BASE, ...indexTagStyle(m, moduleOptions.findIndex((o) => o.key === m)) }}>{mLabel(m)}</span>)}</div> },
            { label: '端', node: <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{(caseDetail.platforms || []).map((p: string) => <span key={p} style={{ ...TAG_BASE, ...platformTagStyle(p) }}>{pLabel(p)}</span>)}</div> },
            { label: '最近结果', node: <span style={{ ...TAG_BASE, ...grayTagStyle() }}>{CASE_STATUS_LABEL[caseDetail.last_status] || caseDetail.last_status}</span> },
            { label: '预期结果', node: <span style={{ fontSize: 13, color: '#334155', lineHeight: 1.7 }}>{caseDetail.expected_result || '—'}</span> },
          ]
          const steps = caseDetail.steps || []
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
              <div style={{ border: '1px solid #ECEFF2', borderRadius: 12, overflow: 'hidden' }}>
                {rows.map((r, i) => (
                  <div key={r.label} style={{ display: 'flex', borderBottom: i === rows.length - 1 ? 'none' : '1px solid #ECEFF2' }}>
                    <div style={{ width: 88, flex: 'none', padding: '14px 18px', fontSize: 12.5, color: '#94A3B8', fontWeight: 500, background: '#FAFBFC' }}>{r.label}</div>
                    <div style={{ flex: 1, minWidth: 0, padding: '11px 18px', display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>{r.node}</div>
                  </div>
                ))}
              </div>
              {steps.length > 0 && (
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#0F172A', marginBottom: 16 }}>测试步骤</div>
                  {steps.map((s: any, i: number) => (
                    <div key={i}>
                      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                        <div style={{ width: 26, height: 26, flex: 'none', borderRadius: '50%', background: SELECTED_BG, color: PRIMARY_DEEP, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, fontFamily: MONO_FONT }}>{i + 1}</div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13.5, fontWeight: 600, color: '#0F172A', lineHeight: 1.5 }}>{s.action}</div>
                          <div style={{ fontSize: 12.5, color: '#64748B', lineHeight: 1.7, marginTop: 2 }}><span style={{ color: '#94A3B8', fontWeight: 600 }}>预期：</span>{s.expected}</div>
                        </div>
                      </div>
                      {i < steps.length - 1 && <div style={{ height: 1, background: '#F1F4F6', margin: '12px 0 12px 40px' }} />}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })()}
        {caseDetail && caseEditMode && (
          <Form form={caseForm} layout="vertical">
            <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
              <Input style={{ fontSize: 13 }} />
            </Form.Item>
            <Form.Item name="priority" label="优先级">
              <Select style={{ maxWidth: 160 }}
                options={['P0', 'P1', 'P2'].map((p) => ({ value: p, label: p }))}
                labelRender={({ value }) => value ? <span style={{ ...TAG_BASE, ...priorityTagStyle(String(value)), borderRadius: 999 }}>{value}</span> : null} />
            </Form.Item>
            <Form.Item name="case_type" label="场景类型">
              <Select allowClear style={{ maxWidth: 180 }}
                options={categoryOptions.map((o) => ({ value: o.key, label: o.label }))}
                labelRender={({ value, label }) => value ? <span style={{ ...TAG_BASE, ...indexTagStyle(String(value), categoryOptions.findIndex((o) => o.key === value)) }}>{label}</span> : null} />
            </Form.Item>
            <Form.Item name="modules" label="模块">
              <Select mode="multiple" placeholder="选择模块"
                options={moduleOptions.map((o) => ({ value: o.key, label: o.label }))}
                tagRender={({ value, closable, onClose }) => (
                  <span style={{ ...TAG_BASE, ...indexTagStyle(String(value), moduleOptions.findIndex((o) => o.key === value)), gap: 4, margin: '2px 4px 2px 0', padding: '2px 8px' }}>
                    {moduleOptions.find((o) => o.key === value)?.label || value}
                    {closable && <span onClick={onClose} style={{ cursor: 'pointer', color: '#94A3B8', fontSize: 14 }}>×</span>}
                  </span>
                )} />
            </Form.Item>
            <Form.Item name="platforms" label="端">
              <Select mode="multiple" placeholder="选择端"
                options={platformOptions.map((o) => ({ value: o.key, label: o.label }))}
                tagRender={({ value, closable, onClose }) => (
                  <span style={{ ...TAG_BASE, ...platformTagStyle(String(value)), gap: 4, margin: '2px 4px 2px 0', padding: '2px 8px' }}>
                    {platformOptions.find((o) => o.key === value)?.label || value}
                    {closable && <span onClick={onClose} style={{ cursor: 'pointer', color: '#94A3B8', fontSize: 14 }}>×</span>}
                  </span>
                )} />
            </Form.Item>
            <Form.Item name="expected_result" label="预期结果">
              <Input.TextArea rows={3} style={{ fontSize: 13 }} />
            </Form.Item>
            <Form.Item label="测试步骤">
              <Form.List name="steps">
                {(fields, { add, remove, move }) => (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                    {fields.map((field, idx) => (
                      <div key={field.key}
                        onDragOver={(e) => { e.preventDefault() }}
                        onDrop={() => { if (dragStepIdx !== null && dragStepIdx !== idx) move(dragStepIdx, idx); setDragStepIdx(null) }}
                        style={{ display: 'flex', gap: 8, alignItems: 'flex-start', opacity: dragStepIdx === idx ? 0.4 : 1 }}>
                        <span draggable
                          onDragStart={() => setDragStepIdx(idx)}
                          onDragEnd={() => setDragStepIdx(null)}
                          title="拖动调整顺序"
                          style={{ flex: 'none', marginTop: 6, cursor: 'grab', color: '#C0C8D0', fontSize: 16, lineHeight: '26px', userSelect: 'none' }}>⠿</span>
                        <div style={{ width: 26, height: 26, flex: 'none', marginTop: 2, borderRadius: '50%', background: SELECTED_BG, color: PRIMARY_DEEP, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, fontFamily: MONO_FONT }}>{idx + 1}</div>
                        <div style={{ flex: 1, minWidth: 0, border: '1.5px solid #E7ECF0', borderRadius: 10, overflow: 'hidden' }}>
                          <Form.Item name={[field.name, 'action']} noStyle rules={[{ required: true, message: '请输入操作步骤' }]}>
                            <Input.TextArea variant="borderless" placeholder="操作步骤" autoSize={{ minRows: 1 }} style={{ padding: '10px 14px', fontSize: 13, borderBottom: '1px solid #F1F4F6', borderRadius: 0, resize: 'none' }} />
                          </Form.Item>
                          <Form.Item name={[field.name, 'expected']} noStyle>
                            <Input.TextArea variant="borderless" placeholder="预期结果" autoSize={{ minRows: 1 }} style={{ padding: '10px 14px', background: '#FAFBFC', fontSize: 12.5, color: '#64748B', borderRadius: 0, resize: 'none' }} />
                          </Form.Item>
                        </div>
                        <span onClick={() => remove(field.name)} style={{ flex: 'none', marginTop: 10, fontSize: 12, color: '#C9332B', cursor: 'pointer', whiteSpace: 'nowrap' }}>删除</span>
                      </div>
                    ))}
                    <div onClick={() => add({ action: '', expected: '' })} style={{ height: 42, border: '1.5px dashed #E7ECF0', borderRadius: 10, fontSize: 13, color: '#94A3B8', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, cursor: 'pointer' }}>+ 添加步骤</div>
                  </div>
                )}
              </Form.List>
            </Form.Item>
          </Form>
        )}
      </Drawer>

      <Modal
        title="需求覆盖分析"
        open={coverageOpen}
        onCancel={() => setCoverageOpen(false)}
        footer={<Button onClick={() => setCoverageOpen(false)}>关闭</Button>}
        width={620}
      >
        {coverageLoading ? (
          <div style={{ textAlign: 'center', padding: '32px 0' }}>
            <Progress type="circle" percent={0} />
            <div style={{ marginTop: 12, color: '#94A3B8' }}>AI 正在比对需求与现有用例，检测漏测…</div>
          </div>
        ) : coverageData ? (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
              <Progress type="circle" size={88}
                percent={coverageData.coverage_percent ?? 0}
                status={(coverageData.coverage_percent ?? 0) >= 100 ? 'success' : 'active'} />
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: '#0F172A' }}>
                  需求覆盖率 {coverageData.coverage_percent ?? 0}%
                  {coverageData.scoped && <Tag color="blue" style={{ marginLeft: 8 }}>按最近选区分析范围</Tag>}
                </div>
                <div style={{ fontSize: 12.5, color: '#64748B', marginTop: 4 }}>
                  现有用例 {coverageData.case_count} 条
                  {coverageData.total_points != null ? ` · 功能点约 ${coverageData.total_points} 个` : ''}
                  {coverageData.covered_points ? ` · 已覆盖 ${coverageData.covered_points.length} 个` : ''}
                </div>
              </div>
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#0F172A', marginBottom: 8 }}>
              未覆盖功能点（漏测，{(coverageData.uncovered_points || []).length} 个）
            </div>
            {(coverageData.uncovered_points || []).length === 0 ? (
              <Alert type="success" showIcon message="未发现漏测，需求已被现有用例覆盖" />
            ) : (
              <List
                size="small"
                bordered
                dataSource={coverageData.uncovered_points}
                renderItem={(p: string, i: number) => (
                  <List.Item
                    actions={[
                      <Button key="gen" type="link" size="small" onClick={() => genForUncovered(p)}>生成用例</Button>,
                      <Button key="ign" type="link" size="small" onClick={() => dropUncovered(p)}>忽略</Button>,
                    ]}
                  >
                    <Space align="start">
                      <span className="ms" style={{ fontSize: 16, color: '#E8930C' }}>warning</span>
                      <span style={{ fontSize: 13 }}>{i + 1}. {p}</span>
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </div>
        ) : null}
      </Modal>
    </div>
  )
}

function SliceBar({ slices, activeSliceId, onSwitch, onDelete }: {
  slices: any[]; activeSliceId?: string
  onSwitch: (id: string) => void; onDelete: (sl: any) => void
}) {
  // 只有一个默认「全文」范围时不显示(没拆分时不打扰)
  if (slices.length <= 1) return null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 14, paddingBottom: 12, borderBottom: '1px solid #F0F2F5' }}>
      <span style={{ fontSize: 12, color: '#94A3B8' }}>负责范围:</span>
      {slices.map((s) => {
        const on = s.id === activeSliceId
        return (
          <span key={s.id} onClick={() => onSwitch(s.id)}
            style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 999, fontSize: 12.5,
              border: on ? '1px solid #E0A57E' : '1px solid #E7ECF0', background: on ? '#FBEEE6' : '#fff', color: on ? '#C25E3F' : '#64748B' }}>
            {s.scope_label}{s.owner_name ? ` · ${s.owner_name}` : ''}
            {!s.is_default && (
              <span className="ms" onClick={(e) => { e.stopPropagation(); onDelete(s) }}
                style={{ fontSize: 14, color: '#C0C7CF' }}>close</span>
            )}
          </span>
        )
      })}
    </div>
  )
}

function ConfirmationPointCard({ reqId, sliceId, point, onDone, isSelected, onToggleSelect }: {
  reqId: string; sliceId?: string; point: any; onDone: () => void
  isSelected: boolean; onToggleSelect: (pointId: string) => void
}) {
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)

  const submit = async (noConfirmationNeeded: boolean) => {
    setSaving(true)
    try {
      await requirementsApi.updateConfirmationPoint(reqId, point.point_id, {
        confirmation: noConfirmationNeeded ? undefined : value,
        no_confirmation_needed: noConfirmationNeeded,
      }, sliceId)
      onDone()
    } finally {
      setSaving(false)
    }
  }

  // ── 已确认行：圆角容器 + 左绿竖线 + 问题/确认意见 + 已确认绿标签 ──
  if (point.status === 'confirmed') {
    return (
      <div style={{ borderRadius: 12, overflow: 'hidden', border: '1px solid #F1F4F6' }}>
        <div style={{ display: 'flex', alignItems: 'stretch', background: '#FAFCFD', padding: '13px 16px', gap: 16 }}>
          <div style={{ width: 3, flex: 'none', borderRadius: 999, background: '#B5E0C8' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, color: '#334155', lineHeight: 1.6 }}>{point.content}</div>
            <div style={{ fontSize: 12.5, color: '#94A3B8', marginTop: 5 }}>
              {point.no_confirmation_needed ? '无需确认' : (point.confirmation || '—')}
            </div>
          </div>
          <span style={{
            alignSelf: 'flex-start', marginTop: 2, display: 'inline-flex', alignItems: 'center', gap: 3,
            fontSize: 11.5, fontWeight: 500, padding: '3px 10px', borderRadius: 7,
            background: '#E9F8EF', color: '#128A43', border: '1px solid #B5E0C8',
          }}>
            <span className="ms" style={{ fontSize: 13 }}>check</span>已确认
          </span>
        </div>
      </div>
    )
  }

  // ── 待确认行：浅橙底 + 问题 + 待确认胶囊 + 输入卡片(textarea + 工具栏) ──
  return (
    <div style={{ background: '#FFFCF9', borderRadius: 12, border: '1px solid #FBE2B0', padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, minWidth: 0 }}>
          <Checkbox checked={isSelected} onChange={() => onToggleSelect(point.point_id)} style={{ marginTop: 1 }} />
          <span className="ms" style={{ fontSize: 16, color: '#E8930C', marginTop: 1 }}>help_outline</span>
          <span style={{ fontSize: 13, color: '#1E293B', fontWeight: 500, lineHeight: 1.65 }}>{point.content}</span>
        </div>
        <span style={{
          flex: 'none', display: 'inline-flex', alignItems: 'center', gap: 3,
          fontSize: 11, fontWeight: 600, padding: '3px 9px', borderRadius: 999,
          background: '#FEF6E7', color: '#B5710A', border: '1px solid #FBE2B0',
        }}>
          <span className="ms" style={{ fontSize: 12 }}>schedule</span>待确认
        </span>
      </div>
      <div className="cp-input" style={{ background: '#fff', border: '1.5px solid #E7ECF0', borderRadius: 10, overflow: 'hidden' }}>
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="输入确认意见，例：保留原图，支持预览和撤销…"
          style={{ width: '100%', height: 68, border: 'none', padding: '11px 14px', fontSize: 13, color: '#334155', lineHeight: 1.6, background: 'transparent', resize: 'none', outline: 'none', boxSizing: 'border-box' }}
        />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', borderTop: '1px solid #F1F4F6', background: '#FAFBFC' }}>
          <span style={{ fontSize: 11.5, color: '#CBD5E1' }}>请描述产品确认后的规则</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              disabled={saving}
              onClick={() => submit(true)}
              style={{ height: 28, padding: '0 12px', background: '#fff', border: '1px solid #E7ECF0', borderRadius: 7, fontSize: 12, color: '#94A3B8', fontWeight: 500, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}
            >
              <span className="ms" style={{ fontSize: 14 }}>remove_circle_outline</span>无需确认
            </button>
            <button
              disabled={saving}
              onClick={() => submit(false)}
              style={{ height: 28, padding: '0 13px', background: TECH_GRADIENT, color: '#fff', border: 'none', borderRadius: 7, fontSize: 12, fontWeight: 600, cursor: 'pointer', boxShadow: '0 3px 8px -3px rgba(217,119,87,.45)', display: 'inline-flex', alignItems: 'center', gap: 4, opacity: saving ? 0.7 : 1 }}
            >
              <span className="ms" style={{ fontSize: 14 }}>check</span>提交确认
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
