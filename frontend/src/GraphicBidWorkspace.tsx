import { AccountNavActions } from './AccountNavActions'
import { useAuth } from './AuthWorkspace'
import { ChapterCollaboration } from './ChapterCollaboration'
import { type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode, type WheelEvent as ReactWheelEvent, useEffect, useMemo, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { readEventStream } from './utils/eventStream'
import { useTextInputDialog } from './utils/useTextInputDialog'
import { DOCX_EXPORT_TEMPLATES, exportGraphicBidDocx, getTenderFormatGaps, parseTenderTableLayout, type DocxExportTemplateId } from './docxExport'
import { downloadBidTemplatePreview, getBidTemplateById, type BidTemplateId } from './utils/bidTemplateCatalog'
import { downloadCustomWordTemplate, importCustomWordTemplate, importRemoteWordTemplate, listCustomWordTemplates, removeCustomWordTemplate, updateCustomWordTemplate, type CustomWordTemplateSummary } from './utils/customWordTemplateStore'
import { type BlindCheckReport, type AssetAuditResult, type AssetRisk, type AssetVerificationResult, type BidCatalogResponse, type BidParseStatus, type BidTableFillResult, type BidTableType, type CatalogNode, type GraphicFormatTemplate, type GraphicSection, type KnowledgeAsset, type MermaidAsset, wordagentApi } from './wordagentApi'
import { companyStorageKey, getAuthToken } from './auth'
import { CaseReferenceSelector } from './components/CaseReferenceSelector'

function authenticatedHeaders() {
  const headers = new Headers()
  const token = getAuthToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

const font: CSSProperties = { fontFamily: "'Noto Sans SC', sans-serif" }
const CHART_TYPES = [
  ['flowchart', '流程图'],
  ['sequence', '时序图'],
  ['gantt', '甘特图'],
  ['er', 'ER图'],
  ['mindmap', '思维导图'],
  ['c4', 'C4架构图'],
] as const

type MermaidRuntime = typeof mermaid
type ImageStrategy = 'none' | 'rag_auto' | 'project_only' | 'valid_only' | 'category_only'
type LengthMode = 'shrink' | 'expand' | 'default'
type ExportScope = 'all' | 'selected' | 'current' | 'content'
type CatalogGenerationProgress = {
  completed: number
  currentIndex: number
  versionName: string
  stage: string
  preview: string
  savedChapters: number
}
type MarkdownTableTarget = { block: string; start: number; end: number }
type TableContextMenuState = MarkdownTableTarget & { x: number; y: number }
type GenerationProgress = { chapterTitle: string; current: number; total: number; stage: 'writing' | 'adjusting'; success: number; failure: number; skipped: number; status: 'running' | 'completed' | 'interrupted' | 'failed' }
type GenerationPreview = { chapterId: number; chapterTitle: string; markdown: string; status: 'streaming' | 'adjusting' | 'completed' | 'failed' | 'interrupted'; result?: GraphicSection }
type GenerationStats = { chapterCount: number; wordCount: number; estimatedPages: number; successCount: number; failureCount: number; withinTolerance?: boolean }
type ImageSubtypeOption = { value: string; label: string; group: string }
type OrchestrationSettings = { targetWords?: string; targetPages?: string; lengthMode?: LengthMode; bidScenario?: string; lengthLevel?: string; formatTemplateId?: string; templateId?: string; formatReferenceName?: string }

function readOrchestrationSettings(): OrchestrationSettings {
  try {
    const legacy = JSON.parse(window.localStorage.getItem(companyStorageKey('new_biaoshuai.orchestrationSettings')) || '{}')
    const format = JSON.parse(window.localStorage.getItem(companyStorageKey('new_biaoshuai.formatSettings')) || '{}')
    const legacyValue = legacy && typeof legacy === 'object' ? legacy as OrchestrationSettings : {}
    const formatValue = format && typeof format === 'object' ? format as { templateId?: string; referenceName?: string } : {}
    return { ...legacyValue, templateId: formatValue.templateId || legacyValue.templateId, formatReferenceName: formatValue.referenceName || undefined }
  } catch {
    return {}
  }
}
const IMAGE_SUBTYPE_OPTIONS: ImageSubtypeOption[] = [
  { value: '营业执照', label: '营业执照', group: '资质证书类' },
  { value: '食品经营许可证', label: '食品经营许可证', group: '资质证书类' },
  { value: '有机产品认证', label: '有机产品认证', group: '资质证书类' },
  { value: 'ISO体系认证', label: '体系认证', group: '资质证书类' },
  { value: '检测报告', label: '检测报告', group: '资质证书类' },
  { value: '身份证', label: '人员身份证', group: '人员证件类' },
  { value: '健康证', label: '健康证', group: '人员证件类' },
  { value: '从业资格证', label: '从业资格证', group: '人员证件类' },
  { value: '安全员证', label: '安全员证', group: '人员证件类' },
  { value: '岗位证书', label: '岗位证书', group: '人员证件类' },
  { value: '冷链配送车', label: '冷链配送车', group: '车辆资产类' },
  { value: '普通货车', label: '普通货车', group: '车辆资产类' },
  { value: '车辆行驶证', label: '车辆行驶证', group: '车辆资产类' },
  { value: '车辆保险单', label: '车辆保险单', group: '车辆资产类' },
  { value: '增值税发票', label: '增值税发票', group: '票据凭证类' },
  { value: '银行转账凭证', label: '银行转账凭证', group: '票据凭证类' },
  { value: '缴费证明', label: '缴费证明', group: '票据凭证类' },
  { value: '保证金单据', label: '保证金单据', group: '票据凭证类' },
  { value: '冷库仓储', label: '冷库仓储', group: '场景现场类' },
  { value: '分拣中心', label: '分拣中心', group: '场景现场类' },
  { value: '门店实景', label: '门店实景', group: '场景现场类' },
  { value: '配送现场', label: '配送现场', group: '场景现场类' },
  { value: '其他附件', label: '其他附件', group: '其他附件类' },
]
const TABLE_FIELD_SETS: Record<BidTableType, string[]> = {
  qualification: ['certificate_name', 'issuing_authority', 'certificate_number', 'valid_until', 'remark'],
  personnel: ['name', 'role', 'certificate_name', 'certificate_number', 'valid_until', 'years_experience'],
  vehicle: ['vehicle_type', 'plate_number', 'approved_load', 'quantity', 'inspection_expire_date', 'cold_chain_equipment', 'remark'],
  equipment: ['equipment_name', 'model', 'quantity', 'condition'],
  performance: ['project_name', 'client_name', 'project_date', 'project_scale', 'remark'],
  financial: ['year', 'revenue', 'total_assets', 'net_profit', 'audit_firm'],
}

declare global {
  interface Window { mermaid?: MermaidRuntime }
}

function apiErrorMessage(error: unknown, fallback: string) {
  const raw = error instanceof Error ? error.message : String(error ?? '')
  try {
    const parsed = JSON.parse(raw)
    return parsed?.message || parsed?.detail || fallback
  } catch {
    return raw || fallback
  }
}

function tableFieldLabel(field: string) {
  const labels: Record<string, string> = {
    certificate_name: '证书名称', issuing_authority: '发证机关', certificate_number: '证书编号', valid_until: '有效期', holder_name: '持证主体', remark: '备注',
    name: '姓名', role: '岗位', years_experience: '从业年限', vehicle_type: '车辆类型', plate_number: '车牌号', approved_load: '核定载重', inspection_expire_date: '年检有效期', cold_chain_equipment: '冷链配置',
    project_name: '项目名称', client_name: '甲方单位', project_date: '项目时间', project_scale: '项目规模', year: '年度', revenue: '营业收入', total_assets: '资产总额', net_profit: '净利润', audit_firm: '审计机构',
    equipment_name: '设备名称', model: '型号规格', quantity: '数量', condition: '状态',
  }
  return labels[field] || field
}

function TopNavLite({ onHome }: { onHome: () => void }) {
  return (
    <header className="top-nav">
      <button className="logo-button" type="button" onClick={onHome}>木石科技</button>
      <AccountNavActions onHome={onHome} />
    </header>
  )
}

export function MermaidDiagramStep({ bidId, onHome, onBack, onNext }: { bidId: number | null; onHome: () => void; onBack: () => void; onNext: () => void }) {
  const [sourceText, setSourceText] = useState('')
  const [chartType, setChartType] = useState('flowchart')
  const [chartTitle, setChartTitle] = useState('')
  const [mermaidCode, setMermaidCode] = useState('flowchart LR\n    A[需求分析] --> B[方案设计]\n    B --> C[组织实施]\n    C --> D[验收交付]')
  const [previewSvg, setPreviewSvg] = useState('')
  const [previewImageData, setPreviewImageData] = useState('')
  const [assets, setAssets] = useState<MermaidAsset[]>([])
  const [saveToRag, setSaveToRag] = useState(false)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [previewScale, setPreviewScale] = useState(1)
  const [viewerOpen, setViewerOpen] = useState(false)
  const [viewerScale, setViewerScale] = useState(1)
  const [viewerOffset, setViewerOffset] = useState({ x: 0, y: 0 })
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  const previewSize = useMemo(() => previewSvg ? getSvgNaturalSize(previewSvg) : { width: 980, height: 560 }, [previewSvg])
  const previewDisplaySvg = useMemo(() => previewSvg ? normalizeSvgForDisplay(previewSvg, previewSize.width, previewSize.height) : '', [previewSvg, previewSize.width, previewSize.height])

  async function refreshAssets() {
    if (!bidId) return
    const res = await wordagentApi.listMermaidAssets(bidId)
    if (res.code === 0) setAssets(res.data.assets)
  }
  useEffect(() => { void refreshAssets() }, [bidId])

  async function generateAndPreview() {
    if (!bidId) { setError('请先创建或进入一个标书项目。'); return }
    if (!sourceText.trim() && !mermaidCode.trim()) { setError('请先输入业务描述或 Mermaid 源码。'); return }
    setLoading(true); setError(''); setNotice('')
    try {
      let finalCode = mermaidCode.trim()
      let finalTitle = chartTitle.trim() || '标书图表'
      let generatedByApi = false
      if (sourceText.trim()) {
        const res = await wordagentApi.generateMermaidCode(bidId, { source_text: sourceText, chart_type: chartType, chart_title: chartTitle || null })
        if (res.code === 0) {
          finalCode = res.data.mermaid_code
          finalTitle = res.data.chart_title || finalTitle
          generatedByApi = res.data.used_api
          setChartTitle(finalTitle)
          setMermaidCode(finalCode)
        }
      }
      const svg = await renderMermaidToSvg(finalCode)
      setPreviewSvg(svg)
      setPreviewScale(1)
      const imageData = await svgToPngDataUrl(svg, { scale: 3, padding: 20 }).catch(() => `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`)
      setPreviewImageData(imageData)
      const res = await wordagentApi.renderMermaidAsset(bidId, { chart_title: finalTitle, chart_type: chartType, mermaid_code: finalCode, source_text: sourceText, chart_image_data: imageData, save_to_rag: saveToRag })
      if (res.code === 0) {
        const genText = sourceText.trim() ? (generatedByApi ? 'AI已生成代码，' : '本地规则已生成代码，') : '源码已渲染，'
        setNotice(`${genText}${saveToRag ? '图表已保存并写入企业知识库。' : '图表已保存到当前标书项目。'}`)
        await refreshAssets()
      }
    } catch (err) {
      setError(apiErrorMessage(err, 'Mermaid语法或渲染失败，请修改源码后重试。'))
    } finally {
      setLoading(false)
    }
  }

  function downloadPng() {
    if (!previewSvg) { setError('请先生成并预览图表。'); return }
    void svgToPngDataUrl(previewSvg, { scale: 3, padding: 20 }).then(dataUrl => triggerDownload(dataUrl, `${chartTitle.trim() || '标书图表'}.png`)).catch(() => {
      if (previewImageData) triggerDownload(previewImageData, `${chartTitle.trim() || '标书图表'}.png`)
      else setError('高清 PNG 导出失败，请重新生成图表后再试。')
    })
  }

  function downloadSvg() {
    if (!previewSvg) { setError('请先生成并预览图表。'); return }
    const blob = new Blob([previewSvg], { type: 'image/svg+xml;charset=utf-8' })
    triggerDownload(URL.createObjectURL(blob), `${chartTitle.trim() || '标书图表'}.svg`, true)
  }

  async function copyCode() {
    if (!mermaidCode.trim()) { setError('暂无可复制的 Mermaid 源码。'); return }
    try {
      await navigator.clipboard.writeText(mermaidCode)
      setNotice('Mermaid 源码已复制。')
    } catch {
      setError('复制失败，请手动选择源码复制。')
    }
  }

  function openViewer() {
    if (!previewSvg) return
    setViewerScale(1)
    setViewerOffset({ x: 0, y: 0 })
    setViewerOpen(true)
  }

  function zoomPreview(delta: number) {
    setPreviewScale(value => Number(clamp(value + delta, 0.35, 4).toFixed(2)))
  }

  function zoomViewer(delta: number) {
    setViewerScale(value => Number(clamp(value + delta, 0.2, 5).toFixed(2)))
  }

  function handlePreviewWheel(event: ReactWheelEvent<HTMLButtonElement>) {
    if (!previewSvg) return
    event.preventDefault()
    const ratio = event.deltaY > 0 ? 0.9 : 1.12
    setPreviewScale(value => Number(clamp(value * ratio, 0.35, 4).toFixed(3)))
  }

  function handleViewerWheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.preventDefault()
    const next = clamp(viewerScale * (event.deltaY > 0 ? 0.9 : 1.12), 0.2, 5)
    setViewerScale(Number(next.toFixed(3)))
  }

  function handleViewerPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    dragRef.current = { x: event.clientX, y: event.clientY, ox: viewerOffset.x, oy: viewerOffset.y }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function handleViewerPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current
    if (!drag) return
    setViewerOffset({ x: drag.ox + event.clientX - drag.x, y: drag.oy + event.clientY - drag.y })
  }

  function handleViewerPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    dragRef.current = null
    try { event.currentTarget.releasePointerCapture(event.pointerId) } catch { /* ignore */ }
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--page-bg)' }}>
      <TopNavLite onHome={onHome} />
      <main className="module-page graphic-module-page">
        <section className="module-header-card">
          <div><p>GRAPHIC BID</p><h1>Mermaid图表生成</h1><span>将业务描述转换为可编辑 Mermaid 图表，图表资产严格绑定当前标书项目。</span></div>
          <button className="light-btn" type="button" onClick={onBack}>返回首页</button>
        </section>
        <section className="graphic-mermaid-layout">
          <div className="form-card graphic-panel">
            <h3>图表配置</h3>
            <label>图表标题<input value={chartTitle} onChange={event => setChartTitle(event.target.value)} placeholder="例如：项目实施流程图" /></label>
            <label>图表类型<select value={chartType} onChange={event => setChartType(event.target.value)}>{CHART_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>业务描述<textarea value={sourceText} onChange={event => setSourceText(event.target.value)} placeholder="粘贴技术方案、架构逻辑、流程机制、组织关系等业务描述。" /></label>
            <label>Mermaid 源码<textarea className="graphic-code-editor" value={mermaidCode} onChange={event => setMermaidCode(event.target.value)} /></label>
            <label className="graphic-check"><input type="checkbox" checked={saveToRag} onChange={event => setSaveToRag(event.target.checked)} />将该图表存入企业RAG知识库</label>
            <button className="dark-btn" type="button" disabled={loading} onClick={() => void generateAndPreview()}>{loading ? '生成中...' : '生成并预览图表'}</button>
            <button className="plain-btn graphic-next-btn" type="button" onClick={onNext}>进入图文标书正文生成</button>
          </div>
          <div className="form-card graphic-panel graphic-preview-panel graphic-mermaid-stage">
            <div className="graphic-preview-toolbar"><h3>图表实时预览</h3><div><span className="graphic-zoom-controls"><button className="light-btn" type="button" disabled={!previewSvg} onClick={() => zoomPreview(-0.2)}>缩小</button><span className="graphic-zoom-value">{Math.round(previewScale * 100)}%</span><button className="light-btn" type="button" disabled={!previewSvg} onClick={() => zoomPreview(0.2)}>放大</button><button className="light-btn" type="button" disabled={!previewSvg} onClick={() => setPreviewScale(1)}>适应</button></span><button className="light-btn" type="button" onClick={downloadPng}>下载 PNG 图片</button><button className="light-btn" type="button" onClick={downloadSvg}>导出 SVG</button><button className="light-btn" type="button" onClick={() => void copyCode()}>复制 Mermaid 源码</button></div></div>
            {notice && <div className="knowledge-notice">{notice}</div>}
            {error && <div className="graphic-error">{error}</div>}
            <button className="graphic-mermaid-preview graphic-mermaid-preview-large" type="button" onClick={openViewer} onWheel={handlePreviewWheel}>{previewDisplaySvg ? <div className="graphic-mermaid-preview-inner" style={{ width: `${Math.max(80, Math.round(previewSize.width * previewScale))}px` }} dangerouslySetInnerHTML={{ __html: previewDisplaySvg }} /> : <span>点击“生成并预览图表”后在此展示大图。</span>}</button>
            <div className="graphic-preview-footer"><strong>{chartTitle || '未命名图表'}</strong><span>{CHART_TYPES.find(([value]) => value === chartType)?.[1] || chartType}</span></div>
            <AssetStrip assets={assets} />
          </div>
        </section>
      </main>
      {viewerOpen && <div className="graphic-viewer-modal" role="dialog" aria-modal="true" aria-label="Mermaid 图表全屏查看器">
        <div className="graphic-viewer-toolbar"><strong>{chartTitle || 'Mermaid 图表'}</strong><div><button className="light-btn" type="button" onClick={() => zoomViewer(-0.2)}>缩小</button><span className="graphic-zoom-value">{Math.round(viewerScale * 100)}%</span><button className="light-btn" type="button" onClick={() => zoomViewer(0.2)}>放大</button><button className="light-btn" type="button" onClick={downloadPng}>下载 PNG</button><button className="light-btn" type="button" onClick={() => { setViewerScale(1); setViewerOffset({ x: 0, y: 0 }) }}>1:1 原始尺寸</button><button className="light-btn" type="button" onClick={() => setViewerOpen(false)}>关闭</button></div></div>
        <div className="graphic-viewer-canvas" onWheel={handleViewerWheel} onPointerDown={handleViewerPointerDown} onPointerMove={handleViewerPointerMove} onPointerUp={handleViewerPointerUp} onPointerCancel={handleViewerPointerUp}>
          <div className="graphic-viewer-art" style={{ width: `${Math.round(previewSize.width)}px`, transform: `translate(${viewerOffset.x}px, ${viewerOffset.y}px) scale(${viewerScale})` }} dangerouslySetInnerHTML={{ __html: previewDisplaySvg }} />
        </div>
      </div>}
    </div>
  )
}

export function GraphicSectionStep({ bidId, onHome, onBackToCatalog, onBackToMermaid, embeddedInP1 = false }: { bidId: number | null; onHome: () => void; onBackToCatalog: () => void; onBackToMermaid: () => void; embeddedInP1?: boolean }) {
  const { requestText, dialog: textInputDialog } = useTextInputDialog(bidId)
  const { company } = useAuth()
  const companyExportTemplate = /^(?:示例公司|示例农产品加工配送有限公司)$/.test(company?.company_name || '') ? 'enterprise-header' : 'default-business'
  const orchestrationSettings = useState(readOrchestrationSettings)[0]
  const [catalog, setCatalog] = useState<BidCatalogResponse | null>(null)
  const [catalogVersionId, setCatalogVersionId] = useState<number | null>(null)
  const [assets, setAssets] = useState<MermaidAsset[]>([])
  const [selectedChapterId, setSelectedChapterId] = useState<number | null>(null)
  const [generationSelectedChapterIds, setGenerationSelectedChapterIds] = useState<number[]>([])
  const [lengthMode, setLengthMode] = useState<LengthMode>(orchestrationSettings.lengthMode || 'default')
  const [lengthTargetWords, setLengthTargetWords] = useState(orchestrationSettings.targetWords || '')
  const [lengthTargetPages, setLengthTargetPages] = useState(orchestrationSettings.targetPages || '')
  const [generationProgress, setGenerationProgress] = useState<GenerationProgress | null>(null)
  const [generationFailures, setGenerationFailures] = useState<string[]>([])
  const [generationReviews, setGenerationReviews] = useState<string[]>([])
  const [generationStats, setGenerationStats] = useState<GenerationStats | null>(null)
  const [wordLimit, setWordLimit] = useState('1200')
  const [ragEnable, setRagEnable] = useState(true)
  const [imageStrategy, setImageStrategy] = useState<ImageStrategy>('rag_auto')
  const [imageSubtypes, setImageSubtypes] = useState<string[]>([])
  const [formatTemplates, setFormatTemplates] = useState<GraphicFormatTemplate[]>([])
  const [formatTemplateId, setFormatTemplateId] = useState(orchestrationSettings.formatTemplateId || 'default-business')
  const defaultExportTemplate = (orchestrationSettings.templateId || (companyExportTemplate === 'enterprise-header' ? companyExportTemplate : orchestrationSettings.formatTemplateId || companyExportTemplate)) as DocxExportTemplateId
  const [exportTemplateId, setExportTemplateId] = useState<DocxExportTemplateId>(defaultExportTemplate)
  const [bidderName, setBidderName] = useState(() => typeof window === 'undefined' ? '' : window.localStorage.getItem(companyStorageKey('bidagent.bidderName')) || '')
  const [selectedCaseProjectId, setSelectedCaseProjectId] = useState<number | null | undefined>(undefined)
  const [templatePreviewOpen, setTemplatePreviewOpen] = useState(false)
  const [customTemplates, setCustomTemplates] = useState<CustomWordTemplateSummary[]>([])
  const [templateManagerOpen, setTemplateManagerOpen] = useState(false)
  const [templateManagerBusy, setTemplateManagerBusy] = useState(false)
  const [templateManagerError, setTemplateManagerError] = useState('')
  const [templateDragActive, setTemplateDragActive] = useState(false)
  const [blindCheck, setBlindCheck] = useState(false)
  const [blindTerms, setBlindTerms] = useState(() => typeof window === 'undefined' ? '' : window.localStorage.getItem(companyStorageKey('new_biaoshuai.blindTerms')) || '')
  const [blindReview, setBlindReview] = useState<{ report: BlindCheckReport; resolve: (value: boolean) => void } | null>(null)
  const [reviewedImages, setReviewedImages] = useState<number[]>([])
  const [reviewedDocument, setReviewedDocument] = useState(false)
  const [exportingDocx, setExportingDocx] = useState(false)
  const [exportProgressMessage, setExportProgressMessage] = useState('')
  const [illustrationCaption, setIllustrationCaption] = useState('')
  const [illustrationCategory, setIllustrationCategory] = useState('现场场景类')
  const [illustrationWidthMode, setIllustrationWidthMode] = useState<'center' | 'wide'>('center')
  const [illustrationSaveToKnowledge, setIllustrationSaveToKnowledge] = useState(false)
  const [illustrationUploading, setIllustrationUploading] = useState(false)
  const [markdown, setMarkdown] = useState('')
  const [editRevision, setEditRevision] = useState<number | null>(null)
  const [collabEditable, setCollabEditable] = useState(false)
  const [collabBusy, setCollabBusy] = useState(false)
  const [savingSection, setSavingSection] = useState(false)
  const [saveConflict, setSaveConflict] = useState(false)
  const saveInFlight = useRef(false)
  const currentEditor = useRef({ bidId, selectedChapterId, markdown })
  currentEditor.current = { bidId, selectedChapterId, markdown }
  const [markdownDirty, setMarkdownDirty] = useState(false)
  const [exportScope, setExportScope] = useState<ExportScope>('all')
  const [exportIncludeImages, setExportIncludeImages] = useState(true)
  const [exportIncludeToc, setExportIncludeToc] = useState(true)
  const [formatBrushPrefix, setFormatBrushPrefix] = useState<string | null>(null)
  const [sectionCharts, setSectionCharts] = useState<MermaidAsset[]>([])
  const [sectionImages, setSectionImages] = useState<KnowledgeAsset[]>([])
  const [selectedAssetIds, setSelectedAssetIds] = useState<number[]>([])
  const [selectedChunkIds, setSelectedChunkIds] = useState<number[]>([])
  const [bidScenario, setBidScenario] = useState(orchestrationSettings.bidScenario || '')
  const [lengthLevel, setLengthLevel] = useState(orchestrationSettings.lengthLevel || '')
  const [assetRisks, setAssetRisks] = useState<AssetRisk[]>([])
  const [documentRisks, setDocumentRisks] = useState<AssetRisk[] | null>(null)
  const [riskPanelOpen, setRiskPanelOpen] = useState(false)
  const [exportRiskAudit, setExportRiskAudit] = useState<AssetAuditResult | null>(null)
  const [exportRiskPromptOpen, setExportRiskPromptOpen] = useState(false)
  const [auditingAssets, setAuditingAssets] = useState(false)
  const [riskLocateTargetId, setRiskLocateTargetId] = useState<number | null>(null)
  const [tableType, setTableType] = useState<BidTableType>('qualification')
  const [tableEmbedImages, setTableEmbedImages] = useState(false)
  const [tableFill, setTableFill] = useState<BidTableFillResult | null>(null)
  const [tableModalOpen, setTableModalOpen] = useState(false)
  const [markdownTableTarget, setMarkdownTableTarget] = useState<MarkdownTableTarget | null>(null)
  const [tableContextMenu, setTableContextMenu] = useState<TableContextMenuState | null>(null)
  const [tableBusy, setTableBusy] = useState(false)
  const [verification, setVerification] = useState<AssetVerificationResult | null>(null)
  const [verificationBusy, setVerificationBusy] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [uploadPercent, setUploadPercent] = useState(0)
  const [parseStatus, setParseStatus] = useState<BidParseStatus | null>(null)
  const [catalogBusy, setCatalogBusy] = useState(false)
  const catalogGenerating = catalogBusy || catalog?.generation_status === 'processing'
  const [catalogProgress, setCatalogProgress] = useState<CatalogGenerationProgress | null>(null)
  const [initialLoading, setInitialLoading] = useState(true)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const streamRef = useRef<AbortController | null>(null)
  const catalogStreamRef = useRef<AbortController | null>(null)
  const [generationPreview, setGenerationPreview] = useState<GenerationPreview | null>(null)
  const editorPanelRef = useRef<HTMLElement | null>(null)
  const editorRef = useRef<HTMLTextAreaElement | null>(null)
  const markdownDirtyRef = useRef(false)
  const illustrationInputRef = useRef<HTMLInputElement | null>(null)
  const customTemplateInputRef = useRef<HTMLInputElement | null>(null)
  const [imageViewer, setImageViewer] = useState<KnowledgeAsset | null>(null)
  const [imageViewerScale, setImageViewerScale] = useState(1)
  const [replaceTargetId, setReplaceTargetId] = useState<number | null>(null)
  const [replacementAssets, setReplacementAssets] = useState<KnowledgeAsset[]>([])
  const [replacementLoading, setReplacementLoading] = useState(false)
  const activeVersion = useMemo(() => {
    if (!catalog?.versions?.length) return null
    return catalog.versions.find(item => item.version_id === catalogVersionId) ?? catalog.versions.find(item => item.is_final) ?? catalog.versions[0]
  }, [catalog, catalogVersionId])
  const chapters = useMemo(() => flattenCatalog(activeVersion?.catalog_content ?? []), [activeVersion])
  const tenderFormatGaps = useMemo(() => catalog && activeVersion ? getTenderFormatGaps(catalog, activeVersion.version_id) : [], [catalog, activeVersion])
  const selectedChapter = chapters.find(chapter => chapter.chapter_id === selectedChapterId) || null
  const leafChapterIds = useMemo(() => chapters.filter(chapter => !chapter.children?.length).map(chapter => chapter.chapter_id), [chapters])
  const savedChapterIds = useMemo(() => new Set(catalog?.graphic_body_chapter_ids), [catalog?.graphic_body_chapter_ids])
  const missingChapterIds = useMemo(() => leafChapterIds.filter(id => !savedChapterIds.has(id)), [leafChapterIds, savedChapterIds])
  const processedChapters = generationProgress ? generationProgress.success + generationProgress.failure + generationProgress.skipped : 0
  const selectedLeafChapterIds = useMemo(() => generationSelectedChapterIds.filter(chapterId => leafChapterIds.includes(chapterId)), [generationSelectedChapterIds, leafChapterIds])
  const selectedExportTemplate = useMemo(() => {
    const custom = customTemplates.find(template => template.id === exportTemplateId)
    if (custom) return {
      id: custom.id as never,
      name: custom.name,
      description: custom.description,
      scenario: custom.scenario,
      filePath: '',
      font: '按上传模板样式',
      fontSize: '按上传模板样式',
      lineSpacing: '按上传模板样式',
      headingLevels: '按上传模板样式',
      structure: `${custom.formatRules.page.paper}；${custom.formatRules.page.orientation}；${custom.formatRules.page.headerFooter}`,
      placeholders: custom.placeholders.map(item => `{${item}}`),
    }
    return getBidTemplateById(exportTemplateId as BidTemplateId)
  }, [customTemplates, exportTemplateId])

  function updateEditedMarkdown(next: string) {
    if (generating || collabBusy || !collabEditable) return
    markdownDirtyRef.current = true
    setMarkdownDirty(true)
    setMarkdown(next)
  }

  async function refreshMermaidAssets() {
    if (!bidId) return
    try {
      const assetRes = await wordagentApi.listMermaidAssets(bidId)
      if (assetRes.code === 0) setAssets(assetRes.data.assets)
    } catch {
      // Mermaid assets are supplemental to正文页面 and must not block entry.
    }
  }

  async function refresh() {
    if (!bidId) {
      setInitialLoading(false)
      return
    }
    try {
      const [catalogRes, templateRes, parseRes] = await Promise.all([wordagentApi.getBidCatalog(bidId), wordagentApi.listGraphicFormatTemplates(bidId), wordagentApi.getBidParseStatus(bidId)])
      if (catalogRes.code === 0) {
        setCatalog(catalogRes.data)
        const defaultVersion = catalogRes.data.versions.find(item => item.is_final) ?? catalogRes.data.versions[0]
        setCatalogVersionId(current => catalogRes.data.versions.some(item => item.version_id === current) ? current : defaultVersion?.version_id ?? null)
        const flat = flattenCatalog(defaultVersion?.catalog_content ?? [])
        setSelectedChapterId(current => current || flat[0]?.chapter_id || null)
      }
      if (templateRes.code === 0) setFormatTemplates(templateRes.data.templates)
      if (parseRes.code === 0) setParseStatus(parseRes.data)
      void refreshMermaidAssets()
    } catch (err) {
      setError(apiErrorMessage(err, '目录加载失败，请稍后重试'))
    } finally {
      setInitialLoading(false)
    }
  }
  useEffect(() => {
    setGenerationPreview(null)
    setGenerationProgress(null)
    setGenerationFailures([])
    setGenerationReviews([])
    setGenerating(false)
    setParseStatus(null)
    void refresh()
    return () => {
      const controller = streamRef.current; streamRef.current = null; controller?.abort()
      const catalogController = catalogStreamRef.current; catalogStreamRef.current = null; catalogController?.abort()
    }
  }, [bidId])
  useEffect(() => {
    if (!bidId || preparing || parseStatus?.parse_status !== 'processing') return
    let active = true
    const timer = window.setInterval(() => {
      void wordagentApi.getBidParseStatus(bidId).then(response => {
        if (active && response.code === 0) {
          setParseStatus(response.data)
          if (response.data.parse_status === 'failed') setError(response.data.parse_error_msg || '招标文件解析失败，请重新上传。')
        }
      }).catch(() => { /* Keep the last known stage during a transient request error. */ })
    }, 2000)
    return () => { active = false; window.clearInterval(timer) }
  }, [bidId, preparing, parseStatus?.parse_status])
  useEffect(() => {
    if (!bidId || catalogBusy || catalog?.generation_status !== 'processing') return
    let active = true
    const timer = window.setInterval(() => {
      void wordagentApi.getBidCatalog(bidId).then(response => {
        if (!active || response.code !== 0) return
        setCatalog(response.data)
        if (response.data.generation_status === 'completed') setNotice('目录已生成，可在左侧编辑后生成正文。')
        if (response.data.generation_status === 'failed') setError(response.data.message || '目录生成失败，请重试。')
      }).catch(() => { /* Retry while the background task is still running. */ })
    }, 2000)
    return () => { active = false; window.clearInterval(timer) }
  }, [bidId, catalogBusy, catalog?.generation_status])
  useEffect(() => {
    if (selectedCaseProjectId !== undefined) setExportTemplateId(defaultExportTemplate)
  }, [defaultExportTemplate, selectedCaseProjectId])
  useEffect(() => {
    let active = true
    let attempts = 0
    let retryTimer: number | undefined
    let startTimer: number | undefined
    async function syncTemplates() {
      try {
        const stored = await listCustomWordTemplates()
        if (!active) return
        setCustomTemplates(stored)
        // Reference templates are lightweight versions of the supplied bid
        // documents. Import them once so they are immediately selectable in
        // the existing local template manager.
        let latestTemplates = stored
        try {
          const references = await wordagentApi.listReferenceWordTemplates()
          if (references.code === 0) {
            for (const reference of references.data.templates) {
              const localId = `custom:${reference.id}`
              if (latestTemplates.some(template => template.id === localId)) continue
              const response = await fetch(wordagentApi.downloadReferenceWordTemplateUrl(reference.id), { credentials: 'include', headers: authenticatedHeaders() })
              if (!response.ok) continue
              const content = await response.arrayBuffer()
              await importRemoteWordTemplate(content, {
                id: localId,
                name: reference.name,
                fileName: reference.file_name,
                description: reference.description,
                scenario: reference.scenario,
                isDefault: false,
              })
              latestTemplates = await listCustomWordTemplates()
              if (!active) return
              setCustomTemplates(latestTemplates)
            }
          }
        } catch {
          // A reference template is optional and must not delay the editor.
        }
        // Reuse a locally imported project template. Downloading a complete DOCX
        // is secondary work and should happen at most once per browser profile.
        const localProjectTemplate = selectedCaseProjectId
          ? latestTemplates.find(template => template.id === `custom:project-${selectedCaseProjectId}`)
          : undefined
        if (!selectedCaseProjectId) {
          const defaultTemplate = latestTemplates.find(template => template.isDefault)
          if (defaultTemplate) setExportTemplateId(current => current.startsWith('custom:') ? current : defaultTemplate.id as DocxExportTemplateId)
          return
        }
        const projects = await wordagentApi.listKnowledgeProjects(50)
        const completedProjects = projects.code === 0
          ? projects.data.filter(item => item.project_id === selectedCaseProjectId && item.format_template?.status === 'completed' && item.format_template.id)
          : []
        const project = completedProjects[0]
        if (project?.format_template?.id) {
          if (localProjectTemplate?.description.includes(project.format_template.id)) {
            setExportTemplateId(localProjectTemplate.id as DocxExportTemplateId)
            return
          }
          const response = await fetch(wordagentApi.downloadKnowledgeProjectTemplateUrl(project.project_id), {
            credentials: 'include',
            headers: authenticatedHeaders(),
          })
          if (response.ok) {
            const content = await response.arrayBuffer()
            const imported = await importRemoteWordTemplate(content, {
              id: `custom:project-${project.project_id}`,
              name: project.format_template.name || `${project.project_name} 格式模板`,
              fileName: `${project.format_template.name || project.project_name}.docx`,
              description: `从项目“${project.project_name}”对应投标文件提取的格式模板；版本 ${project.format_template.id}`,
              scenario: project.format_template.source_file_name ? `来源：${project.format_template.source_file_name}` : '当前招投标项目',
              isDefault: Boolean(project.format_template.is_default),
            })
            if (active) {
              const latest = await listCustomWordTemplates()
              setCustomTemplates(latest)
              setExportTemplateId(imported.id as DocxExportTemplateId)
              setNotice(`已载入项目格式模板：${imported.name}`)
            }
            return
          }
        }
        if (attempts < 8 && active) {
          attempts += 1
          retryTimer = window.setTimeout(() => void syncTemplates(), 2000)
        } else {
          const defaultTemplate = latestTemplates.find(template => template.isDefault)
          if (defaultTemplate) setExportTemplateId(current => current.startsWith('custom:') ? current : defaultTemplate.id as DocxExportTemplateId)
        }
      } catch {
        if (attempts < 3 && active) {
          attempts += 1
          retryTimer = window.setTimeout(() => void syncTemplates(), 2500)
        }
      }
    }
    // Let the catalog/editor paint before IndexedDB and network work begins.
    startTimer = window.setTimeout(() => void syncTemplates(), 900)
    return () => {
      active = false
      if (startTimer) window.clearTimeout(startTimer)
      if (retryTimer) window.clearTimeout(retryTimer)
    }
  }, [selectedCaseProjectId])
  useEffect(() => {
    let cancelled = false
    setDocumentRisks(null)
    setEditRevision(null)
    setCollabEditable(false)
    setSaveConflict(false)
    if (!bidId || !selectedChapterId) {
      setMarkdown('')
      setSectionCharts([])
      setSectionImages([])
      setSelectedAssetIds([])
      setSelectedChunkIds([])
      setBidScenario('')
      setLengthLevel('')
      setAssetRisks([])
      return () => { cancelled = true }
    }
    setMarkdown('')
    setSectionCharts([])
    setSectionImages([])
    setSelectedAssetIds([])
    setSelectedChunkIds([])
    setBidScenario('')
    setLengthLevel('')
    setAssetRisks([])
    wordagentApi.getGraphicSection(bidId, selectedChapterId).then(res => {
      if (cancelled) return
      if (res.code === 0) {
        markdownDirtyRef.current = false
        setMarkdownDirty(false)
        setEditRevision(res.data.revision)
        setMarkdown(res.data.markdown || '')
        markdownDirtyRef.current = false
        setMarkdownDirty(false)
        setSectionCharts(res.data.charts || [])
        setSectionImages(res.data.images?.length ? res.data.images : res.data.selected_images || [])
        setSelectedAssetIds(res.data.selected_asset_ids || [])
        setSelectedChunkIds(res.data.selected_chunk_ids || [])
        setBidScenario(res.data.bid_scenario || '')
        setLengthLevel(res.data.length_level || '')
        setDocumentRisks(null)
        setAssetRisks(res.data.risks || [])
        if (res.data.risks?.length) setRiskPanelOpen(true)
      }
    }).catch(() => {
      if (cancelled) return
      setMarkdown('')
      setSectionCharts([])
      setSectionImages([])
      setAssetRisks([])
    })
    return () => { cancelled = true }
  }, [bidId, selectedChapterId])
  useEffect(() => {
    if (generating || collabBusy || savingSection || saveConflict || !collabEditable || !markdownDirty || !bidId || !selectedChapterId) return
    const timer = window.setTimeout(() => {
      void saveSection()
    }, 700)
    return () => window.clearTimeout(timer)
  }, [markdown, markdownDirty, bidId, selectedChapterId, generating, collabBusy, savingSection, saveConflict, collabEditable])
  useEffect(() => {
    if (!riskLocateTargetId || !selectedChapterId || !markdown) return
    const targetId = riskLocateTargetId
    const timer = window.setTimeout(() => {
      focusRiskImage(targetId)
      setRiskLocateTargetId(null)
    }, 80)
    return () => window.clearTimeout(timer)
  }, [riskLocateTargetId, selectedChapterId, markdown])
  useEffect(() => {
    if (!tableContextMenu) return
    const close = () => setTableContextMenu(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [tableContextMenu])

  function updateSavedChapter(chapterId: number, hasBody: boolean) {
    setCatalog(current => {
      if (!current?.graphic_body_chapter_ids) return current
      const ids = new Set(current.graphic_body_chapter_ids)
      if (ids.has(chapterId) === hasBody) return current
      if (hasBody) ids.add(chapterId)
      else ids.delete(chapterId)
      return { ...current, graphic_body_chapter_ids: Array.from(ids) }
    })
  }

  function applyGraphicSection(section: GraphicSection) {
    updateSavedChapter(section.chapter_id, Boolean(section.markdown?.trim()))
    setEditRevision(section.revision)
    setSaveConflict(false)
    setError('')
    markdownDirtyRef.current = false
    setMarkdownDirty(false)
    setMarkdown(section.markdown || '')
    setSectionCharts(section.charts || [])
    setSectionImages(section.images?.length ? section.images : section.selected_images || [])
    setSelectedAssetIds(section.selected_asset_ids || [])
    setSelectedChunkIds(section.selected_chunk_ids || [])
    setBidScenario(section.bid_scenario || '')
    setLengthLevel(section.length_level || '')
    setDocumentRisks(null)
    setAssetRisks(section.risks || [])
    if (section.risks?.length) setRiskPanelOpen(true)
  }

  function setGenerationSelectionForNodes(nodes: CatalogNode[]) {
    const ids = nodes.flatMap(getLeafChapterIds)
    setGenerationSelectedChapterIds(Array.from(new Set(ids)))
  }

  function selectChapterForEditing(chapterId: number) {
    if (generating || collabBusy || chapterId === selectedChapterId) return
    if (markdownDirtyRef.current || saveInFlight.current) {
      setError('当前章节尚未保存成功，请先保存；若有冲突，请下载本地草稿并对比最新版本。')
      return
    }
    setSelectedChapterId(chapterId)
  }

  function toggleGenerationChapter(chapterId: number, checked: boolean) {
    const chapter = chapters.find(item => item.chapter_id === chapterId)
    if (!chapter) return
    const branchIds = getLeafChapterIds(chapter)
    setGenerationSelectedChapterIds(current => {
      const next = new Set(current)
      branchIds.forEach(id => checked ? next.add(id) : next.delete(id))
      return Array.from(next)
    })
  }

  function selectGenerationPreset(kind: 'business' | 'service' | 'technical' | 'qualification') {
    const patterns = {
      business: /商务|报价|合同|付款|投标函|业绩|企业信息|资格/,
      service: /服务|售后|质量|履约|承诺|客户|应急|保障/,
      technical: /技术|实施|配送|冷链|温控|设备|系统|流程|食品安全|仓储|组织架构/,
      qualification: /资质|证明材料|许可证|营业执照|认证|资格证明/,
    }
    const matching = chapters.filter(chapter => patterns[kind].test(getCatalogPathText(chapter, chapters)))
    setGenerationSelectionForNodes(matching)
  }

  function getLengthTarget() {
    const words = Number(lengthTargetWords)
    const pages = Number(lengthTargetPages)
    if (lengthTargetWords && Number.isFinite(words) && words > 0) return Math.round(words)
    if (lengthTargetPages && Number.isFinite(pages) && pages > 0) return Math.round(pages * 780)
    return undefined
  }

  async function generateChapters(chapterIds: number[], rangeLabel: string, onlyMissing = false) {
    if (generating) return
    if (collabBusy || markdownDirtyRef.current || saveInFlight.current || saveConflict) { setError('请先保存本地编辑并解决版本冲突，再开始生成。'); return }
    if (!bidId || !chapterIds.length) {
      setError('请先选择要生成的章节。')
      return
    }
    if (imageStrategy === 'category_only' && imageSubtypes.length === 0) {
      setError('请至少选择一个细分类别后再生成。')
      return
    }
    const wordLimitValue = Number(wordLimit)
    if (!Number.isInteger(wordLimitValue) || wordLimitValue < 200 || wordLimitValue > 8000) {
      setError('默认单章节字数上限须为 200～8000 的整数。若要生成约 50000 字的整份标书，请在“目标字数”填写 50000，并批量生成多个章节。')
      setNotice('生成未开始，请先修改单章节字数上限。')
      setGenerationPreview(null)
      return
    }
    const ids = Array.from(new Set(chapterIds))
    const targetChapter = chapters.find(chapter => chapter.chapter_id === ids[0])
    const lengthTarget = onlyMissing ? undefined : getLengthTarget()
    if (lengthTarget !== undefined && (lengthTarget < ids.length || lengthTarget > ids.length * 8000)) {
      setError(`目标总字数需在 ${ids.length} 至 ${ids.length * 8000} 之间；单章节控制范围为1至8000字。`)
      return
    }
    const needsConfirm = !onlyMissing && (ids.length > 1 || (ids[0] === selectedChapterId && (Boolean(markdown.trim()) || markdownDirtyRef.current)))
    if (needsConfirm && !window.confirm(`即将按“${rangeLabel}”生成 ${ids.length} 个章节。已存在正文的章节会被覆盖；缩写或扩写模式会基于已有正文调整。确认继续？`)) return

    streamRef.current?.abort()
    const controller = new AbortController()
    streamRef.current = controller
    setGenerating(true)
    setGenerationProgress({ chapterTitle: '准备生成', current: 0, total: ids.length, stage: 'writing', success: 0, failure: 0, skipped: 0, status: 'running' })
    setGenerationPreview({ chapterId: ids[0], chapterTitle: targetChapter?.title || '当前章节', markdown: '', status: 'streaming' })
    setGenerationStats(null)
    setGenerationFailures([])
    setGenerationReviews([])
    setError('')
    setNotice('正在准备章节上下文与知识库素材...')
    markdownDirtyRef.current = false
    setMarkdownDirty(false)
    const failureMessages: string[] = []
    const completedSections = new Map<number, GraphicSection>()
    const failedChapterIds = new Set<number>()
    const skippedChapterIds = new Set<number>()
    let batchCounts: { success: number; failure: number; skipped: number } | null = null
    const url = wordagentApi.generateGraphicSectionStreamUrl(bidId, {
      chapter_id: ids[0], chapter_ids: ids, word_limit: wordLimitValue, bidder_name: bidderName,
      only_missing: onlyMissing, length_target: lengthTarget, length_mode: onlyMissing ? 'default' : lengthMode, rag_enable: ragEnable,
      image_strategy: imageStrategy,
      asset_sub_type: imageStrategy === 'category_only' ? imageSubtypes : undefined,
      priority_expire: imageStrategy === 'valid_only', exclude_ids: [],
      chapter_theme: ids.length === 1 && targetChapter ? targetChapter.number + ' ' + stripCatalogNumber(targetChapter.title) : undefined,
      format_template_id: formatTemplateId,
      selected_asset_ids: !onlyMissing && ids.length === 1 ? selectedAssetIds : undefined,
      selected_chunk_ids: !onlyMissing && ids.length === 1 ? selectedChunkIds : undefined,
      bid_scenario: !onlyMissing && ids.length === 1 ? bidScenario || null : undefined,
      length_level: !onlyMissing && ids.length === 1 && lengthMode === 'default' ? lengthLevel || null : undefined,
    })
    try {
      await readEventStream(url, {
        signal: controller.signal,
        terminalEvents: ['batch_done', 'done'],
        onEvent: (event, data) => {
          if (streamRef.current !== controller || controller.signal.aborted) return
          const chapterId = Number(data.chapter_id || ids[0])
          if (event === 'progress') {
            setGenerationProgress(current => current ? { ...current, chapterTitle: data.chapter_title || '当前章节', current: Number(data.current || 0), total: Number(data.total || ids.length), stage: 'writing' } : current)
            setGenerationPreview({ chapterId, chapterTitle: data.chapter_title || '当前章节', markdown: '', status: 'streaming' })
            setNotice('正在生成：' + (data.chapter_title || '当前章节'))
          } else if (event === 'retrying') {
            setGenerationPreview(current => current ? { ...current, markdown: '' } : current)
            setNotice(`模型响应异常，正在重试本章（第 ${data.attempt}/${data.max_attempts} 次）`)
          } else if (event === 'chunk') {
            setGenerationPreview(current => ({
              chapterId, chapterTitle: current?.chapterTitle || '当前章节',
              markdown: (current?.chapterId === chapterId ? current.markdown : '') + (data.content || ''),
              status: data.phase === 'adjusting' ? 'adjusting' : 'streaming',
            }))
          } else if (event === 'adjusting') {
            setGenerationProgress(current => current ? { ...current, stage: 'adjusting' } : current)
            setGenerationPreview(current => current ? { ...current, markdown: '', status: 'adjusting', result: undefined } : current)
            setNotice('正在按篇幅目标校订正文...')
          } else if (event === 'replace') {
            setGenerationPreview(current => current ? { ...current, markdown: data.markdown || '' } : current)
          } else if (event === 'chapter_done' || event === 'done') {
            const result = data.result as GraphicSection | undefined
            if (!result || typeof result.markdown !== 'string') throw new Error('章节完成事件缺少最终正文，请重新加载章节')
            completedSections.set(chapterId, result)
            if (result.length_control?.within_tolerance === false) {
              const reason = result.length_control.review_reason || '篇幅未达到设定范围'
              setGenerationReviews(current => [...current, `${result.chapter_title || data.chapter_title || '当前章节'}：${reason}`])
            }
            updateSavedChapter(chapterId, Boolean(result.markdown.trim()))
            failedChapterIds.delete(chapterId)
            setGenerationProgress(current => current ? { ...current, success: completedSections.size, failure: failedChapterIds.size } : current)
            setGenerationPreview({ chapterId, chapterTitle: data.chapter_title || result.chapter_title || '当前章节', markdown: result.markdown, status: 'completed', result })
            if (event === 'done') setGenerationStats({ chapterCount: 1, wordCount: Number(data.result.word_count || 0), estimatedPages: Math.ceil(Number(data.result.word_count || 0) / 780), successCount: 1, failureCount: 0, withinTolerance: result.length_control?.within_tolerance })
          } else if (event === 'chapter_error') {
            failureMessages.push((data.chapter_title || '章节' + chapterId) + '：' + (data.message || '生成失败'))
            setGenerationFailures([...failureMessages])
            failedChapterIds.add(chapterId)
            setGenerationProgress(current => current ? { ...current, success: completedSections.size, failure: failedChapterIds.size } : current)
            setGenerationPreview(current => current ? { ...current, status: 'failed', result: undefined } : current)
          } else if (event === 'chapter_skipped') {
            skippedChapterIds.add(chapterId)
            updateSavedChapter(chapterId, true)
            setGenerationProgress(current => current ? { ...current, skipped: skippedChapterIds.size } : current)
            setGenerationPreview(null)
          } else if (event === 'batch_done') {
            const totalWords = Number(data.total_word_count || 0)
            batchCounts = { success: Number(data.success_count || 0), failure: Number(data.failure_count || 0), skipped: Number(data.skipped_count || 0) }
            setGenerationStats({ chapterCount: Number(data.success_count || 0), wordCount: totalWords, estimatedPages: Number(data.estimated_pages || Math.ceil(totalWords / 780)), successCount: Number(data.success_count || 0), failureCount: Number(data.failure_count || 0), withinTolerance: data.within_tolerance })
          } else if (event === 'server_error' || event === 'error') {
            throw new Error(data.message || '正文生成失败')
          }
        },
      })
      if (streamRef.current !== controller) return
      if (controller.signal.aborted) throw new DOMException('生成已停止', 'AbortError')
      const counts = batchCounts as { success: number; failure: number; skipped: number } | null
      const successCount = counts?.success ?? completedSections.size
      const failureCount = counts?.failure ?? failureMessages.length
      const skippedCount = counts?.skipped ?? skippedChapterIds.size
      setGenerationProgress(current => current ? { ...current, success: successCount, failure: failureCount, skipped: skippedCount, status: failureCount ? 'failed' : 'completed' } : current)
      setNotice(rangeLabel + (failureCount ? '未全部完成：已保存 ' : '结束：已保存 ') + successCount + ' 章，失败 ' + failureCount + ' 章' + (skippedCount ? '，已有正文跳过 ' + skippedCount + ' 章' : '') + '。')
      const firstCompleted = completedSections.entries().next().value
      if (firstCompleted) {
        setSelectedChapterId(firstCompleted[0])
        applyGraphicSection(firstCompleted[1])
      }
    } catch (err) {
      if (streamRef.current !== controller) return
      const interrupted = controller.signal.aborted
      setGenerationProgress(current => current ? { ...current, status: interrupted ? 'interrupted' : 'failed' } : current)
      setGenerationPreview(current => current && current.status !== 'completed' ? { ...current, status: interrupted ? 'interrupted' : 'failed', result: undefined } : current)
      if (interrupted) setNotice('已停止生成；完成的章节已保存，当前未完成内容仅保留为预览。')
      else {
        setError(apiErrorMessage(err, '生成连接中断；当前预览尚未完成，请重试。'))
        setNotice('正文生成失败，请按错误提示修改后重试。')
      }
    } finally {
      if (streamRef.current === controller) {
        streamRef.current = null
        setGenerating(false)
      }
    }
  }

  async function handleTenderFile(file: File | null) {
    if (!bidId || !file) return
    setPreparing(true); setUploadPercent(0); setParseStatus(null); setError(''); setNotice('正在上传招标文件...')
    try {
      await wordagentApi.uploadAndParseBidFileWithProgress(bidId, file, setUploadPercent)
      setNotice('上传完成，正在解析招标文件；解析完成后会自动生成目录。')
      for (let index = 0; index < 800; index += 1) {
        await delay(1500)
        const status = await wordagentApi.getBidParseStatus(bidId)
        setParseStatus(status.data)
        if (status.data.parse_status === 'completed') {
          setNotice('招标文件解析完成，正在生成目录...')
          await generateCatalog()
          return
        }
        if (status.data.parse_status === 'failed') throw new Error(status.data.parse_error_msg || '招标文件解析失败')
      }
      setNotice('招标文件仍在后台解析；页面会继续显示状态，解析完成后可重新生成目录。')
    } catch (err) {
      setError(apiErrorMessage(err, '招标文件上传或解析失败'))
    } finally {
      setPreparing(false)
    }
  }

  async function generateCatalog() {
    if (!bidId) return
    setCatalogBusy(true); setError('')
    const controller = new AbortController()
    catalogStreamRef.current = controller
    try {
      const parse = await wordagentApi.getBidParseStatus(bidId)
      setParseStatus(parse.data)
      if (parse.data.parse_status === 'processing') {
        setNotice(`招标文件仍在解析（${parse.data.parse_step_label || '处理中'}），完成后才能重新生成目录。`)
        return
      }
      if (parse.data.parse_status === 'failed' && parse.data.tender_file_name) {
        setError(parse.data.parse_error_msg || '招标文件解析失败，请重新上传后再生成目录。')
        return
      }
      setCatalogProgress({ completed: 0, currentIndex: 0, versionName: '', stage: '正在启动目录生成', preview: '', savedChapters: 0 })
      const started = await wordagentApi.regenerateBidCatalog(bidId)
      if (started.data.status === 'processing') {
        setNotice('目录正在实时生成，下方会逐步显示章节标题。')
        try {
          await readEventStream(wordagentApi.generateBidCatalogStreamUrl(bidId), {
            signal: controller.signal,
            onEvent: async (eventName, payload) => {
              const index = Math.min(Math.max(Number(payload.index) || 0, 0), 3)
              if (eventName === 'version_start') {
                setCatalogProgress(current => ({ ...current!, completed: Math.max(0, index - 1), currentIndex: index, versionName: String(payload.version_name || ''), stage: '正在准备', preview: '', savedChapters: 0 }))
              } else if (eventName === 'progress') {
                setCatalogProgress(current => ({ ...current!, currentIndex: index || current!.currentIndex, versionName: String(payload.version_name || current!.versionName), stage: String(payload.message || '正在生成'), preview: current!.preview }))
              } else if (eventName === 'delta') {
                const preview = String(payload.preview || '').slice(-12000)
                setCatalogProgress(current => current && preview !== current.preview ? { ...current, preview, stage: `正在生成${payload.version_name || current.versionName}` } : current)
              } else if (eventName === 'chapter') {
                setCatalogProgress(current => current ? { ...current, savedChapters: current.savedChapters + 1 } : current)
              } else if (eventName === 'version') {
                setCatalogProgress(current => current ? { ...current, completed: index || current.completed + 1, stage: `${payload.version_name || '当前方案'}已保存`, preview: '' } : current)
                const latest = await wordagentApi.getBidCatalog(bidId)
                if (latest.code === 0) setCatalog(latest.data)
              } else if (eventName === 'done') {
                setCatalogProgress(current => current ? { ...current, completed: 3, currentIndex: 3, stage: '三套目录均已生成', preview: '' } : current)
              }
            },
          })
        } catch (streamError) {
          if (controller.signal.aborted) throw streamError
          const latest = await wordagentApi.getBidCatalog(bidId)
          if (latest.data.generation_status !== 'processing') throw streamError
          setCatalogProgress(current => current ? { ...current, stage: '实时连接中断，后台仍在生成；正在查询任务状态' } : current)
          for (let index = 0; index < 800; index += 1) {
            await delay(1500)
            const status = await wordagentApi.getBidCatalog(bidId)
            setCatalog(status.data)
            if (status.data.generation_status === 'completed') break
            if (status.data.generation_status === 'failed') throw new Error(status.data.message || '目录生成失败')
            if (index === 799) throw new Error('目录仍在后台生成，请稍后返回本页查看结果。')
          }
        }
      }
      await refresh()
      setNotice('目录已生成，可在左侧直接编辑后生成正文。')
    } catch (err) {
      setError(apiErrorMessage(err, '目录生成失败'))
    } finally {
      if (catalogStreamRef.current === controller) catalogStreamRef.current = null
      setCatalogBusy(false)
    }
  }

  async function editChapter(action: 'rename' | 'sibling' | 'child' | 'delete' | 'up' | 'down' | 'upgrade' | 'downgrade') {
    if (!bidId || !selectedChapter || !activeVersion) return
    setError('')
    try {
      if (action === 'rename') {
        const title = await requestText('请输入新的章节标题', stripCatalogNumber(selectedChapter.title))
        if (!title?.trim()) return
        const res = await wordagentApi.updateCatalogChapter(bidId, selectedChapter.chapter_id, title.trim())
        if (res.code === 0) setCatalog(res.data)
      } else if (action === 'sibling') {
        const title = await requestText('请输入同级章节标题', '新增章节')
        if (!title?.trim()) return
        const res = await wordagentApi.createCatalogChapter(bidId, { version_id: activeVersion.version_id, parent_id: selectedChapter.parent_id || 0, chapter_title: title.trim(), level: selectedChapter.level, sort_order: (selectedChapter.sort_order || 0) + 1 })
        if (res.code === 0) setCatalog(res.data)
      } else if (action === 'child') {
        if (selectedChapter.level >= 3) return
        const title = await requestText('请输入子级章节标题', '新增子章节')
        if (!title?.trim()) return
        const res = await wordagentApi.createCatalogChapter(bidId, { version_id: activeVersion.version_id, parent_id: selectedChapter.chapter_id, chapter_title: title.trim(), level: (selectedChapter.level + 1) as 1 | 2 | 3 })
        if (res.code === 0) setCatalog(res.data)
      } else if (action === 'delete') {
        if (!window.confirm('确认删除当前章节及其子章节？')) return
        const res = await wordagentApi.deleteCatalogChapter(bidId, selectedChapter.chapter_id)
        if (res.code === 0) { setCatalog(res.data); setSelectedChapterId(flattenCatalog(res.data.versions.find(item => item.is_final)?.catalog_content ?? res.data.versions[0]?.catalog_content ?? [])[0]?.chapter_id || null) }
      } else {
        const res = await wordagentApi.moveCatalogChapter(bidId, selectedChapter.chapter_id, action)
        if (res.code === 0) setCatalog(res.data)
      }
    } catch (err) {
      setError(apiErrorMessage(err, '目录编辑失败'))
    }
  }

  function generateSection() {
    if (!selectedChapterId) return
    void generateChapters([selectedChapterId], '当前章节')
  }

  async function saveSection(markdownOverride?: string, imagesOverride?: KnowledgeAsset[]) {
    if (generating || collabBusy || saveInFlight.current || !bidId || !selectedChapterId || editRevision === null) return
    if (!collabEditable) { setError('当前章节已锁定或已分配给其他负责人。'); return }
    const nextMarkdown = markdownOverride ?? markdown
    const refIds = chartIdsFromMarkdown(nextMarkdown)
    const imageIds = imageIdsFromMarkdown(nextMarkdown)
    saveInFlight.current = true
    setSavingSection(true)
    try {
      const res = await wordagentApi.saveGraphicSection(bidId, { chapter_id: selectedChapterId, expected_revision: editRevision, markdown: nextMarkdown, ref_chart_ids: refIds, ref_image_asset_ids: imageIds, format_template_id: formatTemplateId })
      if (res.code !== 0) throw new Error(res.message || '保存失败')
      updateSavedChapter(selectedChapterId, Boolean(res.data.markdown?.trim()))
      if (currentEditor.current.bidId !== bidId || currentEditor.current.selectedChapterId !== selectedChapterId) return
      setEditRevision(res.data.revision)
      setSaveConflict(false)
      if (currentEditor.current.markdown === nextMarkdown) {
        markdownDirtyRef.current = false
        setMarkdownDirty(false)
      }
      setNotice('图文段落已保存，版本 ' + res.data.revision + '。')
      setError('')
      setSectionCharts(res.data.charts || [])
      setSectionImages(res.data.images || [])
      setDocumentRisks(null)
      setAssetRisks(res.data.risks || [])
      if (res.data.risks?.length) setRiskPanelOpen(true)
    } catch (err) {
      if (currentEditor.current.bidId !== bidId || currentEditor.current.selectedChapterId !== selectedChapterId) return
      if (imagesOverride) setSectionImages(imagesOverride)
      setSaveConflict(true)
      setError(apiErrorMessage(err, '保存失败；本地编辑仍保留，请使用章节协作面板对比最新版本。'))
    } finally {
      saveInFlight.current = false
      setSavingSection(false)
    }
  }

  async function openReplacementPicker(asset: KnowledgeAsset) {
    setReplaceTargetId(asset.asset_id)
    setReplacementAssets([])
    setReplacementLoading(true)
    setError('')
    try {
      const scoped = await wordagentApi.listKnowledgeAssets({
        asset_sub_type: asset.asset_sub_type || undefined,
        image_category: asset.category || undefined,
        limit: 48,
      })
      if (scoped.code === 0) {
        setReplacementAssets(scoped.data.assets.filter(candidate => candidate.asset_id !== asset.asset_id))
      }
    } catch (err) {
      setError(apiErrorMessage(err, '替换素材加载失败'))
    } finally {
      setReplacementLoading(false)
    }
  }

  async function replaceMatchedImage(targetId: number, replacement: KnowledgeAsset) {
    const nextMarkdown = replaceKnowledgeImageInMarkdown(markdown, targetId, replacement.asset_id)
    const nextImages = sectionImages.map(asset => asset.asset_id === targetId ? replacement : asset)
    updateEditedMarkdown(nextMarkdown)
    setSectionImages(nextImages)
    setReplaceTargetId(null)
    setReplacementAssets([])
    await saveSection(nextMarkdown, nextImages)
  }

  async function removeMatchedImage(assetId: number) {
    const nextMarkdown = removeKnowledgeImageFromMarkdown(markdown, assetId)
    const nextImages = sectionImages.filter(asset => asset.asset_id !== assetId)
    updateEditedMarkdown(nextMarkdown)
    setSectionImages(nextImages)
    setReplaceTargetId(null)
    setReplacementAssets([])
    await saveSection(nextMarkdown, nextImages)
  }

  async function openTableFill(table: BidTableType = tableType) {
    if (!bidId) return
    setTableType(table)
    setTableBusy(true)
    setError('')
    try {
      const response = await wordagentApi.fillBidTable(bidId, { table_type: table, embed_images: tableEmbedImages })
      if (response.code !== 0) throw new Error(response.message || '表格填充失败')
      setTableFill(response.data)
      setTableModalOpen(true)
    } catch (err) {
      setError(apiErrorMessage(err, '表格填充失败'))
    } finally {
      setTableBusy(false)
    }
  }

  function inferTableTypeFromSelection(): BidTableType {
    const editor = editorRef.current
    const selected = editor ? markdown.slice(editor.selectionStart, editor.selectionEnd) : ''
    if (/人员|姓名|岗位|健康证|从业资格/.test(selected)) return 'personnel'
    if (/车辆|车牌|冷链|行驶证/.test(selected)) return 'vehicle'
    if (/设备|冷库|分拣|设施/.test(selected)) return 'equipment'
    if (/业绩|项目案例|甲方/.test(selected)) return 'performance'
    if (/财务|审计|收入|净利润/.test(selected)) return 'financial'
    return 'qualification'
  }

  function tableFillMarkdown() {
    if (!tableFill) return ''
    const headings = markdownTableTarget ? tableFill.fields : tableFill.fields.map(tableFieldLabel)
    const header = `| ${headings.join(' | ')} |\n| ${headings.map(() => '---').join(' | ')} |`
    const body = tableFill.rows.map(row => `| ${headings.map(field => String(row[field] || '')).join(' | ')} |`).join('\n')
    return `${header}\n${body}`
  }

  function insertFilledTable() {
    if (!tableFill || !tableFill.rows.length) {
      setError('暂无可插入的表格数据')
      return
    }
    const value = tableFillMarkdown()
    if (markdownTableTarget) {
      updateEditedMarkdown(markdown.slice(0, markdownTableTarget.start) + value + markdown.slice(markdownTableTarget.end))
    } else {
      insertAtCursor(`\n\n${value}\n\n`)
    }
    setTableModalOpen(false)
    setMarkdownTableTarget(null)
    setNotice('表格已插入正文，可继续编辑后保存')
  }

  function openMarkdownTableEditor(target: MarkdownTableTarget) {
    const rows = markdownTableRows(target.block)
    if (!rows || rows.length < 2) return
    const fields = rows[0].map((field, index) => field || `列${index + 1}`)
    const editableRows = rows.slice(1).map((row, rowIndex) => {
      const values = Object.fromEntries(fields.map((field, index) => [field, row[index] || '']))
      const missingFields = fields.filter(field => !String(values[field] || '').trim())
      return { ...values, asset_id: -(rowIndex + 1), asset_sub_type: '', source_name: '正文表格', missing_fields: missingFields, status: missingFields.length ? '待补充' : '完整', verification_status: 'manual' } as BidTableFillResult['rows'][number]
    })
    const completeCount = editableRows.filter(row => !row.missing_fields.length).length
    setTableFill({ bid_id: bidId || 0, table_type: tableType, embed_images: false, fields, rows: editableRows, row_count: editableRows.length, complete_count: completeCount, pending_count: editableRows.length - completeCount })
    setMarkdownTableTarget(target)
    setTableContextMenu(null)
    setTableModalOpen(true)
  }

  function removeMarkdownTable(target: MarkdownTableTarget) {
    if (!window.confirm('确定删除当前正文表格吗？删除后可通过插入表格重新添加。')) return
    updateEditedMarkdown(`${markdown.slice(0, target.start)}${markdown.slice(target.end)}`.replace(/\n{3,}/g, '\n\n').trim())
    setTableContextMenu(null)
    setNotice('正文表格已删除')
  }

  function insertEmptyTable() {
    const fields = TABLE_FIELD_SETS[tableType]
    const headings = fields.map(tableFieldLabel)
    const body = fields.map(() => '待补充')
    insertAtCursor('\n\n| ' + headings.join(' | ') + ' |\n| ' + headings.map(() => '---').join(' | ') + ' |\n| ' + body.join(' | ') + ' |\n\n')
    setNotice('空白标准表格已插入正文，可继续编辑后保存。')
  }

  function applyTemplateFormat() {
    setNotice('已启用“' + selectedExportTemplate.name + '”的模板格式，预览与 Word 导出将使用该模板的页面、标题和表格样式。')
  }

  function updateTableCell(rowIndex: number, field: string, value: string) {
    setTableFill(current => current ? {
      ...current,
      rows: current.rows.map((row, index) => {
        if (index !== rowIndex) return row
        const next = { ...row, [field]: value }
        const missing_fields = current.fields.filter(key => !String(next[key] || '').trim())
        return { ...next, missing_fields, status: missing_fields.length ? '待补充' : '完整' }
      }),
    } : current)
  }

  function addTableRow() {
    setTableFill(current => {
      if (!current) return current
      const row = Object.fromEntries(current.fields.map(field => [field, ''])) as typeof current.rows[number]
      return { ...current, rows: [...current.rows, { ...row, asset_id: Date.now(), asset_sub_type: '', source_name: '手动新增', missing_fields: [...current.fields], status: '待补充', verification_status: 'pending' }], row_count: current.row_count + 1, pending_count: current.pending_count + 1 }
    })
  }

  function removeTableRow(rowIndex: number) {
    setTableFill(current => {
      if (!current || current.rows.length <= 1) return current
      const rows = current.rows.filter((_, index) => index !== rowIndex)
      return { ...current, rows, row_count: rows.length, complete_count: rows.filter(row => !row.missing_fields.length).length, pending_count: rows.filter(row => row.missing_fields.length > 0).length }
    })
  }

  async function verifyAssets(kind: 'certificates' | 'invoices') {
    if (!bidId) return
    setVerificationBusy(true)
    setError('')
    try {
      const response = kind === 'certificates' ? await wordagentApi.verifyCertificates(bidId) : await wordagentApi.verifyInvoices(bidId)
      if (response.code !== 0) throw new Error(response.message || '核验失败')
      setVerification(response.data)
      setDocumentRisks(current => [...(current || []), ...response.data.risks])
      if (response.data.risks.length) setRiskPanelOpen(true)
      setNotice(`${kind === 'certificates' ? '证件' : '发票'}核验完成：通过 ${response.data.summary.passed}，异常 ${response.data.summary.abnormal}`)
    } catch (err) {
      setError(apiErrorMessage(err, '批量核验失败'))
    } finally {
      setVerificationBusy(false)
    }
  }

  async function exportTemplateDocx(risksForNotice?: AssetRisk[]) {
    if (!bidId || !catalog) return
    const selectedChapterIds = exportScope === 'current'
      ? (selectedChapterId ? [selectedChapterId] : [])
      : exportScope === 'selected' ? selectedLeafChapterIds : undefined
    const editor = editorRef.current
    const selectedText = exportScope === 'content' && editor && editor.selectionStart !== editor.selectionEnd
      ? markdown.slice(editor.selectionStart, editor.selectionEnd)
      : undefined
    if (exportScope !== 'all' && !selectedChapterIds?.length && exportScope !== 'content') {
      setError('请先选择要导出的章节。')
      return
    }
    if (exportScope === 'content' && !selectedText) {
      setError('请先在正文编辑区选中要导出的内容。')
      return
    }
    if (exportTemplateId.startsWith('custom:project-') && !bidderName.trim()) {
      setError('请先填写投标人全称，案例模板封面不能使用旧标书的投标人信息。')
      return
    }
    const formatGaps = tenderFormatGaps
    if (formatGaps.length) {
      setNotice(`招标文件规定的投标资料清单尚缺 ${formatGaps.length} 项（如${formatGaps.slice(0, 3).join('、')}）。本次仅导出章节测试稿，不能作为完整投标文件。`)
    }
    setExportingDocx(true)
    setExportProgressMessage('加载模板')
    setError('')
    setNotice('导出进度：加载模板')
    try {
      const result = await exportGraphicBidDocx({
        bidId,
        catalog,
        versionId: activeVersion?.version_id,
        templateId: exportTemplateId,
        bidderName,
        selectedChapterIds,
        includeImages: exportIncludeImages,
        includeToc: exportIncludeToc,
        selectedText,
        blindCheck,
        blindTerms,
        onBlindReview: report => new Promise<boolean>(resolve => {
          setReviewedImages([])
          setReviewedDocument(false)
          setBlindReview({ report, resolve })
        }),
        onProgress: progress => {
          setExportProgressMessage(progress.message)
          setNotice('导出进度：' + progress.message)
        },
      })
      const finalRisks = risksForNotice ?? documentRisks ?? []
      const riskNotice = finalRisks.length ? ` 当前素材风险：${riskSummaryText(finalRisks)}。` : ''
      setNotice((result.mode === 'fallback' ? '模板导出失败，已自动切换原生 Word 导出。' : `Word 已生成并开始下载${result.fileName.includes('_章节测试稿') ? '（章节测试稿；请对照招标文件核查目录、表单和缺失内容）' : ''}。`) + riskNotice)
    } catch (err) {
      setError(apiErrorMessage(err, '模板 Word 导出失败，请使用原生导出兜底。'))
    } finally {
      setExportingDocx(false)
      setExportProgressMessage('')
    }
  }

  async function handleExportClick() {
    if (!bidId || !catalog || exportingDocx || auditingAssets) return
    setError('')
    let auditRisks: AssetRisk[] = []
    const emptyTableCells = markdown.split(/\n{2,}/).some(block => {
      const rows = block.split('\n').filter(line => line.trim().startsWith('|'))
      return rows.length >= 2 && rows.some(line => line.replace(/\|/g, '').trim() === '')
    })
    if (emptyTableCells && !window.confirm('当前正文表格存在空白单元格，是否仍要继续导出？')) return
    setAuditingAssets(true)
    try {
      const audit = await wordagentApi.auditGraphicAssets(bidId)
      if (audit.code !== 0) throw new Error(audit.message || '素材核验失败')
      auditRisks = audit.data.risks || []
      setDocumentRisks(auditRisks)
      if (audit.data.risks?.length) setRiskPanelOpen(true)
      if ((audit.data.summary?.high || 0) > 0) {
        setExportRiskAudit(audit.data)
        setExportRiskPromptOpen(true)
        return
      }
    } catch (err) {
      setError(`导出前风险核验失败，请重试：${apiErrorMessage(err, '接口不可用')}`)
      return
    } finally {
      setAuditingAssets(false)
    }
    await exportTemplateDocx(auditRisks)
  }

  function locateRisk(risk: AssetRisk) {
    if (!risk.asset_id) {
      setSelectedChapterId(risk.chapter_id)
      setNotice(`已定位到「${risk.chapter_title}」；请核对正文中的相关事实与证明材料。`)
      return
    }
    if (risk.chapter_id !== selectedChapterId) {
      setRiskLocateTargetId(risk.asset_id)
      setSelectedChapterId(risk.chapter_id)
      return
    }
    focusRiskImage(risk.asset_id)
  }

  function focusRiskImage(assetId: number) {
    const node = document.querySelector(`[data-knowledge-asset-id="${assetId}"]`)
    if (!node) {
      setNotice('当前正文预览中未找到该图片，请先确认章节正文包含该素材。')
      return
    }
    node.scrollIntoView({ behavior: 'smooth', block: 'center' })
    node.classList.add('graphic-risk-focus')
    window.setTimeout(() => node.classList.remove('graphic-risk-focus'), 1800)
  }

  function downloadTemplatePreview() {
    if (exportTemplateId.startsWith('custom:')) {
      void downloadCustomWordTemplate(exportTemplateId).then(downloaded => {
        if (!downloaded) setError('自定义模板文件不存在。')
        else setNotice('自定义模板预览版已开始下载。')
      })
      return
    }
    if (!downloadBidTemplatePreview(selectedExportTemplate)) {
      setError('当前环境不支持下载模板预览文件。')
      return
    }
    setNotice('模板示例预览版已开始下载。')
  }

  async function reloadCustomTemplates() {
    const templates = await listCustomWordTemplates()
    setCustomTemplates(templates)
    return templates
  }

  async function handleCustomTemplateFile(file?: File | null) {
    if (!file || templateManagerBusy) return
    setTemplateManagerBusy(true)
    setTemplateManagerError('')
    try {
      const saved = await importCustomWordTemplate(file)
      await reloadCustomTemplates()
      setExportTemplateId(saved.id as DocxExportTemplateId)
      setNotice(`模板“${saved.name}”已保存，可直接用于 Word 导出。`)
    } catch (err) {
      setTemplateManagerError(apiErrorMessage(err, '模板校验失败，请确认文件为兼容的 DOCX 模板。'))
    } finally {
      setTemplateManagerBusy(false)
      if (customTemplateInputRef.current) customTemplateInputRef.current.value = ''
    }
  }

  async function renameCustomTemplate(template: CustomWordTemplateSummary) {
    const name = (await requestText('请输入新的模板名称', template.name))?.trim()
    if (!name || name === template.name) return
    await updateCustomWordTemplate(template.id, { name })
    await reloadCustomTemplates()
    if (exportTemplateId === template.id) setNotice(`模板已重命名为“${name}”。`)
  }

  async function setCustomTemplateDefault(template: CustomWordTemplateSummary) {
    await updateCustomWordTemplate(template.id, { isDefault: true })
    await reloadCustomTemplates()
    setExportTemplateId(template.id as DocxExportTemplateId)
    setNotice(`已将“${template.name}”设为默认模板。`)
  }

  async function deleteCustomTemplate(template: CustomWordTemplateSummary) {
    if (!window.confirm(`确定删除模板“${template.name}”？删除后无法恢复。`)) return
    await removeCustomWordTemplate(template.id)
    const templates = await reloadCustomTemplates()
    if (exportTemplateId === template.id) setExportTemplateId(templates.find(item => item.isDefault)?.id as DocxExportTemplateId || defaultExportTemplate)
    setNotice(`模板“${template.name}”已删除。`)
  }

  async function uploadIllustration(file?: File | null) {
    if (!bidId || !file) return
    setIllustrationUploading(true)
    setError('')
    try {
      const res = await wordagentApi.uploadGraphicIllustration(bidId, file, { caption: illustrationCaption, category: illustrationCategory, save_to_knowledge: illustrationSaveToKnowledge })
      if (res.code === 0) {
        insertAtCursor(`\n\n${applyIllustrationWidth(res.data.markdown, illustrationWidthMode, illustrationCaption)}\n\n`)
        if (res.data.asset) setSectionImages(prev => mergeKnowledgeAssets(prev, [res.data.asset as KnowledgeAsset]))
        setNotice('插图已插入当前章节。')
        setIllustrationCaption('')
      }
    } catch (err) {
      setError(apiErrorMessage(err, '插图上传失败'))
    } finally {
      setIllustrationUploading(false)
      if (illustrationInputRef.current) illustrationInputRef.current.value = ''
    }
  }

  function insertAtCursor(text: string) {
    const editor = editorRef.current
    if (!editor) {
      updateEditedMarkdown(markdown + text)
      return
    }
    const start = editor.selectionStart
    const end = editor.selectionEnd
    const next = `${markdown.slice(0, start)}${text}${markdown.slice(end)}`
    updateEditedMarkdown(next)
    window.setTimeout(() => {
      editor.focus()
      editor.setSelectionRange(start + text.length, start + text.length)
    }, 0)
  }

  function applyFormat(action: 'bold' | 'italic' | 'underline' | 'strike' | 'h1' | 'h2' | 'h3' | 'body' | 'ol' | 'ul' | 'indent' | 'outdent' | 'left' | 'center' | 'right') {
    const editor = editorRef.current
    if (!editor) return
    const start = editor.selectionStart
    const end = editor.selectionEnd
    const selected = markdown.slice(start, end) || '选中文本'
    let replacement = selected
    if (action === 'bold') replacement = `**${selected}**`
    if (action === 'italic') replacement = `*${selected}*`
    if (action === 'underline') replacement = '<u>' + selected + '</u>'
    if (action === 'strike') replacement = '~~' + selected + '~~'
    if (action === 'h1') replacement = prefixLines(selected, '# ')
    if (action === 'h2') replacement = prefixLines(selected, '## ')
    if (action === 'h3') replacement = prefixLines(selected, '### ')
    if (action === 'body') replacement = selected.split('\n').map(line => line.replace(/^#{1,6}\s+/, '')).join('\n')
    if (action === 'ul') replacement = selected.split('\n').map(line => line.trim() ? `- ${line.replace(/^[-\d.\s]+/, '')}` : line).join('\n')
    if (action === 'ol') replacement = selected.split('\n').map((line, index) => line.trim() ? `${index + 1}. ${line.replace(/^[-\d.\s]+/, '')}` : line).join('\n')
    if (action === 'indent') replacement = selected.split('\n').map(line => line.trim() ? `> ${line}` : line).join('\n')
    if (action === 'outdent') replacement = selected.split('\n').map(line => line.replace(/^>\s+/, '')).join('\n')
    if (action === 'left' || action === 'center' || action === 'right') replacement = `<p style="text-align:${action};">${escapeHtml(selected)}</p>`
    const next = `${markdown.slice(0, start)}${replacement}${markdown.slice(end)}`
    updateEditedMarkdown(next)
    window.setTimeout(() => {
      editor.focus()
      editor.setSelectionRange(start, start + replacement.length)
    }, 0)
  }

  function captureFormatBrush() {
    const editor = editorRef.current
    if (!editor) return
    const selected = markdown.slice(editor.selectionStart, editor.selectionEnd) || markdown.split('\n').find(Boolean) || ''
    const line = selected.split('\n')[0]
    const prefix = line.match(/^(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)/)?.[0] || ''
    setFormatBrushPrefix(prefix)
    setNotice(prefix ? '已复制当前段落格式，请选中文本后点击格式刷应用。' : '已复制正文段落格式。')
  }

  function applyFormatBrush() {
    if (formatBrushPrefix === null) return
    const editor = editorRef.current
    if (!editor) return
    const start = editor.selectionStart
    const end = editor.selectionEnd
    const selected = markdown.slice(start, end)
    const replacement = selected.split('\n')
      .map(line => line.replace(/^(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)/, ''))
      .map(line => line ? formatBrushPrefix + line : line)
      .join('\n')
    updateEditedMarkdown(markdown.slice(0, start) + replacement + markdown.slice(end))
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--page-bg)' }}>
      <TopNavLite onHome={onHome} />
      <main className="module-page graphic-module-page">
        <section className="module-header-card">
          <div><p>{embeddedInP1 ? 'BID WRITING' : 'GRAPHIC WRITING'}</p><h1>{embeddedInP1 ? '标书正文生成' : '图文标书正文生成'}</h1><span>{embeddedInP1 ? '基于已确认大纲生成正文，自动检索企业知识库素材并渲染真实业务图片。' : '在本页完成招标文件上传、目录编辑、知识库图片匹配和图文正文生成。'}</span></div>
          <div className="graphic-header-actions"><button className="light-btn" type="button" onClick={embeddedInP1 ? onBackToCatalog : onBackToMermaid}>{embeddedInP1 ? '返回上一步（大纲）' : '返回图表生成'}</button><button className="light-btn" type="button" onClick={onHome}>返回首页</button></div>
        </section>
         {orchestrationSettings.formatReferenceName && <div className="knowledge-notice graphic-format-reference-notice">当前编排参考：{orchestrationSettings.formatReferenceName}。正文内容仍按已选 Word 模板渲染。</div>}
         {!embeddedInP1 && <section className="form-card graphic-prep-card">
          <div><strong>招标文件与目录准备</strong><span>可在图文模块内独立上传 PDF/DOCX，复用现有解析与大纲生成链路。</span></div>
           <label className="light-btn graphic-upload-btn">上传招标文件<input type="file" accept=".pdf,.doc,.docx" disabled={preparing || catalogGenerating || parseStatus?.parse_status === 'processing'} onChange={event => void handleTenderFile(event.target.files?.[0] || null)} /></label>
           <button className="light-btn" type="button" disabled={catalogGenerating || preparing || parseStatus?.parse_status === 'processing' || (parseStatus?.parse_status === 'failed' && !!parseStatus.tender_file_name) || !bidId} title={parseStatus?.parse_status === 'processing' ? '招标文件解析完成后才能重新生成目录' : undefined} onClick={() => void generateCatalog()}>{catalogGenerating ? '目录生成中...' : parseStatus?.parse_status === 'processing' ? '解析中，暂不可生成目录' : parseStatus?.parse_status === 'failed' ? '解析失败，请重新上传' : '重新生成目录'}</button>
           {preparing && uploadPercent < 100 && <span className="graphic-prep-progress">上传 {uploadPercent}%</span>}
           {preparing && uploadPercent === 100 && !parseStatus && <span className="graphic-prep-progress">上传 100% · 正在保存文件</span>}
           {parseStatus?.parse_status === 'processing' && <span className="graphic-prep-progress">上传完成 · {parseStatus.parse_step_label || '解析中'}{parseStatus.extraction_progress?.percent != null ? ` ${parseStatus.extraction_progress.percent}%` : ''}</span>}
        </section>}
         {!embeddedInP1 && (catalogProgress || catalogGenerating) && <section className="form-card graphic-catalog-progress" aria-live="polite">
           <div className="graphic-catalog-progress-heading">
             <strong>目录生成进度</strong>
             <span>已完成 {catalogProgress?.completed ?? 0} / 3 套{catalogProgress?.currentIndex ? ` · 当前第 ${catalogProgress.currentIndex} 套 ${catalogProgress.versionName}` : ''}</span>
           </div>
           <progress max={3} value={catalogProgress?.completed ?? 0} aria-label="目录方案完成进度" />
           <p>{catalogProgress?.stage || '后台正在生成目录，稍后会显示最新状态。'}{catalogProgress?.savedChapters ? ` · 当前方案已保存 ${catalogProgress.savedChapters} 个章节` : ''}</p>
           {catalogProgress?.preview && <div className="graphic-catalog-stream-preview"><strong>实时章节预览（尚未保存）</strong><pre>{catalogProgress.preview}</pre></div>}
         </section>}
        <section className="graphic-section-layout">
          <aside className="form-card graphic-chapter-tree">
             <div className="graphic-catalog-heading"><h3>当前标书目录</h3><label>使用已保存大纲<select value={activeVersion?.version_id || ''} onChange={event => { if (markdownDirtyRef.current || saveInFlight.current) { setError('请先保存当前章节，再切换大纲。'); return }; setCatalogVersionId(Number(event.target.value) || null); setSelectedChapterId(null); setGenerationSelectedChapterIds([]) }}>{catalog?.versions?.map(version => <option key={version.version_id} value={version.version_id}>{version.version_name}{version.is_final ? '（最终版）' : ''}</option>)}</select></label></div>
            {initialLoading ? <p className="graphic-empty">正在加载目录...</p> : chapters.length ? <>
              <details open>
                <summary style={{ cursor: 'pointer' }}>勾选后批量生成（已勾选 {selectedLeafChapterIds.length} 章）</summary>
                <div style={{ display: 'grid', gap: 8, paddingTop: 8 }}>
                  <span>左侧勾选框只决定“生成已勾选章节”的范围；单章生成使用右侧的“当前章节”。</span>
                  <div className="graphic-header-actions">
                    <button className="light-btn" type="button" onClick={() => setGenerationSelectionForNodes(chapters.filter(chapter => !chapter.children?.length))}>全选</button>
                    <button className="light-btn" type="button" onClick={() => { const selected = new Set(selectedLeafChapterIds); setGenerationSelectedChapterIds(leafChapterIds.filter(id => !selected.has(id))) }}>反选</button>
                  </div>
                  <div className="graphic-header-actions">
                    <button className="light-btn" type="button" onClick={() => selectGenerationPreset('business')}>仅商务部分</button>
                    <button className="light-btn" type="button" onClick={() => selectGenerationPreset('service')}>仅服务方案</button>
                    <button className="light-btn" type="button" onClick={() => selectGenerationPreset('technical')}>仅技术方案</button>
                    <button className="light-btn" type="button" onClick={() => selectGenerationPreset('qualification')}>仅资质证明</button>
                  </div>
                  <div className="graphic-header-actions">
                    {([1, 2, 3] as const).map(level => <button className="light-btn" type="button" key={level} onClick={() => setGenerationSelectionForNodes(chapters.filter(chapter => chapter.level === level))}>选中{level}级</button>)}
                  </div>
                  {catalog?.graphic_body_chapter_ids && <div className="graphic-header-actions">
                    <span>正文已保存 {leafChapterIds.length - missingChapterIds.length} / {leafChapterIds.length} 节</span>
                    <button className="light-btn" type="button" disabled={generating || !missingChapterIds.length} onClick={() => void generateChapters(missingChapterIds, '补生成空白小节', true)}>补生成空白小节 {missingChapterIds.length} 节</button>
                    {missingChapterIds.length > 0 && <small>补生成按单章字数上限处理。</small>}
                  </div>}
                  <div className="graphic-header-actions">
                    <button className="light-btn" type="button" disabled={generating || !leafChapterIds.length} onClick={() => void generateChapters(leafChapterIds, '全部章节')}>生成全部 {leafChapterIds.length} 章</button>
                    <button className="dark-btn" type="button" disabled={generating || !selectedLeafChapterIds.length} onClick={() => void generateChapters(selectedLeafChapterIds, '已勾选章节')}>生成已勾选 {selectedLeafChapterIds.length} 章</button>
                  </div>
                </div>
              </details>
              <div style={{ display: 'grid', gap: 5 }}>
                {chapters.map(chapter => {
                  const branchLeaves = getLeafChapterIds(chapter)
                  const selectedCount = branchLeaves.filter(id => selectedLeafChapterIds.includes(id)).length
                  const checked = branchLeaves.length > 0 && selectedCount === branchLeaves.length
                  const indeterminate = selectedCount > 0 && !checked
                  return <div key={chapter.chapter_id} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minWidth: 28 }}>
                      <input type="checkbox" aria-label={`选择${stripCatalogNumber(chapter.title)}及子章节`} checked={checked} ref={element => { if (element) element.indeterminate = indeterminate }} onChange={event => toggleGenerationChapter(chapter.chapter_id, event.target.checked)} />
                    </label>
                     <button type="button" data-generation-chapter-id={chapter.chapter_id} className={chapter.chapter_id === selectedChapterId ? 'active' : ''} style={{ paddingLeft: `${10 + (chapter.level - 1) * 16}px`, flex: 1 }} onClick={() => selectChapterForEditing(chapter.chapter_id)}><span>{chapter.number}</span>{stripCatalogNumber(chapter.title)}{catalog?.graphic_body_chapter_ids && !chapter.children?.length && !savedChapterIds.has(chapter.chapter_id) && <small> · 缺正文</small>}</button>
                  </div>
                })}
              </div>
            </> : <p className="graphic-empty">上传招标文件或点击“重新生成目录”后显示目录。</p>}
            <div className="graphic-catalog-actions"><strong>目录编辑</strong><button type="button" onClick={() => void editChapter('rename')} disabled={!selectedChapter}>重命名</button><button type="button" onClick={() => void editChapter('sibling')} disabled={!selectedChapter}>新增同级</button><button type="button" onClick={() => void editChapter('child')} disabled={!selectedChapter || selectedChapter.level >= 3}>新增子级</button><button type="button" onClick={() => void editChapter('up')} disabled={!selectedChapter}>上移</button><button type="button" onClick={() => void editChapter('down')} disabled={!selectedChapter}>下移</button><button type="button" onClick={() => void editChapter('upgrade')} disabled={!selectedChapter || selectedChapter.level <= 1}>升级</button><button type="button" onClick={() => void editChapter('downgrade')} disabled={!selectedChapter || selectedChapter.level >= 3}>降级</button><button type="button" className="danger" onClick={() => void editChapter('delete')} disabled={!selectedChapter}>删除</button></div>
          </aside>
          <section ref={editorPanelRef} className="form-card graphic-editor-panel">
            {bidId && selectedChapterId && <ChapterCollaboration key={bidId + ':' + selectedChapterId} bidId={bidId} chapterId={selectedChapterId} revision={editRevision} dirty={markdownDirty || savingSection} generating={generating} markdown={markdown} onRevision={setEditRevision} onLoad={applyGraphicSection} onEditable={setCollabEditable} onBusy={setCollabBusy} />}
            <div className="graphic-editor-tabs"><strong>生成结果预览</strong><button className="light-btn" type="button" disabled={generating || collabBusy || savingSection || !collabEditable || editRevision === null} onClick={() => void saveSection()}>保存编辑</button></div>
            <div className="graphic-format-toolbar" aria-label="正文格式工具栏">
               <button type="button" title="加粗" onClick={() => applyFormat('bold')}>B</button>
               <button type="button" title="斜体" onClick={() => applyFormat('italic')}>I</button>
               <button type="button" title="下划线" onClick={() => applyFormat('underline')}>U</button>
               <button type="button" title="删除线" onClick={() => applyFormat('strike')}>S</button>
               <button type="button" title="一级标题" onClick={() => applyFormat('h1')}>H1</button>
               <button type="button" title="二级标题" onClick={() => applyFormat('h2')}>H2</button>
               <button type="button" title="三级标题" onClick={() => applyFormat('h3')}>H3</button>
               <button type="button" title="正文段落" onClick={() => applyFormat('body')}>正文</button>
              <button type="button" title="无序列表" onClick={() => applyFormat('ul')}>• 列表</button>
              <button type="button" title="有序列表" onClick={() => applyFormat('ol')}>1. 列表</button>
               <button type="button" title="段落缩进" onClick={() => applyFormat('indent')}>缩进</button>
               <button type="button" title="减少缩进" onClick={() => applyFormat('outdent')}>减少缩进</button>
              <button type="button" title="左对齐" onClick={() => applyFormat('left')}>左对齐</button>
              <button type="button" title="居中" onClick={() => applyFormat('center')}>居中</button>
               <button type="button" title="右对齐" onClick={() => applyFormat('right')}>右对齐</button>
               <button type="button" title="复制段落格式" onClick={captureFormatBrush}>格式刷</button>
               <button type="button" title="应用段落格式" disabled={formatBrushPrefix === null} onClick={applyFormatBrush}>应用格式</button>
               <button className="light-btn" type="button" title="智能生成表格" onClick={() => void openTableFill(inferTableTypeFromSelection())}>智能生成表格</button>
               <button className="light-btn" type="button" title="插入标准空表格" onClick={insertEmptyTable}>插入表格</button>
               <button className="light-btn" type="button" title="套用当前模板格式" onClick={applyTemplateFormat}>套用模板格式</button>
               <button className="light-btn" type="button" title="导出当前范围" onClick={() => void handleExportClick()}>导出当前范围</button>
            </div>
            {notice && <div className="knowledge-notice">{notice}</div>}{error && <div className="graphic-error">{error}</div>}
            {generationProgress && <section className="graphic-generation-progress" aria-label="正文生成进度" aria-live="polite">
              <div className="graphic-generation-progress-heading"><strong>{generationProgress.status === 'running' ? '正文生成进度' : generationProgress.status === 'completed' ? '本次生成已结束' : generationProgress.status === 'interrupted' ? '本次生成已停止' : '本次生成未完成'}</strong><span>{Math.min(100, Math.round(processedChapters * 100 / Math.max(1, generationProgress.total)))}%</span></div>
              <progress aria-label="正文生成进度" value={Math.min(generationProgress.total, processedChapters)} max={generationProgress.total} />
              <div>已处理 {processedChapters} / {generationProgress.total} 章 · 成功 {generationProgress.success} 章 · 失败 {generationProgress.failure} 章{generationProgress.skipped > 0 && ' · 跳过 ' + generationProgress.skipped + ' 章'}</div>
              {generationProgress.status === 'running' && <small>{generationProgress.current > 0 ? '正在处理第 ' + generationProgress.current + ' / ' + generationProgress.total + ' 章：' + generationProgress.chapterTitle : '正在准备章节上下文与知识库素材'}{generationProgress.stage === 'adjusting' ? '（篇幅校订中）' : ''}</small>}
              {generationReviews.length > 0 && <details open={generationProgress.status !== 'running'}>
                <summary>已保存草稿，篇幅待复核（{generationReviews.length} 节）</summary>
                <ul>{generationReviews.map(message => <li key={message}>{message}</li>)}</ul>
              </details>}
              {generationFailures.length > 0 && <details open={generationProgress.status !== 'running'}>
                <summary>章节生成失败原因（{generationFailures.length} 节）</summary>
                <ul>{generationFailures.map(message => <li key={message}>{message}</li>)}</ul>
              </details>}
            </section>}
            {generationPreview && <section aria-label="正文实时生成预览" style={{ border: '1px solid #d9dbd2', borderRadius: 10, padding: 16, margin: '12px 0' }}>
              <div className="graphic-editor-tabs"><strong>{generationPreview.chapterTitle} · {({ streaming: '实时生成中', adjusting: '实时校订中', completed: '已完成并保存', failed: '生成未完成', interrupted: '已停止，内容未完成' })[generationPreview.status]}</strong>{generating && <button className="light-btn" type="button" onClick={() => streamRef.current?.abort()}>停止生成</button>}</div>
              {generationPreview.status !== 'completed' && <small>以下为模型实时返回的预览，完成后才会保存本章并显示最终排版。</small>}
              <div style={{ maxHeight: 420, overflowY: 'auto', overflowWrap: 'anywhere' }}>
                {generationPreview.status === 'completed' && generationPreview.result
                  ? <MarkdownPreview markdown={generationPreview.markdown} assets={generationPreview.result.charts || []} images={generationPreview.result.images || generationPreview.result.selected_images || []} />
                  : <pre style={{ whiteSpace: 'pre-wrap', font: 'inherit', lineHeight: 1.8 }}>{generationPreview.markdown || '等待模型返回正文…'}</pre>}
              </div>
            </section>}
            {generationStats && <div className="knowledge-notice">本次范围：{generationStats.chapterCount} 章，{generationStats.wordCount.toLocaleString()} 字，约 {generationStats.estimatedPages} 页（按每页 780 字估算）{generationStats.withinTolerance === false ? '；部分章节已保存草稿，篇幅待复核。' : ''}</div>}
            <div className="graphic-mixed-preview"><div className="graphic-document-preview"><MarkdownPreview markdown={markdown} assets={[...assets, ...sectionCharts]} images={sectionImages} risks={assetRisks} onAssetsLoaded={loaded => setSectionImages(prev => mergeKnowledgeAssets(prev, loaded))} onImageClick={asset => { setImageViewer(asset); setImageViewerScale(1) }} onTableDoubleClick={openMarkdownTableEditor} onTableContextMenu={(target, x, y) => setTableContextMenu({ ...target, x, y })} /></div></div>
             <textarea ref={editorRef} className="graphic-markdown-editor" disabled={generating || collabBusy || !collabEditable} value={markdown} onChange={event => updateEditedMarkdown(event.target.value)} placeholder="生成完成后可在这里直接编辑 Markdown 文本，也可手动插入 [[KNOWLEDGE_IMAGE:图片ID]]。" />
          </section>
          <aside className="form-card graphic-params">
            <h3>生成参数</h3>
            <CaseReferenceSelector bidId={bidId} onSelectionChange={setSelectedCaseProjectId} />
             <label>当前章节（右侧单章生成使用）<select value={selectedChapterId || ''} onChange={event => selectChapterForEditing(Number(event.target.value))}>{chapters.map(chapter => <option key={chapter.chapter_id} value={chapter.chapter_id}>{chapter.number} {stripCatalogNumber(chapter.title)}</option>)}</select></label>
            <label>默认单章节字数上限（未设置目标篇幅时）<input type="number" min={200} max={8000} value={wordLimit} onChange={event => setWordLimit(event.target.value)} onBlur={() => { if (wordLimit === '') setWordLimit('1200') }} /></label>
            <small>设置整份标书的“目标字数”后，系统会按所选章节分配，每章最多 8000 字。</small>
            <div className="graphic-category-filter">
              <strong>篇幅控制</strong>
              <label>模式<select value={lengthMode} onChange={event => setLengthMode(event.target.value as LengthMode)}><option value="default">不限制</option><option value="shrink">自动缩写</option><option value="expand">自动扩写</option></select></label>
              <label>目标字数<input type="number" min={1} max={500000} value={lengthTargetWords} onChange={event => { setLengthTargetWords(event.target.value); if (event.target.value) setLengthTargetPages('') }} placeholder="留空时按模式调整已有正文" /></label>
              <label>目标页数<input type="number" min={1} max={640} value={lengthTargetPages} onChange={event => { setLengthTargetPages(event.target.value); if (event.target.value) setLengthTargetWords('') }} placeholder="按每页约780字换算" /></label>
              <span>目标篇幅会按章节现有字数或目录分配；缩写匹配精简版素材，扩写匹配详细版素材。</span>
            </div>
            <label>格式模板<select value={formatTemplateId} onChange={event => setFormatTemplateId(event.target.value)}>{formatTemplates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label>
            <div className="graphic-header-actions"><label>Word 导出模板<select value={exportTemplateId} onChange={event => { const value = event.target.value; if (value === '__manage__') { setTemplateManagerOpen(true); return } setExportTemplateId(value as DocxExportTemplateId) }}><optgroup label="内置模板">{DOCX_EXPORT_TEMPLATES.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</optgroup><optgroup label="我的模板">{customTemplates.length ? customTemplates.map(template => <option key={template.id} value={template.id}>{template.name}{template.isDefault ? '（默认）' : ''}</option>) : <option value="__empty__" disabled>暂无自定义模板</option>}<option value="__manage__">管理模板...</option></optgroup></select></label><button className="light-btn" type="button" onClick={() => setTemplatePreviewOpen(true)}>预览模板</button><button className="light-btn" type="button" onClick={() => setTemplateManagerOpen(true)}>管理模板</button></div>
            <label>投标人全称（用于证明匹配和 Word 封面）<input value={bidderName} onChange={event => { const value = event.target.value; setBidderName(value); window.localStorage.setItem(companyStorageKey('bidagent.bidderName'), value) }} placeholder="请填写本次投标主体的法定全称" /></label>
            <label className="graphic-check"><input type="checkbox" checked={ragEnable} onChange={event => setRagEnable(event.target.checked)} />启用RAG知识库素材</label>
            <label>图片使用策略<select value={imageStrategy} onChange={event => { setImageStrategy(event.target.value as ImageStrategy); if (event.target.value !== 'category_only') setImageSubtypes([]) }}><option value="none">不使用图片</option><option value="rag_auto">自动匹配最优图片</option><option value="valid_only">优先匹配有效期内证件</option><option value="category_only">仅匹配指定分类</option>{!embeddedInP1 && <option value="project_only">仅使用本项目上传图片</option>}</select></label>
            {imageStrategy === 'category_only' && <div className="graphic-category-filter">
              <strong>细分类多选</strong>
              <div className="graphic-category-options">
                {IMAGE_SUBTYPE_OPTIONS.map(option => <label className="graphic-check" key={option.value}><input type="checkbox" checked={imageSubtypes.includes(option.value)} onChange={() => setImageSubtypes(prev => prev.includes(option.value) ? prev.filter(value => value !== option.value) : [...prev, option.value])} />{option.label}</label>)}
              </div>
              {!imageSubtypes.length && <em>请选择至少一个细分类别</em>}
            </div>}
            <div className="graphic-insert-panel">
              <strong>自定义插图</strong>
              <input value={illustrationCaption} onChange={event => setIllustrationCaption(event.target.value)} placeholder="图注文字" />
              <select value={illustrationCategory} onChange={event => setIllustrationCategory(event.target.value)}><option value="资质证书类">资质证书</option><option value="现场场景类">现场场景</option><option value="人员资质类">人员资质</option><option value="设备资产类">设备资产</option><option value="业绩证明类">业绩证明</option><option value="其他附件类">其他附件</option></select>
              <select value={illustrationWidthMode} onChange={event => setIllustrationWidthMode(event.target.value as 'center' | 'wide')}><option value="center">居中适配宽度</option><option value="wide">通栏宽度</option></select>
              <label className="graphic-check"><input type="checkbox" checked={illustrationSaveToKnowledge} onChange={event => setIllustrationSaveToKnowledge(event.target.checked)} />同步存入企业知识库</label>
              <button className="light-btn" type="button" disabled={illustrationUploading || !bidId} onClick={() => illustrationInputRef.current?.click()}>{illustrationUploading ? '上传中...' : '上传并插入图片'}</button>
              <input ref={illustrationInputRef} type="file" accept=".png,.jpg,.jpeg,.webp" onChange={event => void uploadIllustration(event.target.files?.[0] || null)} hidden />
            </div>
            <div className="graphic-insert-panel">
              <strong>表格自动填充</strong>
              <select value={tableType} onChange={event => setTableType(event.target.value as BidTableType)}>
                <option value="qualification">资质证明表</option>
                <option value="personnel">人员信息表</option>
                <option value="vehicle">车辆信息表</option>
                <option value="equipment">设备清单表</option>
                <option value="performance">业绩案例表</option>
                <option value="financial">财务审计表</option>
              </select>
              <label className="graphic-check"><input type="checkbox" checked={tableEmbedImages} onChange={event => setTableEmbedImages(event.target.checked)} />单元格内嵌证件缩略图</label>
              <button className="light-btn" type="button" disabled={tableBusy || !bidId} onClick={() => void openTableFill()}>{tableBusy ? '正在检索素材...' : '预览并填充表格'}</button>
              <div className="graphic-header-actions">
                <button className="light-btn" type="button" disabled={verificationBusy || !bidId} onClick={() => void verifyAssets('certificates')}>批量核验证件</button>
                <button className="light-btn" type="button" disabled={verificationBusy || !bidId} onClick={() => void verifyAssets('invoices')}>批量核验发票</button>
              </div>
             {verification && <span>最近核验：通过 {verification.summary.passed}，异常 {verification.summary.abnormal}，高风险 {verification.summary.high}</span>}
             </div>
             <div className="graphic-insert-panel">
               <strong>导出选项</strong>
               <label>导出范围<select value={exportScope} onChange={event => setExportScope(event.target.value as ExportScope)}>
                 <option value="all">导出全部章节</option>
                 <option value="selected">导出已选章节</option>
                 <option value="current">导出当前章节</option>
                 <option value="content">导出正文选中内容</option>
               </select></label>
               <label className="graphic-check"><input type="checkbox" checked={exportIncludeImages} onChange={event => setExportIncludeImages(event.target.checked)} />导出正文图片和图表</label>
                <label className="graphic-check"><input type="checkbox" checked={exportIncludeToc} onChange={event => setExportIncludeToc(event.target.checked)} />导出目录</label>
                {catalog?.context.price_score_conflict && <span role="status">{catalog.context.price_score_conflict} 导出文件会标为“章节测试稿”。</span>}
            {tenderFormatGaps.length > 0 && <span>当前目录缺少招标文件指定的 {tenderFormatGaps.length} 项资料（如{tenderFormatGaps.slice(0, 3).join('、')}）；导出文件会标为“章节测试稿”。</span>}
               {exportScope === 'selected' && !selectedLeafChapterIds.length && <span>请先在左侧勾选章节。</span>}
             </div>
             <button className="dark-btn" type="button" disabled={generating || !selectedChapterId} onClick={generateSection}>{generating ? '生成中...' : `生成当前章节${selectedChapter ? ` ${selectedChapter.number}` : ''}正文`}</button>
            <button className="light-btn" type="button" disabled={generating || !selectedChapterId} onClick={generateSection}>重新生成当前章节</button>
            {exportingDocx && <span className="graphic-prep-progress">加载模板 → 填充数据 → 渲染图片 → 生成文件；当前：{exportProgressMessage}</span>}
            <button className="dark-btn" type="button" disabled={!bidId || !catalog || generating || exportingDocx || auditingAssets} onClick={() => void handleExportClick()}>{auditingAssets ? '核验素材中...' : exportingDocx ? '导出中：' + exportProgressMessage : '下载图文标书Word'}</button>
            <button className="plain-btn" type="button" disabled={!bidId || exportingDocx || blindCheck || exportTemplateId === 'compact-technical'} onClick={() => bidId && window.open(wordagentApi.downloadGraphicDocxUrl(bidId, bidderName), '_blank')}>原生导出（降级）</button>
            <ImageAssetStrip images={sectionImages} onView={asset => { setImageViewer(asset); setImageViewerScale(1) }} onReplace={asset => void openReplacementPicker(asset)} onRemove={asset => void removeMatchedImage(asset.asset_id)} replaceTargetId={replaceTargetId} replacementAssets={replacementAssets} replacementLoading={replacementLoading} onSelectReplacement={replacement => replaceTargetId !== null ? void replaceMatchedImage(replaceTargetId, replacement) : undefined} onCloseReplacement={() => { setReplaceTargetId(null); setReplacementAssets([]) }} />
            <RiskPanel risks={documentRisks ?? assetRisks} open={riskPanelOpen} onToggle={() => setRiskPanelOpen(value => !value)} onLocate={locateRisk} />
            {verification && <VerificationResults result={verification} onLocate={locateRisk} />}
          </aside>
        </section>
      </main>
      {imageViewer && <KnowledgeImageViewer asset={imageViewer} scale={imageViewerScale} onScale={setImageViewerScale} onClose={() => setImageViewer(null)} />}
      {tableContextMenu && <div className="graphic-table-context-menu" style={{ left: tableContextMenu.x, top: tableContextMenu.y }} role="menu" onClick={event => event.stopPropagation()}>
        <button type="button" onClick={() => openMarkdownTableEditor(tableContextMenu)}>编辑表格</button>
        <button type="button" onClick={() => removeMarkdownTable(tableContextMenu)}>删除表格</button>
        <button type="button" onClick={() => setTableContextMenu(null)}>取消</button>
      </div>}
      {tableModalOpen && tableFill && <div className="knowledge-detail-modal" role="dialog" aria-modal="true" aria-label="表格填充预览">
        <div className="knowledge-detail-modal-panel">
          <header><strong>表格填充预览</strong><button type="button" onClick={() => setTableModalOpen(false)}>关闭</button></header>
          <div className="knowledge-detail-modal-body">
            <p>共识别 {tableFill.row_count} 行，完整 {tableFill.complete_count} 行，待补充 {tableFill.pending_count} 行。字段缺失时保持空值并标记待补充。</p>
            {tableFill.rows.length ? <div className="graphic-insert-panel">
              <table><thead><tr>{tableFill.fields.map(field => <th key={field}>{tableFieldLabel(field)}</th>)}<th>状态</th></tr></thead><tbody>
                {tableFill.rows.slice(0, 80).map((row, rowIndex) => <tr key={`${row.asset_id}-${rowIndex}`}>{tableFill.fields.map(field => <td key={field}><input aria-label={tableFieldLabel(field)} value={String(row[field] || '')} onChange={event => updateTableCell(rowIndex, field, event.target.value)} placeholder={row.missing_fields.includes(field) ? '待补充' : ''} /></td>)}<td>{row.status}<button className="plain-btn" type="button" title="删除当前行" onClick={() => removeTableRow(rowIndex)}>删除</button></td></tr>)}
              </tbody></table>
            </div> : <div className="knowledge-notice">暂无匹配素材，请先上传或解析对应证件、报告。</div>}
            <div className="graphic-header-actions"><button className="light-btn" type="button" onClick={addTableRow}>新增一行</button><button className="light-btn" type="button" onClick={() => setTableModalOpen(false)}>返回调整</button><button className="dark-btn" type="button" disabled={!tableFill.rows.length} onClick={insertFilledTable}>确认插入正文</button></div>
          </div>
        </div>
      </div>}
      {templatePreviewOpen && <div className="knowledge-detail-modal" role="dialog" aria-modal="true" aria-label="Word 导出模板预览">
        <div className="knowledge-detail-modal-panel">
          <header><strong>Word 导出模板预览</strong><button type="button" onClick={() => setTemplatePreviewOpen(false)}>关闭</button></header>
          <div className="knowledge-detail-modal-body">
            <label>查看模板<select value={exportTemplateId} onChange={event => { const value = event.target.value; if (value === '__manage__') { setTemplatePreviewOpen(false); setTemplateManagerOpen(true); return } setExportTemplateId(value as DocxExportTemplateId) }}><optgroup label="内置模板">{DOCX_EXPORT_TEMPLATES.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</optgroup><optgroup label="我的模板">{customTemplates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}<option value="__manage__">管理模板...</option></optgroup></select></label>
            <section><h3>{selectedExportTemplate.name}</h3><p>{selectedExportTemplate.description}</p></section>
            <div className="knowledge-detail-form-grid">
              <section><h3>适用场景</h3><p>{selectedExportTemplate.scenario}</p></section>
              <section><h3>格式特点</h3><p>字体：{selectedExportTemplate.font}<br />字号：{selectedExportTemplate.fontSize}<br />行间距：{selectedExportTemplate.lineSpacing}<br />标题：{selectedExportTemplate.headingLevels}</p></section>
            </div>
            <section><h3>核心结构</h3><p>{selectedExportTemplate.structure}</p></section>
            <section><h3>占位符规范</h3>{selectedExportTemplate.placeholders.map(placeholder => <p key={placeholder}>{placeholder}</p>)}</section>
            <div className="graphic-header-actions"><button className="light-btn" type="button" onClick={downloadTemplatePreview}>下载示例预览版</button><button className="dark-btn" type="button" onClick={() => { setTemplatePreviewOpen(false); setNotice(`已选择${selectedExportTemplate.name}。`) }}>使用此模板</button></div>
          </div>
        </div>
      </div>}
      {templateManagerOpen && <div className="knowledge-detail-modal" role="dialog" aria-modal="true" aria-label="Word 模板管理">
        <div className="knowledge-detail-modal-panel custom-template-manager-modal">
          <header><strong>Word 模板管理</strong><button type="button" onClick={() => setTemplateManagerOpen(false)}>关闭</button></header>
          <div className="knowledge-detail-modal-body">
            <label className={`upload-box custom-template-dropzone${templateDragActive ? ' drag-active' : ''}`} onDragOver={event => { event.preventDefault(); setTemplateDragActive(true) }} onDragLeave={() => setTemplateDragActive(false)} onDrop={event => { event.preventDefault(); setTemplateDragActive(false); void handleCustomTemplateFile(event.dataTransfer.files?.[0]) }}>
              <input ref={customTemplateInputRef} type="file" accept=".docx" onChange={event => void handleCustomTemplateFile(event.target.files?.[0])} />
              <strong>{templateManagerBusy ? '正在校验并保存模板...' : templateDragActive ? '松开以上传 DOCX 模板' : '拖拽 DOCX 模板到这里，或点击选择文件'}</strong>
              <span>仅在当前浏览器本地保存，不上传外部服务；模板至少包含 {`{bid_title}`} 与章节循环占位符。</span>
            </label>
            {templateManagerError && <div className="graphic-error">{templateManagerError}</div>}
            <section><h3>内置模板</h3><div className="custom-template-list">{DOCX_EXPORT_TEMPLATES.map(template => <div className="custom-template-item" key={template.id}><div><strong>{template.name}</strong><span>{template.scenario}</span></div><em>系统模板</em></div>)}</div></section>
            <section><h3>我的模板</h3>{customTemplates.length ? <div className="custom-template-list">{customTemplates.map(template => <div className="custom-template-item" key={template.id}><div><strong>{template.name}{template.isDefault ? '（默认）' : ''}</strong><span>{template.scenario} · {new Date(template.uploadedAt).toLocaleString()}</span><small>占位符 {template.placeholders.join('、')}</small><small>{template.formatRules.styles.body}；{template.formatRules.table}</small></div><div className="custom-template-actions"><button className="light-btn" type="button" onClick={() => void renameCustomTemplate(template)}>重命名</button><button className="light-btn" type="button" onClick={() => void downloadCustomWordTemplate(template.id)}>下载</button>{!template.isDefault && <button className="light-btn" type="button" onClick={() => void setCustomTemplateDefault(template)}>设为默认</button>}<button className="plain-btn" type="button" onClick={() => void deleteCustomTemplate(template)}>删除</button></div></div>)}</div> : <div className="knowledge-notice">暂无自定义模板，请上传一个 DOCX 文件。</div>}</section>
          </div>
        </div>
      </div>}
      {blindReview && <div className="knowledge-detail-modal" role="dialog" aria-modal="true" aria-label="暗标导出检查">
        <div className="knowledge-detail-modal-panel">
          <header><strong>暗标导出检查</strong><button type="button" onClick={() => { blindReview.resolve(false); setBlindReview(null) }}>关闭</button></header>
          <div className="knowledge-detail-modal-body">
            <p>{blindReview.report.notice}</p>
            {blindReview.report.blocked ? <p className="graphic-error">发现 {blindReview.report.issues.length} 项问题，已阻止导出。请修改正文或模板后重新导出。</p> : <p>自动检查未发现词库命中或不可检查内容。请完成下列人工复核后下载。</p>}
            {blindReview.report.issues.map((item, index) => <div className="graphic-risk-confirm-item" key={index}><strong>{item.location}</strong><span>{item.detail}</span>{item.excerpt && <small>{item.excerpt}</small>}</div>)}
            {blindReview.report.images.map((item, index) => <div className="graphic-insert-panel" key={index}>
              <strong>{item.location}</strong>
              <img src={item.preview} alt={'待复核图片 ' + (index + 1)} style={{ maxWidth: '100%', maxHeight: 360, objectFit: 'contain' }} />
              <span>OCR：{item.ocr_text || '未识别到文字'}。请检查 Logo、水印、人物、车牌及 OCR 遗漏。</span>
              {!blindReview.report.blocked && <label className="graphic-check"><input type="checkbox" checked={reviewedImages.includes(index)} onChange={event => setReviewedImages(current => event.target.checked ? [...current, index] : current.filter(value => value !== index))} />已核对这张图片无可识别身份信息</label>}
            </div>)}
            {!blindReview.report.blocked && <label className="graphic-check"><input type="checkbox" checked={reviewedDocument} onChange={event => setReviewedDocument(event.target.checked)} />已核对词库完整，并人工检查全文及模板中的身份线索</label>}
            <div className="graphic-header-actions"><button className="light-btn" type="button" onClick={() => { blindReview.resolve(false); setBlindReview(null) }}>返回修改</button>{!blindReview.report.blocked && <button className="dark-btn" type="button" disabled={!reviewedDocument || reviewedImages.length !== blindReview.report.images.length} onClick={() => { blindReview.resolve(true); setBlindReview(null) }}>确认复核并下载</button>}</div>
          </div>
        </div>
      </div>}
      {exportRiskPromptOpen && exportRiskAudit && <div className="knowledge-detail-modal" role="dialog" aria-modal="true" aria-label="导出风险确认">
        <div className="knowledge-detail-modal-panel graphic-risk-confirm-modal">
          <header><strong>导出前风险确认</strong><button type="button" onClick={() => { setExportRiskPromptOpen(false); setExportRiskAudit(null) }}>关闭</button></header>
          <div className="knowledge-detail-modal-body">
            <p className="graphic-risk-confirm-message">存在可能影响投标的风险项，确认继续导出？</p>
            <div className="graphic-risk-confirm-list">{exportRiskAudit.risks.filter(risk => risk.severity === 'high').map(risk => <div className="graphic-risk-confirm-item" key={risk.risk_id}><span className="graphic-risk-tag high">高风险</span><span>{risk.issue}</span><em>{risk.chapter_title}{risk.asset_id ? ` · 图片 #${risk.asset_id}` : ' · 正文'}</em></div>)}</div>
            <div className="graphic-header-actions"><button className="light-btn" type="button" onClick={() => { setExportRiskPromptOpen(false); setExportRiskAudit(null) }}>返回修改</button><button className="dark-btn" type="button" onClick={() => { setExportRiskPromptOpen(false); setExportRiskAudit(null); void exportTemplateDocx(exportRiskAudit?.risks || []) }}>仍要导出</button></div>
          </div>
        </div>
      </div>}
      {textInputDialog}
    </div>
  )
}

function AssetStrip({ assets, compact }: { assets: MermaidAsset[]; compact?: boolean }) {
  return <div className={compact ? 'graphic-asset-strip compact' : 'graphic-asset-strip'}><strong>本项目图表</strong>{assets.length ? assets.slice(0, compact ? 6 : 4).map(asset => <span key={asset.id}>#{asset.id} {asset.chart_title}</span>) : <em>暂无图表资产</em>}</div>
}

function ImageAssetStrip({ images, onView, onReplace, onRemove, replaceTargetId, replacementAssets, replacementLoading, onSelectReplacement, onCloseReplacement }: { images: KnowledgeAsset[]; onView: (asset: KnowledgeAsset) => void; onReplace: (asset: KnowledgeAsset) => void; onRemove: (asset: KnowledgeAsset) => void; replaceTargetId: number | null; replacementAssets: KnowledgeAsset[]; replacementLoading: boolean; onSelectReplacement: (asset: KnowledgeAsset) => void; onCloseReplacement: () => void }) {
  return <div className="graphic-asset-strip compact">
    <strong>已匹配素材</strong>
    {images.length ? <div className="graphic-matched-image-list">{images.slice(0, 8).map(image => {
      const expiry = imageExpiryMeta(image)
      const subtype = imageSubtypeLabel(image)
      return <div className="graphic-matched-image-row" key={image.asset_id}>
        <button className="graphic-matched-image-thumb" type="button" title="查看大图" onClick={() => onView(image)}>{assetUrl(image.resource_path) ? <img src={assetUrl(image.resource_path)} alt={subtype} /> : <span>#{image.asset_id}</span>}</button>
        <div className="graphic-matched-image-meta">
          <strong>#{image.asset_id} {image.annotation || image.ai_description || image.item_title || '知识库图片'}</strong>
          <div><span className="graphic-match-subtype">{subtype}</span><span className={`graphic-expiry-${expiry.status}`}>{expiry.label}</span>{image.reuse_material && <span className="graphic-image-reuse">复用素材</span>}</div>
          <div className="graphic-matched-image-actions"><button className="light-btn" type="button" onClick={() => onView(image)}>查看</button><button className="light-btn" type="button" onClick={() => onReplace(image)}>替换</button><button className="plain-btn" type="button" onClick={() => onRemove(image)}>移除</button></div>
        </div>
        {replaceTargetId === image.asset_id && <div className="graphic-replacement-picker">
          <div className="graphic-replacement-header"><strong>选择替换素材</strong><button className="plain-btn" type="button" onClick={onCloseReplacement}>关闭</button></div>
          {replacementLoading ? <em>加载候选素材中…</em> : replacementAssets.length ? replacementAssets.slice(0, 8).map(candidate => <button className="graphic-replacement-option" type="button" key={candidate.asset_id} onClick={() => onSelectReplacement(candidate)}><span>#{candidate.asset_id}</span><span className="graphic-match-subtype">{imageSubtypeLabel(candidate)}</span><span className={`graphic-expiry-${imageExpiryMeta(candidate).status}`}>{imageExpiryMeta(candidate).label}</span></button>) : <em>暂无同类候选素材</em>}
        </div>}
      </div>
    })}</div> : <em>生成时将按章节从知识库匹配真实业务图片</em>}
  </div>
}

function RiskPanel({ risks, open, onToggle, onLocate }: { risks: AssetRisk[]; open: boolean; onToggle: () => void; onLocate: (risk: AssetRisk) => void }) {
  const high = risks.filter(risk => risk.severity === 'high')
  const medium = risks.filter(risk => risk.severity === 'medium')
  const low = risks.filter(risk => risk.severity === 'low')
  return <section className="graphic-risk-panel">
    <button className="graphic-risk-panel-toggle" type="button" onClick={onToggle} aria-expanded={open}>
      <strong>风险提示</strong>{risks.length > 0 && <span className="graphic-risk-count">{risks.length}</span>}<span>{open ? '收起' : '展开'}</span>
    </button>
    {open && <div className="graphic-risk-panel-body">
      {!risks.length ? <em>未发现已知的正文或素材风险，提交前仍需人工核对。</em> : <>
        {([['high', '高风险', high], ['medium', '中风险', medium], ['low', '低风险', low]] as const).map(([severity, label, group]) => group.length ? <div className="graphic-risk-group" key={severity}>
          <strong><span className={`graphic-risk-tag ${severity}`}>{label}</span>{group.length} 项</strong>
          {group.map(risk => <div className="graphic-risk-item" key={risk.risk_id}>
            <div className="graphic-risk-thumb">{assetUrl(risk.asset?.resource_path) ? <img src={assetUrl(risk.asset?.resource_path)} alt={`图片 #${risk.asset_id}`} /> : <span>{risk.asset_id ? `#${risk.asset_id}` : '正文'}</span>}</div>
            <div className="graphic-risk-item-content"><strong>{risk.issue}</strong><span>{risk.chapter_title}{risk.asset_id ? ` · 图片 #${risk.asset_id}` : ' · 正文'}{risk.excerpt ? ` · ${risk.excerpt}` : ''}</span><button className="light-btn" type="button" onClick={() => onLocate(risk)}>定位</button></div>
          </div>)}
        </div> : null)}
      </>}
    </div>}
  </section>
}

function VerificationResults({ result, onLocate }: { result: AssetVerificationResult; onLocate: (risk: AssetRisk) => void }) {
  return <section className="graphic-risk-panel">
    <strong>{result.asset_type === 'invoices' ? '证件发票核验 · 发票' : '证件发票核验 · 证件'}</strong>
    <span>通过 {result.summary.passed} · 异常 {result.summary.abnormal}</span>
    {result.risks.length ? result.risks.map(risk => <div className="graphic-risk-item" key={`verify-${risk.risk_id}`}>
      <div className="graphic-risk-thumb">{assetUrl(risk.asset?.resource_path) ? <img src={assetUrl(risk.asset?.resource_path)} alt={`素材 #${risk.asset_id}`} /> : <span>#{risk.asset_id}</span>}</div>
      <div className="graphic-risk-item-content"><strong>{risk.issue}</strong><span>{risk.chapter_title} · 素材 #{risk.asset_id}</span><button className="light-btn" type="button" disabled={!risk.chapter_id} onClick={() => onLocate(risk)}>定位</button></div>
    </div>) : <em>本次核验未发现异常。</em>}
  </section>
}

function MarkdownPreview({ markdown, assets, images, risks, onAssetsLoaded, onImageClick, onTableDoubleClick, onTableContextMenu }: { markdown: string; assets: MermaidAsset[]; images: KnowledgeAsset[]; risks?: AssetRisk[]; onAssetsLoaded?: (assets: KnowledgeAsset[]) => void; onImageClick?: (asset: KnowledgeAsset) => void; onTableDoubleClick?: (target: MarkdownTableTarget) => void; onTableContextMenu?: (target: MarkdownTableTarget, x: number, y: number) => void }) {
  const assetMap = useMemo(() => new Map(assets.map(asset => [asset.id, asset])), [assets])
  const imageMap = useMemo(() => new Map(images.map(image => [image.asset_id, image])), [images])
  const riskMap = useMemo(() => new Map<number, AssetRisk[]>(risks?.reduce((map, risk) => { const current = map.get(risk.asset_id) || []; current.push(risk); map.set(risk.asset_id, current); return map }, new Map<number, AssetRisk[]>()) || []), [risks])
  const imageIds = useMemo(() => imageIdsFromMarkdown(markdown), [markdown])

  useEffect(() => {
    const missing = imageIds.filter(id => !imageMap.has(id)).slice(0, 12)
    if (!missing.length || !onAssetsLoaded) return
    let cancelled = false
    Promise.all(missing.map(id => wordagentApi.getKnowledgeAsset(id).then(res => res.code === 0 ? res.data : null).catch(() => null))).then(results => {
      if (cancelled) return
      const loaded = results.filter(Boolean) as KnowledgeAsset[]
      if (loaded.length) onAssetsLoaded(loaded)
    })
    return () => { cancelled = true }
  }, [imageIds.join(','), imageMap, onAssetsLoaded])

  if (!markdown.trim()) return <span className="graphic-empty">请选择章节并生成图文内容。</span>
  const blocks = markdown.split(/\n{2,}/).map(block => block.trim()).filter(Boolean)
  const rendered: ReactNode[] = []
  let index = 0
  let sourceOffset = 0
  while (index < blocks.length) {
    const editableBlock = blocks[index].replace(/^<!--TENDER_TABLE:[A-Za-z0-9+/=]+-->\n?/, '')
    const blockStart = markdown.indexOf(editableBlock, sourceOffset)
    const target = blockStart >= 0 ? { block: editableBlock, start: blockStart, end: blockStart + editableBlock.length } : null
    if (blockStart >= 0) sourceOffset = blockStart + blocks[index].length
    if (isStandaloneMediaBlock(blocks[index])) {
      const mediaItems: Array<{ media: string; caption?: string }> = []
      while (index < blocks.length && isStandaloneMediaBlock(blocks[index])) {
        const media = blocks[index]
        index += 1
        const caption = index < blocks.length && isCaptionBlock(blocks[index]) ? blocks[index++] : undefined
        mediaItems.push({ media, caption })
      }
      if (mediaItems.length >= 2) {
        rendered.push(<PreviewMediaGrid key={`media-grid-${index}`} items={mediaItems} assetMap={assetMap} imageMap={imageMap} riskMap={riskMap} onImageClick={onImageClick} />)
      } else {
        rendered.push(<PreviewBlock key={`block-${index}`} text={mediaItems[0].media} assetMap={assetMap} imageMap={imageMap} riskMap={riskMap} onImageClick={onImageClick} />)
        if (mediaItems[0].caption) rendered.push(<PreviewBlock key={`caption-${index}`} text={mediaItems[0].caption} assetMap={assetMap} imageMap={imageMap} riskMap={riskMap} onImageClick={onImageClick} />)
      }
      continue
    }
    rendered.push(<PreviewBlock key={`${index}-${blocks[index].slice(0, 20)}`} text={blocks[index]} assetMap={assetMap} imageMap={imageMap} riskMap={riskMap} onImageClick={onImageClick} tableTarget={target || undefined} onTableDoubleClick={onTableDoubleClick} onTableContextMenu={onTableContextMenu} />)
    index += 1
  }
  return <>{rendered}</>
}

function isStandaloneMediaBlock(text: string) {
  return /^\[\[(?:MERMAID_ASSET|KNOWLEDGE_IMAGE):\d+\]\]$/.test(text) || /^!\[[^\]]*\]\([^)]*\)$/.test(text) || /^<img\s+/i.test(text)
}

function isCaptionBlock(text: string) {
  return /^(图注|图|图片说明|说明|caption)[:：]/i.test(text)
}

function PreviewMediaGrid({ items, assetMap, imageMap, riskMap, onImageClick }: { items: Array<{ media: string; caption?: string }>; assetMap: Map<number, MermaidAsset>; imageMap: Map<number, KnowledgeAsset>; riskMap: Map<number, AssetRisk[]>; onImageClick?: (asset: KnowledgeAsset) => void }) {
  return <div className="graphic-image-grid" role="group" aria-label="图文素材表格">
    {items.map((item, index) => <div className="graphic-image-grid-cell" key={`${item.media}-${index}`}>
      {renderPreviewPiece(item.media, index, assetMap, imageMap, riskMap, onImageClick, item.caption)}
    </div>)}
  </div>
}

function PreviewBlock({ text, assetMap, imageMap, riskMap, onImageClick, tableTarget, onTableDoubleClick, onTableContextMenu }: { text: string; assetMap: Map<number, MermaidAsset>; imageMap: Map<number, KnowledgeAsset>; riskMap: Map<number, AssetRisk[]>; onImageClick?: (asset: KnowledgeAsset) => void; tableTarget?: MarkdownTableTarget; onTableDoubleClick?: (target: MarkdownTableTarget) => void; onTableContextMenu?: (target: MarkdownTableTarget, x: number, y: number) => void }) {
  const meta = text.match(/^<!--TENDER_TABLE:[A-Za-z0-9+/=]+-->/)?.[0]
  let layout: ReturnType<typeof parseTenderTableLayout> | undefined
  if (meta) {
    try { layout = parseTenderTableLayout(meta) } catch { return <p className="error">原表结构无效，请重新生成该表。</p> }
    text = text.slice(meta.length).trim()
  }
  const tableLines = text.split('\n').filter(line => line.trim().startsWith('|'))
  const rows = markdownTableRows(text)
  if (tableLines.length >= 2 && rows && layout) {
    if (rows.length !== layout.heights.length || rows[0].length !== layout.widths.length) return <p className="error">招标原表行列已改变，请保留原表结构。</p>
    const total = layout.widths.reduce((a,b) => a+b,0)
    return <div className="graphic-data-table-wrap graphic-data-table-editable" title="招标原表：双击填写，保留行列与报价空栏" onDoubleClick={() => tableTarget && onTableDoubleClick?.(tableTarget)}><table style={{tableLayout:'fixed'}}><colgroup>{layout.widths.map((w,i)=><col key={i} style={{width: String(w/total*100)+'%'}} />)}</colgroup><tbody>{rows.map((row,r)=><tr key={r} style={{height:String(layout!.heights[r])+'pt'}}>{layout!.cells.filter(c=>c.row===r).map(c=><td key={c.col} rowSpan={c.rowspan} colSpan={c.colspan} style={{textAlign:'center',verticalAlign:'middle'}}>{renderInlineMarkdown(row[c.col] || '')}</td>)}</tr>)}</tbody></table></div>
  }
  if (tableLines.length >= 2 && rows) {
    return <div className="graphic-data-table-wrap graphic-data-table-editable" title="双击编辑表格，右键打开表格操作" onDoubleClick={() => tableTarget && onTableDoubleClick?.(tableTarget)} onContextMenu={event => { if (!tableTarget || !onTableContextMenu) return; event.preventDefault(); onTableContextMenu(tableTarget, event.clientX, event.clientY) }}><table><thead><tr>{rows[0].map((cell, index) => <th key={index}>{renderInlineMarkdown(cell)}</th>)}</tr></thead><tbody>{rows.slice(1).map((row, rowIndex) => <tr key={rowIndex}>{rows[0].map((_, cellIndex) => <td key={cellIndex}>{renderInlineMarkdown(row[cellIndex] || '')}</td>)}</tr>)}</tbody></table></div>
  }
  const markerParts = text.split(/(\[\[(?:MERMAID_ASSET|KNOWLEDGE_IMAGE):[^\]]+\]\])/g).filter(Boolean)
  if (markerParts.length > 1) {
    return <>{markerParts.map((part, index) => renderPreviewPiece(part.trim(), index, assetMap, imageMap, riskMap, onImageClick))}</>
  }
  if (/^\[\[(?:MERMAID_ASSET|KNOWLEDGE_IMAGE):[^\]]+\]\]$/.test(text)) {
    const marker = renderPreviewPiece(text, 0, assetMap, imageMap, riskMap, onImageClick)
    if (marker) return marker
  }
  const aligned = text.match(/^<p\s+style=["']text-align:\s*(left|center|right);?["']>([\s\S]*)<\/p>$/i)
  if (aligned) return <p style={{ textAlign: aligned[1] as 'left' | 'center' | 'right' }}>{stripHtml(aligned[2])}</p>
  if (/^<img\s+/i.test(text)) return <HtmlImageFigure html={text} />
  if (text.startsWith('###')) return <h4>{renderInlineMarkdown(text.replace(/^#+\s*/, ''))}</h4>
  if (text.startsWith('##')) return <h3>{renderInlineMarkdown(text.replace(/^#+\s*/, ''))}</h3>
  if (text.startsWith('#')) return <h2>{renderInlineMarkdown(text.replace(/^#+\s*/, ''))}</h2>
  if (/^[-•]/.test(text)) return <ul>{text.split('\n').map((line, idx) => <li key={idx}>{renderInlineMarkdown(line.replace(/^[-•]\s*/, ''))}</li>)}</ul>
  if (/^\d+\.\s+/.test(text)) return <ol>{text.split('\n').map((line, idx) => <li key={idx}>{renderInlineMarkdown(line.replace(/^\d+\.\s*/, ''))}</li>)}</ol>
  if (/^>\s+/.test(text)) return <blockquote>{text.split('\n').map((line, idx) => <p key={idx}>{renderInlineMarkdown(line.replace(/^>\s*/, ''))}</p>)}</blockquote>
  const markdownImage = text.match(/^!\[(.*?)\]\((.*?)\)$/)
  if (markdownImage) return <LocalImageFigure alt={markdownImage[1]} src={markdownImage[2]} />
  return <p>{renderInlineMarkdown(text)}</p>
}

function renderPreviewPiece(text: string, index: number, assetMap: Map<number, MermaidAsset>, imageMap: Map<number, KnowledgeAsset>, riskMap: Map<number, AssetRisk[]>, onImageClick?: (asset: KnowledgeAsset) => void, captionOverride?: string) {
  const chart = text.match(/^\[\[MERMAID_ASSET:(\d+)\]\]$/)
  if (chart) {
    const asset = assetMap.get(Number(chart[1]))
    return <ChartFigure key={`chart-${chart[1]}-${index}`} asset={asset} id={chart[1]} />
  }
  const image = text.match(/^\[\[KNOWLEDGE_IMAGE:(\d+)\]\]$/)
  if (image) {
    const asset = imageMap.get(Number(image[1]))
    return <KnowledgeImageFigure key={`image-${image[1]}-${index}`} asset={asset} id={image[1]} risks={riskMap.get(Number(image[1])) || []} onClick={onImageClick} captionOverride={captionOverride} />
  }
  const invalidImage = text.match(/^\[\[KNOWLEDGE_IMAGE:([^\]]+)\]\]$/)
  if (invalidImage) {
    return <div key={`invalid-image-${invalidImage[1]}-${index}`} className="graphic-image-placeholder">图片占位符无效：{invalidImage[1]}，请重新生成正文或上传插图。</div>
  }
  if (!text) return null
  return <p key={`text-${index}`}>{renderInlineMarkdown(text)}</p>
}

function ChartFigure({ asset, id }: { asset?: MermaidAsset; id: string }) {
  return <figure className="graphic-chart-figure">{asset?.chart_image_data ? <img src={asset.chart_image_data} alt={asset.chart_title} /> : <div>图表 #{id}</div>}<figcaption>{asset ? asset.chart_title : `图表 #${id}`}</figcaption></figure>
}

function KnowledgeImageFigure({ asset, id, risks = [], onClick, captionOverride }: { asset?: KnowledgeAsset; id: string; risks?: AssetRisk[]; onClick?: (asset: KnowledgeAsset) => void; captionOverride?: string }) {
  const [failed, setFailed] = useState(false)
  const src = assetUrl(asset?.resource_path)
  const caption = captionOverride?.replace(/^(图注|图|图片说明|说明|caption)[:：]\s*/i, '') || asset?.asset_sub_type || asset?.category || `知识库图片 #${id}`
  const subtype = imageSubtypeLabel(asset)
  const expiry = imageExpiryMeta(asset)
  const tooltip = imageStructuredTooltip(asset)
  const highestRisk = risks.some(risk => risk.severity === 'high') ? 'high' : risks.some(risk => risk.severity === 'medium') ? 'medium' : risks.length ? 'low' : ''
  return (
    <figure className={`graphic-chart-figure graphic-image-figure ${highestRisk ? `graphic-risk-image ${highestRisk}` : ''}`.trim()} data-knowledge-asset-id={id} title={risks.map(risk => risk.issue).join('；') || tooltip}>
      {highestRisk && <span className={`graphic-risk-corner ${highestRisk}`}>{highestRisk === 'high' ? '风险' : highestRisk === 'medium' ? '提醒' : '核验'}</span>}
      {src && !failed
        ? <button className="graphic-image-open" type="button" title={tooltip} onClick={() => asset && onClick?.(asset)}><img src={src} alt={caption} onError={() => setFailed(true)} /></button>
        : <div className="graphic-image-placeholder"><span>知识库图片 #{id}</span><button type="button" onClick={() => setFailed(false)}>重试</button></div>}
      <figcaption title={tooltip}><span>{caption}</span><span className="graphic-image-caption-separator"> · </span><span className="graphic-match-subtype">{subtype}</span><span className={`graphic-expiry-${expiry.status}`}>{expiry.label}</span>{asset?.reuse_material && <span className="graphic-image-reuse">复用素材</span>}</figcaption>
    </figure>
  )
}

function LocalImageFigure({ alt, src }: { alt: string; src: string }) {
  const [failed, setFailed] = useState(false)
  const finalSrc = assetUrl(src)
  return (
    <figure className="graphic-chart-figure graphic-image-figure">
      {finalSrc && !failed ? <img src={finalSrc} alt={alt} onError={() => setFailed(true)} /> : <div className="graphic-image-placeholder">本地插图无法加载</div>}
      {alt && <figcaption>{alt}</figcaption>}
    </figure>
  )
}

function HtmlImageFigure({ html }: { html: string }) {
  const src = html.match(/src=["']([^"']+)["']/i)?.[1] || ''
  const alt = html.match(/alt=["']([^"']*)["']/i)?.[1] || ''
  const widthMode = html.match(/data-width=["']([^"']+)["']/i)?.[1] === 'wide' ? 'wide' : 'center'
  const [failed, setFailed] = useState(false)
  const finalSrc = assetUrl(src)
  return (
    <figure className={`graphic-chart-figure graphic-image-figure ${widthMode === 'wide' ? 'wide' : ''}`.trim()}>
      {finalSrc && !failed ? <img src={finalSrc} alt={alt} onError={() => setFailed(true)} /> : <div className="graphic-image-placeholder">本地插图无法加载</div>}
      {alt && <figcaption>{alt}</figcaption>}
    </figure>
  )
}

function KnowledgeImageViewer({ asset, scale, onScale, onClose }: { asset: KnowledgeAsset; scale: number; onScale: (scale: number) => void; onClose: () => void }) {
  const src = assetUrl(asset.resource_path)
  const caption = asset.annotation || asset.ai_description || asset.tags?.join('、') || asset.item_title || `知识库图片 #${asset.asset_id}`
  const subtype = imageSubtypeLabel(asset)
  const expiry = imageExpiryMeta(asset)
  const tooltip = imageStructuredTooltip(asset)
  function zoom(delta: number) {
    onScale(Number(clamp(scale + delta, 0.3, 5).toFixed(2)))
  }
  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.preventDefault()
    const next = scale * (event.deltaY > 0 ? 0.9 : 1.12)
    onScale(Number(clamp(next, 0.3, 5).toFixed(3)))
  }
  return (
    <div className="graphic-image-viewer" role="dialog" aria-modal="true" aria-label="知识库图片预览">
      <div className="graphic-image-viewer-toolbar">
        <strong>{asset.item_title || asset.source_name || `知识库图片 #${asset.asset_id}`}</strong>
        <div><button className="light-btn" type="button" onClick={() => zoom(-0.2)}>缩小</button><span className="graphic-zoom-value">{Math.round(scale * 100)}%</span><button className="light-btn" type="button" onClick={() => zoom(0.2)}>放大</button><button className="light-btn" type="button" onClick={() => onScale(1)}>1:1</button><button className="light-btn" type="button" onClick={onClose}>关闭</button></div>
      </div>
      <div className="graphic-image-viewer-body" onWheel={handleWheel}>
        {src ? <img src={src} alt={caption} title={tooltip} style={{ transform: `scale(${scale})` }} /> : <div className="graphic-image-placeholder">图片路径不可用</div>}
      </div>
      <div className="graphic-image-viewer-caption" title={tooltip}><strong>图注：</strong>{caption}<span className="graphic-image-caption-separator"> · </span><span className="graphic-match-subtype">{subtype}</span><span className={`graphic-expiry-${expiry.status}`}>{expiry.label}</span>{asset.reuse_material && <span className="graphic-image-reuse">复用素材</span>}{asset.page_num ? `（来源页码：第 ${asset.page_num} 页）` : ''}</div>
    </div>
  )
}

type ImageExpiryMeta = { status: 'valid' | 'expiring' | 'expired' | 'unknown'; label: string }

function imageSubtypeLabel(asset?: KnowledgeAsset) {
  return asset?.asset_sub_type || asset?.structured_info?.asset_sub_type || asset?.category || '图片资料'
}

function imageExpiryMeta(asset?: KnowledgeAsset): ImageExpiryMeta {
  if (asset?.expire_status) {
    const labels: Record<ImageExpiryMeta['status'], string> = { valid: '有效', expiring: '临期', expired: '过期', unknown: '有效期未知' }
    return { status: asset.expire_status, label: asset.expire_label || labels[asset.expire_status] }
  }
  const raw = asset?.expire_date?.trim()
  if (!raw) return { status: 'unknown', label: '有效期未知' }
  const expiry = new Date(`${raw}T23:59:59`)
  if (Number.isNaN(expiry.getTime())) return { status: 'unknown', label: '有效期未知' }
  const days = Math.ceil((expiry.getTime() - Date.now()) / 86400000)
  if (days < 0) return { status: 'expired', label: '过期' }
  if (days <= 30) return { status: 'expiring', label: '临期' }
  return { status: 'valid', label: '有效' }
}

function imageStructuredTooltip(asset?: KnowledgeAsset) {
  if (!asset) return '知识库图片信息暂不可用'
  const fields = Object.entries(asset.structured_info?.fields || {}).filter(([, value]) => String(value || '').trim()).slice(0, 8).map(([key, value]) => `${key}：${value}`)
  const base = [`细分类别：${imageSubtypeLabel(asset)}`, `有效期：${asset.expire_date || '未知'}`]
  if (asset.cert_no) base.push(`证件编号：${asset.cert_no}`)
  if (asset.holder_name) base.push(`持证主体：${asset.holder_name}`)
  if (fields.length) base.push(...fields)
  return base.join('；')
}

function riskSummaryText(risks: AssetRisk[]) {
  const high = risks.filter(risk => risk.severity === 'high').length
  const medium = risks.filter(risk => risk.severity === 'medium').length
  const low = risks.filter(risk => risk.severity === 'low').length
  return `高风险 ${high}、中风险 ${medium}、低风险 ${low}`
}

function mergeKnowledgeAssets(current: KnowledgeAsset[], incoming: KnowledgeAsset[]) {
  const map = new Map(current.map(asset => [asset.asset_id, asset]))
  incoming.forEach(asset => map.set(asset.asset_id, asset))
  return Array.from(map.values())
}

function markdownTableRows(value: string): string[][] | null {
  const lines = value.split('\n').map(line => line.trim()).filter(Boolean)
  if (lines.length < 2 || !lines.every(line => line.startsWith('|'))) return null
  const rows = lines
    .filter(line => !/^\|?\s*:?-{3,}/.test(line))
    .map(line => line.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim()))
  return rows.length >= 2 ? rows : null
}

function applyIllustrationWidth(markdown: string, mode: 'center' | 'wide', fallbackCaption: string) {
  if (mode !== 'wide') return markdown
  const match = markdown.match(/^!\[(.*?)\]\((.*?)\)([\s\S]*)$/)
  if (!match) return markdown
  const caption = escapeHtml(match[1] || fallbackCaption || '用户上传插图')
  const src = escapeHtml(match[2])
  const tail = (match[3] || '').trim()
  return `<img src="${src}" alt="${caption}" data-width="wide" />${tail ? `\n\n${tail}` : ''}`
}

function prefixLines(text: string, prefix: string) {
  return text.split('\n').map(line => line.trim() ? `${prefix}${line.replace(/^#{1,6}\s*/, '')}` : line).join('\n')
}

function escapeHtml(text: string) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function stripHtml(text: string) {
  const element = document.createElement('div')
  element.innerHTML = text
  return element.textContent || element.innerText || ''
}

function renderInlineMarkdown(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~|<u>[^<]+<\/u>)/g).filter(Boolean)
  if (!parts.length) return text
  return parts.map((part, index) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={index}>{part.slice(2, -2)}</strong>
    if (/^\*[^*]+\*$/.test(part)) return <em key={index}>{part.slice(1, -1)}</em>
    if (/^~~[^~]+~~$/.test(part)) return <s key={index}>{part.slice(2, -2)}</s>
    if (/^<u>[^<]+<\/u>$/.test(part)) return <u key={index}>{part.slice(3, -4)}</u>
    return <span key={index}>{part}</span>
  })
}

function flattenCatalog(nodes: CatalogNode[], prefix: number[] = []): Array<CatalogNode & { number: string }> {
  return nodes.flatMap((node, index) => {
    const parts = [...prefix, index + 1]
    return [{ ...node, number: parts.join('.') }, ...flattenCatalog(node.children || [], parts)]
  })
}

function getLeafChapterIds(node: CatalogNode): number[] {
  const children = node.children || []
  return children.length ? children.flatMap(getLeafChapterIds) : [node.chapter_id]
}

function getCatalogPathText(chapter: CatalogNode, chapters: CatalogNode[]) {
  const byId = new Map(chapters.map(item => [item.chapter_id, item]))
  const path: string[] = []
  let current: CatalogNode | undefined = chapter
  let depth = 0
  while (current && depth < 5) {
    path.push(stripCatalogNumber(current.title))
    current = current.parent_id ? byId.get(current.parent_id) : undefined
    depth += 1
  }
  return path.reverse().join(' ')
}

function chartIdsFromMarkdown(markdown: string) { return Array.from(new Set([...markdown.matchAll(/\[\[MERMAID_ASSET:(\d+)\]\]/g)].map(match => Number(match[1])))) }
function imageIdsFromMarkdown(markdown: string) { return Array.from(new Set([...markdown.matchAll(/\[\[KNOWLEDGE_IMAGE:(\d+)\]\]/g)].map(match => Number(match[1])))) }
function replaceKnowledgeImageInMarkdown(markdown: string, oldId: number, newId: number) {
  return markdown.replace(new RegExp(`\\[\\[KNOWLEDGE_IMAGE:${oldId}\\]\\]`, 'g'), `[[KNOWLEDGE_IMAGE:${newId}]]`)
}
function removeKnowledgeImageFromMarkdown(markdown: string, assetId: number) {
  return markdown.replace(new RegExp(`\\n?\\s*\\[\\[KNOWLEDGE_IMAGE:${assetId}\\]\\]\\s*\\n?`, 'g'), '\n\n').replace(/\n{3,}/g, '\n\n').trim()
}
function stripCatalogNumber(title: string) { return (title || '').replace(/^\s*(?:第[一二三四五六七八九十百千万\d]+[章节篇部分]\s*)+/, '').replace(/^\s*\d+(?:\.\d+)+\s*/, '').replace(/^\s*\d+(?:[、.．]\s*|\s+)/, '').trim() }

function assetUrl(path?: string | null) {
  if (!path) return ''
  if (/^(https?:|data:|blob:)/i.test(path)) return path
  const normalized = path.replace(/\\/g, '/')
  const origin = wordagentApi.apiBase.replace(/\/api\/?$/, '')
  const uploadIndex = normalized.toLowerCase().lastIndexOf('data/uploads/')
  if (uploadIndex >= 0) return `${origin}/uploads/${normalized.slice(uploadIndex + 'data/uploads/'.length)}`
  if (/^[a-z]:\//i.test(normalized)) return ''
  if (normalized.startsWith('/')) return `${origin}${normalized}`
  return `${origin}/${normalized}`
}

function delay(ms: number) { return new Promise(resolve => window.setTimeout(resolve, ms)) }

async function loadMermaidRuntime(): Promise<MermaidRuntime | null> {
  return mermaid
}

async function renderMermaidToSvg(code: string) {
  const runtime = await loadMermaidRuntime()
  if (runtime) {
    runtime.initialize({ startOnLoad: false, securityLevel: 'loose', theme: 'base', flowchart: { curve: 'basis' } })
    const { svg } = await runtime.render(`bid-mermaid-${Date.now()}`, code)
    return svg
  }
  return fallbackSvg(code)
}

function fallbackSvg(code: string) {
  const flow = fallbackFlowSvg(code)
  if (flow) return flow
  const lines = code.split('\n').slice(0, 9).map(line => line.trim()).filter(Boolean)
  const text = lines.map((line, index) => `<text x="46" y="${94 + index * 32}" font-size="17" fill="#3A3A32">${escapeXml(line.slice(0, 72))}</text>`).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" width="980" height="560" viewBox="0 0 980 560"><rect width="980" height="560" rx="24" fill="#fbfaf7"/><rect x="26" y="26" width="928" height="508" rx="18" fill="#fff" stroke="#dddbd4"/><text x="46" y="64" font-size="24" font-weight="800" fill="#2b2b28">本地 Mermaid 预览</text>${text}</svg>`
}

function fallbackFlowSvg(code: string) {
  if (!/^\s*(flowchart|graph)\s+/i.test(code)) return ''
  const nodeMap = new Map<string, string>()
  const edges: Array<[string, string]> = []
  const nodePattern = /([A-Za-z][\w-]*)\s*(?:\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\})?/g
  code.split('\n').forEach(line => {
    const edge = line.match(/([A-Za-z][\w-]*)\s*-+>|==>|--\s*([A-Za-z][\w-]*)/)
    if (line.includes('-->')) {
      const parts = line.split('-->').map(part => part.trim())
      if (parts.length >= 2) edges.push([extractNodeId(parts[0]), extractNodeId(parts[1])])
    } else if (edge) edges.push([edge[1], edge[2]])
    for (const match of line.matchAll(nodePattern)) {
      const id = match[1]
      if (/^(flowchart|graph|subgraph|end|LR|TB|TD|RL|BT)$/i.test(id)) continue
      const label = match[2] || match[3] || match[4] || id
      nodeMap.set(id, label.replace(/"/g, '').slice(0, 22))
    }
  })
  const ids = Array.from(nodeMap.keys()).slice(0, 8)
  if (!ids.length) return ''
  const width = 980, height = 560
  const boxW = 150, boxH = 58
  const gap = Math.max(24, Math.min(78, (width - 92 - ids.length * boxW) / Math.max(1, ids.length - 1)))
  const y = 250
  const positions = new Map(ids.map((id, index) => [id, { x: 46 + index * (boxW + gap), y }]))
  const edgeSvg = edges.slice(0, 10).map(([from, to]) => {
    const a = positions.get(from), b = positions.get(to)
    if (!a || !b) return ''
    return `<line x1="${a.x + boxW}" y1="${a.y + boxH / 2}" x2="${b.x}" y2="${b.y + boxH / 2}" stroke="#3A3A32" stroke-width="2.2" marker-end="url(#arrow)"/>`
  }).join('')
  const nodeSvg = ids.map(id => {
    const pos = positions.get(id)!
    return `<g><rect x="${pos.x}" y="${pos.y}" width="${boxW}" height="${boxH}" rx="12" fill="#ffffff" stroke="#dddbd4"/><text x="${pos.x + boxW / 2}" y="${pos.y + 35}" text-anchor="middle" font-size="15" font-weight="700" fill="#2b2b28">${escapeXml(nodeMap.get(id) || id)}</text></g>`
  }).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#3A3A32"/></marker></defs><rect width="${width}" height="${height}" rx="24" fill="#fbfaf7"/><rect x="26" y="26" width="928" height="508" rx="18" fill="#f0ede6" stroke="#dddbd4"/><text x="46" y="70" font-size="24" font-weight="900" fill="#2b2b28">流程图预览</text>${edgeSvg}${nodeSvg}</svg>`
}

function extractNodeId(text: string) { return (text.match(/([A-Za-z][\w-]*)/) || ['', text])[1] }

function escapeXml(text: string) { return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') }

function triggerDownload(href: string, filename: string, revoke = false) {
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  if (revoke) window.setTimeout(() => URL.revokeObjectURL(href), 800)
}

function clamp(value: number, min: number, max: number) { return Math.max(min, Math.min(max, value)) }

function svgToPngDataUrl(svg: string, options: { scale?: number; trim?: boolean; padding?: number } = {}) {
  return new Promise<string>((resolve, reject) => {
    const size = getSvgNaturalSize(svg)
    const padding = Math.max(0, options.padding ?? 20)
    const exportSvg = normalizeSvgForExport(svg, size.width, size.height)
    const img = new Image()
    const blob = new Blob([exportSvg], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    img.onload = () => {
      const scale = options.scale || 1
      const baseWidth = size.width
      const baseHeight = size.height
      const logicalWidth = baseWidth + padding * 2
      const logicalHeight = baseHeight + padding * 2
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(logicalWidth * scale))
      canvas.height = Math.max(1, Math.round(logicalHeight * scale))
      const ctx = canvas.getContext('2d')
      if (!ctx) { URL.revokeObjectURL(url); reject(new Error('无法创建预览画布')); return }
      ctx.setTransform(scale, 0, 0, scale, 0, 0)
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, logicalWidth, logicalHeight)
      ctx.drawImage(img, padding, padding, baseWidth, baseHeight)
      URL.revokeObjectURL(url)
      if (!options.trim) {
        resolve(canvas.toDataURL('image/png'))
        return
      }
      resolve(trimCanvasWhitespace(canvas, options.padding ?? 24).toDataURL('image/png'))
    }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('SVG转换失败')) }
    img.src = url
  })
}

function getSvgNaturalSize(svg: string) {
  const fallback = { width: 980, height: 560 }
  try {
    const documentSvg = new DOMParser().parseFromString(svg, 'image/svg+xml').querySelector('svg')
    if (!documentSvg) return fallback
    const viewBox = documentSvg.getAttribute('viewBox')
    if (viewBox) {
      const parts = viewBox.trim().split(/[\s,]+/).map(Number)
      const width = parts[2]
      const height = parts[3]
      if (Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0) return { width, height }
    }
    const width = parseSvgLength(documentSvg.getAttribute('width'))
    const height = parseSvgLength(documentSvg.getAttribute('height'))
    if (width > 0 && height > 0) return { width, height }
  } catch {
    // Keep safe defaults if the SVG cannot be parsed.
  }
  return fallback
}

function parseSvgLength(value: string | null) {
  if (!value) return 0
  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function normalizeSvgForDisplay(svg: string, width: number, height: number) {
  try {
    const parser = new DOMParser()
    const doc = parser.parseFromString(svg, 'image/svg+xml')
    const root = doc.querySelector('svg')
    if (!root) return svg
    root.setAttribute('width', String(width))
    root.setAttribute('height', String(height))
    if (!root.getAttribute('viewBox')) root.setAttribute('viewBox', `0 0 ${width} ${height}`)
    root.setAttribute('preserveAspectRatio', 'xMidYMid meet')
    root.removeAttribute('style')
    root.classList.add('mermaid-display-svg')
    return new XMLSerializer().serializeToString(root)
  } catch {
    return svg
  }
}

function normalizeSvgForExport(svg: string, width: number, height: number) {
  try {
    const parser = new DOMParser()
    const doc = parser.parseFromString(svg, 'image/svg+xml')
    const root = doc.querySelector('svg')
    if (!root) return svg
    root.setAttribute('width', String(width))
    root.setAttribute('height', String(height))
    if (!root.getAttribute('viewBox')) root.setAttribute('viewBox', `0 0 ${width} ${height}`)
    root.setAttribute('preserveAspectRatio', 'xMidYMid meet')
    root.removeAttribute('style')
    return new XMLSerializer().serializeToString(root)
  } catch {
    return svg
  }
}

function trimCanvasWhitespace(canvas: HTMLCanvasElement, padding: number) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return canvas
  const { width, height } = canvas
  const data = ctx.getImageData(0, 0, width, height).data
  let minX = width, minY = height, maxX = 0, maxY = 0
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4
      const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3]
      if (a > 12 && (r < 246 || g < 246 || b < 246)) {
        if (x < minX) minX = x
        if (y < minY) minY = y
        if (x > maxX) maxX = x
        if (y > maxY) maxY = y
      }
    }
  }
  if (minX >= width || minY >= height) return canvas
  const left = Math.max(0, minX - padding)
  const top = Math.max(0, minY - padding)
  const right = Math.min(width, maxX + padding)
  const bottom = Math.min(height, maxY + padding)
  const out = document.createElement('canvas')
  out.width = Math.max(1, right - left)
  out.height = Math.max(1, bottom - top)
  const outCtx = out.getContext('2d')
  if (!outCtx) return canvas
  outCtx.fillStyle = '#ffffff'
  outCtx.fillRect(0, 0, out.width, out.height)
  outCtx.drawImage(canvas, left, top, out.width, out.height, 0, 0, out.width, out.height)
  return out
}
