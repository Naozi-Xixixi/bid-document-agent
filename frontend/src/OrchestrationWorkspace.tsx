import { AccountNavActions } from './AccountNavActions'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { DOCX_EXPORT_TEMPLATES, type DocxExportTemplateId } from './docxExport'
import { inspectWordFormatReference, type WordTemplateFormatRules } from './utils/customWordTemplateStore'
import { type ApiProject, type CatalogNode, type GraphicSection, type KnowledgeAsset, wordagentApi } from './wordagentApi'
import { companyStorageKey } from './auth'

const FORMAT_SETTINGS_KEY = companyStorageKey('new_biaoshuai.formatSettings')

type Props = {
  bidId: number | null
  onBidChange: (bidId: number) => void
  ensureBid: () => Promise<number | null>
  onHome: () => void
  onOpenGraphic: () => void
}

type ChapterOption = CatalogNode & { number: string }

function apiErrorMessage(error: unknown, fallback: string) {
  const raw = error instanceof Error ? error.message : String(error ?? '')
  try {
    const parsed = JSON.parse(raw)
    return parsed?.message || parsed?.detail || fallback
  } catch {
    return raw || fallback
  }
}

function flattenCatalog(nodes: CatalogNode[], prefix: number[] = []): ChapterOption[] {
  return nodes.flatMap((node, index) => {
    const number = [...prefix, index + 1].join('.')
    return [{ ...node, number }, ...flattenCatalog(node.children || [], [...prefix, index + 1])]
  })
}

function assetUrl(value?: string | null) {
  if (!value) return ''
  if (/^(https?:|data:|blob:)/i.test(value)) return value
  const origin = new URL(wordagentApi.apiBase).origin
  const normalized = value.replace(/\\/g, '/').replace(/^\/+/, '')
  const uploadIndex = normalized.toLowerCase().lastIndexOf('data/uploads/')
  if (uploadIndex >= 0) return `${origin}/uploads/${normalized.slice(uploadIndex + 'data/uploads/'.length)}`
  if (normalized.toLowerCase().startsWith('uploads/')) return `${origin}/${normalized}`
  return `${origin}/uploads/${normalized.split('/').pop() || normalized}`
}

function cleanHeading(text: string) {
  return text.replace(/^#{1,6}\s*/, '').replace(/^\d+(?:\.\d+)*[、.．]?\s*/, '')
}

function inlineText(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~)/g).filter(Boolean)
  if (parts.length === 1) return text
  return parts.map((part, index) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={index}>{part.slice(2, -2)}</strong>
    if (/^\*[^*]+\*$/.test(part)) return <em key={index}>{part.slice(1, -1)}</em>
    if (/^~~[^~]+~~$/.test(part)) return <s key={index}>{part.slice(2, -2)}</s>
    return <span key={index}>{part}</span>
  })
}

function isImageMarker(value: string) {
  return /^\[\[(?:KNOWLEDGE_IMAGE|MERMAID_ASSET):\d+\]\]$/.test(value) || /^!\[[^\]]*\]\([^)]+\)$/.test(value)
}

function imageId(value: string) {
  return Number(value.match(/:(\d+)\]\]$/)?.[1] || 0)
}

function captionText(value?: string) {
  return value?.replace(/^(图注|图片说明|说明|caption)[:：]?\s*/i, '').trim() || '企业资质与服务能力资料'
}

function formatDate(value?: string | null) {
  return value ? value.replace('T', ' ').slice(0, 16) : '暂无时间'
}

function EnterpriseHeader() {
  return (
    <div className="format-enterprise-header" aria-label="企业页眉">
      <div className="format-enterprise-brand"><i aria-hidden="true" /><span>农业产业化龙头企业</span></div>
      <div className="format-enterprise-bar">示例公司标书编排中心 <b>&gt;&gt;&gt;</b></div>
      <strong>示例配送</strong>
    </div>
  )
}

function EnterpriseFooter() {
  return (
    <div className="format-enterprise-footer" aria-label="企业页脚">
      <span>汇集四方绿色&nbsp;&nbsp;配送万家新鲜</span>
      <strong>1</strong>
      <b>&gt;&gt;&gt;</b>
    </div>
  )
}

function FormatPreview({ section, chapterTitle }: { section: GraphicSection | null; chapterTitle: string }) {
  const [loadedAssets, setLoadedAssets] = useState<KnowledgeAsset[]>(section?.images || section?.selected_images || [])
  const blocks = useMemo(() => (section?.markdown || '').split(/\n{2,}/).map(item => item.trim()).filter(Boolean), [section?.markdown])
  const assetMap = useMemo(() => new Map(loadedAssets.map(asset => [asset.asset_id, asset])), [loadedAssets])
  const chartMap = useMemo(() => new Map((section?.charts || []).map(chart => [chart.id, chart])), [section?.charts])

  useEffect(() => {
    setLoadedAssets(section?.images || section?.selected_images || [])
    const ids = (section?.markdown || '').match(/\[\[KNOWLEDGE_IMAGE:(\d+)\]\]/g)?.map(item => imageId(item)) || []
    const missing = Array.from(new Set(ids)).filter(id => !loadedAssets.some(asset => asset.asset_id === id)).slice(0, 12)
    if (!missing.length) return
    let cancelled = false
    Promise.all(missing.map(id => wordagentApi.getKnowledgeAsset(id).then(res => res.code === 0 ? res.data : null).catch(() => null))).then(items => {
      if (cancelled) return
      const additions = items.filter(Boolean) as KnowledgeAsset[]
      if (additions.length) setLoadedAssets(current => Array.from(new Map([...current, ...additions].map(asset => [asset.asset_id, asset])).values()))
    })
    return () => { cancelled = true }
  }, [section?.chapter_id, section?.markdown])

  if (!section?.markdown?.trim()) return <div className="format-preview-empty">当前章节还没有生成正文，请先在图文标书正文页生成内容。</div>

  const rendered: ReactNode[] = []
  let index = 0
  const firstBlockIsHeading = /^#{1,2}\s*/.test(blocks[0] || '') || /^\d+\D\s*/.test(blocks[0] || '')
  if (chapterTitle && !firstBlockIsHeading) rendered.push(<h2 className="format-section-band" key="chapter-heading">{chapterTitle}</h2>)
  while (index < blocks.length) {
    const block = blocks[index]
    if (isImageMarker(block)) {
      const media: Array<{ id: number; kind: 'knowledge' | 'chart' | 'local'; src?: string; alt?: string; caption?: string }> = []
      while (index < blocks.length && isImageMarker(blocks[index])) {
        const token = blocks[index]
        const chart = token.match(/^\[\[MERMAID_ASSET:(\d+)\]\]$/)
        const knowledge = token.match(/^\[\[KNOWLEDGE_IMAGE:(\d+)\]\]$/)
        const local = token.match(/^!\[(.*?)\]\((.*?)\)$/)
        const id = imageId(token)
        const caption = blocks[index + 1] && !isImageMarker(blocks[index + 1]) && /^(图注|图片说明|说明|caption)[:：]/i.test(blocks[index + 1]) ? blocks[index + 1] : undefined
        media.push({ id, kind: chart ? 'chart' : knowledge ? 'knowledge' : 'local', src: local?.[2], alt: local?.[1], caption })
        index += caption ? 2 : 1
      }
      rendered.push(<div className="format-image-table" key={`images-${index}`}>{media.map((item, mediaIndex) => { const asset = assetMap.get(item.id); const chart = chartMap.get(item.id); const src = item.kind === 'local' ? assetUrl(item.src) : item.kind === 'chart' ? chart?.chart_image_data || '' : assetUrl(asset?.resource_path); const caption = item.caption || chart?.chart_title || item.alt || asset?.annotation || asset?.ai_description; return <figure className="format-image-cell" key={`${item.kind}-${item.id}-${mediaIndex}`}>{src ? <img src={src} alt={captionText(caption ?? undefined)} /> : <div className="format-image-placeholder">图片 #{item.id || mediaIndex + 1}</div>}<figcaption>{captionText(caption ?? undefined)}</figcaption></figure> })}</div>)
      continue
    }
    index += 1
    if (/^#{1,2}\s*/.test(block) || /^\d+[、.．]\s*/.test(block)) {
      rendered.push(<h2 className="format-section-band" key={`heading-${index}`}>{cleanHeading(block)}</h2>)
      continue
    }
    if (/^#{3,6}\s*/.test(block)) {
      rendered.push(<h3 className="format-subheading" key={`subheading-${index}`}>{cleanHeading(block)}</h3>)
      continue
    }
    if (/^[-*]\s+/.test(block)) {
      rendered.push(<ul key={`list-${index}`}>{block.split('\n').map(line => <li key={line}>{inlineText(line.replace(/^[-*]\s+/, ''))}</li>)}</ul>)
      continue
    }
    if (/^\d+\.\s+/.test(block)) {
      rendered.push(<ol key={`ordered-${index}`}>{block.split('\n').map(line => <li key={line}>{inlineText(line.replace(/^\d+\.\s+/, ''))}</li>)}</ol>)
      continue
    }
    rendered.push(<p key={`paragraph-${index}`}>{inlineText(block.replace(/\n/g, ''))}</p>)
  }
  return <>{rendered}</>
}

export function OrchestrationWorkspace({ bidId, onBidChange, ensureBid, onHome, onOpenGraphic }: Props) {
  const [projects, setProjects] = useState<ApiProject[]>([])
  const [activeBidId, setActiveBidId] = useState<number | null>(bidId)
  const [activeProjectId, setActiveProjectId] = useState('')
  const [chapters, setChapters] = useState<ChapterOption[]>([])
  const [selectedChapterId, setSelectedChapterId] = useState<number | null>(null)
  const [section, setSection] = useState<GraphicSection | null>(null)
  const [templateId, setTemplateId] = useState<DocxExportTemplateId>('default-business')
  const [formatMode, setFormatMode] = useState<'open' | 'blind'>('open')
  const [formatReference, setFormatReference] = useState<{ name: string; rules: WordTemplateFormatRules } | null>(null)
  const [loading, setLoading] = useState(true)
  const [sectionLoading, setSectionLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const formatReferenceInputRef = useRef<HTMLInputElement | null>(null)

  const selectedTemplate = DOCX_EXPORT_TEMPLATES.find(template => template.id === templateId) || DOCX_EXPORT_TEMPLATES[0]
  const activeProject = projects.find(project => project.id === activeProjectId)

  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(FORMAT_SETTINGS_KEY) || '{}') as { templateId?: string; formatMode?: 'open' | 'blind'; referenceName?: string; referenceRules?: WordTemplateFormatRules }
      if (stored.templateId) setTemplateId(stored.templateId as DocxExportTemplateId)
      if (stored.formatMode) setFormatMode(stored.formatMode)
      if (stored.referenceName && stored.referenceRules) setFormatReference({ name: stored.referenceName, rules: stored.referenceRules })
    } catch {
      // Ignore invalid local preferences.
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    wordagentApi.listProjects().then(async response => {
      if (cancelled) return
      const nextProjects = response.projects || []
      setProjects(nextProjects)
      const current = nextProjects.find(project => project.id === String(bidId)) || nextProjects[0]
      if (!current) return
      setActiveProjectId(current.id)
      let nextBidId = bidId
      if (!nextBidId) nextBidId = (await wordagentApi.getProject(current.id)).bidId || null
      if (!nextBidId || cancelled) return
      setActiveBidId(nextBidId)
      if (nextBidId !== bidId) onBidChange(nextBidId)
    }).catch(errorValue => {
      if (!cancelled) setError(apiErrorMessage(errorValue, '标书项目加载失败'))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [bidId])

  useEffect(() => {
    setActiveBidId(bidId)
  }, [bidId])

  useEffect(() => {
    if (!activeBidId) return
    let cancelled = false
    wordagentApi.getBidCatalog(activeBidId).then(response => {
      if (cancelled || response.code !== 0) return
      const version = response.data.versions.find(item => item.is_final) || response.data.versions[0]
      const nextChapters = flattenCatalog(version?.catalog_content || [])
      setChapters(nextChapters)
      setSelectedChapterId(current => current && nextChapters.some(item => item.chapter_id === current) ? current : nextChapters[0]?.chapter_id || null)
    }).catch(errorValue => { if (!cancelled) setError(apiErrorMessage(errorValue, '目录加载失败')) })
    return () => { cancelled = true }
  }, [activeBidId])

  useEffect(() => {
    if (!activeBidId || !selectedChapterId) {
      setSection(null)
      return
    }
    let cancelled = false
    setSectionLoading(true)
    wordagentApi.getGraphicSection(activeBidId, selectedChapterId).then(response => {
      if (!cancelled) setSection(response.code === 0 ? response.data : null)
    }).catch(errorValue => { if (!cancelled) { setSection(null); setError(apiErrorMessage(errorValue, '章节正文加载失败')) } }).finally(() => { if (!cancelled) setSectionLoading(false) })
    return () => { cancelled = true }
  }, [activeBidId, selectedChapterId])

  async function selectProject(projectId: string) {
    setActiveProjectId(projectId)
    const detail = await wordagentApi.getProject(projectId)
    if (!detail.bidId) { setError('该项目还没有可用的标书解析任务'); return }
    setActiveBidId(detail.bidId)
    onBidChange(detail.bidId)
    window.localStorage.setItem(companyStorageKey('new_biaoshuai.lastBidId'), String(detail.bidId))
    setNotice('已切换标书项目，正在刷新章节预览。')
  }

  function saveFormatSettings() {
    window.localStorage.setItem(FORMAT_SETTINGS_KEY, JSON.stringify({ templateId, formatMode, referenceName: formatReference?.name || '', referenceRules: formatReference?.rules || null }))
    setNotice(`已选择“${selectedTemplate.name}”，正文编排预览将按该模板的标题与页面风格呈现。`)
  }

  function applyQuickFormat(nextTemplateId: DocxExportTemplateId, nextMode: 'open' | 'blind') {
    const nextTemplate = DOCX_EXPORT_TEMPLATES.find(template => template.id === nextTemplateId) || DOCX_EXPORT_TEMPLATES[0]
    setTemplateId(nextTemplateId)
    setFormatMode(nextMode)
    window.localStorage.setItem(FORMAT_SETTINGS_KEY, JSON.stringify({ templateId: nextTemplateId, formatMode: nextMode, referenceName: formatReference?.name || '', referenceRules: formatReference?.rules || null }))
    setNotice(`已切换为“${nextTemplate.name}”，正文页和 Word 导出将同步使用该格式。`)
  }

  async function handleFormatReference(file?: File | null) {
    if (!file) return
    try {
      const inspection = inspectWordFormatReference(await file.arrayBuffer())
      setFormatReference({ name: file.name, rules: inspection.formatRules })
      setNotice(`已读取“${file.name}”的页面、标题和表格格式，可作为当前编排参考。`)
    } catch (errorValue) {
      setError(apiErrorMessage(errorValue, '格式参考文件读取失败，请选择可打开的 DOCX 文件'))
    } finally {
      if (formatReferenceInputRef.current) formatReferenceInputRef.current.value = ''
    }
  }

  async function openEditor() {
    saveFormatSettings()
    setBusy(true)
    const nextBidId = activeBidId || await ensureBid()
    if (!nextBidId) { setError('请先选择或创建一个标书项目。'); setBusy(false); return }
    onOpenGraphic()
  }

  return (
    <div className="p1-app">
      <header className="top-nav"><button className="logo-button" type="button" onClick={onHome}>木石科技</button><AccountNavActions onHome={onHome} /></header>
      <main className="module-page format-center-page">
        <section className="module-header-card"><div><p>DOCUMENT FORMAT CENTER</p><h1>标书智能编排中心</h1><span>将已生成正文整理为“标题带 + 正文 + 图片表格 + 图注”的标书页面格式。</span></div><button className="light-btn" type="button" onClick={onHome}>返回首页</button></section>
        {notice && <div className="status-banner success">{notice}</div>}
        {error && <div className="status-banner error">{error}</div>}
        <section className="form-card format-quick-switch">
          <strong>一键切换格式</strong>
          <div role="group" aria-label="一键切换格式">
            <button className={templateId === 'default-business' ? 'active' : ''} type="button" onClick={() => applyQuickFormat('default-business', 'open')}>标准明标</button>
            <button className={templateId === 'enterprise-header' ? 'active' : ''} type="button" onClick={() => applyQuickFormat('enterprise-header', 'open')}>企业页眉页脚</button>
            <button className={templateId === 'compact-technical' ? 'active' : ''} type="button" onClick={() => applyQuickFormat('compact-technical', 'blind')}>精简暗标</button>
          </div>
        </section>
        <section className="form-card format-control-bar">
          <label>标书项目<select value={activeProjectId} onChange={event => void selectProject(event.target.value)} disabled={loading}><option value="">{activeBidId ? `当前项目 #${activeBidId}` : '请选择项目'}</option>{projects.map(project => <option key={project.id} value={project.id}>{project.settings.bidTitle || '未命名标书项目'}</option>)}</select></label>
          <label>章节<select value={selectedChapterId || ''} onChange={event => setSelectedChapterId(Number(event.target.value) || null)} disabled={!chapters.length || sectionLoading}><option value="">请选择章节</option>{chapters.map(chapter => <option key={chapter.chapter_id} value={chapter.chapter_id}>{chapter.number} {chapter.title}</option>)}</select></label>
          <label>明标 / 暗标<select value={formatMode} onChange={event => { const next = event.target.value as 'open' | 'blind'; setFormatMode(next); setTemplateId(next === 'blind' ? 'compact-technical' : 'default-business') }}><option value="open">明标版</option><option value="blind">暗标版</option></select></label>
          <label>页面模板<select value={templateId} onChange={event => setTemplateId(event.target.value as DocxExportTemplateId)}>{DOCX_EXPORT_TEMPLATES.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label>
          <div className="format-reference-control"><span>已有文件格式参考</span><button className="light-btn" type="button" onClick={() => formatReferenceInputRef.current?.click()}>选择 DOCX</button><input ref={formatReferenceInputRef} type="file" accept=".docx" hidden onChange={event => void handleFormatReference(event.target.files?.[0])} />{formatReference && <small title={formatReference.name}>{formatReference.name}</small>}</div>
          <button className="dark-btn" type="button" onClick={saveFormatSettings}>应用排版</button>
        </section>
        <section className="format-preview-shell">
          <div className="format-preview-toolbar"><div><strong>{selectedChapterId ? chapters.find(chapter => chapter.chapter_id === selectedChapterId)?.title : '正文排版预览'}</strong><span>{sectionLoading ? '正在加载正文...' : `${selectedTemplate.name} · ${formatMode === 'blind' ? '暗标版式' : '明标版式'} · 标题带、正文和图片表格`}</span>{formatReference && <small className="format-reference-note">参考文件：{formatReference.name} · {formatReference.rules.page.paper} · {formatReference.rules.table}</small>}</div><button className="light-btn" type="button" onClick={() => void openEditor()} disabled={busy}>{busy ? '准备中...' : '进入正文编辑与导出'}</button></div>
          <article className={templateId === 'enterprise-header' ? 'format-paper format-paper-enterprise' : 'format-paper'}>
            {templateId === 'enterprise-header' && <EnterpriseHeader />}
            <div className="format-paper-content"><div className="format-paper-title">{activeProject?.settings.bidTitle || '标书正文'}</div><FormatPreview section={section} chapterTitle={chapters.find(chapter => chapter.chapter_id === selectedChapterId)?.title || ''} /></div>
            {templateId === 'enterprise-header' && <EnterpriseFooter />}
          </article>
        </section>
      </main>
    </div>
  )
}

export default OrchestrationWorkspace
