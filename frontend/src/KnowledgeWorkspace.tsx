import { KnowledgeAnalysisProgress } from './components/KnowledgeAnalysisProgress'
import { type ChangeEvent, type CSSProperties, type DragEvent, type MouseEvent, type WheelEvent, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ArrowLeft, Bell, CheckCircle2, ChevronRight, CircleHelp, FileText, Folder, GitCompareArrows, Images, LayoutGrid, MoreHorizontal, Plus, RefreshCw, Search, Settings, UploadCloud } from 'lucide-react'
import { type KnowledgeAsset, type KnowledgeCategory, type KnowledgeGraph, type KnowledgeImportTask, type KnowledgeItem, type KnowledgeProject, type KnowledgeProjectDocument, type KnowledgeSearchResult, wordagentApi } from './wordagentApi'
import { isKnowledgeProcessingActive, KnowledgeProcessingProgress, KnowledgeUploadProgressPanel, type KnowledgeUploadProgressState } from './components/KnowledgeProcessingProgress'
import { useTextInputDialog } from './utils/useTextInputDialog'

const font: CSSProperties = { fontFamily: "'Noto Sans SC', sans-serif" }
const EVIDENCE_TYPES = ['营业执照', '食品经营许可证', '食品安全管理体系认证证书', '质量管理体系认证证书', '诚信管理体系认证证书', '供应链安全管理体系认证证书', '食品可追溯管理体系认证证书', '认证查询截图', '业绩合同', '业绩证明', '车辆行驶证', '车辆租赁合同', '车辆购买合同', '车辆购置发票', '车辆照片']
const IMAGE_CATEGORIES = ['资质证书类', '现场场景类', '人员资质类', '业绩证明类', '设备资产类', '其他附件类']
type AssetSubtypeGroup = 'qualification' | 'personnel' | 'vehicle' | 'document' | 'scene' | 'other'
type AssetSubtypeOption = { value: string; label: string; group: AssetSubtypeGroup }
const ASSET_SUBTYPE_OPTIONS: AssetSubtypeOption[] = [
  { value: '营业执照', label: '营业执照', group: 'qualification' },
  { value: '食品经营许可证', label: '食品经营许可证', group: 'qualification' },
  { value: '有机产品认证', label: '有机产品认证', group: 'qualification' },
  { value: 'ISO体系认证', label: '体系认证', group: 'qualification' },
  { value: '检测报告', label: '检测报告', group: 'qualification' },
  { value: '居民身份证', label: '居民身份证', group: 'personnel' },
  { value: '健康证明', label: '健康证明', group: 'personnel' },
  { value: '身份证', label: '人员身份证（历史）', group: 'personnel' },
  { value: '健康证', label: '健康证（历史）', group: 'personnel' },
  { value: '从业资格证', label: '从业资格证', group: 'personnel' },
  { value: '安全员证', label: '安全员证', group: 'personnel' },
  { value: '岗位证书', label: '岗位证书', group: 'personnel' },
  { value: '冷链配送车', label: '冷链配送车', group: 'vehicle' },
  { value: '普通货车', label: '普通货车', group: 'vehicle' },
  { value: '车辆行驶证', label: '车辆行驶证', group: 'vehicle' },
  { value: '车辆保险单', label: '车辆保险单', group: 'vehicle' },
  { value: '增值税发票', label: '增值税发票', group: 'document' },
  { value: '银行转账凭证', label: '银行转账凭证', group: 'document' },
  { value: '缴费证明', label: '缴费证明', group: 'document' },
  { value: '保证金单据', label: '保证金单据', group: 'document' },
  { value: '冷库仓储', label: '冷库仓储', group: 'scene' },
  { value: '分拣中心', label: '分拣中心', group: 'scene' },
  { value: '门店实景', label: '门店实景', group: 'scene' },
  { value: '配送现场', label: '配送现场', group: 'scene' },
  { value: '其他附件', label: '其他附件', group: 'other' },
]
const SUBTYPE_GROUP_LABELS: Record<AssetSubtypeGroup, string> = { qualification: '资质证书类', personnel: '人员证件类', vehicle: '车辆资产类', document: '票据凭证类', scene: '场景现场类', other: '其他附件' }

function subtypeOption(value?: string | null) {
  const normalized = (value || '').trim()
  const aliases: Record<string, string> = { '人员身份证': '居民身份证', '身份证': '居民身份证', '健康证': '健康证明', '体系认证': 'ISO体系认证' }
  return ASSET_SUBTYPE_OPTIONS.find(option => option.value === (aliases[normalized] || normalized))
}

function subtypeOptionsForCategory(category: string) {
  const value = (category || '').trim()
  if (!value) return ASSET_SUBTYPE_OPTIONS
  let groups: AssetSubtypeGroup[] = []
  if (/资质证书|资质/.test(value)) groups = ['qualification']
  else if (/人员/.test(value)) groups = ['personnel']
  else if (/设备资产/.test(value)) groups = ['vehicle']
  else if (/现场图片|现场场景/.test(value)) groups = ['scene', 'vehicle']
  else if (/方案案例|业绩/.test(value)) groups = ['document']
  else if (/其他附件/.test(value)) groups = ['other']
  return groups.length ? ASSET_SUBTYPE_OPTIONS.filter(option => groups.includes(option.group)) : ASSET_SUBTYPE_OPTIONS
}

function subtypeTagClass(value?: string | null) {
  const group = subtypeGroup(value)
  return 'knowledge-subtype-tag knowledge-subtype-' + group
}
type KnowledgeViewMode = 'items' | 'images' | 'projects' | 'newProject'
type KnowledgeProjectMap = NonNullable<KnowledgeProject['maps']>[number]

function apiErrorMessage(error: unknown, fallback: string) {
  const raw = error instanceof Error ? error.message : String(error ?? '')
  try {
    const parsed = JSON.parse(raw)
    return parsed?.message || parsed?.detail || fallback
  } catch {
    return raw || fallback
  }
}

function TopNavLite({ onHome }: { onHome: () => void }) {
  return (
    <header className="top-nav knowledge-top-nav">
      <button className="logo-button" type="button" onClick={onHome}><span className="knowledge-brand-mark"><LayoutGrid size={16} /></span><strong>木石 AI</strong><span>企业工作台</span></button>
      <nav className="nav-actions" aria-label="工作台导航">
        <span>使用帮助</span><CircleHelp size={16} aria-hidden="true" /><Bell size={16} aria-hidden="true" /><span className="knowledge-user-mark">企</span><strong>企业管理员</strong>
      </nav>
    </header>
  )
}

export function KnowledgeWorkspace({ onHome }: { onHome: () => void }) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const tenderInputRef = useRef<HTMLInputElement | null>(null)
  const bidInputRef = useRef<HTMLInputElement | null>(null)
  const [categories, setCategories] = useState<KnowledgeCategory[]>([])
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [assets, setAssets] = useState<KnowledgeAsset[]>([])
  const [projects, setProjects] = useState<KnowledgeProject[]>([])
  const [selectedProject, setSelectedProject] = useState<KnowledgeProject | null>(null)
  const [viewMode, setViewMode] = useState<KnowledgeViewMode>('projects')
  const { requestText, dialog: textInputDialog } = useTextInputDialog(`${viewMode}:${selectedProject?.project_id || ''}`)
  const [keyword, setKeyword] = useState('')
  const [projectStatusFilter, setProjectStatusFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [fileTypeFilter, setFileTypeFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [imageCategoryFilter, setImageCategoryFilter] = useState('')
  const [assetSubTypeFilter, setAssetSubTypeFilter] = useState('')
  const [bidTypeFilter, setBidTypeFilter] = useState('')
  const [lengthLevelFilter, setLengthLevelFilter] = useState('')
  const [toolMenuOpen, setToolMenuOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState('')
  const [detail, setDetail] = useState<KnowledgeItem | null>(null)
  const [graphOpen, setGraphOpen] = useState(false)
  const [graph, setGraph] = useState<KnowledgeGraph>({ nodes: [], links: [] })
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResult[]>([])
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [selectedAssetIds, setSelectedAssetIds] = useState<number[]>([])
  const [importTask, setImportTask] = useState<KnowledgeImportTask | null>(null)
  const [importingLocal, setImportingLocal] = useState(false)
  const [importFailuresOpen, setImportFailuresOpen] = useState(false)
  const [assetActionBusy, setAssetActionBusy] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<KnowledgeUploadProgressState | null>(null)
  const [progressRefreshError, setProgressRefreshError] = useState('')
  const refreshRef = useRef<() => Promise<void>>(async () => {})
  const projectPollInFlight = useRef(false)
  const [projectName, setProjectName] = useState('')
  const [tenderFile, setTenderFile] = useState<File | null>(null)
  const [bidFile, setBidFile] = useState<File | null>(null)
  const [projectUploading, setProjectUploading] = useState(false)
  const [projectUploadPercent, setProjectUploadPercent] = useState<number | null>(null)
  const selectedCategoryName = categories.find(category => String(category.category_id) === categoryFilter)?.name || ''
  const subtypeFilterOptions = useMemo(() => subtypeOptionsForCategory(viewMode === 'images' ? imageCategoryFilter : selectedCategoryName), [viewMode, imageCategoryFilter, selectedCategoryName])
  const visibleProjects = useMemo(() => projects.filter(project => {
    const searchText = `${project.project_name} ${project.tender_file_name || ''} ${project.bid_file_name || ''}`.toLowerCase()
    const matchesKeyword = !keyword.trim() || searchText.includes(keyword.trim().toLowerCase())
    const hasFailure = (project.status === 'failed' || project.status === 'needs_attention') || project.tender_status === 'failed' || project.bid_status === 'failed'
    const isReady = project.status === 'completed' && project.tender_status === 'completed' && project.bid_status === 'completed'
    const matchesStatus = !projectStatusFilter || (projectStatusFilter === 'failed' ? hasFailure : projectStatusFilter === 'completed' ? isReady : !hasFailure && !isReady)
    return matchesKeyword && matchesStatus
  }), [projects, keyword, projectStatusFilter])
  const projectStats = useMemo(() => ({
    total: projects.length,
    files: projects.reduce((sum, project) => sum + Number(Boolean(project.tender_doc_id)) + Number(Boolean(project.bid_doc_id)), 0),
    completed: projects.filter(project => project.status === 'completed' && project.tender_status === 'completed' && project.bid_status === 'completed').length,
    attention: projects.filter(project => (project.status === 'failed' || project.status === 'needs_attention') || project.status === 'needs_attention' || project.tender_status === 'failed' || project.bid_status === 'failed').length,
  }), [projects])
  const processingItemIds = useMemo(
    () => [...new Set([...(uploadProgress?.items || []), ...items, ...(detail ? [detail] : [])].filter(isKnowledgeProcessingActive).map(item => item.item_id))].sort((a, b) => a - b),
    [items, uploadProgress?.items, detail],
  )
  const subtypesByItem = useMemo(() => {
    const map = new Map<number, string[]>()
    assets.forEach(asset => {
      const subtype = asset.asset_sub_type || ''
      if (!subtype) return
      const values = map.get(asset.item_id) || []
      if (!values.includes(subtype)) values.push(subtype)
      map.set(asset.item_id, values)
    })
    return map
  }, [assets])

  async function refresh() {
    if (viewMode === 'newProject') return
    setLoading(true)
    try {
      if (viewMode === 'projects') {
        const projectRes = await wordagentApi.listKnowledgeProjects()
        if (projectRes.code === 0) setProjects(projectRes.data)
        return
      }
      const itemRequest = viewMode === 'items'
        ? wordagentApi.listKnowledgeItems({ category_id: categoryFilter ? Number(categoryFilter) : null, keyword, status: statusFilter, file_type: fileTypeFilter, bid_type: bidTypeFilter, length_level: lengthLevelFilter, include_expired: true, limit: 200 })
        : null
      const assetRequest = viewMode === 'images' || (viewMode === 'items' && Boolean(assetSubTypeFilter))
        ? wordagentApi.listKnowledgeAssets({ keyword, image_category: viewMode === 'images' ? imageCategoryFilter : '', asset_sub_type: assetSubTypeFilter, bid_type: bidTypeFilter, length_level: lengthLevelFilter, limit: 500 })
        : null
      const [categoryRes, itemRes, assetRes] = await Promise.all([
        wordagentApi.listKnowledgeCategories(),
        itemRequest,
        assetRequest,
      ])
      if (categoryRes.code === 0) setCategories(categoryRes.data)
      const nextAssets = assetRes?.code === 0 ? assetRes.data.assets : []
      if (viewMode === 'images' && assetRes?.code === 0) setAssets(nextAssets)
      if (viewMode === 'items' && itemRes?.code === 0) {
        const nextItems = viewMode === 'items' && assetSubTypeFilter
          ? itemRes.data.items.filter(item => nextAssets.some(asset => asset.item_id === item.item_id))
          : itemRes.data.items
        setItems(nextItems)
      }
      if (viewMode === 'items' && !assetSubTypeFilter) setAssets([])
    } catch (error) {
      window.alert(apiErrorMessage(error, '知识库数据加载失败'))
    } finally {
      setLoading(false)
    }
  }
  refreshRef.current = refresh

  useEffect(() => {
    if (viewMode === 'newProject') return
    void refresh()
  }, [statusFilter, fileTypeFilter, categoryFilter, imageCategoryFilter, assetSubTypeFilter, bidTypeFilter, lengthLevelFilter, viewMode])
  useEffect(() => {
    const activeProjects = selectedProject ? [...projects, selectedProject] : projects
    const activeProjectStatuses = ['processing', 'matching', 'indexing', 'pending']
    if (viewMode !== 'projects' || !activeProjects.some(project => activeProjectStatuses.includes(project.workflow?.status || '') || Object.values(project.documents || {}).some(doc => activeProjectStatuses.includes(doc?.ai_interpretation?.status || '') || Boolean(doc?.processing && isKnowledgeProcessingActive(doc.processing))) || activeProjectStatuses.includes(project.status) || activeProjectStatuses.includes(project.tender_status || '') || activeProjectStatuses.includes(project.bid_status || ''))) return
    let cancelled = false
    let timer: number | undefined
    const pollProjects = async () => {
      if (cancelled) return
      if (projectPollInFlight.current || document.visibilityState !== 'visible') {
        timer = window.setTimeout(() => void pollProjects(), 5000)
        return
      }
      projectPollInFlight.current = true
      try {
        const res = await wordagentApi.listKnowledgeProjects()
        if (!cancelled && res.code === 0) {
          setProjects(res.data)
          if (selectedProject) {
            const current = await wordagentApi.getKnowledgeProject(selectedProject.project_id)
            if (!cancelled && current.code === 0) setSelectedProject(current.data)
          }
        }
      } catch {
        // The project row remains usable when a background status refresh fails.
      } finally {
        projectPollInFlight.current = false
        if (!cancelled) timer = window.setTimeout(() => void pollProjects(), 5000)
      }
    }
    timer = window.setTimeout(() => void pollProjects(), 5000)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [viewMode, projects.map(project => `${project.project_id}:${project.status}:${project.workflow?.status}:${project.tender_status}:${project.bid_status}:${project.documents?.tender?.ai_interpretation?.status}:${project.documents?.bid?.ai_interpretation?.status}:${project.documents?.bid?.processing?.deep_status}:${project.documents?.tender?.processing?.deep_status}:${project.documents?.bid?.processing?.vector_status}`).join('|'), selectedProject?.project_id, selectedProject?.workflow?.status])
  useEffect(() => {
    if (assetSubTypeFilter && !subtypeFilterOptions.some(option => option.value === assetSubTypeFilter)) setAssetSubTypeFilter('')
  }, [assetSubTypeFilter, subtypeFilterOptions])
  useEffect(() => {
    if (viewMode === 'newProject' || viewMode === 'projects') return
    if (!keyword.trim()) return
    const timer = window.setTimeout(() => void refresh(), 260)
    return () => window.clearTimeout(timer)
  }, [keyword])
  useEffect(() => {
    if (!processingItemIds.length) {
      setProgressRefreshError('')
      return
    }
    let cancelled = false
    let inFlight = false
    const poll = async () => {
      if (document.visibilityState !== 'visible' || inFlight || cancelled) return
      inFlight = true
      try {
        const results = await Promise.allSettled(processingItemIds.map(itemId => wordagentApi.getKnowledgeItemStatus(itemId)))
        if (cancelled) return
        const statusMap = new Map<number, Awaited<ReturnType<typeof wordagentApi.getKnowledgeItemStatus>>['data']>()
        results.forEach(result => {
          if (result.status === 'fulfilled' && result.value.code === 0) statusMap.set(result.value.data.item_id, result.value.data)
        })
        setProgressRefreshError(statusMap.size < processingItemIds.length ? '进度暂时无法更新，正在自动重试；请勿重复上传。' : '')
        if (!statusMap.size) return
        const merge = (item: KnowledgeItem) => ({ ...item, ...statusMap.get(item.item_id) })
        const finishedIds = [...statusMap.values()].filter(status => !isKnowledgeProcessingActive(status)).map(status => status.item_id)
        setItems(current => current.map(merge))
        setUploadProgress(current => current ? { ...current, items: current.items.map(merge) } : current)
        setDetail(current => current && statusMap.has(current.item_id) ? merge(current) : current)
        // Refresh heavy contents once when processing ends, preserving fields the user is editing.
        if (detail && finishedIds.includes(detail.item_id)) {
          try {
            const response = await wordagentApi.getKnowledgeItem(detail.item_id)
            if (response.code === 0) setDetail(current => current?.item_id === response.data.item_id ? {
              ...current, content_text: response.data.content_text, content_preview: response.data.content_preview,
              assets: response.data.assets, chunks: response.data.chunks, versions: response.data.versions,
            } : current)
          } catch { /* Status remains visible if the content refresh fails. */ }
        }
        if (finishedIds.length) await refreshRef.current()
      } finally {
        inFlight = false
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 3000)
    const onVisible = () => { if (document.visibilityState === 'visible') void poll() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [processingItemIds.join(','), detail?.item_id])
  useEffect(() => {
    if (keyword.trim().length < 2) {
      setSearchResults([])
      return
    }
    const timer = window.setTimeout(async () => {
      try {
        const res = await wordagentApi.searchKnowledge({ query: keyword.trim(), image_category: viewMode === 'images' ? imageCategoryFilter || null : null, bid_type: bidTypeFilter || null, length_level: lengthLevelFilter || null, include_images: true, limit: 6 })
        if (res.code === 0) setSearchResults(res.data.results)
      } catch {
        setSearchResults([])
      }
    }, 360)
    return () => window.clearTimeout(timer)
  }, [keyword, imageCategoryFilter, bidTypeFilter, lengthLevelFilter, viewMode])
  useEffect(() => {
    if (!importTask || !['pending', 'processing'].includes(importTask.status)) return
    const timer = window.setInterval(() => void refreshImportTask(importTask.task_id, true), 5000)
    return () => window.clearInterval(timer)
  }, [importTask?.task_id, importTask?.status])

  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [evidenceKind, setEvidenceKind] = useState('营业执照')
  const [evidenceFiles, setEvidenceFiles] = useState<File[]>([])

  async function uploadFiles(files: File[], kind?: string) {
    if (!files.length) return
    setUploading(true)
    setUploadProgress({ total: files.length, uploadPercent: 0, items: [], failures: [], startedAt: Date.now(), phase: 'uploading' })
    try {
      const onProgress = (percent: number) => setUploadProgress(prev => prev ? { ...prev, uploadPercent: percent } : prev)
      const res = kind
        ? await wordagentApi.uploadKnowledgeEvidence(files, kind, onProgress)
        : await wordagentApi.uploadKnowledgeItemsWithProgress(files, { tags: '' }, onProgress)
      if (res.code === 0) {
        setUploadProgress(prev => prev ? { ...prev, phase: 'accepted', uploadPercent: 100, items: res.data.items, failures: res.data.failures || [] } : prev)
        setNotice(`已提交 ${res.data.success_count} 个文件处理${res.data.failure_count ? `，${res.data.failure_count} 个失败` : ''}`)
        if (kind && res.data.success_count) {
          setEvidenceOpen(false)
          setEvidenceFiles([])
        }
        await refresh()
      } else throw new Error(res.message || '文件上传未成功')
    } catch (error) {
      setUploadProgress(prev => prev ? { ...prev, phase: 'failed', error: apiErrorMessage(error, '文件上传失败') } : prev)
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  async function refreshImportTask(taskId: string, refreshWhenDone = false) {
    try {
      const res = await wordagentApi.getKnowledgeLocalImportStatus(taskId)
      if (res.code !== 0) return
      setImportTask(res.data)
      if (refreshWhenDone && !['pending', 'processing'].includes(res.data.status)) {
        await refresh()
        setNotice(`批量导入完成：成功 ${res.data.success_count} 个，跳过 ${res.data.skipped_count} 个，失败 ${res.data.failed_count} 个`)
      }
    } catch {
      // 轮询失败不打断页面使用，下一轮继续尝试。
    }
  }

  async function startLocalImport() {
    const confirmed = window.confirm('将批量导入“示例集团提供的标书”目录下的招标文件与成品投标文件，是否继续？')
    if (!confirmed) return
    setImportingLocal(true)
    setImportFailuresOpen(false)
    try {
      const res = await wordagentApi.startKnowledgeLocalImport()
      if (res.code === 0) {
        setNotice(`批量导入任务已启动，共发现 ${res.data.total_files} 个文件`)
        await refreshImportTask(res.data.task_id)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '批量导入启动失败'))
    } finally {
      setImportingLocal(false)
    }
  }

  async function retryLocalImportFailed() {
    if (!importTask || importTask.failed_count <= 0) return
    setImportingLocal(true)
    setImportFailuresOpen(false)
    try {
      const res = await wordagentApi.retryKnowledgeLocalImportFailed(importTask.task_id)
      if (res.code === 0) {
        setImportTask(res.data)
        setNotice('失败文件已重新加入处理队列')
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '失败文件重试失败'))
    } finally {
      setImportingLocal(false)
    }
  }

  async function openDetail(itemId: number) {
    try {
      const res = await wordagentApi.getKnowledgeItem(itemId)
      if (res.code === 0) setDetail(res.data)
    } catch (error) {
      window.alert(apiErrorMessage(error, '知识详情读取失败'))
    }
  }

  async function saveDetail() {
    if (!detail) return
    try {
      const res = await wordagentApi.updateKnowledgeItem(detail.item_id, { title: detail.title, category_id: detail.category_id, tags: detail.tags, valid_until: detail.valid_until })
      if (res.code === 0) {
        setDetail(res.data)
        await refresh()
        setNotice('知识条目已保存')
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '保存失败'))
    }
  }

  async function deleteItem(item: KnowledgeItem) {
    if (!window.confirm(`确认删除“${item.title}”？`)) return
    await wordagentApi.deleteKnowledgeItem(item.item_id)
    if (detail?.item_id === item.item_id) setDetail(null)
    await refresh()
  }

  async function retryItem(item: KnowledgeItem) {
    try {
      const res = await wordagentApi.retryKnowledgeItem(item.item_id)
      if (res.code === 0) {
        setItems(current => current.map(existing => existing.item_id === item.item_id ? res.data : existing))
        setUploadProgress(current => current ? { ...current, items: current.items.map(existing => existing.item_id === item.item_id ? res.data : existing) } : current)
        setDetail(current => current?.item_id === item.item_id ? { ...current, ...res.data } : current)
        setNotice(`已重新提交“${item.title}”处理任务`)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '重试处理失败'))
    }
  }

  async function openGraph() {
    setGraphOpen(true)
    const res = await wordagentApi.getKnowledgeGraph(null)
    if (res.code === 0) setGraph(res.data)
  }

  async function runBatch(action: 'delete' | 'expire' | 'restore') {
    if (!selectedIds.length) return
    if (action === 'delete' && !window.confirm(`确认批量删除 ${selectedIds.length} 个条目？`)) return
    await wordagentApi.batchKnowledgeItems({ item_ids: selectedIds, action })
    setSelectedIds([])
    await refresh()
  }

  async function regenerateAnnotations(onlyMissing = true) {
    if (!onlyMissing && !window.confirm('将调用大模型重新识别图片文字、分类与语义注释，可能消耗较多 token。确认继续？')) return
    setAssetActionBusy(true)
    try {
      const res = await wordagentApi.regenerateKnowledgeAssetAnnotations({ only_missing: onlyMissing, limit: onlyMissing ? 120 : 1000 })
      if (res.code === 0) {
        const ocrInfo = res.data.qwen_ocr_requested !== undefined
          ? `；${res.data.qwen_ocr_model || 'QwenOCR'} 请求 ${res.data.qwen_ocr_requested} 张，成功 ${res.data.qwen_ocr_succeeded || 0} 张，失败 ${res.data.qwen_ocr_failed || 0} 张`
          : ''
        setNotice(`图片信息已更新：${res.data.updated} / ${res.data.scanned} 张${ocrInfo}`)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '图片注释重新生成失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function cleanupAssets() {
    if (!window.confirm('将清理无效装饰图、重复图片，以及本地文件已不存在的图片记录。确认继续？')) return
    setAssetActionBusy(true)
    try {
      const res = await wordagentApi.cleanupKnowledgeAssets()
      if (res.code === 0) {
        setNotice(`图片清理完成：移除 ${res.data.removed} 张，重复关联 ${res.data.linked_duplicates} 张，清理脏注释 ${res.data.cleared_bad_info || 0} 条`)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '图片清理失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function reextractCurrentSelection() {
    const itemIds = selectedIds.length ? selectedIds : items.map(item => item.item_id)
    const assetIds = selectedAssetIds.length ? selectedAssetIds : assets.map(asset => asset.asset_id)
    if (viewMode === 'items' && !itemIds.length && !assetIds.length) {
      setNotice('当前筛选条件下没有可重提取的素材')
      return
    }
    if (viewMode === 'images' && !assetIds.length) {
      setNotice('当前筛选条件下没有可重提取的素材')
      return
    }
    setAssetActionBusy(true)
    setNotice('正在批量重提取结构化信息，请稍候…')
    try {
      const res = viewMode === 'items'
        ? await wordagentApi.reextractKnowledgeAssetsBatch({ item_ids: itemIds, only_missing: false, limit: 2000 })
        : await wordagentApi.reextractKnowledgeAssetsBatch({ asset_ids: assetIds, only_missing: false, limit: 2000 })
      if (res.code === 0) {
        setNotice('结构化重提取完成：成功 ' + res.data.updated + ' 张，失败 ' + res.data.failed + ' 张，跳过 ' + res.data.skipped + ' 张')
        setSelectedIds([])
        setSelectedAssetIds([])
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '批量重提取失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function reextractSingleAsset(asset: KnowledgeAsset) {
    setAssetActionBusy(true)
    setNotice('正在重新提取素材结构化信息…')
    try {
      const res = await wordagentApi.reextractKnowledgeItemAssets(asset.item_id, { asset_ids: [asset.asset_id], only_missing: false, limit: 1 })
      if (res.code === 0) {
        setNotice('素材结构化信息已重新提取')
        await refresh()
        if (detail?.item_id === asset.item_id) {
          const updated = await wordagentApi.getKnowledgeItem(asset.item_id)
          if (updated.code === 0) setDetail(updated.data)
        }
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '单条重提取失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function reextractItem(itemId: number) {
    setAssetActionBusy(true)
    setNotice('正在重新提取文档结构化信息…')
    try {
      const res = await wordagentApi.reextractKnowledgeItemAssets(itemId, { only_missing: false, limit: 2000 })
      if (res.code === 0) {
        setNotice('文档结构化重提取完成：成功 ' + res.data.updated + ' 张，失败 ' + res.data.failed + ' 张')
        const updated = await wordagentApi.getKnowledgeItem(itemId)
        if (updated.code === 0) setDetail(updated.data)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '文档重提取失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function deepExtractItem(itemId: number) {
    setAssetActionBusy(true)
    setNotice('正在检查是否有待补识别的图片…')
    try {
      const res = await wordagentApi.deepExtractKnowledgeItem(itemId)
      if (res.code === 0) {
        setNotice(res.data.message || (res.data.queued ? '深度识别已启动' : '无需重复提交，请查看当前处理状态'))
        setDetail(current => current?.item_id === itemId ? { ...current, ...res.data } : current)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '深度识别启动失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function deepExtractSelectedItems() {
    const itemIds = selectedIds.length ? selectedIds : items.map(item => item.item_id)
    if (!itemIds.length) {
      setNotice('当前筛选条件下没有可进行深度识别的文档')
      return
    }
    setAssetActionBusy(true)
    try {
      const res = await wordagentApi.deepExtractKnowledgeItemsBatch(itemIds)
      if (res.code === 0) {
        setNotice(`本次启动 ${res.data.queued} 个，已完成或处理中 ${res.data.total - res.data.queued - res.data.failed} 个，失败 ${res.data.failed} 个`)
        setSelectedIds([])
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '批量深度识别启动失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function reidentifyPersonnel() {
    if (!window.confirm('将重新识别历史“其他附件”中的人员证件，并更新分类与结构化字段。是否继续？')) return
    setAssetActionBusy(true)
    try {
      const res = await wordagentApi.reidentifyPersonnelAssets()
      if (res.code === 0) {
        setNotice(`人员证件重识别完成：识别 ${res.data.recognized_count} 张，分类修正 ${res.data.category_corrected_count} 张，信息补全 ${res.data.structured_completed_count} 张`)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '人员证件重识别失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function uploadProject() {
    if (!tenderFile || !bidFile) {
      setNotice('请先选择招标文件和对应投标文件')
      return
    }
    setProjectUploading(true)
    setProjectUploadPercent(0)
    setUploadProgress(null)
    setNotice('')
    try {
      const name = projectName.trim() || tenderFile.name.replace(/\.[^.]+$/, '')
      const res = await wordagentApi.uploadKnowledgeProject(name, tenderFile, bidFile, percent => setProjectUploadPercent(percent))
      if (res.code === 0) {
        const created = res.data
        setProjects(current => [{
          ...created,
          tender_file_name: tenderFile.name,
          bid_file_name: bidFile.name,
          tender_status: 'pending',
          bid_status: 'pending',
          requirement_count: 0,
          mapped_count: 0,
          tender_asset_count: 0,
          bid_asset_count: 0,
          create_time: new Date().toISOString(),
          update_time: new Date().toISOString(),
        }, ...current.filter(project => project.project_id !== created.project_id)])
        setNotice('项目已创建，两份文件已进入后台解析；解析完成后会自动生成需求-方案映射')
        setViewMode('projects')
        setSelectedProject(null)
        setProjectName('')
        setTenderFile(null)
        setBidFile(null)
        if (tenderInputRef.current) tenderInputRef.current.value = ''
        if (bidInputRef.current) bidInputRef.current.value = ''
        try {
          const latest = await wordagentApi.listKnowledgeProjects()
          if (latest.code === 0) setProjects(latest.data)
        } catch {
          // The new project is already visible from its successful upload response.
        }
      } else {
        window.alert(res.message || '项目上传失败，请稍后重试')
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '项目上传失败'))
    } finally {
      setProjectUploading(false)
      setProjectUploadPercent(null)
    }
  }

  async function resumeProjectAnalysis(project: KnowledgeProject) {
    setAssetActionBusy(true)
    setNotice('正在提交“' + project.project_name + '”的解读重试，请稍候…')
    try {
      const res = await wordagentApi.resumeKnowledgeProjectAnalysis(project.project_id, AbortSignal.timeout(20000))
      if (res.code === 0) {
        setProjects(current => current.map(p => p.project_id === project.project_id ? res.data : p))
        setSelectedProject(current => current?.project_id === project.project_id ? res.data : current)
        setNotice('已继续 AI 解读，已完成批次会保留。')
      } else {
        setNotice(res.message || '解读重试未能提交，请刷新状态后重试。')
      }
    } catch (error) {
      if (error instanceof DOMException && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
        setNotice('提交超时，暂时无法确认任务是否已启动。请刷新项目状态，确认后再重试，避免重复提交。')
      } else setNotice(apiErrorMessage(error, '解读启动失败'))
    } finally { setAssetActionBusy(false) }
  }

  async function openProject(projectId: number) {
    try {
      const res = await wordagentApi.getKnowledgeProject(projectId)
      if (res.code === 0) {
        setSelectedProject(res.data)
        setViewMode('projects')
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '项目详情读取失败'))
    }
  }

  async function retryProjectDocument(itemId: number) {
    try {
      const res = await wordagentApi.retryKnowledgeItem(itemId)
      if (res.code === 0) {
        setNotice('已重新提交解析；Word 正文和章节切片会先完成，向量索引在后台更新')
        await refresh()
        if (selectedProject) await openProject(selectedProject.project_id)
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '重新解析失败'))
    }
  }

  async function matchProject(project: KnowledgeProject) {
    setAssetActionBusy(true)
    try {
      const res = await wordagentApi.matchKnowledgeProject(project.project_id)
      if (res.code === 0) {
        setSelectedProject(res.data)
        setNotice(`需求-方案匹配完成：${res.data.matched_count} 条映射`)
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '需求-方案匹配失败，请确认两份文件已解析完成'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function deleteProject(project: KnowledgeProject) {
    if (!window.confirm(`确认删除项目“${project.project_name}”？原始知识文档不会删除。`)) return
    try {
      const res = await wordagentApi.deleteKnowledgeProject(project.project_id)
      if (res.code === 0) {
        setSelectedProject(null)
        setNotice('项目关系已删除，原始文档仍保留在知识库')
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '项目删除失败'))
    }
  }

  async function extractProjectTemplate(project: KnowledgeProject) {
    setAssetActionBusy(true)
    try {
      const res = await wordagentApi.extractKnowledgeProjectTemplate(project.project_id)
      if (res.code === 0) {
        setNotice('公司格式模板已进入后台提取，完成后会自动出现在图文标书导出模板中')
        await refresh()
        if (selectedProject?.project_id === project.project_id) setSelectedProject(res.data)
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '格式模板提取启动失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  async function activateProjectTemplate(project: KnowledgeProject) {
    setAssetActionBusy(true)
    try {
      const res = await wordagentApi.activateKnowledgeProjectTemplate(project.project_id)
      if (res.code === 0) {
        setNotice('已将该项目格式模板设为后续导出的默认模板')
        await refresh()
        if (selectedProject?.project_id === project.project_id) setSelectedProject(current => current ? { ...current, format_template: res.data } : current)
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '默认格式模板设置失败'))
    } finally {
      setAssetActionBusy(false)
    }
  }

  function downloadProjectTemplate(project: KnowledgeProject) {
    window.open(wordagentApi.downloadKnowledgeProjectTemplateUrl(project.project_id), '_blank', 'noopener,noreferrer')
  }

  async function renameProject(project: KnowledgeProject) {
    const nextName = (await requestText('请输入新的项目名称', project.project_name))?.trim()
    if (!nextName || nextName === project.project_name) return
    try {
      const res = await wordagentApi.renameKnowledgeProject(project.project_id, nextName)
      if (res.code === 0) {
        setSelectedProject(res.data)
        setNotice('项目名称已更新')
        await refresh()
      }
    } catch (error) {
      window.alert(apiErrorMessage(error, '项目重命名失败'))
    }
  }

  async function reuseProject(project: KnowledgeProject) {
    const rawBidId = (await requestText('请输入要引用到的标书编号', ''))?.trim()
    const targetBidId = Number(rawBidId)
    if (!Number.isInteger(targetBidId) || targetBidId <= 0) return
    try {
      const res = await wordagentApi.reuseKnowledgeProject(project.project_id, { target_bid_id: targetBidId, map_ids: (project.maps || []).map(item => item.map_id) })
      if (res.code === 0) setNotice(`历史方案已引用：写入 ${(res.data.applied_count as number) || 0} 个章节`)
    } catch (error) {
      window.alert(apiErrorMessage(error, '历史方案引用失败'))
    }
  }

  return (
    <div className="knowledge-app-shell">
      {textInputDialog}
      <TopNavLite onHome={onHome} />
      <div className="knowledge-shell-body">
        <aside className="knowledge-nav-sidebar">
          <p>知识库导航</p>
          <button type="button" className={viewMode === 'projects' && !selectedProject ? 'active' : ''} onClick={() => { setViewMode('projects'); setSelectedProject(null) }}><LayoutGrid size={17} />项目总览</button>
          <button type="button" className={viewMode === 'newProject' ? 'active' : ''} onClick={() => { setViewMode('newProject'); setSelectedProject(null) }}><UploadCloud size={17} />新建项目入库</button>
          <button type="button" className={selectedProject ? 'active' : ''} onClick={() => { if (selectedProject) setViewMode('projects'); else if (projects[0]) void openProject(projects[0].project_id); else setNotice('请先创建招投标项目') }}><GitCompareArrows size={17} />解析与对应关系</button>
          <div className="knowledge-nav-divider" />
          <button type="button" className={viewMode === 'items' ? 'active' : ''} onClick={() => setViewMode('items')}><FileText size={17} />文档库</button>
          <button type="button" className={viewMode === 'images' ? 'active' : ''} onClick={() => setViewMode('images')}><Images size={17} />图片库</button>
          <div className="knowledge-nav-spacer" />
          <button type="button" onClick={onHome}><Settings size={17} />返回主页</button>
        </aside>

        <main className="knowledge-content-area">
          <div className="knowledge-breadcrumb"><span>企业知识库</span><ChevronRight size={14} /><span>{selectedProject ? '解析与对应关系' : viewMode === 'newProject' ? '新建项目入库' : viewMode === 'projects' ? '项目总览' : viewMode === 'images' ? '图片库' : '文档库'}</span>{selectedProject && <><ChevronRight size={14} /><strong>{selectedProject.project_name}</strong></>}</div>
          <header className="knowledge-page-heading">
            <div><h1>{selectedProject ? selectedProject.project_name : viewMode === 'newProject' ? '新建项目' : viewMode === 'projects' ? '企业知识库' : viewMode === 'images' ? '图片库' : '文档库'}</h1><p>{selectedProject ? '查看招标要求、对应投标方案和项目资料。' : viewMode === 'newProject' ? '上传招标文件和对应标书，自动提取目录并建立需求与响应关系。' : viewMode === 'projects' ? '招投标文件与标书沉淀为可检索、可引用的企业知识。' : '检索和管理已入库的企业资料。'}</p></div>
            {viewMode === 'projects' && !selectedProject && <button type="button" className="dark-btn" onClick={() => setViewMode('newProject')}><Plus size={16} />新建项目</button>}
            {selectedProject && <button type="button" className="light-btn" onClick={() => setSelectedProject(null)}><ArrowLeft size={15} />返回项目列表</button>}
          </header>

          {!selectedProject && viewMode !== 'newProject' && <div className="knowledge-project-view-tabs" role="tablist" aria-label="知识库视图">
            <button type="button" className={viewMode === 'items' ? 'active' : ''} onClick={() => setViewMode('items')}><FileText size={15} />文档库</button>
            <button type="button" className={viewMode === 'images' ? 'active' : ''} onClick={() => setViewMode('images')}><Images size={15} />图片库</button>
            <button type="button" className={viewMode === 'projects' ? 'active' : ''} onClick={() => setViewMode('projects')}><Folder size={15} />项目视图</button>
          </div>}

          {viewMode === 'projects' && !selectedProject && <section className="knowledge-overview-stats" aria-label="项目统计">
            <div><span>项目总数</span><div><strong>{projectStats.total}</strong><small>当前项目视图</small></div></div>
            <div><span>入库文件</span><div><strong>{projectStats.files}</strong><small>招标文件与标书</small></div></div>
            <div><span>解析完成</span><div><strong>{projectStats.completed}</strong><small>可查看对应关系</small></div></div>
            <div className={projectStats.attention ? 'has-attention' : ''}><span>待处理</span><div><strong>{projectStats.attention}</strong><small>解析失败或需要重试</small></div></div>
          </section>}

        <section className={`knowledge-main knowledge-content-panel ${viewMode === 'projects' || viewMode === 'newProject' ? '' : 'form-card'}`} onDragOver={(event: DragEvent<HTMLElement>) => event.preventDefault()} onDrop={(event: DragEvent<HTMLElement>) => { event.preventDefault(); if (viewMode === 'items' || viewMode === 'images') void uploadFiles(Array.from(event.dataTransfer.files || [])) }}>
            {viewMode === 'projects' && !selectedProject && <div className="knowledge-project-filterbar">
              <label><Search size={16} /><input value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="搜索项目名称或文件关键词" /></label>
              <select value={projectStatusFilter} onChange={event => setProjectStatusFilter(event.target.value)}><option value="">全部状态</option><option value="completed">已完成</option><option value="processing">处理中</option><option value="failed">待处理</option></select>
              <span>共 {visibleProjects.length} 个项目</span>
            </div>}

            {(viewMode === 'items' || viewMode === 'images') && <div className="knowledge-toolbar">
              <div className="knowledge-toolbar-row knowledge-search-row">
                <input value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="搜索资料、图片或说明文字" />
              </div>
              <div className="knowledge-toolbar-row knowledge-control-row">
                <div className="knowledge-filter-group">
                  {viewMode === 'items' ? <>
                  <select value={categoryFilter} onChange={event => setCategoryFilter(event.target.value)}><option value="">所属分类</option>{categories.map(category => <option key={category.category_id} value={category.category_id}>{category.name}</option>)}</select>
                  <select value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="">全部状态</option><option value="processing">处理中</option><option value="completed">已完成</option><option value="failed">失败</option></select>
                  <select value={fileTypeFilter} onChange={event => setFileTypeFilter(event.target.value)}><option value="">全部类型</option><option value="pdf">PDF</option><option value="docx">DOCX</option><option value="txt">TXT</option><option value="png">PNG</option><option value="jpg">JPG</option></select>
                  <SubtypeFilterSelect options={subtypeFilterOptions} value={assetSubTypeFilter} onChange={setAssetSubTypeFilter} />
                  <select value={bidTypeFilter} onChange={event => setBidTypeFilter(event.target.value)}><option value="">&#x6807;&#x4e66;&#x7c7b;&#x578b;</option><option value="open">&#x660e;&#x6807;</option><option value="blind">&#x6697;&#x6807;</option></select>
                  <select value={lengthLevelFilter} onChange={event => setLengthLevelFilter(event.target.value)}><option value="">&#x7bc7;&#x5e45;&#x7b49;&#x7ea7;</option><option value="short">&#x7cbe;&#x7b80;&#x7248;</option><option value="standard">&#x6807;&#x51c6;&#x7248;</option><option value="detailed">&#x8be6;&#x7ec6;&#x7248;</option></select>
                  </> : <>
                  <select value={imageCategoryFilter} onChange={event => setImageCategoryFilter(event.target.value)}><option value="">图片分类</option>{IMAGE_CATEGORIES.map(category => <option key={category} value={category}>{category}</option>)}</select>
                   <SubtypeFilterSelect options={subtypeFilterOptions} value={assetSubTypeFilter} onChange={setAssetSubTypeFilter} />
                   <select value={bidTypeFilter} onChange={event => setBidTypeFilter(event.target.value)}><option value="">&#x6807;&#x4e66;&#x7c7b;&#x578b;</option><option value="open">&#x660e;&#x6807;</option><option value="blind">&#x6697;&#x6807;</option></select>
                   <select value={lengthLevelFilter} onChange={event => setLengthLevelFilter(event.target.value)}><option value="">&#x7bc7;&#x5e45;&#x7b49;&#x7ea7;</option><option value="short">&#x7cbe;&#x7b80;&#x7248;</option><option value="standard">&#x6807;&#x51c6;&#x7248;</option><option value="detailed">&#x8be6;&#x7ec6;&#x7248;</option></select>
                  </>}
                </div>
                <div className="knowledge-tool-actions">
                  <div className="knowledge-tools-more">
                    <button type="button" className="light-btn" onClick={() => setToolMenuOpen(open => !open)}>更多工具</button>
                    {toolMenuOpen && <div className="knowledge-tools-menu">
                      <button type="button" onClick={() => { setToolMenuOpen(false); void deepExtractSelectedItems() }} disabled={assetActionBusy}>深度识别结构化信息</button>
                      <button type="button" onClick={() => { setToolMenuOpen(false); void startLocalImport() }} disabled={importingLocal || (importTask ? ['pending', 'processing'].includes(importTask.status) : false)}>{importingLocal ? '启动中' : '批量导入本地标书'}</button>
                       <button type="button" onClick={() => { setToolMenuOpen(false); void regenerateAnnotations(false) }} disabled={assetActionBusy}>AI补全图片信息</button>
                       <button type="button" onClick={() => { setToolMenuOpen(false); void cleanupAssets() }} disabled={assetActionBusy}>一键清理冗余图片</button>
                       <button type="button" onClick={() => { setToolMenuOpen(false); void reextractCurrentSelection() }} disabled={assetActionBusy}>{assetActionBusy ? '重提取中…' : '批量重提取结构化信息'}</button>
                       <button type="button" onClick={() => { setToolMenuOpen(false); void reidentifyPersonnel() }} disabled={assetActionBusy}>批量重识别人员证件</button>
                    </div>}
                  </div>
                  {viewMode === 'items' && <button type="button" className="light-btn" onClick={() => setEvidenceOpen(open => !open)} aria-expanded={evidenceOpen} disabled={uploading}>上传企业证明材料</button>}
                  <button type="button" className="dark-btn" title="支持点击上传或拖拽文件到主内容区" onClick={() => fileInputRef.current?.click()} disabled={uploading}>{uploading ? '上传中' : '上传文件'}</button>
                </div>
              </div>
              {viewMode === 'items' && evidenceOpen && <section className="knowledge-evidence-panel" aria-label="上传企业证明材料">
                <div className="knowledge-detail-form-grid">
                  <label>材料类型<select value={evidenceKind} onChange={event => setEvidenceKind(event.target.value)}>{EVIDENCE_TYPES.map(kind => <option key={kind}>{kind}</option>)}</select></label>
                  <label>证明原件<input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.webp" onChange={event => setEvidenceFiles(Array.from(event.target.files || []))} /></label>
                </div>
                <p>支持 PDF、JPG、PNG 和 WEBP。多页合同请合成一个 PDF，识别完成后在资料详情核验。</p>
                <div className="knowledge-evidence-actions">
                  <button className="light-btn" type="button" onClick={() => { setEvidenceOpen(false); setEvidenceFiles([]) }} disabled={uploading}>取消</button>
                  <button className="dark-btn" type="button" onClick={() => void uploadFiles(evidenceFiles, evidenceKind)} disabled={!evidenceFiles.length || uploading}>{uploading ? '正在上传…' : '上传并识别'}</button>
                </div>
              </section>}
              <input ref={fileInputRef} type="file" multiple accept=".pdf,.doc,.docx,.txt,.md,.png,.jpg,.jpeg,.webp" onChange={(event: ChangeEvent<HTMLInputElement>) => void uploadFiles(Array.from(event.target.files || []))} hidden />
            </div>}

            {viewMode === 'newProject' && <section className="knowledge-project-create-page">
              <section className="knowledge-project-name-card"><label htmlFor="knowledge-project-name">项目名称</label><input id="knowledge-project-name" value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="未填写时使用招标文件名" /></section>
              <div className="knowledge-project-file-pair">
                <ProjectFileDropzone role="tender" file={tenderFile} inputRef={tenderInputRef} onChange={file => { setTenderFile(file); if (file && !projectName.trim()) setProjectName(file.name.replace(/\.[^.]+$/, '')) }} />
                <ProjectFileDropzone role="bid" file={bidFile} inputRef={bidInputRef} onChange={setBidFile} />
              </div>
              {projectUploading && <div className="knowledge-project-upload-progress" role="progressbar" aria-label="上传招投标文件" aria-valuemin={0} aria-valuemax={100} aria-valuenow={projectUploadPercent ?? 0}>
                <div><span>正在上传招标文件和对应标书</span><strong>{projectUploadPercent ?? 0}%</strong></div>
                <i><b style={{ width: `${projectUploadPercent ?? 0}%` }} /></i>
                <small>上传完成后自动开始目录、正文和表格解析，图片识别与向量索引在后台继续。</small>
              </div>}
              <div className="knowledge-project-create-actions"><span aria-live="polite">{tenderFile && bidFile ? '两份文件已就绪' : '请分别添加招标文件和对应标书'}</span><button type="button" className="dark-btn" onClick={() => void uploadProject()} disabled={projectUploading || !tenderFile || !bidFile}>{projectUploading ? `上传中 ${projectUploadPercent ?? 0}%` : '创建项目并开始解析'}</button></div>
            </section>}

            {notice && <div className="knowledge-notice">{notice}</div>}
            {progressRefreshError && <div className="knowledge-notice" role="status">{progressRefreshError}</div>}
            {uploadProgress && (viewMode === 'items' || viewMode === 'images') && <KnowledgeUploadProgressPanel progress={uploadProgress} onClose={() => setUploadProgress(null)} onRetry={item => void retryItem(item)} />}
            {importTask && <ImportProgress task={importTask} failuresOpen={importFailuresOpen} onToggleFailures={() => setImportFailuresOpen(open => !open)} onRetry={() => void retryLocalImportFailed()} />}
            {(viewMode === 'items' || viewMode === 'images') && (selectedIds.length > 0 || selectedAssetIds.length > 0) && <div className="knowledge-batch"><span>已选择 {viewMode === 'images' ? selectedAssetIds.length : selectedIds.length} 项</span>{viewMode === 'items' && <><button onClick={() => void runBatch('expire')}>标记过期</button><button onClick={() => void runBatch('restore')}>恢复</button><button onClick={() => void runBatch('delete')}>删除</button></>}<button onClick={() => void reextractCurrentSelection()} disabled={assetActionBusy}>{assetActionBusy ? '重提取中…' : '重提取结构化信息'}</button></div>}
            {searchResults.length > 0 && <SearchResults results={searchResults} query={keyword} onOpen={openDetail} />}

            {viewMode === 'newProject' ? null : loading ? <StatusBox>正在加载知识库资料…</StatusBox> : viewMode === 'projects' ? (
              <ProjectView onResumeAnalysis={resumeProjectAnalysis} projects={visibleProjects} selectedProject={selectedProject} onOpen={openProject} onOpenItem={openDetail} onMatch={matchProject} onRename={renameProject} onReuse={reuseProject} onDelete={deleteProject} onRetryDocument={retryProjectDocument} onExtractTemplate={extractProjectTemplate} onActivateTemplate={activateProjectTemplate} onDownloadTemplate={downloadProjectTemplate} onClose={() => setSelectedProject(null)} busy={assetActionBusy} />
            ) : viewMode === 'images' ? (
              <ImageLibrary assets={assets} selectedAssetIds={selectedAssetIds} onSelectAsset={(assetId, checked) => setSelectedAssetIds(prev => checked ? [...prev, assetId] : prev.filter(id => id !== assetId))} onOpenItem={openDetail} onRefresh={refresh} onReextract={reextractSingleAsset} reextractBusy={assetActionBusy} />
            ) : (
              <KnowledgeTable
                items={items}
                selectedIds={selectedIds}
                onSelect={(itemId, checked) => setSelectedIds(prev => checked ? [...prev, itemId] : prev.filter(id => id !== itemId))}
                onOpen={itemId => void openDetail(itemId)}
                onDelete={item => void deleteItem(item)}
                onRetry={item => void retryItem(item)}
                subtypesByItem={subtypesByItem}
              />
            )}
          </section>
        </main>
      </div>
      {detail && <DetailModal item={detail} categories={categories} onChange={setDetail} onClose={() => setDetail(null)} onSave={() => void saveDetail()} onRefresh={refresh} onReextractItem={reextractItem} onDeepExtract={deepExtractItem} reextractBusy={assetActionBusy} onReextractAsset={reextractSingleAsset} progressRefreshError={progressRefreshError} />}
      {graphOpen && <GraphModal graph={graph} categories={categories} items={items} initialCategoryId={null} onClose={() => setGraphOpen(false)} />}
    </div>
  )
}

function LegacyProjectView({ projects, selectedProject, onOpen, onMatch, onRename, onReuse, onDelete, onClose, busy }: { projects: KnowledgeProject[]; selectedProject: KnowledgeProject | null; onOpen: (projectId: number) => void; onMatch: (project: KnowledgeProject) => void; onRename: (project: KnowledgeProject) => void; onReuse: (project: KnowledgeProject) => void; onDelete: (project: KnowledgeProject) => void; onClose: () => void; busy: boolean }) {
  return <div className="knowledge-asset-grid">
    {projects.length ? projects.map(project => <article className="knowledge-structured-card" key={project.project_id}>
      <div className="knowledge-structured-card-head"><strong>{project.project_name}</strong><span className="knowledge-subtype-tag knowledge-subtype-other">{project.status === 'completed' ? '已完成' : '处理中'}</span></div>
      <div className="knowledge-structured-fields"><div className="knowledge-structured-row"><span>招标文件</span><strong>{project.tender_file_name || '—'}<small>{project.tender_asset_count ? ` · ${project.tender_asset_count} 个图片素材` : ''}</small></strong></div><div className="knowledge-structured-row"><span>投标文件</span><strong>{project.bid_file_name || '—'}<small>{project.bid_asset_count ? ` · ${project.bid_asset_count} 个图片素材` : ''}</small></strong></div><div className="knowledge-structured-row"><span>需求点 / 已映射</span><strong>{project.requirement_count} / {project.mapped_count}</strong></div><div className="knowledge-structured-row"><span>更新时间</span><strong>{formatDate(project.update_time)}</strong></div></div>
      <div className="knowledge-card-actions"><button type="button" className="light-btn" onClick={() => onOpen(project.project_id)}>查看详情</button><button type="button" className="dark-btn" onClick={() => onMatch(project)} disabled={busy || project.tender_status !== 'completed' || project.bid_status !== 'completed'}>匹配需求方案</button><button type="button" className="light-btn" onClick={() => onReuse(project)} disabled={!project.mapped_count}>引用到当前标书</button><button type="button" className="light-btn" onClick={() => onRename(project)}>重命名</button><button type="button" className="light-btn" onClick={() => onDelete(project)}>删除项目</button></div>
    </article>) : <EmptyState />}
    {selectedProject && <div className="knowledge-detail-modal" role="dialog" aria-modal="true"><div className="knowledge-detail-modal-panel"><header><strong>{selectedProject.project_name} · 需求-方案映射</strong><button type="button" onClick={onClose}>关闭</button></header><div className="knowledge-detail-modal-body"><p className="knowledge-muted">招标文件：{selectedProject.tender_file_name || '—'}；投标文件：{selectedProject.bid_file_name || '—'}</p>{selectedProject.maps?.length ? selectedProject.maps.map(item => <article className="knowledge-structured-card" key={item.map_id}><div className="knowledge-structured-card-head"><span>匹配度 {(item.match_score * 100).toFixed(1)}%</span><strong>{item.source_chapter || '未命名章节'}</strong></div><div className="knowledge-structured-fields"><div className="knowledge-structured-row"><span>需求</span><strong>{item.requirement_content}</strong></div><div className="knowledge-structured-row"><span>方案</span><strong>{item.solution_content || '—'}</strong></div></div></article>) : <StatusBox>暂无映射关系，请在两份文件解析完成后执行匹配。</StatusBox>}</div></div></div>}
  </div>
}

function ProjectFileDropzone({ role, file, inputRef, onChange }: { role: 'tender' | 'bid'; file: File | null; inputRef: { current: HTMLInputElement | null }; onChange: (file: File | null) => void }) {
  const tender = role === 'tender'
  const label = tender ? '招标文件' : '对应投标文件'
  return (
    <div
      className={`knowledge-project-file ${file ? 'has-file' : ''}`}
      onDragOver={event => event.preventDefault()}
      onDrop={event => { event.preventDefault(); onChange(event.dataTransfer.files?.[0] || null) }}
    >
      <input ref={inputRef} type="file" accept=".pdf,.doc,.docx,.txt,.md" aria-label={`选择${label}`} onChange={event => onChange(event.target.files?.[0] || null)} hidden />
      <span className="knowledge-project-file-head"><strong><FileText size={17} />{label}</strong><em className={file ? 'is-ready' : ''}>{file ? '已添加' : '必选'}</em></span>
      {file ? <span className="knowledge-project-file-selected"><FileText size={19} /><span><strong title={file.name}>{file.name}</strong><small>{formatFileSize(file.size)}</small></span><button type="button" aria-label={`移除${label}`} onClick={event => { event.preventDefault(); event.stopPropagation(); if (inputRef.current) inputRef.current.value = ''; onChange(null) }}>×</button></span> : <>
        <button type="button" className="knowledge-project-file-select" onClick={() => inputRef.current?.click()}>
          <span className="knowledge-project-file-drop-icon"><UploadCloud size={23} /></span>
          <span className="knowledge-project-file-hint">点击选择，或拖拽文件到此处</span>
          <span className="knowledge-project-file-types">支持 DOC、DOCX、PDF、TXT</span>
        </button>
      </>}
    </div>
  )
}

function ProjectView({ onResumeAnalysis, projects, selectedProject, onOpen, onOpenItem, onMatch, onRename, onReuse, onDelete, onRetryDocument, onExtractTemplate, onActivateTemplate, onDownloadTemplate, onClose, busy }: { onResumeAnalysis: (project: KnowledgeProject) => void; projects: KnowledgeProject[]; selectedProject: KnowledgeProject | null; onOpen: (projectId: number) => void; onOpenItem: (itemId: number) => void; onMatch: (project: KnowledgeProject) => void; onRename: (project: KnowledgeProject) => void; onReuse: (project: KnowledgeProject) => void; onDelete: (project: KnowledgeProject) => void; onRetryDocument: (itemId: number) => void; onExtractTemplate: (project: KnowledgeProject) => void; onActivateTemplate: (project: KnowledgeProject) => void; onDownloadTemplate: (project: KnowledgeProject) => void; onClose: () => void; busy: boolean }) {
  const [selectedMapping, setSelectedMapping] = useState<KnowledgeProjectMap | null>(null)
  const [openProjectMenu, setOpenProjectMenu] = useState<number | null>(null)
  useEffect(() => { setSelectedMapping(null) }, [selectedProject?.project_id])
  if (selectedProject) {
    const tenderReady = selectedProject.tender_status === 'completed'
    const bidReady = selectedProject.bid_status === 'completed'
    const maps = selectedProject.maps || []
    const mapComplete = selectedProject.status === 'completed' || maps.length > 0
    const vectorRefining = selectedProject.status === 'indexing' && maps.length > 0
    return <div className="knowledge-mapping-page">
      {selectedProject.workflow?.error && <p role="status" className="knowledge-project-alert">{selectedProject.workflow.error}</p>}
      <div className="knowledge-project-source-pair">
        <ProjectDocumentColumn role="tender" document={selectedProject.documents?.tender} name={selectedProject.tender_file_name} status={selectedProject.tender_status} ready={tenderReady} assets={selectedProject.tender_asset_count} onOpen={selectedProject.tender_doc_id ? () => onOpenItem(selectedProject.tender_doc_id as number) : undefined} onRetry={selectedProject.tender_doc_id && selectedProject.tender_status === 'failed' ? () => onRetryDocument(selectedProject.tender_doc_id as number) : undefined} />
        <ProjectDocumentColumn role="bid" document={selectedProject.documents?.bid} name={selectedProject.bid_file_name} status={selectedProject.bid_status} ready={bidReady} assets={selectedProject.bid_asset_count} onOpen={selectedProject.bid_doc_id ? () => onOpenItem(selectedProject.bid_doc_id as number) : undefined} onRetry={selectedProject.bid_doc_id && selectedProject.bid_status === 'failed' ? () => onRetryDocument(selectedProject.bid_doc_id as number) : undefined} />
      </div>
      <div className="knowledge-mapping-progress"><span className={tenderReady ? 'done' : selectedProject.tender_status === 'failed' ? 'failed' : ''}><i>{tenderReady ? <CheckCircle2 size={15} /> : selectedProject.tender_status === 'failed' ? <AlertCircle size={15} /> : '1'}</i>招标文件结构解析{tenderReady ? '完成' : selectedProject.tender_status === 'failed' ? '失败' : '中'}</span><b /><span className={bidReady ? 'done' : selectedProject.bid_status === 'failed' ? 'failed' : ''}><i>{bidReady ? <CheckCircle2 size={15} /> : selectedProject.bid_status === 'failed' ? <AlertCircle size={15} /> : '2'}</i>对应标书结构解析{bidReady ? '完成' : selectedProject.bid_status === 'failed' ? '失败' : '中'}</span><b /><span className={mapComplete ? 'done' : ''}><i>{mapComplete ? <CheckCircle2 size={15} /> : '3'}</i>需求与响应关系{mapComplete ? '已生成' : '待建立'}</span><em>{vectorRefining ? '初步对应已可查看，正在优化语义匹配' : mapComplete ? '可以查看方案并引用' : '文件处理完后自动匹配'}</em></div>
      {(selectedProject.tender_status === 'failed' || selectedProject.bid_status === 'failed') && <div className="knowledge-project-alert"><AlertCircle size={17} /><span>至少一份文件解析失败。查看上方错误详情并重新解析，已完成文件和项目关系会保留。</span></div>}
      <div className="knowledge-mapping-section-head"><button type="button" className="light-btn" disabled={busy || ['pending', 'processing'].includes(selectedProject.workflow?.status || '') || !tenderReady || !bidReady} onClick={() => onResumeAnalysis(selectedProject)}>补全 / 重试 AI 解读</button><div><strong>需求与标书响应</strong><span>{maps.length} 条需求 · 已匹配 {maps.length} 条</span></div><div><button type="button" className="light-btn" onClick={onClose}>收起项目详情</button><button type="button" className="light-btn" onClick={() => onMatch(selectedProject)} disabled={busy || !tenderReady || !bidReady}><RefreshCw size={14} />重新匹配</button><button type="button" className="dark-btn" onClick={() => onReuse(selectedProject)} disabled={!selectedProject.mapped_count}>引用方案</button></div></div>
      {maps.length ? <>
        <div className="knowledge-mapping-table">
          <div className="knowledge-mapping-table-head"><span>招标需求 / 来源</span><span>对应标书响应 / 引用位置</span><span>匹配状态</span><span>操作</span></div>
          {maps.map(item => <article className="knowledge-mapping-row" key={item.map_id}><div><strong>{item.requirement_content}</strong><small>招标文件 · {item.source_chapter || '正文需求'}</small></div><div><strong>{item.source_chapter || '对应方案'}</strong><p>{item.solution_content || '未提取到对应响应内容'}</p></div><span className="knowledge-mapping-score">{item.match_score >= 0.42 ? '已匹配' : '待确认'}<small>{Math.round(item.match_score * 100)}%</small></span><button type="button" className="light-btn" aria-expanded={selectedMapping?.map_id === item.map_id} aria-controls={`knowledge-mapping-detail-${item.map_id}`} onClick={() => setSelectedMapping(current => current?.map_id === item.map_id ? null : item)}>{selectedMapping?.map_id === item.map_id ? '收起' : '查看'}</button>{selectedMapping?.map_id === item.map_id && <section id={`knowledge-mapping-detail-${item.map_id}`} className="knowledge-solution-preview knowledge-solution-preview-inline"><div><span>标书响应 · {item.source_chapter || '对应方案'}</span><strong>{item.solution_content || '暂无对应方案内容'}</strong></div><small>来源：{selectedProject.bid_file_name || '对应标书'} · 匹配度 {Math.round(item.match_score * 100)}%</small></section>}</article>)}
        </div>
      </> : <div className="knowledge-empty knowledge-mapping-empty">{tenderReady && bidReady ? '暂未建立对应关系。可以重新匹配，或检查文档的目录和正文是否提取完整。' : '两份文件解析完成后，会自动按章节与内容建立对应关系。'}</div>}
      {selectedProject.format_template && <div className="knowledge-template-tools"><span>标书格式模板：{selectedProject.format_template.status === 'completed' ? selectedProject.format_template.name || '已提取' : selectedProject.format_template.status === 'failed' ? '提取失败' : '未提取'}</span><div><button type="button" className="light-btn" onClick={() => onExtractTemplate(selectedProject)} disabled={busy || selectedProject.format_template.status === 'pending' || selectedProject.format_template.status === 'processing'}>{selectedProject.format_template.status === 'failed' ? '重试提取' : '提取模板'}</button>{selectedProject.format_template.status === 'completed' && <><button type="button" className="light-btn" onClick={() => onDownloadTemplate(selectedProject)}>下载</button><button type="button" className="light-btn" onClick={() => onActivateTemplate(selectedProject)} disabled={busy || Boolean(selectedProject.format_template.is_default)}>{selectedProject.format_template.is_default ? '默认模板' : '设为默认'}</button></>}</div></div>}
    </div>
  }

  if (!projects.length) return <div className="knowledge-project-empty"><Folder size={30} /><strong>还没有招投标项目</strong><span>上传招标文件和对应标书，建立可复用的项目知识。</span></div>
  return <div className="knowledge-project-list">
    {projects.map(project => {
      const tenderReady = project.tender_status === 'completed'
      const bidReady = project.bid_status === 'completed'
      const hasFailure = project.tender_status === 'failed' || project.bid_status === 'failed' || (project.status === 'failed' || project.status === 'needs_attention')
      const stateLabel = project.status === 'completed' ? '已完成' : hasFailure ? '待处理' : project.status === 'matching' ? '正在匹配' : project.status === 'indexing' ? '建立向量索引' : '文件解析中'
      return <article className="knowledge-project-row" key={project.project_id}>
        <div className="knowledge-project-row-head"><div className="knowledge-project-name"><span className="knowledge-project-folder"><Folder size={19} /></span><strong>{project.project_name}</strong><small>{formatDate(project.update_time)} · {project.requirement_count} 个需求</small></div><span className={`knowledge-project-status ${project.status === 'completed' ? 'is-ready' : hasFailure ? 'is-failed' : ''}`}>{stateLabel}</span></div>
        <div className="knowledge-project-pair-grid">
          <ProjectDocumentColumn role="tender" document={project.documents?.tender} name={project.tender_file_name} status={project.tender_status} ready={tenderReady} assets={project.tender_asset_count} onOpen={project.tender_doc_id ? () => onOpenItem(project.tender_doc_id as number) : undefined} onRetry={project.tender_doc_id && project.tender_status === 'failed' ? () => onRetryDocument(project.tender_doc_id as number) : undefined} />
          <ProjectDocumentColumn role="bid" document={project.documents?.bid} name={project.bid_file_name} status={project.bid_status} ready={bidReady} assets={project.bid_asset_count} onOpen={project.bid_doc_id ? () => onOpenItem(project.bid_doc_id as number) : undefined} onRetry={project.bid_doc_id && project.bid_status === 'failed' ? () => onRetryDocument(project.bid_doc_id as number) : undefined} />
        </div>
        {project.workflow?.error && <p role="status" className="knowledge-project-alert">{project.workflow.error}</p>}
        <div className="knowledge-project-row-actions"><button type="button" className="light-btn" disabled={busy || ['pending', 'processing'].includes(project.workflow?.status || '') || !tenderReady || !bidReady} onClick={() => onResumeAnalysis(project)}>补全 / 重试 AI 解读</button><span>已建立方案关系 <strong>{project.mapped_count}</strong></span><div><button type="button" className="dark-btn" onClick={() => onOpen(project.project_id)}>{hasFailure ? '查看处理详情' : '查看项目'}</button><button type="button" className="knowledge-icon-button" aria-label="更多项目操作" onClick={() => setOpenProjectMenu(open => open === project.project_id ? null : project.project_id)}><MoreHorizontal size={18} /></button></div>
          {openProjectMenu === project.project_id && <div className="knowledge-project-menu"><button type="button" onClick={() => { setOpenProjectMenu(null); onOpen(project.project_id) }}>{hasFailure ? '查看失败原因' : '查看需求对应关系'}</button>{project.tender_doc_id && project.tender_status === 'failed' && <button type="button" onClick={() => { setOpenProjectMenu(null); onRetryDocument(project.tender_doc_id as number) }}>重新解析招标文件</button>}{project.bid_doc_id && project.bid_status === 'failed' && <button type="button" onClick={() => { setOpenProjectMenu(null); onRetryDocument(project.bid_doc_id as number) }}>重新解析对应标书</button>}<button type="button" onClick={() => { setOpenProjectMenu(null); onRename(project) }}>重命名</button><button type="button" onClick={() => { setOpenProjectMenu(null); onExtractTemplate(project) }} disabled={busy || project.format_template?.status === 'processing' || project.format_template?.status === 'pending'}>{project.format_template?.status === 'completed' ? '重新提取格式模板' : '提取格式模板'}</button>{project.format_template?.status === 'completed' && <><button type="button" onClick={() => { setOpenProjectMenu(null); onDownloadTemplate(project) }}>下载格式模板</button><button type="button" onClick={() => { setOpenProjectMenu(null); onActivateTemplate(project) }} disabled={busy || Boolean(project.format_template.is_default)}>设为默认模板</button></>}<button type="button" className="danger" onClick={() => { setOpenProjectMenu(null); onDelete(project) }}>删除项目</button></div>}
        </div>
      </article>
    })}
  </div>
}

function ProjectStatusPill({ project }: { project: KnowledgeProject }) {
  const failed = (project.status === 'failed' || project.status === 'needs_attention') || project.tender_status === 'failed' || project.bid_status === 'failed'
  const ready = project.status === 'completed'
  const text = ready ? '已完成' : failed ? '待处理' : project.status === 'indexing' ? '建立索引中' : project.status === 'matching' ? '匹配中' : '解析中'
  return <span className={`knowledge-project-status ${ready ? 'is-ready' : failed ? 'is-failed' : ''}`}>{text}</span>
}

function ProjectMoreActions({ project, onRename, onDelete }: { project: KnowledgeProject; onRename: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false)
  return <div className="knowledge-project-menu-wrap"><button type="button" className="knowledge-icon-button" aria-label="更多项目操作" onClick={() => setOpen(value => !value)}><MoreHorizontal size={18} /></button>{open && <div className="knowledge-project-menu"><button type="button" onClick={() => { setOpen(false); onRename() }}>重命名</button><button type="button" className="danger" onClick={() => { setOpen(false); onDelete() }}>删除项目</button></div>}</div>
}

function ProjectDocumentColumn({ role, document, name, status, ready, assets, onOpen, onRetry }: { role: 'tender' | 'bid'; document?: KnowledgeProjectDocument; name?: string | null; status?: string | null; ready: boolean; assets?: number; onOpen?: () => void; onRetry?: () => void }) {
  const tender = role === 'tender'
  const aiStatus = document?.ai_interpretation?.status
  const aiLabel = aiStatus === 'completed' ? 'AI 解读完成' : (aiStatus === 'processing' || aiStatus === 'pending') ? 'AI 解读中' : aiStatus === 'failed' ? 'AI 解读失败，已保留基础解析' : ''
  const preview = [document?.content_preview?.trim(), aiLabel].filter(Boolean).join(' · ')
  const pages = document?.total_pages || 0
  const chars = document?.text_char_count || 0
  const chunks = document?.chunk_count || 0
  return <div className={`knowledge-project-document ${status === 'failed' ? 'is-failed' : ''}`}><div className="knowledge-project-document-head"><span>{tender ? '招标文件' : '对应投标文件'}</span><em className={ready ? 'is-ready' : status === 'failed' ? 'is-failed' : ''}>{ready ? '解析完成' : status === 'failed' ? '解析失败' : '处理中'}</em></div><strong>{name || '文件未关联'}</strong><small>{ready ? `${pages ? `${pages} 页 · ` : ''}${chars ? `${chars.toLocaleString()} 字 · ` : ''}${chunks} 个文本片段 · ${assets || 0} 个图片素材` : status === 'failed' ? document?.process_error || '解析任务失败，请重新提交' : '正在读取正文、目录和表格'}</small>{preview && <p>{preview}</p>}<KnowledgeAnalysisProgress analysis={document?.ai_interpretation} />{document?.processing && <KnowledgeProcessingProgress item={document.processing} />}<div className="knowledge-project-document-actions">{onOpen && <button type="button" className="light-btn" onClick={onOpen}>查看资料详情</button>}{onRetry && <button type="button" className="dark-btn" onClick={onRetry}><RefreshCw size={14} />重新解析</button>}</div></div>
}

function CategoryButton({ category, active, child, onSelect }: { category: KnowledgeCategory; active: boolean; child?: boolean; onSelect: () => void }) {
  return <div className={child ? 'knowledge-category-wrap child' : 'knowledge-category-wrap'}><button type="button" className={active ? 'knowledge-category active' : 'knowledge-category'} onClick={onSelect}><span>{category.name}</span><em>{category.item_count}</em></button></div>
}

function SubtypeFilterSelect({ options, value, onChange }: { options: AssetSubtypeOption[]; value: string; onChange: (value: string) => void }) {
  const groups = (Object.keys(SUBTYPE_GROUP_LABELS) as AssetSubtypeGroup[]).map(group => ({ group, options: options.filter(option => option.group === group) })).filter(entry => entry.options.length > 0)
  return (
    <select value={value} onChange={event => onChange(event.target.value)}>
      <option value="">细分类别</option>
      {groups.map(entry => <optgroup key={entry.group} label={SUBTYPE_GROUP_LABELS[entry.group]}>{entry.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</optgroup>)}
    </select>
  )
}

function KnowledgeTable({ items, selectedIds, onSelect, onOpen, onDelete, onRetry, subtypesByItem }: { items: KnowledgeItem[]; selectedIds: number[]; onSelect: (itemId: number, checked: boolean) => void; onOpen: (itemId: number) => void; onDelete: (item: KnowledgeItem) => void; onRetry: (item: KnowledgeItem) => void; subtypesByItem: Map<number, string[]> }) {
  if (!items.length) return <EmptyState />
  return (
    <div className="knowledge-table" role="table" aria-label="知识资料列表">
      <div className="knowledge-table-row head" role="row">
        <span>缩略图</span><span>资料标题</span><span>所属分类</span><span>细分类</span><span>处理状态</span><span>文件大小</span><span>更新时间</span><span>操作</span>
      </div>
      {items.map(item => <KnowledgeTableRow key={item.item_id} item={item} selected={selectedIds.includes(item.item_id)} onSelect={onSelect} onOpen={onOpen} onDelete={onDelete} onRetry={onRetry} subtypes={subtypesByItem.get(item.item_id) || []} />)}
    </div>
  )
}

function KnowledgeTableRow({ item, selected, onSelect, onOpen, onDelete, onRetry, subtypes }: { item: KnowledgeItem; selected: boolean; onSelect: (itemId: number, checked: boolean) => void; onOpen: (itemId: number) => void; onDelete: (item: KnowledgeItem) => void; onRetry: (item: KnowledgeItem) => void; subtypes: string[] }) {
  return (
    <div className="knowledge-table-row" role="row">
      <span className="knowledge-table-thumb-cell"><input type="checkbox" checked={selected} onChange={event => onSelect(item.item_id, event.target.checked)} /><Thumbnail item={item} onOpen={() => onOpen(item.item_id)} /></span>
      <button type="button" className="knowledge-title-button" onClick={() => onOpen(item.item_id)}><strong>{item.title}</strong><em>{item.content_preview || '等待处理完成后显示文本摘要。'}</em></button>
      <span>{item.category_name || '未分类'}</span>
      <span className="knowledge-subtype-cell">{item.evidence_kind ? <span className={subtypeTagClass(item.evidence_kind)}>{item.evidence_kind}</span> : subtypes.length ? subtypes.map(subtype => <span className={subtypeTagClass(subtype)} key={subtype}>{subtypeOption(subtype)?.label || subtype}</span>) : <small>—</small>}</span>
      <span className="knowledge-status-cell"><KnowledgeProcessingProgress item={item} compact />{item.evidence_kind && <small>{evidenceStatusText(item)}</small>}</span>
      <span>{formatFileSize((item as KnowledgeItem & { file_size?: number | null }).file_size)}</span>
      <span>{formatDate(item.update_time)}</span>
      <span className="knowledge-table-actions"><button type="button" onClick={() => onOpen(item.item_id)}>查看</button><RowMoreActions item={item} onOpen={onOpen} onDelete={onDelete} onRetry={onRetry} /></span>
    </div>
  )
}

function StatusBadge({ item }: { item: KnowledgeItem }) {
  const kind = item.status === 'completed' ? 'completed' : item.status === 'failed' ? 'failed' : item.status === 'processing' ? 'processing' : 'pending'
  const label = item.status === 'processing' ? `处理中 ${item.process_progress}%` : statusText(item)
  return <em className={`knowledge-status-badge ${kind}`}>{label}</em>
}

function RowMoreActions({ item, onOpen, onDelete, onRetry }: { item: KnowledgeItem; onOpen: (itemId: number) => void; onDelete: (item: KnowledgeItem) => void; onRetry: (item: KnowledgeItem) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="knowledge-row-more">
      <button type="button" aria-label="更多操作" onClick={() => setOpen(value => !value)}>•••</button>
      {open && <div className="knowledge-row-menu">
        <button type="button" onClick={() => { setOpen(false); onOpen(item.item_id) }}>编辑</button>
        {isRetryableFailure(item) && <button type="button" onClick={() => { setOpen(false); onRetry(item) }}>重试</button>}
        <button type="button" onClick={() => { setOpen(false); onDelete(item) }}>删除</button>
      </div>}
    </div>
  )
}

function ImageLibrary({ assets, selectedAssetIds, onSelectAsset, onOpenItem, onRefresh, onReextract, reextractBusy }: { assets: KnowledgeAsset[]; selectedAssetIds: number[]; onSelectAsset: (assetId: number, checked: boolean) => void; onOpenItem: (itemId: number) => Promise<void>; onRefresh: () => Promise<void>; onReextract: (asset: KnowledgeAsset) => void; reextractBusy: boolean }) {
  const [previewIndex, setPreviewIndex] = useState<number | null>(null)
  if (!assets.length) return <div className="knowledge-empty">暂无图片资源。上传含图片的 PDF / DOCX 后，提取结果会在这里集中展示。</div>
  return (
    <>
      <div className="knowledge-image-library-head"><strong>图片库</strong><span>{assets.length} 张图片</span></div>
      <div className="knowledge-image-grid-full" aria-label="图片资源库">
        {assets.map((asset, index) => <div className="knowledge-asset-card-shell" key={asset.asset_id}><label className="knowledge-asset-select"><input type="checkbox" checked={selectedAssetIds.includes(asset.asset_id)} onChange={event => onSelectAsset(asset.asset_id, event.target.checked)} />选择</label><AssetCard asset={asset} onOpen={() => setPreviewIndex(index)} /></div>)}
      </div>
      {previewIndex !== null && assets[previewIndex] && <AssetPreview assets={assets} index={previewIndex} onIndex={setPreviewIndex} onClose={() => setPreviewIndex(null)} onSaved={onRefresh} onReextract={onReextract} reextractBusy={reextractBusy} />}
    </>
  )
}

function AssetThumb({ asset, onOpen }: { asset: KnowledgeAsset; onOpen: () => void }) {
  const [failed, setFailed] = useState(false)
  const src = assetUrl(asset.resource_path)
  return <button type="button" className="knowledge-thumb" onClick={onOpen}>{src && !failed ? <img src={src} alt={asset.ai_description || ''} onError={() => setFailed(true)} /> : '图片'}</button>
}

function Thumbnail({ item, onOpen }: { item: KnowledgeItem; onOpen: () => void }) {
  const src = assetUrl(item.thumbnail_path)
  return <button type="button" className="knowledge-thumb" onClick={onOpen}>{src ? <img src={src} alt="" /> : item.file_type.toUpperCase()}</button>
}

function SearchResults({ results, query, onOpen }: { results: KnowledgeSearchResult[]; query: string; onOpen: (itemId: number) => Promise<void> }) {
  return <section className="knowledge-search-results"><strong>检索结果</strong>{results.map(result => <button key={result.chunk_id} type="button" onClick={() => void onOpen(result.item_id)}><span>{result.title} · 相似度 {Math.round(result.similarity * 100)}%</span><p>{highlight(result.content.slice(0, 180), query)}</p></button>)}</section>
}

function ImportProgress({ task, failuresOpen, onToggleFailures, onRetry }: { task: KnowledgeImportTask; failuresOpen: boolean; onToggleFailures: () => void; onRetry: () => void }) {
  const percent = task.total_files ? Math.round((task.processed_files / task.total_files) * 100) : 100
  const running = ['pending', 'processing'].includes(task.status)
  return (
    <section className="knowledge-import-progress">
      <div className="knowledge-import-progress-head">
        <strong>{running ? '本地标书批量导入中' : '本地标书批量导入结果'}</strong>
        <span>已处理 {task.processed_files} / {task.total_files} 个文件</span>
      </div>
      <div className="knowledge-import-bar"><i style={{ width: `${Math.max(4, Math.min(100, percent))}%` }} /></div>
      <div className="knowledge-import-meta">
        <span>成功 {task.success_count}</span><span>跳过 {task.skipped_count}</span><span>失败 {task.failed_count}</span>{task.current_file && <span>当前：{task.current_file}</span>}
      </div>
      {task.failed_count > 0 && <div className="knowledge-import-actions"><button type="button" className="mini-action-btn" onClick={onToggleFailures}>{failuresOpen ? '收起失败详情' : '查看失败详情'}</button>{!running && <button type="button" className="mini-action-btn" onClick={onRetry}>重试失败文件</button>}</div>}
      {failuresOpen && task.failures.length > 0 && <div className="knowledge-import-failures">{task.failures.map((failure, index) => <p key={`${failure.file_path}-${index}`}><strong>{failure.file_name}</strong><span>{failure.reason}</span></p>)}</div>}
    </section>
  )
}

function subtypeGroup(value?: string | null): AssetSubtypeGroup {
  const option = subtypeOption(value)
  if (option) return option.group
  const text = (value || '').trim()
  if (/营业执照|食品经营|有机产品|体系认证|检测报告|资质/.test(text)) return 'qualification'
  if (/身份证|健康证|从业资格|安全员|岗位|人员/.test(text)) return 'personnel'
  if (/冷链|货车|行驶证|保险单|车辆/.test(text)) return 'vehicle'
  if (/发票|转账|缴费|保证金|票据/.test(text)) return 'document'
  if (/冷库|分拣|门店|配送现场|场景/.test(text)) return 'scene'
  return 'other'
}

function structuredValueMap(asset: KnowledgeAsset): Record<string, unknown> {
  const legacy = { ...(asset.person_info || {}), ...(asset.employee_info || {}), ...(asset.contract_info || {}) } as Record<string, unknown>
  const fields = (asset.structured_info?.fields || {}) as Record<string, unknown>
  const nestedLegacy = (asset.structured_info?.legacy || {}) as Record<string, unknown>
  return {
    ...legacy,
    ...nestedLegacy,
    ...fields,
    cert_no: asset.cert_no || fields.cert_no || legacy.cert_no,
    expire_date: asset.expire_date || fields.expire_date || legacy.expire_date,
    holder_name: asset.holder_name || fields.holder_name || legacy.holder_name,
    description: fields.description || fields.说明 || asset.annotation || asset.ai_description,
  }
}

function structuredText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map(item => structuredText(item)).filter(Boolean).join('、')
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return '' }
  }
  return String(value).trim()
}

function structuredFieldRows(asset: KnowledgeAsset) {
  const values = structuredValueMap(asset)
  const read = (...keys: string[]) => {
    for (const key of keys) {
      const value = structuredText(values[key])
      if (value) return value
    }
    return ''
  }
  const group = subtypeGroup(asset.asset_sub_type || asset.structured_info?.asset_sub_type)
  const subtype = asset.asset_sub_type || asset.structured_info?.asset_sub_type || ''
  const fields: Array<[string, string]> = subtype === '居民身份证' || subtype === '身份证'
    ? [['姓名', read('name', '姓名')], ['性别', read('gender', '性别')], ['民族', read('ethnicity', 'nationality', '民族')], ['出生日期', read('birth_date', '出生日期')], ['住址', read('address', '住址')], ['公民身份号码', read('id_number', 'identity_number', '公民身份号码')], ['签发机关', read('issuing_authority', '签发机关')], ['有效期起始日', read('valid_from', '有效期起始日')], ['有效期截止日', read('valid_until', 'expire_date', '有效期截止日')]]
    : subtype === '健康证明' || subtype === '健康证'
      ? [['姓名', read('name', '姓名')], ['性别', read('gender', '性别')], ['年龄', read('age', '年龄')], ['从业类别', read('occupation_type', '从业类别')], ['发证日期', read('issue_date', '发证日期')], ['有效期至', read('valid_until', 'expire_date', '有效期至')], ['发证单位', read('issuing_unit', 'issuing_authority', '发证单位')], ['健康结论', read('health_conclusion', '健康结论')]]
      : group === 'qualification'
    ? [['证件编号', read('cert_no', 'certificate_no', 'certificate_number', '证件编号', '证书编号')], ['发证机关', read('issuing_authority', 'issuer', '发证机关')], ['有效期', read('expire_date', 'valid_until', '有效期')], ['持证主体', read('holder_name', 'subject_name', '持证主体')], ['经营范围', read('business_scope', 'scope', '经营范围')]]
    : group === 'personnel'
      ? [['姓名', read('name', 'person_name', '姓名')], ['性别', read('gender', '性别')], ['证件编号', read('id_number', 'identity_number', '身份证号', '证件编号')], ['岗位', read('position', 'role', 'employee_role', '岗位')], ['有效期', read('expire_date', 'valid_until', '有效期')]]
      : group === 'vehicle'
        ? [['车牌号', read('plate_no', 'license_plate', 'plate_number', '车牌号')], ['车型', read('vehicle_type', 'model', '车型')], ['核定载重', read('approved_load', 'load', '核定载重')], ['年检有效期', read('inspection_expire_date', 'annual_inspection_expire_date', '年检有效期')], ['冷链配置', read('cold_chain_equipment', 'cold_chain_config', '冷链配置')]]
        : group === 'document'
          ? [['发票号码', read('invoice_number', 'invoice_no', '发票号码')], ['开票日期', read('issue_date', 'invoice_date', '开票日期')], ['金额', read('amount', 'total_amount', '金额')], ['抬头', read('title', 'invoice_title', '抬头')], ['税号', read('tax_no', 'tax_number', '税号')]]
          : group === 'scene'
            ? [['拍摄地点', read('location', 'address', '拍摄地点')], ['场景类型', read('scene_type', 'scene_keywords', '场景类型')], ['说明', read('description', '说明')]]
            : [['说明', read('description', '说明')]]
  return fields.filter(([, value]) => Boolean(value))
}

function StructuredInfoSection({ assets, onReextract, reextractBusy }: { assets: KnowledgeAsset[]; onReextract: () => void; reextractBusy: boolean }) {
  return (
    <section className="knowledge-structured-section">
      <div className="knowledge-structured-head"><h3>结构化信息</h3><button type="button" className="light-btn" onClick={onReextract} disabled={reextractBusy || !assets.length}>{reextractBusy ? '提取中…' : '重新提取信息'}</button></div>
      {assets.length ? <div className="knowledge-structured-grid">{assets.map(asset => {
        const rows = structuredFieldRows(asset)
        const subtype = asset.asset_sub_type || asset.structured_info?.asset_sub_type || '其他附件'
        return <article className="knowledge-structured-card" key={asset.asset_id}><div className="knowledge-structured-card-head"><span className={subtypeTagClass(subtype)}>{subtypeOption(subtype)?.label || subtype}</span><strong>{asset.source_name || asset.item_title || assetFileName(asset) || '未命名素材'}</strong></div>{rows.length ? <div className="knowledge-structured-fields">{rows.map(([label, value]) => <div className="knowledge-structured-row" key={`${asset.asset_id}-${label}`}><span>{label}</span><strong>{value}</strong></div>)}</div> : <p className="knowledge-structured-empty">暂无可展示的结构化字段</p>}</article>
      })}</div> : <p className="knowledge-structured-empty">暂无可展示的结构化信息</p>}
    </section>
  )
}


function evidenceStatusText(item: KnowledgeItem) {
  if (item.evidence_review?.status === 'verified') return item.is_expired ? '已到期' : '已核验'
  return item.evidence_review?.status === 'withdrawn' ? '已撤销核验' : '待核验'
}

function EvidenceReviewPanel({ item, onChange }: { item: KnowledgeItem; onChange: (item: KnowledgeItem) => void }) {
  const [subject, setSubject] = useState(item.evidence_review?.subject ?? '')
  const [identifier, setIdentifier] = useState(item.evidence_review?.identifier ?? '')
  const [validUntil, setValidUntil] = useState(item.evidence_review?.valid_until ?? '')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const status = item.evidence_review?.status
  const ready = item.status === 'completed' && !isKnowledgeProcessingActive(item)
  const kind = item.evidence_kind || ''
  const idLabel = kind.startsWith('车辆') ? '车牌号或车辆识别代号'
    : kind.startsWith('业绩') ? '合同编号或可核对的项目名称' : '证件编号'
  const recognized = item.evidence_suggestions
  function useRecognizedFields() {
    setSubject(value => value || recognized?.subject || '')
    setIdentifier(value => value || recognized?.identifier || '')
    setValidUntil(value => value || recognized?.valid_until || '')
    setConfirmed(false)
  }
  async function review(verified: boolean) {
    setBusy(true)
    setMessage('')
    try {
      const res = await wordagentApi.reviewKnowledgeEvidence(item.item_id, { verified, subject, identifier, valid_until: validUntil || null })
      if (res.code !== 0) throw new Error(res.message)
      onChange(res.data)
      setConfirmed(false)
      setMessage(verified ? '已保存核验结果' : '已撤销核验')
    } catch (error) {
      setMessage(apiErrorMessage(error, '核验保存失败'))
    } finally {
      setBusy(false)
    }
  }
  return <section className="knowledge-evidence-panel">
    <h3>{kind} · {evidenceStatusText(item)}</h3>
    <p>在「图片资源」查看原件页。请核对归属主体、编号、完整性和有效期，再允许标书自动引用。</p>
    {item.evidence_review?.reviewed_by && <p>操作人 {item.evidence_review.reviewed_by} · {new Date(item.evidence_review.reviewed_at).toLocaleString()}</p>}
    <div className="knowledge-detail-form-grid">
      <label>适用企业全称<input maxLength={200} value={subject} onChange={event => { setSubject(event.target.value); setConfirmed(false) }} /></label>
      <label>{idLabel}<input maxLength={180} value={identifier} onChange={event => { setIdentifier(event.target.value); setConfirmed(false) }} /></label>
      <label>有效期截止日<input type="date" value={validUntil} onChange={event => { setValidUntil(event.target.value); setConfirmed(false) }} /></label>
    </div>
    <label className="knowledge-evidence-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />已核对原件并确认可用于当前企业的投标证明</label>
    <div className="knowledge-evidence-actions">
      <button className="light-btn" type="button" onClick={useRecognizedFields} disabled={!ready || !Object.keys(recognized || {}).length}>带入识别结果</button>
      <button className="dark-btn" type="button" onClick={() => void review(true)} disabled={!ready || busy || !confirmed || !subject.trim() || !identifier.trim()}>确认核验</button>
      {status === 'verified' && <button className="light-btn" type="button" onClick={() => void review(false)} disabled={busy}>撤销核验</button>}
    </div>
    {!ready && <p role="status">原件页正在处理，处理结束后可核验。</p>}
    {message && <p role="status">{message}</p>}
  </section>
}


function DetailModal({ item, categories, onChange, onClose, onSave, onRefresh, onReextractItem, onDeepExtract, reextractBusy, onReextractAsset, progressRefreshError }: { progressRefreshError: string; item: KnowledgeItem; categories: KnowledgeCategory[]; onChange: (item: KnowledgeItem) => void; onClose: () => void; onSave: () => void; onRefresh: () => Promise<void>; onReextractItem: (itemId: number) => void; onDeepExtract: (itemId: number) => void; reextractBusy: boolean; onReextractAsset: (asset: KnowledgeAsset) => void }) {
  const imageAssets = (item.assets || []).filter(asset => asset.asset_type === '图片' || assetUrl(asset.resource_path))
  const groupedAssets = groupAssetsByCategory(imageAssets)
  const [previewIndex, setPreviewIndex] = useState<number | null>(null)
  const [detailTab, setDetailTab] = useState<'content' | 'images' | 'versions'>('content')
  return (
    <div className="knowledge-detail-modal" role="dialog" aria-modal="true" aria-label="知识详情">
      <div className="knowledge-detail-modal-panel">
        <header><strong>知识详情</strong><button onClick={onClose}>关闭</button></header>
        <div className="knowledge-detail-modal-body">
          <KnowledgeProcessingProgress item={item} />
          {progressRefreshError && <p className="knowledge-processing-error" role="status">{progressRefreshError}</p>}
          <div className="knowledge-process-stats"><span>总页数 {item.total_pages || 0}</span><span>文字页 {item.text_page_count || 0}</span><span>扫描页 {item.scan_page_count || 0}</span><button type="button" className="light-btn" onClick={() => onDeepExtract(item.item_id)} disabled={reextractBusy || isKnowledgeProcessingActive(item)}>补全 / 重试深度识别</button></div>
          <div className="knowledge-detail-form-grid">
            <label>标题<input value={item.title} onChange={event => onChange({ ...item, title: event.target.value })} /></label>
            <label>分类<select value={item.category_id || ''} onChange={event => onChange({ ...item, category_id: event.target.value ? Number(event.target.value) : null })}><option value="">未分类</option>{categories.map(category => <option key={category.category_id} value={category.category_id}>{category.name}</option>)}</select></label>
            <label>标签<input value={(item.tags || []).join('，')} onChange={event => onChange({ ...item, tags: event.target.value.split(/[，,\s]+/).filter(Boolean) })} /></label>
            {!item.evidence_kind && <label>有效期<input type="date" value={item.valid_until?.slice(0, 10) || ''} onChange={event => onChange({ ...item, valid_until: event.target.value || null })} /></label>}
          </div>
          {item.evidence_kind && <EvidenceReviewPanel key={item.item_id} item={item} onChange={onChange} />}
          <div className="knowledge-detail-tabs"><button type="button" className={detailTab === 'content' ? 'active' : ''} onClick={() => setDetailTab('content')}>基础信息</button><button type="button" className={detailTab === 'images' ? 'active' : ''} onClick={() => setDetailTab('images')}>图片资源</button><button type="button" className={detailTab === 'versions' ? 'active' : ''} onClick={() => setDetailTab('versions')}>版本记录</button><button className="dark-btn" type="button" onClick={onSave}>保存修改</button></div>
          {detailTab === 'content' && <><section><h3>处理统计</h3><div className="knowledge-process-stats"><span>文本字数 {item.text_char_count || 0}</span><span>提取图片 {item.extracted_image_count || item.asset_count || 0}</span><span>处理页码 {item.total_pages ? `${item.current_page || item.total_pages}/${item.total_pages}` : '-'}</span>{Object.entries(item.image_category_stats || {}).map(([name, count]) => <span key={name}>{name} {count}</span>)}</div></section><StructuredInfoSection assets={imageAssets} onReextract={() => onReextractItem(item.item_id)} reextractBusy={reextractBusy} /><section><h3>全文内容</h3><pre>{item.content_text || item.content_preview || '暂无可提取文本。'}</pre></section></>}
          {detailTab === 'images' && <section className="knowledge-detail-image-tab"><h3>图片资源</h3>{imageAssets.length ? <div className="knowledge-asset-groups">{groupedAssets.map(group => <details key={group.category} open={group.assets.length > 0}><summary>{group.category}<span>{group.assets.length}</span></summary><div className="knowledge-asset-grid">{group.assets.map(asset => <AssetCard key={asset.asset_id} asset={asset} onOpen={() => setPreviewIndex(imageAssets.findIndex(item => item.asset_id === asset.asset_id))} />)}</div></details>)}</div> : <p>暂无关联图片或附件。</p>}</section>}
          {detailTab === 'versions' && <section><h3>版本记录</h3>{item.versions?.length ? item.versions.map(version => <p key={version.version_id}>V{version.version_no} · {version.create_time}</p>) : <p>暂无版本记录。</p>}</section>}
        </div>
      </div>
      {previewIndex !== null && imageAssets[previewIndex] && <AssetPreview assets={imageAssets} index={previewIndex} onIndex={setPreviewIndex} onClose={() => setPreviewIndex(null)} onSaved={async () => { await onRefresh(); const updated = await wordagentApi.getKnowledgeItem(item.item_id); if (updated.code === 0) onChange(updated.data) }} onReextract={onReextractAsset} reextractBusy={reextractBusy} />}
    </div>
  )
}

function AssetCard({ asset, onOpen }: { asset: KnowledgeAsset; onOpen: () => void }) {
  const [failed, setFailed] = useState(false)
  const src = assetUrl(asset.resource_path)
  const description = assetDisplayDescription(asset)
  return (
    <button type="button" className="knowledge-asset-card" onClick={onOpen}>
      <span>{src && !failed ? <img src={src} alt={description || asset.tags.join('、') || '关联资源'} onError={() => setFailed(true)} /> : <i>图片文件待重新上传</i>}</span>
      <strong>{asset.category || '其他附件类'}{asset.page_num ? ` · 第${asset.page_num}页` : ''}</strong>
      {asset.asset_sub_type && <span className={subtypeTagClass(asset.asset_sub_type)}>{subtypeOption(asset.asset_sub_type)?.label || asset.asset_sub_type}</span>}
      <em>{description || asset.tags.slice(0, 4).join('、') || assetFileName(asset) || '关联资源'}</em>
    </button>
  )
}

function AssetPreview({ assets, index, onIndex, onClose, onSaved, onReextract, reextractBusy }: { assets: KnowledgeAsset[]; index: number; onIndex: (index: number) => void; onClose: () => void; onSaved?: () => Promise<void> | void; onReextract?: (asset: KnowledgeAsset) => void; reextractBusy?: boolean }) {
  const asset = assets[index]
  const src = assetUrl(asset.resource_path)
  const [annotation, setAnnotation] = useState(assetDisplayDescription(asset))
  const [bidType, setBidType] = useState(asset.bid_type || '')
  const [lengthLevel, setLengthLevel] = useState(asset.length_level || '')
  const [extendTags, setExtendTags] = useState((asset.extend_tags || []).join(', '))
  const [saving, setSaving] = useState(false)
  const [scale, setScale] = useState(1)
  const [failed, setFailed] = useState(false)
  const [editing, setEditing] = useState(false)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState<{ x: number; y: number; originX: number; originY: number } | null>(null)
  useEffect(() => { setAnnotation(assetDisplayDescription(asset)); setBidType(asset.bid_type || ''); setLengthLevel(asset.length_level || ''); setExtendTags((asset.extend_tags || []).join(', ')) }, [asset.asset_id, asset.annotation, asset.ai_description, asset.bid_type, asset.length_level, asset.extend_tags])
  useEffect(() => { setScale(1); setOffset({ x: 0, y: 0 }); setFailed(false); setEditing(false) }, [asset.asset_id])
  const prev = () => onIndex((index - 1 + assets.length) % assets.length)
  const next = () => onIndex((index + 1) % assets.length)
  const fitImage = () => { setScale(1); setOffset({ x: 0, y: 0 }) }
  const zoom = (delta: number) => setScale(value => Math.max(0.25, Math.min(6, Number((value + delta).toFixed(2)))))
  const wheelZoom = (event: WheelEvent<HTMLElement>) => {
    event.preventDefault()
    setScale(value => Math.max(0.25, Math.min(6, Number((value * (event.deltaY > 0 ? 0.9 : 1.12)).toFixed(3)))))
  }
  const startDrag = (event: MouseEvent<HTMLElement>) => {
    if (!src || failed || scale <= 1) return
    event.preventDefault()
    setDragging({ x: event.clientX, y: event.clientY, originX: offset.x, originY: offset.y })
  }
  const dragImage = (event: MouseEvent<HTMLElement>) => {
    if (!dragging) return
    event.preventDefault()
    setOffset({ x: dragging.originX + event.clientX - dragging.x, y: dragging.originY + event.clientY - dragging.y })
  }
  const endDrag = () => setDragging(null)
  const metaTags = Array.from(new Set([asset.category || '其他附件类', asset.page_num ? `第 ${asset.page_num} 页` : '', asset.item_title || asset.source_name || '', asset.source_chapter || '', ...asset.tags.slice(0, 6)].filter((tag): tag is string => Boolean(tag))))
  const cleanOcrText = cleanAssetText(asset.ocr_text, 500)
  const hasImage = Boolean(src && !failed)
  const imageStyle: CSSProperties = { transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }
  async function saveAnnotation() {
    setSaving(true)
    try {
      await wordagentApi.updateKnowledgeAssetAnnotation(asset.asset_id, annotation)
      await onSaved?.()
    } catch (error) {
      window.alert(apiErrorMessage(error, '图片注释保存失败'))
    } finally {
      setSaving(false)
    }
  }
  async function saveMetadata() {
    setSaving(true)
    try {
      await wordagentApi.updateKnowledgeAssetMetadata(asset.asset_id, { bid_type: bidType || null, length_level: lengthLevel || null, extend_tags: extendTags.split(/[,，\s]+/).map(tag => tag.trim()).filter(Boolean) })
      await onSaved?.()
    } catch (error) {
      window.alert(apiErrorMessage(error, '图片维度标签保存失败'))
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="knowledge-image-preview" role="dialog" aria-modal="true">
      <div>
        <header><strong>{assetPreviewTitle(asset)}</strong><div className="knowledge-image-preview-tools"><button type="button" className="light-btn" onClick={() => onReextract?.(asset)} disabled={!onReextract || reextractBusy}>{reextractBusy ? '提取中…' : '重新提取信息'}</button><button type="button" onClick={() => zoom(-0.2)}>缩小</button><span>{Math.round(scale * 100)}%</span><button type="button" onClick={() => zoom(0.2)}>放大</button><button type="button" onClick={fitImage}>适应</button><button type="button" onClick={onClose}>关闭</button></div></header>
        <section onWheel={wheelZoom} onMouseDown={startDrag} onMouseMove={dragImage} onMouseUp={endDrag} onMouseLeave={endDrag} className={`knowledge-image-zoom-stage ${scale > 1 && hasImage ? 'is-draggable' : ''}`}>{hasImage ? <img src={src} alt={annotation || assetPreviewTitle(asset)} style={imageStyle} draggable={false} onError={() => setFailed(true)} /> : <div className="knowledge-image-empty"><strong>图片文件不存在或已被清理</strong><span>当前记录指向的本地图片无法访问，请先清理冗余图片后重新上传资料。</span>{assetFileName(asset) && <small>{assetFileName(asset)}</small>}</div>}</section>
        <div className="knowledge-image-meta">{metaTags.map(tag => <span key={tag}>{tag}</span>)}</div>
        <div className="knowledge-image-annotation">
          <label>标书类型<select value={bidType} onChange={event => setBidType(event.target.value)}><option value="">未设置</option><option value="open">明标</option><option value="blind">暗标</option></select></label>
          <label>篇幅等级<select value={lengthLevel} onChange={event => setLengthLevel(event.target.value)}><option value="">未设置</option><option value="short">精简版</option><option value="standard">标准版</option><option value="detailed">详细版</option></select></label>
          <label>扩展标签<input value={extendTags} onChange={event => setExtendTags(event.target.value)} placeholder="例如：服务方案, 资质证书" /></label>
          <button type="button" onClick={() => void saveMetadata()} disabled={saving}>{saving ? '保存中...' : '保存维度标签'}</button>
        </div>
        {annotation && !editing && <p className="knowledge-image-description">{annotation}</p>}
        {cleanOcrText && <details className="knowledge-ocr-text"><summary>查看 OCR 摘要</summary><pre>{cleanOcrText}</pre></details>}
        <div className="knowledge-image-annotation"><button type="button" className="knowledge-image-editor-toggle" onClick={() => setEditing(value => !value)}>{editing ? '收起注释编辑' : '编辑注释'}</button>{editing && <><label>语义注释<textarea value={annotation} onChange={event => setAnnotation(event.target.value)} placeholder="图片核心主体、关键信息提取、业务说明" /></label><button type="button" onClick={() => void saveAnnotation()} disabled={saving}>{saving ? '保存中...' : '保存注释'}</button></>}</div>
        {assets.length > 1 && <footer><button type="button" onClick={prev}>上一张</button><span>{index + 1} / {assets.length}</span><button type="button" onClick={next}>下一张</button></footer>}
      </div>
    </div>
  )
}

function GraphModal({ graph, categories, items, initialCategoryId, onClose }: { graph: KnowledgeGraph; categories: KnowledgeCategory[]; items: KnowledgeItem[]; initialCategoryId: number | null; onClose: () => void }) {
  const width = 1280
  const height = 760
  const [categoryId, setCategoryId] = useState<number | null>(initialCategoryId)
  const [scale, setScale] = useState(1)
  const [rotation, setRotation] = useState({ x: -0.18, y: 0.36 })
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [positions, setPositions] = useState<Record<string, { x: number; y: number; z: number }>>({})
  const [dragging, setDragging] = useState<
    | { type: 'rotate'; startX: number; startY: number; rotateX: number; rotateY: number }
    | { type: 'pan'; startX: number; startY: number; panX: number; panY: number }
    | { type: 'node'; id: string; startX: number; startY: number; nodeX: number; nodeY: number }
    | null
  >(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const filteredNodes = useMemo(() => graph.nodes.filter(node => !categoryId || node.category_id === categoryId).slice(0, 160), [graph.nodes, categoryId])
  const nodeIds = useMemo(() => new Set(filteredNodes.map(node => node.id)), [filteredNodes])
  const filteredLinks = useMemo(() => graph.links.filter(link => nodeIds.has(link.source) && nodeIds.has(link.target)).slice(0, 280), [graph.links, nodeIds])
  const degrees = useMemo(() => {
    const map = new Map<string, number>()
    filteredLinks.forEach(link => { map.set(link.source, (map.get(link.source) || 0) + 1); map.set(link.target, (map.get(link.target) || 0) + 1) })
    return map
  }, [filteredLinks])
  const selectedNode = filteredNodes.find(node => node.id === selectedId) || null
  const hoveredNode = filteredNodes.find(node => node.id === hoveredId) || null
  const connectedIds = useMemo(() => {
    const id = hoveredId || selectedId
    const set = new Set<string>()
    if (!id) return set
    filteredLinks.forEach(link => {
      if (link.source === id || link.target === id) { set.add(link.source); set.add(link.target) }
    })
    return set
  }, [filteredLinks, hoveredId, selectedId])

  useEffect(() => {
    setPositions(prev => {
      const next: Record<string, { x: number; y: number; z: number }> = {}
      const total = Math.max(1, filteredNodes.length)
      filteredNodes.forEach((node, index) => {
        const tier = graphNodeTier(node, degrees.get(node.id) || 0)
        const theta = index * Math.PI * (3 - Math.sqrt(5))
        const y = 1 - (index / Math.max(1, total - 1)) * 2
        const radiusAtY = Math.sqrt(Math.max(0, 1 - y * y))
        const radius = (tier === 'primary' ? 190 : tier === 'secondary' ? 330 : 455) + Math.min(150, total * 1.6)
        next[node.id] = prev[node.id] || { x: Math.cos(theta) * radiusAtY * radius, y: y * radius * 0.72, z: Math.sin(theta) * radiusAtY * radius }
      })
      return next
    })
    setSelectedId(null)
  }, [filteredNodes, degrees])

  const projectPoint = (point: { x: number; y: number; z: number }) => {
    const cosY = Math.cos(rotation.y), sinY = Math.sin(rotation.y)
    const cosX = Math.cos(rotation.x), sinX = Math.sin(rotation.x)
    const x1 = point.x * cosY - point.z * sinY
    const z1 = point.x * sinY + point.z * cosY
    const y2 = point.y * cosX - z1 * sinX
    const z2 = point.y * sinX + z1 * cosX
    const perspective = 760 / (760 + z2)
    return { x: width / 2 + pan.x + x1 * perspective * scale, y: height / 2 + pan.y + y2 * perspective * scale, z: z2, perspective }
  }
  const projected = useMemo(() => {
    const map = new Map<string, { x: number; y: number; z: number; perspective: number }>()
    filteredNodes.forEach(node => {
      const point = positions[node.id]
      if (point) map.set(node.id, projectPoint(point))
    })
    return map
  }, [filteredNodes, positions, rotation, pan, scale])
  const wheel = (event: WheelEvent<SVGSVGElement>) => {
    event.preventDefault()
    setScale(value => Math.max(0.2, Math.min(3, value + (event.deltaY > 0 ? -0.08 : 0.08))))
  }
  const move = (event: MouseEvent<SVGSVGElement>) => {
    if (!dragging) return
    if (dragging.type === 'rotate') {
      setRotation({ x: dragging.rotateX + (event.clientY - dragging.startY) * 0.006, y: dragging.rotateY + (event.clientX - dragging.startX) * 0.006 })
      return
    }
    if (dragging.type === 'pan') {
      setPan({ x: dragging.panX + event.clientX - dragging.startX, y: dragging.panY + event.clientY - dragging.startY })
      return
    }
    const dx = (event.clientX - dragging.startX) / Math.max(0.2, scale)
    const dy = (event.clientY - dragging.startY) / Math.max(0.2, scale)
    setPositions(prev => ({ ...prev, [dragging.id]: { ...(prev[dragging.id] || { z: 0 }), x: dragging.nodeX + dx, y: dragging.nodeY + dy } }))
  }
  const sceneDown = (event: MouseEvent<SVGSVGElement>) => {
    setDragging(event.shiftKey ? { type: 'pan', startX: event.clientX, startY: event.clientY, panX: pan.x, panY: pan.y } : { type: 'rotate', startX: event.clientX, startY: event.clientY, rotateX: rotation.x, rotateY: rotation.y })
  }
  const nodeDown = (event: MouseEvent<SVGGElement>, id: string) => {
    event.stopPropagation()
    const pos = positions[id]
    if (!pos) return
    setDragging({ type: 'node', id, startX: event.clientX, startY: event.clientY, nodeX: pos.x, nodeY: pos.y })
  }

  return (
    <div className="knowledge-graph-modal">
      <div className="knowledge-graph-panel">
        <header>
          <strong>知识图谱</strong>
          <div><select value={categoryId || ''} onChange={event => setCategoryId(event.target.value ? Number(event.target.value) : null)}><option value="">全部分类</option>{categories.map(category => <option key={category.category_id} value={category.category_id}>{category.name}</option>)}</select><button onClick={onClose}>关闭</button></div>
        </header>
        <svg viewBox={`0 0 ${width} ${height}`} onWheel={wheel} onMouseDown={sceneDown} onMouseMove={move} onMouseUp={() => setDragging(null)} onMouseLeave={() => setDragging(null)} role="img" aria-label="三维知识图谱">
          <defs><filter id="node-glow"><feDropShadow dx="0" dy="0" stdDeviation="6" floodColor="rgba(255,255,255,0.28)" /></filter></defs>
          <g>
            {filteredLinks.map(link => {
              const s = projected.get(link.source), t = projected.get(link.target)
              if (!s || !t) return null
              const active = connectedIds.size === 0 || connectedIds.has(link.source) || connectedIds.has(link.target)
              const depth = Math.max(0.35, Math.min(1, (s.perspective + t.perspective) / 2))
              const sourceNode = filteredNodes.find(node => node.id === link.source)
              const targetNode = filteredNodes.find(node => node.id === link.target)
              const strong = Boolean(sourceNode && targetNode && graphNodeTier(sourceNode, degrees.get(sourceNode.id) || 0) === 'primary' && graphNodeTier(targetNode, degrees.get(targetNode.id) || 0) !== 'tertiary')
              return <line key={link.id} className={`${active ? 'active' : ''} ${strong ? 'strong' : ''}`.trim()} x1={s.x} y1={s.y} x2={t.x} y2={t.y} strokeWidth={(strong ? 2.6 : active ? 1.5 : 0.55) * depth} />
            })}
            {[...filteredNodes].sort((a, b) => (projected.get(a.id)?.z || 0) - (projected.get(b.id)?.z || 0)).map(node => {
              const pos = projected.get(node.id)
              if (!pos) return null
              const degree = degrees.get(node.id) || 0
              const tier = graphNodeTier(node, degree)
              const size = graphNodeSize(tier, degree, pos.perspective)
              const active = connectedIds.size === 0 || connectedIds.has(node.id) || node.id === selectedId
              const label = node.name.slice(0, 10)
              const labelWidth = Math.max(72, label.length * 13 + 18)
              return <g key={node.id} className={`${active ? 'knowledge-graph-node active' : 'knowledge-graph-node'} ${tier}`.trim()} transform={`translate(${pos.x} ${pos.y})`} onMouseDown={event => nodeDown(event, node.id)} onMouseEnter={() => setHoveredId(node.id)} onMouseLeave={() => setHoveredId(null)} onClick={() => setSelectedId(node.id)}><circle r={size} fill={graphNodeColor(node, tier)} /><rect className="knowledge-graph-label-bg" x={-labelWidth / 2} y={size + 7} width={labelWidth} height="24" rx="12" /><text className="knowledge-graph-label" y={size + 23} textAnchor="middle">{label}</text><text className="knowledge-graph-sub" y={-size - 7} textAnchor="middle">{degree} 关联</text></g>
            })}
          </g>
        </svg>
        {!filteredNodes.length && <StatusBox>暂无图谱节点，请先上传并完成知识处理。</StatusBox>}
        {hoveredNode && <aside className="knowledge-graph-tooltip"><strong>{hoveredNode.name}</strong><span>{hoveredNode.type} · {hoveredNode.category_name || '未分类'} · {degrees.get(hoveredNode.id) || 0} 个关联</span></aside>}
        {selectedNode && <GraphNodePanel node={selectedNode} degree={degrees.get(selectedNode.id) || 0} item={items.find(item => item.item_id === selectedNode.item_id) || null} />}
      </div>
    </div>
  )
}

function GraphNodePanel({ node, degree, item }: { node: KnowledgeGraph['nodes'][number]; degree: number; item: KnowledgeItem | null }) {
  return <aside className="knowledge-graph-detail"><strong>{node.name}</strong><span>{node.type} · {node.category_name || '未分类'} · {degree} 个关联</span>{item ? <p>{item.title}</p> : <p>暂无关联资料条目。</p>}</aside>
}

function StatusBox({ children }: { children: string }) { return <div style={{ ...font, fontSize: '13px', color: 'var(--form-desc-color)', background: 'var(--form-ref-bg)', border: '1px solid var(--form-divider)', borderRadius: '6px', padding: '12px 14px' }}>{children}</div> }
function EmptyState() { return <div className="knowledge-empty">暂无知识条目，请点击“上传文件”或拖拽文件入库。</div> }
function statusText(item: KnowledgeItem) { return item.status === 'completed' ? '已完成' : item.status === 'processing' ? `处理中 ${item.process_progress}%` : item.status === 'failed' ? '处理失败' : '待处理' }
function deepStatusText(item: KnowledgeItem) {
  if (item.deep_status === 'processing' || item.deep_status === 'pending') return `深度识别 ${item.deep_progress || 0}%`
  if (item.deep_status === 'completed') return '深度识别已完成'
  if (item.deep_status === 'failed') return item.deep_error ? `深度识别失败：${item.deep_error}` : '深度识别失败'
  return ''
}
function statusMeta(item: KnowledgeItem) {
  if (item.status === 'processing' || item.status === 'pending') {
    if (item.total_pages) return `第 ${item.current_page || 0}/${item.total_pages} 页 · 图片 ${item.extracted_image_count || 0}`
    return item.extracted_image_count ? `图片 ${item.extracted_image_count}` : ''
  }
  if (item.status === 'completed') return `文本 ${item.text_char_count || 0} 字 · 图片 ${item.extracted_image_count || item.asset_count || 0}`
  return item.process_error || ''
}
function isRetryableFailure(item: KnowledgeItem) { return item.status === 'failed' && /database is locked|数据库锁|锁冲突|中断|timeout|timed out/i.test(item.process_error || '') }
function highlight(text: string, query: string) { const key = query.trim().slice(0, 12); if (!key || !text.includes(key)) return text; const parts = text.split(key); return <>{parts.map((part, index) => <span key={index}>{part}{index < parts.length - 1 && <mark>{key}</mark>}</span>)}</> }
function formatDate(value: string | null | undefined) { return value ? value.replace('T', ' ').slice(0, 16) : '-' }
function formatFileSize(value: number | null | undefined) { if (!value) return '-'; if (value < 1024) return `${value} B`; if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB` }
function cleanAssetText(value?: string | null, maxLength = 180) {
  const text = (value || '').trim()
  if (!text) return ''
  const compact = text.replace(/\s+/g, ' ')
  const looksLikeRawData = /^[\[{]/.test(compact) || /["']?(person_info|employee_info|contract_info|ocr_text|ai_description)["']?\s*[:=]/i.test(compact)
  if (looksLikeRawData) return ''
  return compact.length > maxLength ? `${compact.slice(0, maxLength)}...` : compact
}
function assetFileName(asset: KnowledgeAsset) {
  return asset.resource_path?.split(/[\\/]/).pop() || ''
}
function assetDisplayDescription(asset: KnowledgeAsset) {
  return cleanAssetText(asset.annotation, 360) || cleanAssetText(asset.ai_description, 260)
}
function assetPreviewTitle(asset: KnowledgeAsset) {
  const page = asset.page_num ? ` · 第${asset.page_num}页` : ''
  return `${asset.category || '图片预览'}${page}`
}
function assetUrl(path?: string | null) {
  if (!path) return ''
  if (/^(https?:|data:|blob:)/i.test(path)) return path
  const normalized = path.replace(/\\/g, '/')
  if (/^[a-z]:\//i.test(normalized)) return ''
  const origin = wordagentApi.apiBase.replace(/\/api\/?$/, '')
  if (normalized.startsWith('data/uploads/')) return `${origin}/uploads/${normalized.slice('data/uploads/'.length)}`
  if (normalized.startsWith('/')) return `${origin}${normalized}`
  return `${origin}/${normalized}`
}
function categoryColor(categoryId: number | null) {
  const colors = ['#F4F2EE', '#EDEBE4', '#FFF9E8', '#F7FAF5', '#EEF3FE', '#FFFFFF']
  return colors[Math.abs(categoryId || 0) % colors.length]
}
type GraphTier = 'primary' | 'secondary' | 'tertiary'
function graphNodeTier(node: KnowledgeGraph['nodes'][number], degree = 0): GraphTier {
  if (/项目|标书项目|项目名称/.test(node.type) || /项目|集团|公司|食堂|采购/.test(node.name) && degree >= 3) return 'primary'
  if (/分类|知识条目|资质名称|设备名称|人员/.test(node.type) || degree >= 5) return 'secondary'
  return 'tertiary'
}
function graphNodeSize(tier: GraphTier, degree: number, perspective: number) {
  const base = tier === 'primary' ? 42 : tier === 'secondary' ? 23 : 10
  const bonus = tier === 'primary' ? Math.min(18, degree * 2.2) : tier === 'secondary' ? Math.min(11, degree * 1.5) : Math.min(5, degree * 0.8)
  return (base + bonus) * Math.max(0.68, Math.min(1.28, perspective))
}
function graphNodeColor(node: KnowledgeGraph['nodes'][number], tier: GraphTier) {
  const primary = ['#FF8A3D', '#2DAA8A', '#4B8FF7', '#C45BE8', '#F0B429', '#E05252', '#2EB7D8', '#7AA65A']
  const base = primary[Math.abs((node.category_id || Number(node.id.replace(/\D/g, '')) || 0)) % primary.length]
  if (tier === 'primary') return base
  if (tier === 'secondary') return softenColor(base, 0.48)
  return softenColor(base, 0.72)
}
function softenColor(hex: string, ratio: number) {
  const value = hex.replace('#', '')
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  const mix = (channel: number) => Math.round(channel + (255 - channel) * ratio).toString(16).padStart(2, '0')
  return `#${mix(r)}${mix(g)}${mix(b)}`
}
function groupAssetsByCategory(assets: KnowledgeAsset[]) {
  const categories = [...IMAGE_CATEGORIES]
  assets.forEach(asset => {
    const category = normalizeImageCategory(asset.category)
    if (!categories.includes(category)) categories.push(category)
  })
  return categories.map(category => ({ category, assets: assets.filter(asset => normalizeImageCategory(asset.category) === category) })).filter(group => group.assets.length > 0)
}

function normalizeImageCategory(category?: string | null) {
  const value = (category || '').trim()
  if (!value) return '其他附件类'
  if (IMAGE_CATEGORIES.includes(value)) return value
  if (/人员|健康证|身份证|从业/.test(value)) return '人员资质类'
  if (/营业|许可|认证|资质|证书|荣誉/.test(value)) return '资质证书类'
  if (/设备|资产|检测仪|保鲜/.test(value)) return '设备资产类'
  if (/业绩|合同|中标|验收|评价/.test(value)) return '业绩证明类'
  if (/现场|冷库|车辆|冷链|分拣|实验室|食材/.test(value)) return '现场场景类'
  return '其他附件类'
}

function formatPersonInfo(personInfo: Record<string, string>) {
  const labels: Record<string, string> = { name: '姓名', gender: '性别', id_number: '证件编号', position: '职位', valid_until: '有效期' }
  return Object.entries(personInfo).filter(([, value]) => Boolean(value)).map(([key, value]) => `${labels[key] || key}：${value}`).join('；')
}

function formatEmployeeInfo(employeeInfo?: Record<string, string> | null) {
  if (!employeeInfo) return ''
  const labels: Record<string, string> = { name: '姓名', gender: '性别', role: '角色', certificate_type: '证件类型', certificate_no: '证件编号', position: '岗位', valid_until: '有效期', skill_tags: '能力标签', source_context: '来源' }
  return Object.entries(employeeInfo).filter(([, value]) => Boolean(value)).map(([key, value]) => `${labels[key] || key}：${value}`).join('；')
}

function formatContractInfo(contractInfo?: Record<string, string> | null) {
  if (!contractInfo) return ''
  const labels: Record<string, string> = { contract_name: '合同名称', project_name: '项目名称', party_a: '甲方', party_b: '乙方', amount: '金额', signed_date: '签订日期', service_period: '服务期限', contract_type: '类型', archive_year: '年份', archive_date: '归档日期' }
  return Object.entries(contractInfo).filter(([, value]) => Boolean(value)).map(([key, value]) => `${labels[key] || key}：${value}`).join('；')
}
