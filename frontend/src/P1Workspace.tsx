import { lazy, Suspense, type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import {
  type ApiProject,
  type BidCatalogResponse,
  type BidParseDetail,
  type BidParseInfo,
  type BidParseStatus,
  type CatalogNode,
  type ChapterMaterials,
  type DuplicateBidParseMatch,
  type KnowledgeAsset,
  wordagentApi,
} from './wordagentApi'
import { companyStorageKey } from './auth'
import { readEventStream } from './utils/eventStream'
import { LiveExtractionProgress } from './components/ExtractionProgress'
import { ProjectDeleteDialog } from './components/ProjectDeleteDialog'
import { ProjectSourceSummary } from './components/ProjectSourceSummary'
import { CaseReferenceSelector } from './components/CaseReferenceSelector'
import type { UploadProgress } from './wordagentApi'
import { useAuth } from './AuthWorkspace'
import { AdminWorkspace } from './AdminWorkspace'
import { ModelSettingsWorkspace } from './ModelSettingsWorkspace'
import mushiLogo from './assets/mushi-logo.png'
const TenderSearchWorkspace = lazy(() => import('./TenderSearchWorkspace').then(module => ({ default: module.TenderSearchWorkspace })))
const KnowledgeWorkspace = lazy(() => import('./KnowledgeWorkspace').then(module => ({ default: module.KnowledgeWorkspace })))
const GraphicSectionStep = lazy(() => import('./GraphicBidWorkspace').then(module => ({ default: module.GraphicSectionStep })))
const MermaidDiagramStep = lazy(() => import('./GraphicBidWorkspace').then(module => ({ default: module.MermaidDiagramStep })))
const AuditWorkspace = lazy(() => import('./AuditWorkspace').then(module => ({ default: module.AuditWorkspace })))
const OrchestrationWorkspace = lazy(() => import('./OrchestrationWorkspace').then(module => ({ default: module.OrchestrationWorkspace })))
const InvoiceVerificationWorkspace = lazy(() => import('./InvoiceBatchWorkspace').then(module => ({ default: module.InvoiceBatchWorkspace })))
const VehicleRouteWorkspace = lazy(() => import('./VehicleRouteWorkspace').then(module => ({ default: module.VehicleRouteWorkspace })))
const DeliveryRouteWorkspace = lazy(() => import('./MultiDepotWorkspace').then(module => ({ default: module.MultiDepotWorkspace })))
const PptWorkspace = lazy(() => import('./PptWorkspace').then(module => ({ default: module.PptWorkspace })))

type View = 'tenderSearch' | 'home' | 'p1Select' | 'p1' | 'knowledge' | 'graphicMermaid' | 'graphicSection' | 'audit' | 'orchestration' | 'invoice' | 'vehicleRoute' | 'deliveryRoute' | 'admin' | 'models' | 'ppt'
type Phase = 0 | 1 | 2
type ParseMethod = 'auto' | 'paste' | null
type DuplicateFileChoice = 'overwrite' | 'new' | 'merge' | 'cancel'
type CatalogLeftTab = '思路' | '评分' | '概述'
type RunApi = <T>(action: () => Promise<T>) => Promise<T | undefined>
type BidCategoryCode = 'service' | 'goods' | 'engineering' | 'other'

const TENDER_UPLOAD_MAX_SIZE = 1024 * 1024 * 1024
const TENDER_UPLOAD_EXTENSIONS = ['.doc', '.docx', '.pdf']
const PASTE_PARSE_MAX_LENGTH = 30000
const BID_CATEGORY_TO_API: Record<string, BidCategoryCode> = {
  服务类: 'service',
  货物类: 'goods',
  '工程类(含施组方案)': 'engineering',
  更多行业: 'other',
}
const BID_CATEGORY_FROM_API: Record<BidCategoryCode, string> = {
  service: '服务类',
  goods: '货物类',
  engineering: '工程类(含施组方案)',
  other: '更多行业',
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

function formatTransferBytes(value: number) {
  return value >= 1024 * 1024 ? (value / (1024 * 1024)).toFixed(1) + ' MiB' : (value / 1024).toFixed(1) + ' KiB'
}

function formatProjectDate(value: string | null | undefined) {
  return value ? value.replace('T', ' ').slice(0, 16) : '暂无时间'
}

export function P1Workspace() {
  const lastBidIdKey = companyStorageKey('new_biaoshuai.lastBidId')
  const [view, setView] = useState<View>('home')
  const [phase, setPhase] = useState<Phase>(0)
  const [project, setProject] = useState<ApiProject | null>(null)
  const [bidId, setBidId] = useState<number | null>(() => Number(window.localStorage.getItem(lastBidIdKey) || 0) || null)
  const [booting, setBooting] = useState(false)

  const run: RunApi = async action => {
    try {
      return await action()
    } catch (error) {
      console.error(error)
      window.alert(apiErrorMessage(error, '操作失败，请检查后端服务是否已启动'))
      return undefined
    }
  }

  async function ensureBid() {
    setBooting(true)
    try {
      if (bidId) {
        try {
          // Entry validation only needs the lightweight status response. The
          // full parse payload can contain the complete tender text.
          await wordagentApi.getBidParseStatus(bidId)
          return bidId
        } catch (error) {
          console.warn('Cached bidId is unavailable, creating a fresh project.', error)
          setBidId(null)
          window.localStorage.removeItem(lastBidIdKey)
        }
      }
      const response = await wordagentApi.createProject({
        bidTitle: '投标文件',
        pageCount: 200,
        wordsPerPage: 800,
        estimatedWordCount: 160000,
        bidType: 'open',
        selectedModel: 'qwen3.7-flash',
        selectedProvider: 'qwen',
      })
      setProject(response.project)
      if (response.bidId) {
        setBidId(response.bidId)
        window.localStorage.setItem(lastBidIdKey, String(response.bidId))
        return response.bidId
      }
      return null
    } finally {
      setBooting(false)
    }
  }

  async function openP1() {
    setPhase(0)
    setView('p1Select')
  }

  async function startFreshP1Project() {
    const response = await run(() => wordagentApi.createProject({
      bidTitle: '投标文件',
      pageCount: 200,
      wordsPerPage: 800,
      estimatedWordCount: 160000,
      bidType: 'open',
      selectedModel: 'qwen3.7-flash',
      selectedProvider: 'qwen',
    }))
    if (!response?.bidId) return
    setProject(response.project)
    setBidId(response.bidId)
    window.localStorage.setItem(lastBidIdKey, String(response.bidId))
    setPhase(0)
    setView('p1')
  }

  async function loadP1Project(projectId: string) {
    const response = await run(() => wordagentApi.getProject(projectId))
    if (!response?.bidId) return
    setProject(response.project)
    setBidId(response.bidId)
    window.localStorage.setItem(lastBidIdKey, String(response.bidId))
    setPhase(0)
    setView('p1')
  }

  function handleProjectDeleted(projectId: string, deletedBidId?: number) {
    const wasSelected = project?.id === projectId || (deletedBidId !== undefined && bidId === deletedBidId)
    try {
      const cachedBidId = Number(window.localStorage.getItem(lastBidIdKey) || 0)
      if ((deletedBidId !== undefined && cachedBidId === deletedBidId) || (wasSelected && cachedBidId === bidId)) {
        window.localStorage.removeItem(lastBidIdKey)
      }
    } catch {
      // Selection in memory still needs clearing if browser storage is unavailable.
    }
    if (wasSelected) {
      setProject(null)
      setBidId(null)
      setPhase(0)
    }
  }

  function backHome() {
    setView('home')
  }

  function openKnowledge() {
    setView('knowledge')
  }

  async function openGraphic() {
    setView('graphicSection')
    // Render the workspace first; project validation/creation continues in the background.
    void ensureBid().catch(error => {
      console.error(error)
      window.alert(apiErrorMessage(error, '项目初始化失败，请稍后重试'))
      setView('home')
    })
  }

  async function openGraphicImage() {
    setView('graphicMermaid')
    void ensureBid().catch(error => {
      console.error(error)
      window.alert(apiErrorMessage(error, '项目初始化失败，请稍后重试'))
      setView('home')
    })
  }

  function openAudit() {
    setView('audit')
  }

  function openOrchestration() {
    setView('orchestration')
  }

  function openInvoice() {
    setView('invoice')
  }

  function openVehicleRoute() {
    setView('vehicleRoute')
  }

  function openDeliveryRoute() {
    setView('deliveryRoute')
  }

  function openAdmin() {
    setView('admin')
  }

  return (
    <div className="p1-app">
      {view === 'home' && <HomePage onOpen={openP1} onOpenKnowledge={openKnowledge} onOpenGraphic={openGraphic} onOpenGraphicImage={openGraphicImage} onOpenAudit={openAudit} onOpenOrchestration={openOrchestration} onOpenInvoice={openInvoice} onOpenVehicleRoute={openVehicleRoute} onOpenDeliveryRoute={openDeliveryRoute} onOpenAdmin={openAdmin} onOpenModels={() => setView('models')} onOpenPpt={() => setView('ppt')} onOpenTenderSearch={() => setView('tenderSearch')} booting={booting} />}
      {view === 'tenderSearch' && <Suspense fallback={<ModuleLoading />}><TenderSearchWorkspace onHome={backHome} /></Suspense>}
      {view === 'admin' && <AdminWorkspace onHome={backHome} />}
      {view === 'models' && <ModelSettingsWorkspace onHome={backHome} />}
      {view === 'ppt' && <Suspense fallback={<ModuleLoading />}><PptWorkspace onHome={backHome} /></Suspense>}
      {view === 'p1Select' && <P1ProjectSelect onHome={backHome} onCreate={() => void startFreshP1Project()} onSelect={projectId => void loadP1Project(projectId)} onDeleted={handleProjectDeleted} />}
      {view === 'knowledge' && <Suspense fallback={<ModuleLoading />}><KnowledgeWorkspace onHome={backHome} /></Suspense>}
      {view === 'audit' && <Suspense fallback={<ModuleLoading />}><AuditWorkspace onHome={backHome} /></Suspense>}
      {view === 'orchestration' && <Suspense fallback={<ModuleLoading />}><OrchestrationWorkspace bidId={bidId} onBidChange={setBidId} ensureBid={ensureBid} onHome={backHome} onOpenGraphic={openGraphic} /></Suspense>}
      {view === 'invoice' && <Suspense fallback={<ModuleLoading />}><InvoiceVerificationWorkspace onHome={backHome} /></Suspense>}
      {view === 'vehicleRoute' && <Suspense fallback={<ModuleLoading />}><VehicleRouteWorkspace onHome={backHome} /></Suspense>}
      {view === 'deliveryRoute' && <Suspense fallback={<ModuleLoading />}><DeliveryRouteWorkspace bidId={bidId} onHome={backHome} /></Suspense>}
      {view === 'p1' && phase === 0 && (
        <TenderStep
          bidId={bidId}
          run={run}
          onBack={() => setView('p1Select')}
          onHome={backHome}
          onProjectChange={(nextProject, nextBidId) => { setProject(nextProject); setBidId(nextBidId); window.localStorage.setItem(lastBidIdKey, String(nextBidId)) }}
          onReady={() => setPhase(1)}
        />
      )}
      {view === 'p1' && phase === 1 && (
        <CatalogStep
          bidId={bidId}
          project={project}
          onProject={setProject}
          onBack={() => setPhase(0)}
          onHome={backHome}
          onNext={() => setPhase(2)}
        />
      )}
      {view === 'p1' && phase === 2 && <Suspense fallback={<ModuleLoading />}>
        <GraphicSectionStep
          bidId={bidId}
          onHome={backHome}
          onBackToCatalog={() => setPhase(1)}
          onBackToMermaid={() => setPhase(1)}
          embeddedInP1
        />
      </Suspense>}
      {view === 'graphicMermaid' && <Suspense fallback={<ModuleLoading />}><MermaidDiagramStep bidId={bidId} onHome={backHome} onBack={backHome} onNext={() => setView('graphicSection')} /></Suspense>}
      {view === 'graphicSection' && <Suspense fallback={<ModuleLoading />}><GraphicSectionStep bidId={bidId} onHome={backHome} onBackToCatalog={() => setView('graphicMermaid')} onBackToMermaid={() => setView('graphicMermaid')} /></Suspense>}
    </div>
  )
}

function TopNav({ onLogoClick, onAdmin, onBack }: { onLogoClick?: () => void; onAdmin?: () => void; onBack?: () => void }) {
  const { user, company, logout } = useAuth()
  return (
    <header className="top-nav">
      <button className="logo-button brand-logo-button" type="button" onClick={onLogoClick}><img src={mushiLogo} alt="" aria-hidden="true" /><span>木石科技</span></button>
      <nav className="nav-actions" aria-label="主导航">
        {onBack && <button className="nav-btn nav-btn-a" type="button" onClick={onBack}>返回上一步</button>}
        <span className="nav-company-name" title={company?.company_name}>{company?.company_name || '示例公司'}</span>
        {onAdmin && user?.role === 'admin' && user.company_id === 'company_default' && <button className="nav-btn nav-btn-a" type="button" onClick={onAdmin}>账号管理</button>}
        <button className="nav-btn nav-btn-b" type="button" onClick={() => void logout()}>退出登录</button>
      </nav>
    </header>
  )
}

function HeroTitle() {
  return (
    <svg viewBox="0 0 860 100" width="860" height="100" style={{ maxWidth: '90%', overflow: 'visible', display: 'block' }} aria-label="示例公司智能标书助手">
      <defs>
        <path id="p1-hero-arc" d="M 30,90 C 200,68 660,68 830,90" fill="none" />
        <filter id="p1-soft-shadow" x="-4%" y="-20%" width="108%" height="160%">
          <feDropShadow dx="1" dy="1" stdDeviation="0" floodColor="rgba(50,44,30,0.18)" />
          <feDropShadow dx="2" dy="2" stdDeviation="0" floodColor="rgba(50,44,30,0.14)" />
          <feDropShadow dx="3" dy="3" stdDeviation="0" floodColor="rgba(50,44,30,0.10)" />
          <feDropShadow dx="4" dy="4" stdDeviation="0" floodColor="rgba(50,44,30,0.07)" />
          <feDropShadow dx="3" dy="5" stdDeviation="4" floodColor="rgba(0,0,0,0.12)" />
        </filter>
      </defs>
      <text filter="url(#p1-soft-shadow)" style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: '72px', fontWeight: 900, fill: 'var(--title-color)', letterSpacing: '1px' }}>
        <textPath href="#p1-hero-arc" textAnchor="middle" startOffset="50%">示例公司智能标书助手</textPath>
      </text>
    </svg>
  )
}

function HomeCard({ title, desc, icon, onClick, disabled, booting }: { title: string; desc: string; icon: ReactNode; onClick?: () => void; disabled?: boolean; booting?: boolean }) {
  return (
    <button
      className={disabled ? 'home-feature-card disabled' : 'home-feature-card'}
      type="button"
      title={disabled ? '敬请期待' : undefined}
      disabled={disabled || booting}
      onClick={onClick}
    >
      {icon}
      <span className="home-card-title">{title}</span>
      <span className="home-card-desc">{booting ? '正在进入...' : desc}</span>
    </button>
  )
}

function HomePage({ onOpen, onOpenKnowledge, onOpenGraphic, onOpenGraphicImage, onOpenAudit, onOpenOrchestration, onOpenInvoice, onOpenVehicleRoute, onOpenDeliveryRoute, onOpenAdmin, onOpenModels, onOpenPpt, onOpenTenderSearch, booting }: { onOpen: () => void; onOpenKnowledge: () => void; onOpenGraphic: () => void; onOpenGraphicImage: () => void; onOpenAudit: () => void; onOpenOrchestration: () => void; onOpenInvoice: () => void; onOpenVehicleRoute: () => void; onOpenDeliveryRoute: () => void; onOpenAdmin: () => void; onOpenModels: () => void; onOpenPpt: () => void; onOpenTenderSearch: () => void; booting: boolean }) {
  return (
    <div style={{ minHeight: '100vh', background: 'var(--page-bg)', display: 'flex', flexDirection: 'column' }}>
      <TopNav onAdmin={onOpenAdmin} />
      <section aria-label="产品介绍" style={{ display: 'flex', justifyContent: 'center', paddingTop: '160px', paddingBottom: '120px' }}>
        <HeroTitle />
      </section>
      <section aria-label="功能入口" style={{ maxWidth: 'var(--card-max-width)', width: '100%', margin: '0 auto', padding: '0 var(--card-section-pad-x) 96px' }}>
        <div className="home-feature-grid home-primary-grid">
          <HomeCard title="招标项目检索" desc="汇总公开采购公告，按重点地区筛选并查看来源" icon={<IconTarget />} onClick={onOpenTenderSearch} />
          <HomeCard title="招标文件和大纲生成" desc="解析招标文件，提取评分点与废标风险，生成可编辑投标大纲" icon={<IconTarget />} onClick={onOpen} booting={booting} />
          <HomeCard title="图文标书正文" desc="按章节生成、编辑和导出图文标书正文" icon={<IconDocument />} onClick={onOpenGraphic} booting={booting} />
          <HomeCard title="标书智能编排中心" desc="将已生成正文排成标题、文字与图片表格版式" icon={<IconLayout />} onClick={onOpenOrchestration} />
          <HomeCard title="发票智能核验" desc="OCR 识别发票，打开税务总局平台完成查验" icon={<InvoiceHomeIcon />} onClick={onOpenInvoice} />
        </div>
        <div className="home-feature-grid home-tools-grid">
          <HomeCard title="车辆网点路线图" desc="规划配送中心与多个网点之间的驾车路线" icon={<IconVehicleRoute />} onClick={onOpenVehicleRoute} />
          <HomeCard title="示例配送配送路线图" desc="结合自有送菜网点和本次招标学校/单位生成路线图" icon={<IconVehicleRoute />} onClick={onOpenDeliveryRoute} />
          <HomeCard title="企业知识库入口" desc="资质、方案、图片和案例统一管理" icon={<IconLibrary />} onClick={onOpenKnowledge} />
          <HomeCard title="图表与图片生成" desc="生成 Mermaid 业务图表，渲染后保存到章节或知识库" icon={<IconImage />} onClick={onOpenGraphicImage} booting={booting} />
          <HomeCard title="标书智能审核" desc="检查资质缺口、评分覆盖、废标条款和格式风险" icon={<IconAudit />} onClick={onOpenAudit} />
          <HomeCard title="制作项目汇报PPT" desc="从已保存的标书章节生成可编辑PPTX，支持图文和表格" icon={<IconLayout />} onClick={onOpenPpt} />
          <HomeCard title="场景模型设置" desc="分别选择解读、目录、正文、知识库等任务使用的千问模型" icon={<IconLayout />} onClick={onOpenModels} />
        </div>
      </section>
    </div>
  )
}

function P1ProjectSelect({ onHome, onCreate, onSelect, onDeleted }: { onHome: () => void; onCreate: () => void; onSelect: (projectId: string) => void; onDeleted: (projectId: string, bidId?: number) => void }) {
  const [projects, setProjects] = useState<ApiProject[]>([])
  const [parseByProject, setParseByProject] = useState<Record<string, BidParseStatus>>({})
  const [sourceFailures, setSourceFailures] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [deleteWarnings, setDeleteWarnings] = useState<string[]>([])
  const [deleteTarget, setDeleteTarget] = useState<ApiProject | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const deletingRef = useRef(false)
  const mountedRef = useRef(true)
  const deletedIdsRef = useRef(new Set<string>())
  const historyHeadingRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    let cancelled = false
    mountedRef.current = true
    setLoading(true)
    wordagentApi.listProjects().then(res => {
      if (cancelled) return
      const nextProjects = res.projects || []
      setProjects(nextProjects)
      setLoading(false)
      nextProjects.forEach(project => {
        if (!project.bidId) return
        void wordagentApi.getBidParseStatus(project.bidId).then(parse => {
          if (parse.code !== 0) throw new Error('来源信息暂不可用')
          if (!cancelled && !deletedIdsRef.current.has(project.id)) setParseByProject(current => ({ ...current, [project.id]: parse.data }))
        }).catch(() => {
          if (!cancelled && !deletedIdsRef.current.has(project.id)) setSourceFailures(current => [...current, project.id])
        })
      })
    }).catch(err => {
      if (!cancelled) { setError(apiErrorMessage(err, '历史项目加载失败')); setLoading(false) }
    })
    return () => { cancelled = true; mountedRef.current = false }
  }, [])
  useEffect(() => {
    if (notice && !deleteTarget) historyHeadingRef.current?.focus()
  }, [notice, deleteTarget])

  const projectName = (item: ApiProject) => parseByProject[item.id]?.tender_file_name || item.settings.bidTitle || '未命名标书'
  const projectSource = (item: ApiProject) => <ProjectSourceSummary project={item} parse={parseByProject[item.id]} loadState={!item.bidId || sourceFailures.includes(item.id) ? 'unavailable' : parseByProject[item.id] ? 'ready' : 'loading'} />
  function cancelDelete() {
    if (deletingRef.current) return
    setDeleteTarget(null)
    setDeleteError('')
  }
  async function confirmDelete() {
    if (!deleteTarget || deletingRef.current) return
    const target = deleteTarget
    const name = projectName(target)
    deletingRef.current = true
    setDeleting(true)
    setDeleteError('')
    try {
      const result = await wordagentApi.deleteProject(target.id)
      if (!result.success || !result.deleted) throw new Error('未收到删除成功确认，请重新查看项目状态。')
      if (!mountedRef.current) return
      deletedIdsRef.current.add(target.id)
      setProjects(current => current.filter(item => item.id !== target.id))
      setParseByProject(current => {
        const next = { ...current }
        delete next[target.id]
        return next
      })
      onDeleted(target.id, result.bidId ?? parseByProject[target.id]?.bid_id)
      setDeleteTarget(null)
      setNotice(`已删除项目“${name}”。${result.cleanup?.preservedShared ? '仍被其他项目或知识库使用的资料已保留。' : ''}`)
      setDeleteWarnings(result.warnings?.length ? result.warnings : result.cleanup?.status === 'pending' ? ['项目已删除，部分上传副本尚待清理。'] : [])
    } catch (err) {
      if (mountedRef.current) setDeleteError('删除未确认成功，列表暂未更新。\n' + apiErrorMessage(err, '请检查连接并稍后重试。'))
    } finally {
      deletingRef.current = false
      if (mountedRef.current) setDeleting(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--page-bg)' }}>
      <div inert={Boolean(deleteTarget)}>
      <TopNav onLogoClick={onHome} onBack={onHome} />
      <main className="module-page p1-project-select-page">
        <section className="module-header-card">
          <div><p>BID OUTLINE</p><h1>招标文件和大纲生成</h1><span>请选择新建解析项目或加载历史项目，再进入招标文件解读与大纲编辑。</span></div>
          <button className="light-btn" type="button" onClick={onHome}>返回首页</button>
        </section>
        <section className="p1-project-select-grid">
          <button className="form-card p1-project-choice" type="button" onClick={onCreate}>
            <strong>新建解析项目</strong>
            <span>上传新的招标文件，重新解析项目概述、评分点、资格门槛与废标风险。</span>
          </button>
          <div className="form-card p1-history-panel">
            <div className="p1-history-head"><strong ref={historyHeadingRef} tabIndex={-1}>选择历史项目</strong><span>{loading ? '加载中...' : `${projects.length} 个项目`}</span></div>
            <div className="p1-history-list">
              {error && <div className="graphic-error">{error}</div>}
              {notice && <div className="knowledge-notice" role="status">{notice}</div>}
              {deleteWarnings.length > 0 && <div role="status" style={{ color: '#9A6A00', fontSize: 13 }}>{deleteWarnings.map((warning, index) => <p key={index} style={{ margin: '4px 0' }}>{warning}</p>)}</div>}
              {!loading && !projects.length && <p className="graphic-empty">暂无历史项目，可先新建解析项目。</p>}
              {projects.map(project => (
                <div key={project.id} className="p1-history-row" style={{ cursor: 'default' }}>
                  <button type="button" onClick={() => onSelect(project.id)} disabled={deleting} style={{ flex: 1, minWidth: 0, display: 'grid', gap: 6, border: 0, background: 'transparent', textAlign: 'left', padding: 0, cursor: 'pointer' }}>
                    <strong style={{ overflowWrap: 'anywhere' }}>{projectName(project)}</strong>
                    {projectSource(project)}
                    <span>{formatProjectDate(parseByProject[project.id]?.update_time || project.updatedAt)} · {project.settings.bidType === 'blind' ? '暗标' : '明标'}</span>
                  </button>
                  <button className="plain-btn" type="button" aria-label={`删除项目：${projectName(project)}`} disabled={deleting} style={{ color: '#b42318', flexShrink: 0 }} onClick={event => { event.stopPropagation(); setDeleteError(''); setNotice(''); setDeleteWarnings([]); setDeleteTarget(project) }}>删除</button>
                </div>
              ))}
            </div>
          </div>
        </section>
      </main>
      </div>
      {deleteTarget && <ProjectDeleteDialog projectName={projectName(deleteTarget)} source={projectSource(deleteTarget)} deleting={deleting} error={deleteError} onCancel={cancelDelete} onConfirm={() => void confirmDelete()} />}
    </div>
  )
}

function IconTarget() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <circle cx="36" cy="36" r="24" stroke="currentColor" strokeWidth="3.5" />
      <circle cx="36" cy="36" r="14" stroke="currentColor" strokeWidth="3.5" />
      <circle cx="36" cy="36" r="4.5" fill="currentColor" />
      <line x1="54" y1="18" x2="40" y2="32" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <polyline points="46,16 54,16 54,24" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function IconVehicleRoute() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <path d="M12 20h31v28H12zM43 29h10l8 9v10H43z" stroke="currentColor" strokeWidth="3.5" strokeLinejoin="round" />
      <circle cx="23" cy="51" r="5" fill="var(--page-bg)" stroke="currentColor" strokeWidth="3.5" />
      <circle cx="52" cy="51" r="5" fill="var(--page-bg)" stroke="currentColor" strokeWidth="3.5" />
      <path d="M21 12c-5 0-8 3.8-8 8.1 0 5.2 8 13 8 13s8-7.8 8-13c0-4.3-3-8.1-8-8.1Z" stroke="currentColor" strokeWidth="3" />
      <circle cx="21" cy="20" r="2" fill="currentColor" />
    </svg>
  )
}

function IconLibrary() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <rect x="14" y="16" width="12" height="44" rx="2" stroke="currentColor" strokeWidth="3.5" />
      <rect x="30" y="12" width="12" height="48" rx="2" stroke="currentColor" strokeWidth="3.5" />
      <rect x="46" y="20" width="12" height="40" rx="2" stroke="currentColor" strokeWidth="3.5" />
      <line x1="16" y1="48" x2="24" y2="48" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      <line x1="32" y1="46" x2="40" y2="46" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      <line x1="48" y1="50" x2="56" y2="50" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

function IconDocument() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <rect x="18" y="12" width="36" height="48" rx="4" stroke="currentColor" strokeWidth="3.5" />
      <line x1="27" y1="25" x2="45" y2="25" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <line x1="27" y1="36" x2="45" y2="36" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <line x1="27" y1="47" x2="39" y2="47" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
    </svg>
  )
}

function IconLayout() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <rect x="12" y="14" width="48" height="44" rx="4" stroke="currentColor" strokeWidth="3.5" />
      <line x1="12" y1="28" x2="60" y2="28" stroke="currentColor" strokeWidth="3.5" />
      <line x1="30" y1="28" x2="30" y2="58" stroke="currentColor" strokeWidth="3.5" />
      <line x1="42" y1="38" x2="53" y2="38" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <line x1="42" y1="47" x2="53" y2="47" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
    </svg>
  )
}

function IconImage() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <rect x="12" y="16" width="48" height="40" rx="4" stroke="currentColor" strokeWidth="3.5" />
      <circle cx="27" cy="29" r="4" stroke="currentColor" strokeWidth="3.5" />
      <path d="M16 50 30 37l9 8 7-6 10 11" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function IconAudit() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <rect x="18" y="14" width="36" height="44" rx="4" stroke="currentColor" strokeWidth="3.5" />
      <rect x="28" y="10" width="16" height="10" rx="3" stroke="currentColor" strokeWidth="3.5" />
      <polyline points="27,38 34,45 47,30" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function InvoiceHomeIcon() {
  return (
    <svg className="feature-icon" viewBox="0 0 72 72" fill="none" aria-hidden="true">
      <path d="M17 10h30l8 8v44H17V10Z" stroke="currentColor" strokeWidth="3.5" />
      <path d="M47 10v10h10M25 31h22M25 40h22M25 49h13" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <circle cx="50" cy="51" r="10" fill="var(--card-bg)" stroke="currentColor" strokeWidth="3.5" />
      <path d="m45 51 3 3 6-7" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function TenderStep({ bidId, run, onBack, onHome, onProjectChange, onReady }: { bidId: number | null; run: RunApi; onBack: () => void; onHome: () => void; onProjectChange: (project: ApiProject, bidId: number) => void; onReady: () => void }) {
  const [parseMethod, setParseMethod] = useState<ParseMethod>(null)
  const [bidCategory, setBidCategory] = useState('服务类')
  const [parseInfo, setParseInfo] = useState<BidParseInfo | null>(null)
  const [pasteModalOpen, setPasteModalOpen] = useState(false)
  const [pasteSaving, setPasteSaving] = useState(false)
  const [parseResultSaving, setParseResultSaving] = useState(false)
  const [parseDetail, setParseDetail] = useState<BidParseDetail | null>(null)
  const [parseDetailLoading, setParseDetailLoading] = useState(false)
  const [autoUploading, setAutoUploading] = useState(false)
  const [autoTransfer, setAutoTransfer] = useState<UploadProgress | null>(null)
  const [autoSelectedFile, setAutoSelectedFile] = useState<{ name: string; size: number } | null>(null)
  const [autoStatusWarning, setAutoStatusWarning] = useState('')
  const uploadControllerRef = useRef<AbortController | null>(null)
  const uploadTargetBidIdRef = useRef<number | null>(null)
  const activeBidIdRef = useRef(bidId)
  activeBidIdRef.current = bidId
  const mountedRef = useRef(true)
  const parseRequestRef = useRef(0)
  const [duplicatePrompt, setDuplicatePrompt] = useState<{ match: DuplicateBidParseMatch; choose: (choice: DuplicateFileChoice) => void } | null>(null)
  const duplicateDialogRef = useRef<HTMLElement | null>(null)
  const [autoUploadError, setAutoUploadError] = useState('')
  const [parseTakingLong, setParseTakingLong] = useState(false)
  const parseStartedAtRef = useRef<number | null>(null)
  const autoFileInputRef = useRef<HTMLInputElement | null>(null)
  const resultOpenedRef = useRef(false)
  const font: CSSProperties = { fontFamily: "'Noto Sans SC', sans-serif" }

  const categories = ['服务类', '货物类', '工程类(含施组方案)', '更多行业']
  const pasteSaved = Boolean(parseInfo?.parsed_project_overview?.trim() && parseInfo?.parsed_score_requirements?.trim())
  const autoActive = parseMethod === 'auto'
  const autoParsing = autoActive && parseInfo?.parse_status === 'processing'
  const autoCompleted = autoActive && !autoUploading && parseInfo?.parse_status === 'completed'
  const autoFailed = autoActive && !autoUploading && parseInfo?.parse_status === 'failed'
  const autoBusy = autoUploading || autoParsing
  const autoErrorText = autoUploadError || (autoFailed ? (parseInfo?.parse_error_msg || parseInfo?.parse_error || '解析失败，请重新上传') : '')
  const autoWarningText = autoCompleted ? (parseInfo?.parse_warning_msg || '') : ''
  const autoParseStep = autoUploading ? 1 : parseInfo?.parse_current_step ?? (autoCompleted ? 4 : autoParsing ? 2 : 0)
  const parseProgressSteps = ['文件上传', '文档解析', '信息提取', '解析完成']
  const resultReady = !autoBusy && (pasteSaved || autoCompleted)

  const applyParseInfo = (info: BidParseInfo, fromUpload = false) => {
    if (!mountedRef.current || info.bid_id !== activeBidIdRef.current || (uploadControllerRef.current && !fromUpload)) return
    setParseInfo(info)
    setParseMethod(info.parse_mode === 'paste_text' ? 'paste' : info.parse_mode === 'auto_upload' || (info.parse_status === 'failed' && Boolean(info.tender_file_name)) ? 'auto' : null)
    setBidCategory(BID_CATEGORY_FROM_API[info.bid_category] ?? '服务类')
    if (info.parse_status !== 'failed') setAutoUploadError('')
    if (info.parse_status !== 'processing') {
      setParseTakingLong(false)
      parseStartedAtRef.current = null
    }
  }

  const clearProjectResult = () => {
    setParseInfo(null)
    setParseDetail(null)
    setParseDetailLoading(false)
    setPasteModalOpen(false)
    setAutoUploadError('')
    setAutoStatusWarning('')
    setParseTakingLong(false)
    parseStartedAtRef.current = null
    resultOpenedRef.current = false
  }

  const loadParseDetail = async () => {
    if (!bidId || !mountedRef.current || activeBidIdRef.current !== bidId || uploadControllerRef.current) return
    const requestBidId = bidId
    const requestVersion = parseRequestRef.current
    const current = () => mountedRef.current && activeBidIdRef.current === requestBidId && parseRequestRef.current === requestVersion
    setParseDetailLoading(true)
    try {
      const res = await wordagentApi.getBidParseDetail(requestBidId)
      if (current() && res.code === 0) setParseDetail(res.data)
    } catch (error) {
      if (current()) setAutoStatusWarning(apiErrorMessage(error, '解析详情读取失败，请稍后重试'))
    } finally {
      if (current()) setParseDetailLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    const ownedUpload = uploadControllerRef.current && uploadTargetBidIdRef.current === bidId
    if (ownedUpload) return () => { cancelled = true }
    const requestVersion = ++parseRequestRef.current
    const previousUpload = uploadControllerRef.current
    uploadControllerRef.current = null
    uploadTargetBidIdRef.current = null
    previousUpload?.abort()
    clearProjectResult()
    setParseMethod(null)
    setAutoUploading(false)
    setAutoTransfer(null)
    setAutoSelectedFile(null)
    if (!bidId) return () => { cancelled = true }
    const current = () => !cancelled && mountedRef.current && activeBidIdRef.current === bidId && parseRequestRef.current === requestVersion && !uploadControllerRef.current
    void wordagentApi.getBidParseInfo(bidId).then(res => {
      if (current() && res.code === 0) applyParseInfo(res.data)
    }).catch(error => {
      if (current()) setAutoStatusWarning(apiErrorMessage(error, '项目解析状态读取失败，请稍后重试'))
    })
    return () => { cancelled = true }
  }, [bidId])

  useEffect(() => {
    if (resultReady && !resultOpenedRef.current) {
      resultOpenedRef.current = true
      void loadParseDetail()
    }
    if (!resultReady) resultOpenedRef.current = false
  }, [resultReady])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      ++parseRequestRef.current
      const controller = uploadControllerRef.current
      uploadControllerRef.current = null
      uploadTargetBidIdRef.current = null
      controller?.abort()
    }
  }, [])

  useEffect(() => {
    if (!bidId || autoUploading || parseInfo?.bid_id !== bidId || parseInfo.parse_mode !== 'auto_upload' || parseInfo.parse_status !== 'processing') return
    if (!parseStartedAtRef.current) parseStartedAtRef.current = Date.now()
    let cancelled = false
    const requestVersion = parseRequestRef.current
    const current = () => !cancelled && mountedRef.current && activeBidIdRef.current === bidId && parseRequestRef.current === requestVersion && !uploadControllerRef.current
    let timer: number | undefined
    const poll = async () => {
      if (!current()) return
      if (parseStartedAtRef.current && Date.now() - parseStartedAtRef.current > 5 * 60 * 1000) setParseTakingLong(true)
      try {
        const status = await wordagentApi.getBidParseStatus(bidId)
        if (!current()) return
        if (status.code !== 0) throw new Error(status.message || '解析状态获取失败')
        setAutoStatusWarning('')
        if (status.data.parse_status === 'completed' || status.data.parse_status === 'failed') {
          const full = await wordagentApi.getBidParseInfo(bidId)
          if (full.code !== 0) throw new Error(full.message || '解析结果读取失败，正在重试')
          if (current()) applyParseInfo(full.data)
          return
        }
        setParseInfo(prev => prev ? { ...prev, ...status.data, parse_mode: status.data.parse_mode ?? prev.parse_mode } : prev)
      } catch (error) {
        if (current()) setAutoStatusWarning(apiErrorMessage(error, '暂时无法获取进度，正在重试；请勿重复上传'))
      }
      if (current()) timer = window.setTimeout(() => void poll(), 2000)
    }
    void poll()
    return () => { cancelled = true; if (timer) window.clearTimeout(timer) }
  }, [bidId, autoUploading, parseInfo?.parse_mode, parseInfo?.parse_status])

  useEffect(() => {
    if (!duplicatePrompt) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = duplicateDialogRef.current
    const buttons = () => Array.from(dialog?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])
    buttons()[0]?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        duplicatePrompt.choose('cancel')
      } else if (event.key === 'Tab') {
        const available = buttons()
        const first = available[0]
        const last = available[available.length - 1]
        if (event.shiftKey && (document.activeElement === first || !dialog?.contains(document.activeElement))) {
          event.preventDefault()
          last?.focus()
        } else if (!event.shiftKey && (document.activeElement === last || !dialog?.contains(document.activeElement))) {
          event.preventDefault()
          first?.focus()
        }
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('keydown', handleKey)
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [duplicatePrompt])

  const renderAutoParseProgress = () => {
    if (!autoActive || (!autoBusy && !autoCompleted && !autoFailed)) return null
    return (
      <div style={{ width: '100%', marginTop: 14 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, marginBottom: 12 }}>
          {parseProgressSteps.map((label, index) => {
            const step = index + 1
            const done = autoCompleted || autoParseStep > step
            const current = !autoCompleted && !autoFailed && autoParseStep === step
            return <span key={label} style={{ fontSize: 12, color: current || done ? 'var(--form-label-color)' : 'var(--form-desc-color)', fontWeight: current ? 700 : 400 }}>{done ? '✓ ' : step + '. '}{label}</span>
          })}
        </div>
        {autoUploading && <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 8 }}>
            <strong>{autoTransfer?.stage === 'processing' ? '文件传输完成，服务器正在接收处理' : autoTransfer ? '正在上传文件' : '正在检查文件信息'}</strong>
            <span>{autoTransfer?.percent == null ? '准备中' : autoTransfer.percent + '%'}</span>
          </div>
          <progress aria-label="文件上传进度" max={100} value={autoTransfer?.percent ?? undefined} style={{ width: '100%', height: 14, accentColor: 'var(--form-cta-bg)' }} />
          <p style={{ ...font, fontSize: 12, color: 'var(--form-desc-color)', margin: '8px 0', overflowWrap: 'anywhere' }}>
            {autoSelectedFile?.name}
            {autoTransfer ? ' · ' + formatTransferBytes(Math.min(autoSelectedFile?.size ?? autoTransfer.loaded, autoTransfer.loaded)) + ' / ' + formatTransferBytes(autoSelectedFile?.size ?? autoTransfer.total) : autoSelectedFile ? ' · ' + formatTransferBytes(autoSelectedFile.size) : ''}
            {autoTransfer?.stage === 'uploading' && autoTransfer.bytesPerSecond > 0 ? ' · ' + formatTransferBytes(autoTransfer.bytesPerSecond) + '/秒' : ''}
          </p>
          <p style={{ ...font, fontSize: 12, color: 'var(--form-desc-color)', margin: 0 }}>100% 表示文件传输结束，后续文档解析和信息提取将继续显示状态。</p>
          <button type="button" disabled={autoTransfer?.stage === 'processing'} onClick={() => uploadControllerRef.current?.abort()} style={{ marginTop: 10, padding: '6px 12px' }}>{autoTransfer?.stage === 'processing' ? '等待服务器确认' : '取消上传'}</button>
          {autoTransfer?.stage === 'processing' && <p style={{ ...font, fontSize: 12, color: 'var(--form-desc-color)' }}>文件已传输，服务器可能已开始处理，此时关闭页面不会撤回文件；再次进入本项目可查看状态。</p>}
        </div>}
        {!autoUploading && parseInfo?.bid_id === bidId && (autoParseStep >= 3 || Boolean(parseInfo.extraction_progress) || autoCompleted) && <LiveExtractionProgress key={bidId} progress={parseInfo.extraction_progress} parseStatus={parseInfo.parse_status} />}
        {!autoUploading && autoParsing && <p role="status" style={{ ...font, fontSize: 13, color: 'var(--form-desc-color)', margin: 0 }}>
          {parseProgressSteps[Math.max(1, Math.min(2, autoParseStep - 1))]}中，文件已上传，无需重复提交。
          {parseTakingLong ? ' 大文件处理仍在进行，页面会继续更新。' : ''}
        </p>}
      </div>
    )
  }

  const openAutoFilePicker = () => {
    if (autoBusy) return
    autoFileInputRef.current?.click()
  }

  const requestDuplicateChoice = (match: DuplicateBidParseMatch, signal: AbortSignal): Promise<DuplicateFileChoice> => {
    signal.throwIfAborted()
    return new Promise((resolve, reject) => {
      let settled = false
      const finish = (choice: DuplicateFileChoice, error?: Error) => {
        if (settled) return
        settled = true
        signal.removeEventListener('abort', abort)
        if (mountedRef.current) setDuplicatePrompt(null)
        if (error) reject(error)
        else resolve(choice)
      }
      const abort = () => finish('cancel', new DOMException('文件操作已取消', 'AbortError'))
      signal.addEventListener('abort', abort, { once: true })
      setDuplicatePrompt({ match, choose: choice => finish(choice) })
    })
  }

  const handleAutoFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || !bidId || uploadControllerRef.current) return
    setParseMethod('auto')
    setAutoUploadError('')
    if (!TENDER_UPLOAD_EXTENSIONS.some(ext => file.name.toLowerCase().endsWith(ext))) {
      setAutoUploadError('文件格式不支持，仅支持 doc、docx、pdf')
      return
    }
    if (file.size > TENDER_UPLOAD_MAX_SIZE) {
      setAutoUploadError('单个文件不能超过 1 GiB；如需调整，请同时修改服务器上传上限')
      return
    }
    const controller = new AbortController()
    uploadControllerRef.current = controller
    uploadTargetBidIdRef.current = bidId
    ++parseRequestRef.current
    setParseDetail(null)
    setParseDetailLoading(false)
    resultOpenedRef.current = false
    setAutoSelectedFile({ name: file.name, size: file.size })
    setAutoTransfer(null)
    setPasteModalOpen(false)
    setAutoUploading(true)
    setParseTakingLong(false)
    setAutoStatusWarning('')
    let targetBidId = bidId
    let shouldRefresh = true
    let attemptedUpload = false
    let failureMessage = ''
    const current = () => mountedRef.current && uploadControllerRef.current === controller && activeBidIdRef.current === targetBidId
    const switchProject = (nextProject: ApiProject, nextBidId: number) => {
      controller.signal.throwIfAborted()
      if (!mountedRef.current || uploadControllerRef.current !== controller) throw new DOMException('文件操作已取消', 'AbortError')
      targetBidId = nextBidId
      uploadTargetBidIdRef.current = nextBidId
      activeBidIdRef.current = nextBidId
      ++parseRequestRef.current
      clearProjectResult()
      setParseMethod('auto')
      onProjectChange(nextProject, nextBidId)
    }
    try {
      const decision = await resolveDuplicateFileDecision(targetBidId, file, run, switchProject, controller.signal, requestDuplicateChoice)
      controller.signal.throwIfAborted()
      if (!current()) return
      if (decision === 'cancel') return
      if (decision === 'merged') {
        const merged = await wordagentApi.getBidParseInfo(targetBidId)
        controller.signal.throwIfAborted()
        if (!current()) return
        if (merged.code !== 0) throw new Error(merged.message || '合并结果读取失败')
        ++parseRequestRef.current
        applyParseInfo(merged.data, true)
        shouldRefresh = false
        return
      }
      if (typeof decision === 'number') targetBidId = decision
      setAutoTransfer({ loaded: 0, total: file.size, percent: 0, bytesPerSecond: 0, stage: 'uploading' })
      attemptedUpload = true
      const res = await wordagentApi.uploadAndParseBidFileWithProgress(targetBidId, file, undefined, {
        signal: controller.signal,
        onTransferProgress: progress => { if (current()) setAutoTransfer(progress) },
      })
      controller.signal.throwIfAborted()
      if (!current()) return
      if (res.code !== 0) throw new Error(res.message || '上传未成功，请重试')
      ++parseRequestRef.current
      parseStartedAtRef.current = Date.now()
      applyParseInfo(res.data.parse_info, true)
      shouldRefresh = false
    } catch (error) {
      if (current()) {
        failureMessage = controller.signal.aborted ? '文件操作已取消，正在确认服务器状态。' : apiErrorMessage(error, '上传未完成，正在确认服务器状态。')
        setAutoUploadError(failureMessage)
      }
    } finally {
      if (current() && shouldRefresh) {
        const requestVersion = ++parseRequestRef.current
        setAutoStatusWarning('正在确认目标项目的文件状态，请勿重复提交。')
        try {
          const refreshed = await wordagentApi.getBidParseInfo(targetBidId)
          if (current() && parseRequestRef.current === requestVersion) {
            if (refreshed.code !== 0) throw new Error(refreshed.message || '项目状态读取失败')
            applyParseInfo(refreshed.data, true)
            const accepted = attemptedUpload && refreshed.data.tender_file_name === file.name && refreshed.data.tender_file_size === file.size && ['processing', 'completed'].includes(refreshed.data.parse_status)
            setAutoStatusWarning(accepted ? '服务器已接收同名同大小文件，已恢复项目状态；请勿重复上传。' : '')
            setAutoUploadError(accepted ? '' : controller.signal.aborted ? '文件操作已取消，已同步服务器中的项目状态。' : failureMessage)
          }
        } catch (error) {
          if (current()) {
            setParseInfo(null)
            setParseDetail(null)
            setParseMethod('auto')
            setAutoStatusWarning('暂时无法确认服务器是否已收到文件，请重新进入本项目检查状态后再上传。')
            setAutoUploadError(failureMessage || apiErrorMessage(error, '项目状态读取失败'))
          }
        }
      }
      if (uploadControllerRef.current === controller) {
        uploadControllerRef.current = null
        uploadTargetBidIdRef.current = null
        if (mountedRef.current) setAutoUploading(false)
      }
    }
  }

  const openPasteModal = async () => {
    if (bidId && parseInfo?.parse_mode === 'auto_upload' && !pasteSaved) {
      const res = await run(() => wordagentApi.cleanupAutoParseContent(bidId))
      if (res?.code === 0) applyParseInfo(res.data)
    }
    setParseMethod('paste')
    setAutoUploadError('')
    setPasteModalOpen(true)
  }

  const savePasteContent = async (payload: { projectOverview: string; scoreRequirements: string }) => {
    if (!bidId) return
    setPasteSaving(true)
    try {
      const res = await run(() => wordagentApi.savePasteParseContent(bidId, {
        project_overview: payload.projectOverview,
        score_requirements: payload.scoreRequirements,
      }))
      if (res?.code === 0) {
        applyParseInfo(res.data)
        setPasteModalOpen(false)
        setParseDetail(null)
        void loadParseDetail()
      }
    } finally {
      setPasteSaving(false)
    }
  }

  const saveParseResultContent = async (payload: { projectOverview: string; scoreRequirements: string }) => {
    if (!bidId) return
    setParseResultSaving(true)
    try {
      const res = await run(() => wordagentApi.saveParseResultEdit(bidId, {
        project_overview: payload.projectOverview,
        score_requirements: payload.scoreRequirements,
      }))
      if (res?.code === 0) {
        applyParseInfo(res.data)
        void loadParseDetail()
      }
    } finally {
      setParseResultSaving(false)
    }
  }

  const reparseAutoFile = async () => {
    if (!bidId) return
    setAutoUploadError('')
    setParseTakingLong(false)
    parseStartedAtRef.current = Date.now()
    const res = await run(() => wordagentApi.reparseBidFile(bidId))
    if (res?.code === 0) {
      applyParseInfo(res.data.parse_info)
      setParseDetail(null)
    }
  }

  const submitTenderStep = async () => {
    if (!bidId) return
    const ready = parseMethod === 'paste' ? pasteSaved : autoCompleted
    if (!ready) {
      window.alert('请先完成招标文件解读，并确认项目概述和评分要求。')
      return
    }
    const parseModeForApi = parseMethod === 'paste' ? 'paste_text' : 'auto_upload'
    const saved = await run(() => wordagentApi.updateBidParseInfo(bidId, {
      parse_mode: parseModeForApi,
      bid_category: BID_CATEGORY_TO_API[bidCategory] ?? 'service',
    }))
    if (!saved || saved.code !== 0) return
    applyParseInfo(saved.data)
    const completed = await run(() => wordagentApi.completeBidStep2(bidId))
    if (!completed || completed.code !== 0) return
    applyParseInfo(completed.data.parse_info)
    onReady()
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--page-bg)', display: 'flex', flexDirection: 'column' }}>
      <TopNav onLogoClick={onHome} onBack={onBack} />
      <input ref={autoFileInputRef} type="file" accept=".doc,.docx,.pdf" style={{ display: 'none' }} onChange={handleAutoFileChange} />
      <main style={{ flex: 1, maxWidth: '860px', width: '100%', margin: '0 auto', padding: '40px 0 48px', display: 'flex', flexDirection: 'column', gap: '36px' }}>
        <div>
          <FieldLabel required sub="以下两种方式任选一种">招标文件解析</FieldLabel>
          <div style={{ display: 'flex', gap: '16px', marginTop: '12px' }}>
            {([
              { key: 'auto' as const, title: '招标文件自动解析', desc: '自动解析招标文件，总结项目概述、评分要求\n文件格式：doc、docx、pdf' },
              { key: 'paste' as const, title: '粘贴项目信息解析', desc: '手动粘贴项目概述、评分要求' },
            ]).map(opt => {
              const active = parseMethod === opt.key
              const filled = opt.key === 'paste' && pasteSaved
              const isAuto = opt.key === 'auto'
              const title = isAuto ? (autoUploading ? '上传中...' : autoParsing ? '解析中，请稍候...' : autoCompleted ? '已解析' : opt.title) : opt.title
              const desc = isAuto ? (autoUploading ? '正在上传招标文件，请稍候' : autoParsing ? (parseInfo?.tender_file_name || '文件已上传，正在提取全文文本') : autoCompleted ? (parseInfo?.tender_file_name || '招标文件解析成功') : autoFailed ? autoErrorText : opt.desc) : (filled ? '已填写项目概述与技术评分要求' : opt.desc)
              return (
                <button key={opt.key} type="button" onClick={opt.key === 'auto' ? openAutoFilePicker : openPasteModal} disabled={autoBusy} style={{ flex: 1, minHeight: '116px', display: 'flex', flexDirection: 'column', alignItems: 'flex-start', justifyContent: 'center', textAlign: 'left', padding: '18px 20px', borderRadius: 'var(--form-card-radius)', border: `1.5px solid ${active ? 'var(--form-card-border-active)' : 'var(--form-card-border)'}`, background: active ? 'var(--form-ref-bg)' : '#fff', cursor: isAuto && autoBusy ? 'wait' : 'pointer' }}>
                  <span style={{ ...font, fontSize: '15px', fontWeight: 600, color: 'var(--form-label-color)', marginBottom: '8px' }}>{title}</span>
                  <span style={{ ...font, fontSize: '12px', lineHeight: 1.7, whiteSpace: 'pre-line', color: autoFailed && isAuto ? '#C00000' : 'var(--form-desc-color)' }}>{desc}</span>

                </button>
              )
            })}
          </div>
          {autoWarningText && <p style={{ ...font, color: '#9A6A00', fontSize: '12px', margin: '10px 0 0' }}>{autoWarningText}</p>}
        </div>

        <div>
          <FieldLabel required sub="用于后续目录生成与评分点覆盖判断">标书类型</FieldLabel>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginTop: '12px' }}>
            {categories.map(category => (
              <button key={category} type="button" onClick={() => setBidCategory(category)} style={{ ...font, minHeight: '42px', borderRadius: 'var(--form-input-radius)', border: `1px solid ${bidCategory === category ? 'var(--form-card-border-active)' : 'var(--form-card-border)'}`, background: bidCategory === category ? 'var(--form-ref-bg)' : '#fff', color: 'var(--form-label-color)', cursor: 'pointer', fontSize: '13px', fontWeight: bidCategory === category ? 600 : 400 }}>{category}</button>
            ))}
          </div>
        </div>

        {(parseInfo || autoUploading || autoErrorText) && (
          <section style={{ border: '1px solid var(--form-card-border)', borderRadius: 'var(--form-card-radius)', background: '#fff', padding: '18px 20px', boxShadow: 'var(--form-card-shadow)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px' }}>
              <div>
                <p style={{ ...font, margin: '0 0 6px', color: 'var(--form-label-color)', fontSize: '15px', fontWeight: 700 }}>解读状态</p>
                <p style={{ ...font, margin: 0, color: 'var(--form-desc-color)', fontSize: '12px', lineHeight: 1.7 }}>{autoUploading ? '文件正在传输，请保持页面打开。' : parseInfo ? statusLabel(parseInfo) : '请选择文件'}</p>
              </div>
            </div>
            {renderAutoParseProgress()}
            {autoErrorText && <p role="alert" style={{ color: '#C00000', marginBottom: 0 }}>{autoErrorText}</p>}
            {autoStatusWarning && <p role="status" style={{ color: '#9A6A00', marginBottom: 0 }}>{autoStatusWarning}</p>}
          </section>
        )}

        {parseInfo && resultReady && (
          <InlineParseResult
            parseInfo={parseInfo}
            detail={parseDetail}
            detailLoading={parseDetailLoading}
            saving={parseResultSaving}
            onSave={saveParseResultContent}
            onReparse={parseInfo.parse_mode === 'auto_upload' ? reparseAutoFile : undefined}
          />
        )}

        <div style={{ display: 'flex', justifyContent: 'center', gap: '16px', paddingTop: '4px' }}>
          <button type="button" onClick={onBack} style={{ ...font, fontSize: '14px', fontWeight: 500, padding: '10px 32px', borderRadius: 'var(--btn-radius)', background: 'var(--form-input-bg)', border: '1px solid var(--form-input-border)', color: 'var(--form-label-color)', cursor: 'pointer' }}>返回上一步</button>
          <button type="button" onClick={onHome} style={{ ...font, fontSize: '14px', fontWeight: 500, padding: '10px 32px', borderRadius: 'var(--btn-radius)', background: 'var(--form-input-bg)', border: '1px solid var(--form-input-border)', color: 'var(--form-label-color)', cursor: 'pointer' }}>返回首页</button>
          <button type="button" onClick={() => void submitTenderStep()} disabled={autoBusy} style={{ ...font, fontSize: '14px', fontWeight: 600, padding: '10px 32px', borderRadius: 'var(--btn-radius)', background: 'var(--form-cta-bg)', color: 'var(--form-cta-text)', border: 'none', cursor: autoBusy ? 'not-allowed' : 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,0.15)', opacity: autoBusy ? 0.65 : 1 }}>确认并生成大纲</button>
        </div>
      </main>

      {duplicatePrompt && <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0, 0, 0, 0.35)', display: 'grid', placeItems: 'center', padding: 24 }}>
        <section ref={duplicateDialogRef} role="dialog" aria-modal="true" aria-labelledby="duplicate-file-title" style={{ ...font, width: 'min(520px, 100%)', borderRadius: 12, background: '#fff', padding: 24, boxShadow: '0 16px 48px rgba(0,0,0,0.2)' }}>
          <h2 id="duplicate-file-title" style={{ marginTop: 0, fontSize: 20 }}>发现已有解析记录</h2>
          <p style={{ lineHeight: 1.7, overflowWrap: 'anywhere' }}>{duplicatePrompt.match.project_title || duplicatePrompt.match.tender_file_name}</p>
          <p style={{ color: 'var(--form-desc-color)', lineHeight: 1.7 }}>请选择如何处理这份文件。覆盖会替换已有项目的招标文件；保留两者会新建项目。</p>
          <div style={{ display: 'grid', gap: 10 }}>
            <button type="button" className="dark-btn" onClick={() => duplicatePrompt.choose('merge')}>合并到当前项目</button>
            <button type="button" className="light-btn" onClick={() => duplicatePrompt.choose('new')}>保留两者并新建项目</button>
            <button type="button" className="light-btn" onClick={() => duplicatePrompt.choose('overwrite')}>覆盖已有项目的文件</button>
            <button type="button" className="light-btn" onClick={() => duplicatePrompt.choose('cancel')}>取消</button>
          </div>
        </section>
      </div>}
      <PasteParseModal open={pasteModalOpen} initialProjectOverview={parseInfo?.parsed_project_overview || ''} initialScoreRequirements={parseInfo?.parsed_score_requirements || ''} saving={pasteSaving} onCancel={() => setPasteModalOpen(false)} onConfirm={savePasteContent} />
    </div>
  )
}

function statusLabel(info: BidParseInfo) {
  if (info.parse_status === 'processing') return `正在解析：${info.tender_file_name || '招标文件'}`
  if (info.parse_status === 'failed') return info.parse_error_msg || info.parse_error || '解析失败，请重新上传或粘贴项目信息。'
  if (info.parse_mode === 'paste_text') return '已保存粘贴项目信息，可继续生成大纲。'
  if (info.parse_status === 'completed') return `已完成自动解析：${info.tender_file_name || '招标文件'}`
  return '等待选择解析方式。'
}

async function resolveDuplicateFileDecision(
  bidId: number,
  file: File,
  run: RunApi,
  onProjectChange: (project: ApiProject, bidId: number) => void,
  signal: AbortSignal,
  choose: (match: DuplicateBidParseMatch, signal: AbortSignal) => Promise<DuplicateFileChoice>,
): Promise<'continue' | 'cancel' | 'merged' | number> {
  signal.throwIfAborted()
  const duplicate = await run(() => wordagentApi.checkDuplicateBidParseFile(bidId, { tender_file_name: file.name, tender_file_size: file.size }))
  signal.throwIfAborted()
  const match = duplicate?.code === 0 ? duplicate.data.matches[0] : null
  if (!match) return 'continue'
  const choice = await choose(match, signal)
  signal.throwIfAborted()
  if (choice === 'cancel') return 'cancel'
  if (choice === 'merge') {
    signal.throwIfAborted()
    const merged = await run(() => wordagentApi.mergeDuplicateBidParse(bidId, match.bid_id))
    signal.throwIfAborted()
    return merged?.code === 0 ? 'merged' : 'cancel'
  }
  if (choice === 'new') {
    signal.throwIfAborted()
    const response = await run(() => wordagentApi.createProject({
      bidTitle: file.name.replace(/\.[^.]+$/, '') || '投标文件',
      pageCount: 200,
      wordsPerPage: 800,
      estimatedWordCount: 160000,
      bidType: 'open',
      selectedModel: 'qwen3.7-flash',
      selectedProvider: 'qwen',
    }))
    signal.throwIfAborted()
    if (!response?.bidId) return 'cancel'
    onProjectChange(response.project, response.bidId)
    return response.bidId
  }
  if (choice === 'overwrite') {
    signal.throwIfAborted()
    const existing = await run(() => wordagentApi.getProject(match.project_id))
    signal.throwIfAborted()
    if (!existing?.bidId) return 'cancel'
    onProjectChange(existing.project, existing.bidId)
    return existing.bidId
  }
  return 'cancel'
}

function FieldLabel({ children, required, sub }: { children: ReactNode; required?: boolean; sub?: string }) {
  return (
    <div style={{ marginBottom: sub ? '6px' : '10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginBottom: sub ? '4px' : 0 }}>
        <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: '14px', fontWeight: 600, color: 'var(--form-label-color)' }}>{children}</span>
        {required && <span style={{ color: 'var(--form-required-color)', fontSize: '14px', fontWeight: 600, lineHeight: 1 }}>*</span>}
      </div>
      {sub && <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: '12px', fontWeight: 400, color: 'var(--form-desc-color)' }}>{sub}</span>}
    </div>
  )
}

type ParseResultTab = 'overview' | 'score' | 'qualification' | 'risk' | 'insight'

function InlineParseResult({ parseInfo, detail, detailLoading, saving, onSave, onReparse }: { parseInfo: BidParseInfo; detail?: BidParseDetail | null; detailLoading?: boolean; saving: boolean; onSave: (payload: { projectOverview: string; scoreRequirements: string }) => Promise<void>; onReparse?: () => Promise<void> | void }) {
  const [activeTab, setActiveTab] = useState<ParseResultTab>('overview')
  const [projectOverview, setProjectOverview] = useState('')
  const [techScoring, setTechScoring] = useState('')
  const [qualification, setQualification] = useState('')
  const [invalidRisk, setInvalidRisk] = useState('')
  const [coreInsight, setCoreInsight] = useState('')

  useEffect(() => {
    setProjectOverview(detail?.parsed_project_overview || parseInfo.parsed_project_overview || '')
    setTechScoring(formatParseText(detail?.tech_scoring_items) || parseInfo.parsed_tech_scoring || parseInfo.parsed_score_requirements || detail?.parsed_score_requirements || '')
    setQualification(formatParseText(detail?.qualification_requirements) || parseInfo.qualification_requirements || '')
    setInvalidRisk(formatParseText(detail?.invalid_bid_clauses) || parseInfo.parsed_invalid_clauses || parseInfo.invalid_bid_clauses || '')
    setCoreInsight(formatKeyPoints(detail?.bid_key_points) || parseInfo.bid_key_points || '')
  }, [detail, parseInfo])

  const tabs: Array<{ key: ParseResultTab; label: string }> = [
    { key: 'overview', label: '项目概述' },
    { key: 'score', label: '技术评分' },
    { key: 'qualification', label: '资格门槛' },
    { key: 'risk', label: '废标风险' },
    { key: 'insight', label: '核心解读' },
  ]
  const editorByTab: Record<ParseResultTab, { label: string; value: string; setter: (value: string) => void; placeholder: string }> = {
    overview: { label: '项目概述', value: projectOverview, setter: setProjectOverview, placeholder: '请输入项目概述' },
    score: { label: '技术评分', value: techScoring, setter: setTechScoring, placeholder: '请输入技术评分要求' },
    qualification: { label: '资格门槛', value: qualification, setter: setQualification, placeholder: '请输入资格条件、商务门槛或响应要求' },
    risk: { label: '废标风险', value: invalidRisk, setter: setInvalidRisk, placeholder: '请输入废标条款、否决项或关键风险' },
    insight: { label: '核心解读', value: coreInsight, setter: setCoreInsight, placeholder: '请输入投标重点、优先级和目录建议' },
  }
  const current = editorByTab[activeTab]

  const save = async () => {
    const scoreRequirements = [
      techScoring.trim(),
      qualification.trim() ? `【资格门槛】\n${qualification.trim()}` : '',
      invalidRisk.trim() ? `【废标风险】\n${invalidRisk.trim()}` : '',
      coreInsight.trim() ? `【核心解读】\n${coreInsight.trim()}` : '',
    ].filter(Boolean).join('\n\n')
    await onSave({ projectOverview: projectOverview.trim(), scoreRequirements })
  }

  return (
    <section className="inline-parse-result form-card">
      <div className="section-title-row">
        <div className="form-title">
          <h2>招标文件解读结果</h2>
          <p>{detailLoading ? '正在读取结构化结果…' : '可直接编辑，确认后用于生成投标大纲。'}</p>
        </div>
      </div>
      <div className="result-tabs">
        {tabs.map(tab => <button key={tab.key} type="button" className={activeTab === tab.key ? 'active' : ''} onClick={() => setActiveTab(tab.key)}>{tab.label}</button>)}
      </div>
      <label className="inline-parse-field">
        <span>{current.label}</span>
        <textarea value={current.value} maxLength={PASTE_PARSE_MAX_LENGTH} placeholder={current.placeholder} onChange={event => current.setter(event.target.value.slice(0, PASTE_PARSE_MAX_LENGTH))} />
        <small>{current.value.length} / {PASTE_PARSE_MAX_LENGTH}</small>
      </label>
      {activeTab === 'insight' && <CoreInsightPreview detail={detail} />}
      <div className="inline-parse-actions">
        {onReparse && <button className="light-btn" type="button" onClick={onReparse} disabled={saving}>重新解析</button>}
        <button className="dark-btn" type="button" onClick={() => void save()} disabled={saving || !projectOverview.trim()}>{saving ? '保存中' : '保存结果'}</button>
      </div>
    </section>
  )
}

function CoreInsightPreview({ detail }: { detail?: BidParseDetail | null }) {
  const keyPoints = detail?.bid_key_points ?? {}
  const groups = [
    ['核心投标重点', parseDetailItems((keyPoints as Record<string, unknown>)['core_points'])],
    ['高分值优先级', parseDetailItems((keyPoints as Record<string, unknown>)['high_score_priorities'] ?? detail?.tech_scoring_items)],
    ['目录建议', parseDetailItems((keyPoints as Record<string, unknown>)['catalog_suggestions'])],
    ['废标风险提示', parseDetailItems((keyPoints as Record<string, unknown>)['invalid_risks'] ?? detail?.invalid_bid_clauses)],
  ] as const
  return (
    <div className="inline-core-grid">
      {groups.map(([title, items]) => (
        <div key={title}>
          <strong>{title}</strong>
          {items.length ? <ul>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul> : <p>暂无识别结果</p>}
        </div>
      ))}
    </div>
  )
}

function formatParseText(value: unknown) {
  return parseDetailItems(value, 30).join('\n')
}

function formatKeyPoints(value: unknown) {
  if (!value || typeof value !== 'object') return ''
  const record = value as Record<string, unknown>
  return [
    ['核心投标重点', record.core_points],
    ['高分值优先级', record.high_score_priorities],
    ['目录建议', record.catalog_suggestions],
    ['废标风险提示', record.invalid_risks],
  ].map(([title, items]) => {
    const rows = parseDetailItems(items, 12)
    return rows.length ? `【${title}】\n${rows.join('\n')}` : ''
  }).filter(Boolean).join('\n\n')
}

function parseDetailItems(value: unknown, limit = 6) {
  if (!value) return []
  const items = Array.isArray(value) ? value : [value]
  return items.map(item => {
    if (typeof item === 'string') return item.trim()
    if (!item || typeof item !== 'object') return ''
    const record = item as Record<string, unknown>
    const score = record.score || record.points ? `（${String(record.score ?? record.points)}分）` : ''
    const title = String(record.title ?? record.name ?? record.item ?? record.clause ?? record.category ?? '')
    const text = String(record.criteria ?? record.text ?? record.requirement ?? record.description ?? '')
    return `${title}${score}${text ? `：${text}` : ''}`.trim()
  }).filter(Boolean).slice(0, limit)
}

function PasteParseModal({
  open,
  title = '粘贴项目信息解析',
  helperText = '请先填写项目概述和技术部分评分要求',
  confirmText = '确认',
  cancelText = '取消',
  badgeText,
  detail,
  detailLoading,
  secondaryText,
  initialProjectOverview,
  initialScoreRequirements,
  saving,
  onCancel,
  onConfirm,
  onSecondary,
}: {
  open: boolean
  title?: string
  helperText?: string
  confirmText?: string
  cancelText?: string
  badgeText?: string
  detail?: BidParseDetail | null
  detailLoading?: boolean
  secondaryText?: string
  initialProjectOverview: string
  initialScoreRequirements: string
  saving: boolean
  onCancel: () => void
  onConfirm: (payload: { projectOverview: string; scoreRequirements: string }) => Promise<void>
  onSecondary?: () => Promise<void> | void
}) {
  const [projectOverview, setProjectOverview] = useState(initialProjectOverview)
  const [scoreRequirements, setScoreRequirements] = useState(initialScoreRequirements)
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (!open) return
    setProjectOverview(initialProjectOverview)
    setScoreRequirements(initialScoreRequirements)
    setSubmitted(false)
  }, [open, initialProjectOverview, initialScoreRequirements])

  if (!open) return null

  const overviewFilled = projectOverview.trim().length > 0
  const scoreFilled = scoreRequirements.trim().length > 0
  const formValid = overviewFilled && scoreFilled
  const canSubmit = formValid && !saving
  const helperError = submitted && !formValid

  const submit = async () => {
    setSubmitted(true)
    if (!formValid || saving) return
    await onConfirm({ projectOverview: projectOverview.trim(), scoreRequirements: scoreRequirements.trim() })
  }

  return (
    <div className="paste-parse-modal-mask" role="presentation">
      <div className="paste-parse-modal" role="dialog" aria-modal="true" aria-labelledby="paste-parse-title">
        <div className="paste-parse-modal-header">
          <h2 id="paste-parse-title">{title}{badgeText && <span className="paste-parse-ai-badge">{badgeText}</span>}</h2>
          <button className="paste-parse-modal-close" type="button" aria-label="关闭" onClick={onCancel}>×</button>
        </div>
        <div className="paste-parse-modal-body">
          <label className="paste-parse-field">
            <span>项目概述 <em>*</em></span>
            <textarea value={projectOverview} maxLength={PASTE_PARSE_MAX_LENGTH} placeholder="请粘贴或填写项目概述" onChange={event => setProjectOverview(event.target.value.slice(0, PASTE_PARSE_MAX_LENGTH))} />
            <small>{projectOverview.length} / {PASTE_PARSE_MAX_LENGTH}</small>
          </label>
          <label className="paste-parse-field">
            <span>技术部分评分要求 <em>*</em></span>
            <textarea value={scoreRequirements} maxLength={PASTE_PARSE_MAX_LENGTH} placeholder="请粘贴或填写评分要求" onChange={event => setScoreRequirements(event.target.value.slice(0, PASTE_PARSE_MAX_LENGTH))} />
            <small>{scoreRequirements.length} / {PASTE_PARSE_MAX_LENGTH}</small>
          </label>
          {(detailLoading || detail) && <ParseDetailPanel detail={detail} detailLoading={detailLoading} />}
        </div>
        <div className="paste-parse-modal-footer">
          <p className={helperError ? 'is-error' : ''}>{helperError ? '请填写项目概述和技术部分评分要求' : helperText}</p>
          <div>
            {secondaryText && <button className="paste-parse-btn paste-parse-btn-secondary" type="button" onClick={onSecondary} disabled={saving}>{secondaryText}</button>}
            <button className="paste-parse-btn paste-parse-btn-secondary" type="button" onClick={onCancel}>{cancelText}</button>
            <button className="paste-parse-btn paste-parse-btn-primary" type="button" disabled={!canSubmit} onClick={submit}>{saving ? '保存中' : confirmText}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

function ParseDetailPanel({ detail, detailLoading }: { detail?: BidParseDetail | null; detailLoading?: boolean }) {
  const listFromValue = (value: unknown, limit = 5) => {
    if (!value) return []
    const items = Array.isArray(value) ? value : [value]
    return items.map(item => {
      if (typeof item === 'string') return item
      if (!item || typeof item !== 'object') return ''
      const record = item as Record<string, unknown>
      const score = record.score || record.points ? `（${String(record.score ?? record.points)}分）` : ''
      const title = String(record.title ?? record.name ?? record.item ?? record.clause ?? '')
      const text = String(record.criteria ?? record.text ?? record.requirement ?? '')
      return `${title}${score}${text ? `：${text}` : ''}`.trim()
    }).filter(Boolean).slice(0, limit)
  }
  const keyPoints = detail?.bid_key_points ?? {}
  const groups = [
    ['核心投标重点', listFromValue((keyPoints as Record<string, unknown>)['core_points'])],
    ['高分值优先级', listFromValue((keyPoints as Record<string, unknown>)['high_score_priorities'] ?? detail?.tech_scoring_items)],
    ['目录覆盖建议', listFromValue((keyPoints as Record<string, unknown>)['catalog_suggestions'])],
    ['废标风险提示', listFromValue((keyPoints as Record<string, unknown>)['invalid_risks'] ?? detail?.invalid_bid_clauses)],
  ] as const
  return (
    <details className="paste-parse-detail" open>
      <summary>核心解读</summary>
      {detailLoading ? <p>正在读取结构化解析结果...</p> : (
        <div className="paste-parse-detail-grid">
          {groups.map(([title, items]) => (
            <div key={title}>
              <strong>{title}</strong>
              {items.length ? <ul>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul> : <p>暂无识别结果</p>}
            </div>
          ))}
        </div>
      )}
    </details>
  )
}

function CatalogStep({ bidId, project, onProject, onBack, onHome, onNext }: { bidId: number | null; project: ApiProject | null; onProject: (project: ApiProject) => void; onBack: () => void; onHome: () => void; onNext: () => void }) {
  const [leftTab, setLeftTab] = useState<CatalogLeftTab>('思路')
  const [catalog, setCatalog] = useState<BidCatalogResponse | null>(null)
  const [loadingCatalog, setLoadingCatalog] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [catalogError, setCatalogError] = useState('')
  const [catalogNotice, setCatalogNotice] = useState('')
  const [chapterBusy, setChapterBusy] = useState<number | null>(null)
  const [undoStack, setUndoStack] = useState<Array<{ versionId: number; tree: CatalogNode[] }>>([])
  const [styleMenuOpen, setStyleMenuOpen] = useState(false)
  const [selectedChapterId, setSelectedChapterId] = useState<number | null>(null)
  const [chapterMaterials, setChapterMaterials] = useState<ChapterMaterials | null>(null)
  const [materialsLoading, setMaterialsLoading] = useState(false)
  const [materialsBusy, setMaterialsBusy] = useState(false)
  const [materialFilter, setMaterialFilter] = useState<'all' | 'image' | 'snippet'>('all')
  const [materialStats, setMaterialStats] = useState<Record<number, { matched: number; selected: number }>>({})
  const [bidScenario, setBidScenario] = useState('')
  const [lengthLevel, setLengthLevel] = useState('')
  const [materialViewer, setMaterialViewer] = useState<KnowledgeAsset | null>(null)
  const catalogStreamRef = useRef<AbortController | null>(null)
  const [catalogPreview, setCatalogPreview] = useState('')
  const [catalogStreamMessage, setCatalogStreamMessage] = useState('')
  const font: CSSProperties = { fontFamily: "'Noto Sans SC', sans-serif" }

  const versions = catalog?.versions ?? []
  const activeVersion = useMemo(() => versions.find(item => item.is_final) ?? versions[0] ?? null, [versions])
  const activeTree = activeVersion?.catalog_content ?? []
  const flatTree = useMemo(() => flattenCatalog(activeTree), [activeTree])
  const scoreLines = splitDisplayLines(catalog?.context.score_requirements ?? '')
  const overviewText = (catalog?.context.project_overview ?? '').trim()
  const isProcessing = generating || catalog?.generation_status === 'processing'

  useEffect(() => {
    if (!selectedChapterId && flatTree.length) setSelectedChapterId(flatTree[0].chapter_id)
    if (selectedChapterId && !flatTree.some(node => node.chapter_id === selectedChapterId)) setSelectedChapterId(flatTree[0]?.chapter_id || null)
  }, [flatTree, selectedChapterId])

  useEffect(() => {
    let alive = true
    async function loadChapterMaterials() {
      if (!bidId || !selectedChapterId) {
        setChapterMaterials(null)
        return
      }
      setMaterialsLoading(true)
      try {
        const selectedNode = flatTree.find(node => node.chapter_id === selectedChapterId)
        const response = await wordagentApi.getChapterMaterials(bidId, selectedChapterId, {
          bid_scenario: bidScenario || null,
          length_level: lengthLevel || null,
          chapter_theme: selectedNode ? `${selectedNode.number} ${selectedNode.title}` : null,
        })
        if (!alive || response.code !== 0) return
        setChapterMaterials(response.data)
        setMaterialStats(prev => ({ ...prev, [selectedChapterId]: { matched: response.data.total, selected: response.data.selected_asset_ids.length + response.data.selected_chunk_ids.length } }))
      } catch (error) {
        if (alive) setCatalogError(apiErrorMessage(error, '章节素材加载失败'))
      } finally {
        if (alive) setMaterialsLoading(false)
      }
    }
    void loadChapterMaterials()
    return () => { alive = false }
  }, [bidId, selectedChapterId, bidScenario, lengthLevel])

  const pushUndoSnapshot = () => {
    if (!activeVersion) return
    const snapshot = JSON.parse(JSON.stringify(activeVersion.catalog_content)) as CatalogNode[]
    setUndoStack(stack => [{ versionId: activeVersion.version_id, tree: snapshot }, ...stack].slice(0, 5))
  }

  const notifyCatalog = (message: string) => {
    setCatalogNotice(message)
    window.setTimeout(() => setCatalogNotice(current => current === message ? '' : current), 1800)
  }

  const applyCatalog = (next: BidCatalogResponse) => {
    setCatalog(next)
  }

  const loadCatalog = async () => {
    if (!bidId) return null
    const res = await wordagentApi.getBidCatalog(bidId)
    applyCatalog(res.data)
    return res.data
  }

  const startCatalogStream = async (force = false, style?: string) => {
    if (!bidId || catalogStreamRef.current) return
    const controller = new AbortController()
    catalogStreamRef.current = controller
    setGenerating(true)
    setCatalogError('')
    setCatalogPreview('')
    setCatalogStreamMessage('正在准备目录与大纲，请稍候……')
    try {
      await readEventStream(wordagentApi.generateBidCatalogStreamUrl(bidId, { force, style }), {
        signal: controller.signal,
        onEvent: (eventName, payload) => {
          if (catalogStreamRef.current !== controller) return
          if (eventName === 'start' || eventName === 'progress') {
            if (payload.message) setCatalogStreamMessage(String(payload.message))
          } else if (eventName === 'version_start') {
            setCatalogPreview('')
            setCatalogStreamMessage('正在生成：' + (payload.version_name || '目录大纲'))
          } else if (eventName === 'delta') {
            if (typeof payload.preview === 'string') setCatalogPreview(payload.preview)
          } else if (eventName === 'version') {
            const version = payload.version as NonNullable<BidCatalogResponse['versions']>[number]
            if (!version?.catalog_content) return
            setCatalog(prev => {
              if (!prev) return prev
              const versions = [...prev.versions]
              const index = versions.findIndex(item => item.version_name === version.version_name)
              if (index >= 0) versions[index] = version
              else versions.push(version)
              return { ...prev, versions, generation_status: 'processing', message: '目录生成中' }
            })
          }
        },
      })
      if (catalogStreamRef.current !== controller) return
      const res = await wordagentApi.getBidCatalog(bidId)
      if (catalogStreamRef.current !== controller) return
      applyCatalog(res.data)
      setCatalogPreview('')
      setCatalogStreamMessage('目录与大纲已生成并保存')
    } catch (error) {
      if (catalogStreamRef.current !== controller) return
      setCatalogError(controller.signal.aborted ? '已停止生成；下方预览未保存为完整目录，已保存的版本仍保留。' : apiErrorMessage(error, '目录生成失败，请重试'))
      setCatalog(prev => prev ? { ...prev, generation_status: 'failed' } : prev)
      setCatalogStreamMessage('生成未完成')
    } finally {
      if (catalogStreamRef.current === controller) {
        catalogStreamRef.current = null
        setGenerating(false)
      }
    }
  }

  useEffect(() => {
    let alive = true
    async function bootstrap() {
      if (!bidId) return
      setLoadingCatalog(true)
      setCatalogError('')
      try {
        const res = await wordagentApi.getBidCatalog(bidId)
        if (!alive) return
        applyCatalog(res.data)
        if (!res.data.versions.length || res.data.generation_status === 'processing') void startCatalogStream(false)
      } catch (error) {
        if (alive) setCatalogError(apiErrorMessage(error, '目录数据加载失败'))
      } finally {
        if (alive) setLoadingCatalog(false)
      }
    }
    void bootstrap()
    return () => { alive = false }
  }, [bidId])

  useEffect(() => () => { const controller = catalogStreamRef.current; catalogStreamRef.current = null; controller?.abort() }, [])

  async function regenerateCatalog(style?: string) {
    if (!bidId || isProcessing) return
    if (!window.confirm('重新生成会刷新当前目录内容，已有编辑内容将被覆盖。确认继续？')) return
    setStyleMenuOpen(false)
    setCatalogError('')
    setCatalog(prev => prev ? { ...prev, generation_status: 'processing', message: '目录重新生成中' } : prev)
    startCatalogStream(true, style)
  }

  async function saveChapterTitle(node: CatalogNode, target: HTMLElement) {
    if (!bidId) return
    const nextTitle = target.innerText.replace(/\s+/g, ' ').trim().slice(0, 200)
    if (!nextTitle) {
      target.innerText = node.title
      return
    }
    if (nextTitle === node.title) return
    setChapterBusy(node.chapter_id)
    setCatalogError('')
    try {
      pushUndoSnapshot()
      await wordagentApi.updateCatalogChapter(bidId, node.chapter_id, nextTitle)
      await loadCatalog()
      notifyCatalog('章节已保存')
    } catch (error) {
      target.innerText = node.title
      setCatalogError(apiErrorMessage(error, '章节保存失败'))
    } finally {
      setChapterBusy(null)
    }
  }

  async function runChapterAction(node: CatalogNode, action: 'expand' | 'optimize') {
    if (!bidId || chapterBusy) return
    setChapterBusy(node.chapter_id)
    setCatalogError('')
    try {
      if (action === 'expand') await wordagentApi.expandCatalogChapter(bidId, node.chapter_id, '补充更专业、可落地的下级目录，覆盖评分重点')
      else await wordagentApi.optimizeCatalogChapter(bidId, node.chapter_id, '强化招投标规范表达，突出评分点覆盖')
      await loadCatalog()
    } catch (error) {
      setCatalogError(apiErrorMessage(error, action === 'expand' ? '章节扩写失败' : '章节优化失败'))
    } finally {
      setChapterBusy(null)
    }
  }

  async function runEditOperation(node: CatalogNode, action: () => Promise<unknown>, successMessage: string) {
    if (!bidId || !activeVersion || chapterBusy || isProcessing) return
    pushUndoSnapshot()
    setChapterBusy(node.chapter_id)
    setCatalogError('')
    try {
      await action()
      await loadCatalog()
      notifyCatalog(successMessage)
    } catch (error) {
      setCatalogError(apiErrorMessage(error, successMessage.replace('已', '') + '失败'))
    } finally {
      setChapterBusy(null)
    }
  }

  async function addSiblingChapter(node: CatalogNode) {
    if (!bidId || !activeVersion) return
    await runEditOperation(node, () => wordagentApi.createCatalogChapter(bidId, {
      version_id: activeVersion.version_id,
      parent_id: node.parent_id ?? 0,
      chapter_title: `新增${node.level === 1 ? '章节' : node.level === 2 ? '小节' : '条目'}`,
      level: node.level,
      sort_order: (node.sort_order ?? 0) + 1,
    }), '章节已新增')
  }

  async function addChildChapter(node: CatalogNode) {
    if (!bidId || !activeVersion || node.level >= 3) return
    await runEditOperation(node, () => wordagentApi.createCatalogChapter(bidId, {
      version_id: activeVersion.version_id,
      parent_id: node.chapter_id,
      chapter_title: `新增${node.level === 1 ? '小节' : '条目'}`,
      level: (node.level + 1) as 1 | 2 | 3,
      sort_order: (node.children?.length ?? 0) + 1,
    }), '子级已新增')
  }

  async function deleteCatalogNode(node: CatalogNode) {
    if (!bidId) return
    if (!window.confirm(node.level === 1 ? '删除后该章节及所有下级小节将一并移除，无法恢复。确认删除？' : '确认删除该章节？')) return
    await runEditOperation(node, () => wordagentApi.deleteCatalogChapter(bidId, node.chapter_id), '章节已删除')
  }

  async function moveCatalogNode(node: CatalogNode, moveType: 'up' | 'down' | 'upgrade' | 'downgrade') {
    if (!bidId) return
    await runEditOperation(node, () => wordagentApi.moveCatalogChapter(bidId, node.chapter_id, moveType), '章节已调整')
  }

  async function setFinalVersion() {
    if (!bidId || !activeVersion || isProcessing) return
    setCatalogError('')
    try {
      const res = await wordagentApi.setFinalCatalogVersion(bidId, activeVersion.version_id)
      applyCatalog(res.data)
      notifyCatalog('已设为最终版本')
    } catch (error) {
      setCatalogError(apiErrorMessage(error, '最终版本设置失败'))
    }
  }

  async function undoCatalogEdit() {
    if (!bidId || !undoStack.length || isProcessing) return
    const [snapshot, ...rest] = undoStack
    setUndoStack(rest)
    setCatalogError('')
    try {
      await wordagentApi.replaceCatalogVersionTree(bidId, snapshot.versionId, snapshot.tree)
      await loadCatalog()
      notifyCatalog('已撤销最近一步')
    } catch (error) {
      setCatalogError(apiErrorMessage(error, '撤销失败'))
    }
  }

  async function handleNext() {
    if (project && activeVersion) onProject({ ...project, directory: flattenCatalogToDirectory(activeVersion.catalog_content) })
    onNext()
  }

  async function saveMaterialSelection(nextAssetIds: number[], nextChunkIds: number[]) {
    if (!bidId || !selectedChapterId || materialsBusy) return
    setMaterialsBusy(true)
    try {
      const response = await wordagentApi.saveChapterMaterials(bidId, selectedChapterId, { selected_asset_ids: nextAssetIds, selected_chunk_ids: nextChunkIds, bid_scenario: bidScenario || null, length_level: lengthLevel || null })
      if (response.code !== 0) throw new Error(response.message || '章节素材保存失败')
      setChapterMaterials(response.data)
      setMaterialStats(prev => ({ ...prev, [selectedChapterId]: { matched: response.data.total, selected: response.data.selected_asset_ids.length + response.data.selected_chunk_ids.length } }))
    } catch (error) {
      setCatalogError(apiErrorMessage(error, '章节素材保存失败'))
    } finally {
      setMaterialsBusy(false)
    }
  }

  function toggleChapterMaterial(kind: 'asset' | 'chunk', id: number) {
    if (!chapterMaterials) return
    const assetIds = [...chapterMaterials.selected_asset_ids]
    const chunkIds = [...chapterMaterials.selected_chunk_ids]
    const target = kind === 'asset' ? assetIds : chunkIds
    const index = target.indexOf(id)
    if (index >= 0) target.splice(index, 1)
    else target.push(id)
    setChapterMaterials({ ...chapterMaterials, selected_asset_ids: assetIds, selected_chunk_ids: chunkIds })
    void saveMaterialSelection(assetIds, chunkIds)
  }

  function chooseBestChapterMaterials() {
    if (!chapterMaterials) return
    const assetIds = chapterMaterials.images.slice(0, 2).map(item => item.asset_id)
    const chunkIds = chapterMaterials.snippets.slice(0, 1).map(item => item.chunk_id)
    setChapterMaterials({ ...chapterMaterials, selected_asset_ids: assetIds, selected_chunk_ids: chunkIds })
    void saveMaterialSelection(assetIds, chunkIds)
  }

  async function matchAllChapterMaterials() {
    if (!bidId || materialsBusy || !flatTree.length) return
    setMaterialsBusy(true)
    try {
      const response = await wordagentApi.matchAllChapterMaterials(bidId, { chapter_ids: flatTree.map(node => node.chapter_id), bid_scenario: bidScenario || null, length_level: lengthLevel || null })
      if (response.code !== 0) throw new Error(response.message || '全章节匹配失败')
      const nextStats: Record<number, { matched: number; selected: number }> = {}
      response.data.chapters.forEach(item => { nextStats[item.chapter_id] = { matched: item.total, selected: item.selected_asset_ids.length + item.selected_chunk_ids.length } })
      setMaterialStats(nextStats)
      const current = response.data.chapters.find(item => item.chapter_id === selectedChapterId)
      if (current) setChapterMaterials(current)
      notifyCatalog(`已完成 ${response.data.matched_chapters} 个章节的素材预匹配`)
    } catch (error) {
      setCatalogError(apiErrorMessage(error, '全章节素材匹配失败'))
    } finally {
      setMaterialsBusy(false)
    }
  }

  const editableTitle = (node: CatalogNode, className = '') => (
    <span
      className={`catalog-editor-title ${className}`.trim()}
      contentEditable={!isProcessing && chapterBusy !== node.chapter_id}
      suppressContentEditableWarning
      onDoubleClick={event => {
        const range = document.createRange()
        range.selectNodeContents(event.currentTarget)
        const selection = window.getSelection()
        selection?.removeAllRanges()
        selection?.addRange(range)
      }}
      onBlur={event => void saveChapterTitle(node, event.currentTarget)}
      onKeyDown={event => {
        if (event.key === 'Enter') {
          event.preventDefault()
          event.currentTarget.blur()
        }
      }}
      style={{ cursor: isProcessing ? 'default' : 'text' }}
      title="双击可重命名，失焦自动保存"
    >
      {stripCatalogNumber(node.title)}
    </span>
  )

  const nodeActionButtons = (node: CatalogNode) => (
    <div className="catalog-node-actions">
      <button type="button" title="新增同级" onClick={() => void addSiblingChapter(node)} disabled={isProcessing || Boolean(chapterBusy)}>同级</button>
      <button type="button" title="新增子级" onClick={() => void addChildChapter(node)} disabled={isProcessing || Boolean(chapterBusy) || node.level >= 3}>子级</button>
      <button type="button" title="上移" onClick={() => void moveCatalogNode(node, 'up')} disabled={isProcessing || Boolean(chapterBusy)}>上移</button>
      <button type="button" title="下移" onClick={() => void moveCatalogNode(node, 'down')} disabled={isProcessing || Boolean(chapterBusy)}>下移</button>
      <button type="button" title="升级" onClick={() => void moveCatalogNode(node, 'upgrade')} disabled={isProcessing || Boolean(chapterBusy) || node.level <= 1}>升级</button>
      <button type="button" title="降级" onClick={() => void moveCatalogNode(node, 'downgrade')} disabled={isProcessing || Boolean(chapterBusy) || node.level >= 3}>降级</button>
      <button type="button" title="删除" className="danger" onClick={() => void deleteCatalogNode(node)} disabled={isProcessing || Boolean(chapterBusy)}>删除</button>
    </div>
  )

  const formatCatalogIndex = (parts: number[]) => parts.join('.')

  const renderLeftTree = (nodes: CatalogNode[], level = 1, prefix: number[] = []): ReactNode => nodes.map((node, index) => {
    const current = [...prefix, index + 1]
    const stats = materialStats[node.chapter_id]
    return (
    <div key={node.chapter_id} className="catalog-node-row" onClick={() => setSelectedChapterId(node.chapter_id)} style={{ marginBottom: level === 1 ? '14px' : '6px', marginLeft: (level - 1) * 16, cursor: 'pointer' }}>
      <span style={{ ...font, display: 'flex', gap: '6px', alignItems: 'center', fontSize: level === 1 ? '13px' : '12px', fontWeight: level === 1 ? 700 : 400, color: level === 1 ? 'var(--form-label-color)' : 'var(--form-desc-color)', lineHeight: '1.6' }}>{formatCatalogIndex(current)} {stripCatalogNumber(node.title)}{stats?.matched ? <small style={{ color: 'var(--form-cta-bg)', fontWeight: 700 }}>素材 {stats.matched}</small> : null}{stats?.selected ? <small style={{ color: '#5E8C61', fontWeight: 700 }}>已选 {stats.selected}</small> : null}</span>
      {node.children.length > 0 && <div style={{ marginTop: '5px' }}>{renderLeftTree(node.children, level + 1, current)}</div>}
    </div>
    )
  })

  const renderCatalogEditorNode = (node: CatalogNode, parts: number[]): ReactNode => (
    <div key={node.chapter_id} className="catalog-editor-node">
      <div className={`catalog-editor-row level-${node.level}`} onClick={() => setSelectedChapterId(node.chapter_id)} style={{ '--level': node.level, cursor: 'pointer' } as CSSProperties}>
        <span className="catalog-editor-index">{formatCatalogIndex(parts)}</span>
        <div className="catalog-editor-title-wrap">
          {editableTitle(node, `level-${node.level}`)}
          {materialStats[node.chapter_id]?.matched ? <small style={{ marginLeft: '8px', color: 'var(--form-cta-bg)' }}>素材 {materialStats[node.chapter_id].matched}</small> : null}
          {materialStats[node.chapter_id]?.selected ? <small style={{ marginLeft: '6px', color: '#5E8C61' }}>已选</small> : null}
          {chapterBusy === node.chapter_id && <span className="catalog-editor-busy">处理中……</span>}
          {!chapterBusy && isProcessing && node.level === 1 && <span className="catalog-editor-busy">编写中……</span>}
        </div>
        {nodeActionButtons(node)}
      </div>
      {node.children.length > 0 && (
        <div className="catalog-editor-children">
          {node.children.map((child, index) => renderCatalogEditorNode(child, [...parts, index + 1]))}
        </div>
      )}
    </div>
  )

  const styleOptions = ['标准均衡型', '技术深度型', '服务落地型']

  return (
    <div style={{ minHeight: '100vh', background: 'var(--page-bg)', display: 'flex', flexDirection: 'column' }}>
      <TopNav onLogoClick={onHome} onBack={onBack} />
      <div className="catalog-workspace" style={{ flex: 1, maxWidth: '1440px', width: '100%', margin: '0 auto', boxSizing: 'border-box', minHeight: 0 }}>
        <div className="catalog-left-panel" style={{ display: 'flex', flexDirection: 'column', background: '#fff', borderRadius: 'var(--form-card-radius)', border: '1px solid var(--form-card-border)', overflow: 'hidden' }}>
          <div style={{ display: 'flex', borderBottom: '1px solid var(--form-divider)', padding: '0 20px' }}>
            {([['思路', '目录编写思路'], ['评分', '评分点'], ['概述', '项目概述']] as const).map(([key, label]) => {
              const active = leftTab === key
              return <button key={key} onClick={() => setLeftTab(key)} style={{ ...font, fontSize: '13px', fontWeight: active ? 600 : 400, color: active ? 'var(--form-label-color)' : 'var(--form-desc-color)', background: 'none', border: 'none', padding: '14px 16px 12px', cursor: 'pointer', lineHeight: 1.4, borderBottom: active ? '2px solid var(--form-label-color)' : '2px solid transparent', marginBottom: '-1px' }}>{label}</button>
            })}
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '20px' }}>
            {leftTab === '思路' && (activeTree.length ? renderLeftTree(activeTree) : <p style={{ ...font, fontSize: '13px', color: 'var(--form-desc-color)', margin: 0 }}>目录生成后将在这里同步展示。</p>)}
            {leftTab === '评分' && <div style={{ ...font, fontSize: '13px', color: 'var(--form-desc-color)', lineHeight: '1.8' }}><p style={{ margin: '0 0 12px', fontWeight: 600, color: 'var(--form-label-color)' }}>评分要求概览</p>{scoreLines.length ? scoreLines.map((item, index) => <p key={index} style={{ margin: index === scoreLines.length - 1 ? 0 : '0 0 8px' }}>• {item}</p>) : <p style={{ margin: 0 }}>暂无评分要求，请先在招标文件解读中补充。</p>}</div>}
            {leftTab === '概述' && <div style={{ ...font, fontSize: '13px', color: 'var(--form-desc-color)', lineHeight: '1.8', whiteSpace: 'pre-wrap' }}><p style={{ margin: '0 0 12px', fontWeight: 600, color: 'var(--form-label-color)' }}>项目概述</p><p style={{ margin: '0 0 8px' }}>项目名称：{catalog?.context.bid_title || project?.settings.bidTitle || '未命名标书'}</p><p style={{ margin: 0 }}>{overviewText || '暂无项目概述，请先在招标文件解读中补充。'}</p></div>}
          </div>
        </div>

        <div className="catalog-editor-panel" style={{ minWidth: 0, display: 'flex', flexDirection: 'column', background: '#fff', borderRadius: 'var(--form-card-radius)', border: '1px solid var(--form-card-border)', overflow: 'hidden' }}>
          <div style={{ padding: '12px 20px 0' }}><CaseReferenceSelector bidId={bidId} /></div>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', padding: '16px 20px 14px', borderBottom: '1px solid var(--form-divider)' }}>
            <p style={{ ...font, fontSize: '15px', fontWeight: 700, color: 'var(--form-label-color)', margin: 0 }}>目录</p>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center', position: 'relative' }}>
              <button onClick={() => setStyleMenuOpen(open => !open)} disabled={!bidId || isProcessing} style={{ ...toolbarButtonStyle(font), cursor: !bidId || isProcessing ? 'not-allowed' : 'pointer', opacity: !bidId || isProcessing ? 0.6 : 1 }}>选择风格重新生成</button>
              {styleMenuOpen && <div style={{ position: 'absolute', top: '34px', right: '178px', zIndex: 5, background: '#fff', border: '1px solid var(--form-card-border)', borderRadius: '6px', boxShadow: '0 8px 24px rgba(58,58,50,0.10)', padding: '6px', minWidth: '138px' }}>{styleOptions.map(option => <button key={option} onClick={() => void regenerateCatalog(option)} style={{ ...font, display: 'block', width: '100%', textAlign: 'left', fontSize: '12px', color: 'var(--form-label-color)', background: 'transparent', border: 'none', borderRadius: '4px', padding: '8px 10px', cursor: 'pointer' }}>{option}</button>)}</div>}
               <button onClick={() => void matchAllChapterMaterials()} disabled={!flatTree.length || materialsBusy || isProcessing} style={{ ...toolbarButtonStyle(font), cursor: flatTree.length && !materialsBusy && !isProcessing ? 'pointer' : 'not-allowed', opacity: flatTree.length && !materialsBusy && !isProcessing ? 1 : 0.6 }}>一键匹配全章节素材</button>
               <button onClick={() => activeVersion && bidId && window.open(wordagentApi.downloadBidCatalogUrl(bidId, activeVersion.version_id), '_blank')} disabled={!activeVersion || !bidId} style={{ ...toolbarButtonStyle(font), cursor: activeVersion && bidId ? 'pointer' : 'not-allowed', opacity: activeVersion && bidId ? 1 : 0.6 }}>下载大纲</button>
              <button onClick={() => void setFinalVersion()} disabled={!activeVersion || !bidId || isProcessing || activeVersion.is_final} style={{ ...toolbarButtonStyle(font), color: activeVersion?.is_final ? 'var(--form-desc-color)' : 'var(--form-cta-text)', background: activeVersion?.is_final ? 'var(--form-ref-bg)' : 'var(--form-cta-bg)', cursor: !activeVersion || !bidId || isProcessing || activeVersion.is_final ? 'not-allowed' : 'pointer', opacity: !activeVersion || !bidId || isProcessing ? 0.6 : 1 }}>{activeVersion?.is_final ? '已设为最终' : '设为最终版本'}</button>
              <button onClick={() => void undoCatalogEdit()} disabled={!undoStack.length || isProcessing} style={{ ...toolbarButtonStyle(font), cursor: undoStack.length && !isProcessing ? 'pointer' : 'not-allowed', opacity: undoStack.length && !isProcessing ? 1 : 0.6 }}>撤销</button>
            </div>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {loadingCatalog && <StatusBox>正在加载目录数据……</StatusBox>}
            {(isProcessing || catalogPreview) && <section aria-label="目录实时生成预览" style={{ background: 'var(--form-ref-bg)', border: '1px solid var(--form-card-border)', borderRadius: 8, padding: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <strong role="status">{catalogStreamMessage || '正在生成目录与大纲'}</strong>
                {generating && <button type="button" onClick={() => catalogStreamRef.current?.abort()} style={toolbarButtonStyle(font)}>停止生成</button>}
              </div>
              <pre style={{ ...font, margin: '12px 0 0', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 420, overflowY: 'auto', fontSize: 14, lineHeight: 1.8 }}>{catalogPreview || '正在等待模型输出，收到内容后会在这里实时显示。'}</pre>
              <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--form-desc-color)' }}>生成过程中为预览，完整校验并保存后才可编辑和下载。</p>
            </section>}
            {catalogError && <StatusBox danger>{catalogError}</StatusBox>}
            {catalogNotice && <StatusBox success>{catalogNotice}</StatusBox>}
            {!activeTree.length && !loadingCatalog && !isProcessing && !catalogError && <StatusBox>还没有目录数据。点击“选择风格重新生成”即可生成目录。</StatusBox>}
            {activeTree.map((chapter, index) => (
              <div key={chapter.chapter_id} className="catalog-node-card">
                <div className="catalog-editor-list">
                  {renderCatalogEditorNode(chapter, [index + 1])}
                </div>
                <div className="catalog-card-actions">
                  <button onClick={() => void runChapterAction(chapter, 'expand')} disabled={isProcessing || Boolean(chapterBusy)} style={chapterActionStyle(font, isProcessing || Boolean(chapterBusy))}>AI扩写目录</button>
                  <button onClick={() => void runChapterAction(chapter, 'optimize')} disabled={isProcessing || Boolean(chapterBusy)} style={chapterActionStyle(font, isProcessing || Boolean(chapterBusy))}>AI优化目录</button>
                </div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', gap: '16px', padding: '18px 20px', borderTop: '1px solid var(--form-divider)' }}>
            <button onClick={onBack} style={{ ...font, fontSize: '14px', fontWeight: 500, padding: '10px 32px', borderRadius: 'var(--btn-radius)', background: 'var(--form-input-bg)', border: '1px solid var(--form-input-border)', color: 'var(--form-label-color)', cursor: 'pointer' }}>上一步</button>
            <button disabled={!activeVersion || isProcessing} style={{ ...font, fontSize: '14px', fontWeight: 600, padding: '10px 32px', borderRadius: 'var(--btn-radius)', background: 'var(--form-cta-bg)', color: 'var(--form-cta-text)', border: 'none', cursor: !activeVersion || isProcessing ? 'not-allowed' : 'pointer', opacity: !activeVersion || isProcessing ? 0.65 : 1, boxShadow: '0 2px 8px rgba(0,0,0,0.15)' }} onClick={() => void handleNext()}>下一步：生成标书正文</button>
         </div>
       </div>

        <ChapterMaterialPanel
          chapter={flatTree.find(node => node.chapter_id === selectedChapterId) || null}
          materials={chapterMaterials}
          loading={materialsLoading}
          busy={materialsBusy}
          filter={materialFilter}
          onFilterChange={setMaterialFilter}
          onToggle={toggleChapterMaterial}
          onChooseBest={chooseBestChapterMaterials}
          bidScenario={bidScenario}
          lengthLevel={lengthLevel}
          onBidScenarioChange={setBidScenario}
          onLengthLevelChange={setLengthLevel}
          onView={setMaterialViewer}
        />
      </div>
      {materialViewer && <div className="knowledge-detail-modal" role="dialog" aria-modal="true" aria-label="素材详情">
        <div className="knowledge-detail-modal-panel">
          <header><strong>素材详情</strong><button type="button" onClick={() => setMaterialViewer(null)}>关闭</button></header>
          <div className="knowledge-detail-modal-body">
            <div style={{ display: 'grid', placeItems: 'center', minHeight: '240px', background: 'var(--form-ref-bg)', borderRadius: 'var(--form-input-radius)' }}>{materialAssetUrl(materialViewer.resource_path) ? <img src={materialAssetUrl(materialViewer.resource_path)} alt={assetSubtypeText(materialViewer)} style={{ maxWidth: '100%', maxHeight: '52vh', objectFit: 'contain' }} /> : <span>暂无图片预览</span>}</div>
            <div className="knowledge-detail-form-grid"><section><h3>细分类别</h3><p>{assetSubtypeText(materialViewer)}</p></section><section><h3>有效期</h3><p>{materialViewer.expire_label || materialViewer.expire_date || '未知'}</p></section><section><h3>来源</h3><p>{materialViewer.item_title || materialViewer.source_name || '知识库素材'}</p></section><section><h3>说明</h3><p>{materialViewer.annotation || materialViewer.ai_description || '暂无说明'}</p></section></div>
          </div>
        </div>
      </div>}
    </div>
  )
}

function toolbarButtonStyle(font: CSSProperties): CSSProperties {
  return { ...font, fontSize: '12px', fontWeight: 400, color: 'var(--form-desc-color)', background: 'none', border: '1px solid var(--form-card-border)', borderRadius: 'var(--form-input-radius)', padding: '5px 12px' }
}

function ChapterMaterialPanel({ chapter, materials, loading, busy, filter, onFilterChange, onToggle, onChooseBest, bidScenario, lengthLevel, onBidScenarioChange, onLengthLevelChange, onView }: {
  chapter: (CatalogNode & { number: string }) | null
  materials: ChapterMaterials | null
  loading: boolean
  busy: boolean
  filter: 'all' | 'image' | 'snippet'
  onFilterChange: (value: 'all' | 'image' | 'snippet') => void
  onToggle: (kind: 'asset' | 'chunk', id: number) => void
  onChooseBest: () => void
  bidScenario: string
  lengthLevel: string
  onBidScenarioChange: (value: string) => void
  onLengthLevelChange: (value: string) => void
  onView: (asset: KnowledgeAsset) => void
}) {
  const selectedAssetIds = new Set(materials?.selected_asset_ids || [])
  const selectedChunkIds = new Set(materials?.selected_chunk_ids || [])
  const showImages = filter === 'all' || filter === 'image'
  const showSnippets = filter === 'all' || filter === 'snippet'
  return (
    <aside className="form-card catalog-material-panel" style={{ display: 'grid', alignContent: 'start', gap: '12px', padding: '18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'flex-start' }}>
        <div>
          <h3 style={{ margin: 0, color: 'var(--form-label-color)', fontSize: '15px' }}>章节素材预览</h3>
          <p style={{ margin: '5px 0 0', color: 'var(--form-desc-color)', fontSize: '11px', lineHeight: 1.5 }}>{chapter ? `${chapter.number} ${stripCatalogNumber(chapter.title)}` : '请选择章节'}</p>
        </div>
        <span style={{ color: 'var(--form-desc-color)', fontSize: '11px', whiteSpace: 'nowrap' }}>已选 {(materials?.selected_asset_ids.length || 0) + (materials?.selected_chunk_ids.length || 0)} / {materials?.total || 0}</span>
      </div>
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        {([['all', '全部'], ['image', '仅看图片'], ['snippet', '仅看方案']] as const).map(([value, label]) => <button key={value} type="button" className={filter === value ? 'dark-btn' : 'light-btn'} style={{ padding: '5px 8px', fontSize: '11px' }} onClick={() => onFilterChange(value)}>{label}</button>)}
      </div>
      <div className="catalog-material-filters">
        <label style={{ color: 'var(--form-desc-color)', fontSize: '11px' }}>标书场景<select value={bidScenario} onChange={event => onBidScenarioChange(event.target.value)}><option value="">全部</option><option value="明标">明标</option><option value="暗标">暗标</option></select></label>
        <label style={{ color: 'var(--form-desc-color)', fontSize: '11px' }}>篇幅等级<select value={lengthLevel} onChange={event => onLengthLevelChange(event.target.value)}><option value="">全部</option><option value="精简版">精简版</option><option value="标准版">标准版</option><option value="详细版">详细版</option></select></label>
      </div>
      <button className="light-btn" type="button" disabled={!materials?.total || busy} onClick={onChooseBest}>{busy ? '保存中...' : '一键选用最优匹配'}</button>
      {loading && <StatusBox>正在加载章节素材...</StatusBox>}
      {!loading && !materials?.total && <StatusBox>暂无匹配素材，可先继续生成正文。</StatusBox>}
      {!loading && materials && showImages && materials.images.length > 0 && <section style={{ display: 'grid', gap: '8px' }}>
        <strong style={{ color: 'var(--form-label-color)', fontSize: '12px' }}>图片素材</strong>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px' }}>
          {materials.images.map(asset => {
            const url = materialAssetUrl(asset.resource_path)
            const selected = selectedAssetIds.has(asset.asset_id)
            return <div key={asset.asset_id} className="knowledge-asset-card-shell" style={{ border: selected ? '1px solid var(--form-card-border-active)' : '1px solid var(--form-divider)', borderRadius: 'var(--form-input-radius)', padding: '6px', background: selected ? 'var(--form-ref-bg)' : '#fff', cursor: busy ? 'wait' : 'pointer' }}>
              <label className="knowledge-asset-select"><input type="checkbox" checked={selected} disabled={busy} onChange={() => onToggle('asset', asset.asset_id)} />选用</label>
              <button type="button" className="light-btn" onClick={event => { event.preventDefault(); event.stopPropagation(); onView(asset) }} style={{ display: 'grid', placeItems: 'center', aspectRatio: '1.25', overflow: 'hidden', background: 'var(--form-ref-bg)', borderRadius: 'var(--form-input-radius)', padding: 0 }}>{url ? <img src={url} alt={assetSubtypeText(asset)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : <small>#{asset.asset_id}</small>}</button>
              <strong style={{ color: 'var(--form-label-color)', fontSize: '11px', lineHeight: 1.35 }}>{assetSubtypeText(asset)}</strong>
              <small style={{ color: asset.expire_status === 'expired' ? '#C00000' : asset.expire_status === 'expiring' ? '#9A6B00' : 'var(--form-desc-color)' }}>{asset.expire_label || '有效期未知'}</small>
            </div>
          })}
        </div>
      </section>}
      {!loading && materials && showSnippets && materials.snippets.length > 0 && <section style={{ display: 'grid', gap: '8px' }}>
        <strong style={{ color: 'var(--form-label-color)', fontSize: '12px' }}>方案片段</strong>
        <div style={{ display: 'grid', gap: '8px' }}>
          {materials.snippets.map(snippet => {
            const selected = selectedChunkIds.has(snippet.chunk_id)
            return <div key={snippet.chunk_id} style={{ display: 'grid', gap: '5px', border: selected ? '1px solid var(--form-card-border-active)' : '1px solid var(--form-divider)', borderRadius: 'var(--form-input-radius)', padding: '8px', background: selected ? 'var(--form-ref-bg)' : '#fff', cursor: busy ? 'wait' : 'pointer' }}>
              <label className="knowledge-asset-select"><input type="checkbox" checked={selected} disabled={busy} onChange={() => onToggle('chunk', snippet.chunk_id)} />选用</label>
              <strong style={{ color: 'var(--form-label-color)', fontSize: '11px' }}>{snippet.section_title || snippet.title || '方案片段'}</strong>
              <small style={{ color: 'var(--form-desc-color)', lineHeight: 1.45 }}>{snippet.summary}</small>
              <small style={{ color: 'var(--form-desc-color)' }}>{snippet.source_document}{snippet.page_number ? ` · 第 ${snippet.page_number} 页` : ''}</small>
            </div>
          })}
        </div>
      </section>}
    </aside>
  )
}

function materialAssetUrl(path?: string | null) {
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

function assetSubtypeText(asset: KnowledgeAsset) {
  return asset.asset_sub_type || asset.category || asset.item_title || `素材 #${asset.asset_id}`
}

function chapterActionStyle(font: CSSProperties, disabled: boolean): CSSProperties {
  return { ...font, fontSize: '11px', fontWeight: 500, padding: '4px 10px', borderRadius: '5px', background: 'var(--form-cta-bg)', color: 'var(--form-cta-text)', border: 'none', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.65 : 1 }
}

function StatusBox({ children, danger, success }: { children: ReactNode; danger?: boolean; success?: boolean }) {
  return (
    <div style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: '13px', color: danger ? '#C00000' : success ? '#5E8C61' : 'var(--form-desc-color)', background: danger ? '#FFF7F5' : success ? '#F7FAF5' : 'var(--form-ref-bg)', border: `1px solid ${danger ? '#F0D6D2' : success ? '#D8E5D0' : 'var(--form-divider)'}`, borderRadius: '6px', padding: '12px 14px' }}>
      {children}
    </div>
  )
}

function ModuleLoading() {
  return <main className="module-page"><StatusBox>正在加载功能模块...</StatusBox></main>
}

function splitDisplayLines(text: string, limit = 40) {
  return text
    .split(/[\r\n；;]+/)
    .map(item => item.replace(/^[-•\s]+/, '').trim())
    .filter(Boolean)
    .slice(0, limit)
}

function flattenCatalogToDirectory(nodes: CatalogNode[]) {
  const rows: Array<{ id: string; level: 1 | 2; title: string; subItems?: string[]; status: 'pending'; wordsTotal?: number }> = []
  nodes.forEach(chapter => {
    rows.push({ id: String(chapter.chapter_id), level: 1, title: stripCatalogNumber(chapter.title), status: 'pending' })
    chapter.children.forEach(section => {
      rows.push({ id: String(section.chapter_id), level: 2, title: stripCatalogNumber(section.title), subItems: section.children.map(item => stripCatalogNumber(item.title)), status: 'pending', wordsTotal: 800 })
    })
  })
  return rows
}

function flattenCatalog(nodes: CatalogNode[], prefix: number[] = []): Array<CatalogNode & { number: string }> {
  return nodes.flatMap((node, index) => {
    const parts = [...prefix, index + 1]
    return [{ ...node, number: parts.join('.') }, ...flattenCatalog(node.children || [], parts)]
  })
}

function stripCatalogNumber(title: string) {
  return (title || '')
    .replace(/^\s*(?:第[一二三四五六七八九十百千万\d]+[章节篇部分]\s*)+/, '')
    .replace(/^\s*\d+(?:\.\d+)+\s*/, '')
    .replace(/^\s*\d+(?:[、.．]\s*|\s+)/, '')
    .trim()
}

export default P1Workspace
