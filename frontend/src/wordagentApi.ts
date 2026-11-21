import type { BidExtractionProgress } from './components/ExtractionProgress'
import { API_BASE, getAuthToken } from './auth'

function requestHeaders(options: RequestInit) {
  const headers = new Headers(options.headers)
  if (options.body !== undefined && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const token = getAuthToken()
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: requestHeaders(options),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}

async function requestBlob(path: string, options: RequestInit = {}): Promise<Blob> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: requestHeaders(options),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.blob()
}

export type TenderNotice = { notice_id: string; title: string; url: string; source: string; published_at: string; region: string; buyer: string; fetched_at: string; follow_state: string }
export type TenderSourceStatus = { source: string; ok: boolean; count: number; error?: string }

export type ApiEnvelope<T> = { code: number; message: string; data: T }

export type BlindCheckReport = { required: boolean; blocked: boolean; terms_count: number; notice: string; issues: Array<{ kind: string; location: string; detail: string; excerpt: string }>; images: Array<{ location: string; ocr_status: string; ocr_text: string; preview: string }> }

export type CaseReferenceSelection = {
  project_id: number | null
  selected: { project_id: number; project_name: string; bid_file_name: string; text_chunk_count: number; format_template_status?: string; format_template_id?: string | null } | null
  options: Array<{ project_id: number; project_name: string; bid_file_name: string; text_chunk_count: number; format_template_status?: string; format_template_id?: string | null }>
}

export type ModelScene = { scene: string; label: string; kind: 'text' | 'vision' | 'embedding'; model: string; default: string }
export type QwenCatalogModel = { id: string; name: string; capabilities: string[] }
export type PptSource = {
  bid_id: number
  project_title: string
  version_id: number
  versions: Array<{ version_id: number; version_name: string; is_final: number | boolean }>
  chapters: Array<{ chapter_id: number; parent_id: number; chapter_title: string; level: number; available: boolean; source_kind: string; image_count: number; preview: string }>
}
export type PptGeneratePayload = {
  version_id: number
  chapter_ids: number[]
  title: string
  subtitle: string
  theme: 'green' | 'blue'
  max_slides_per_chapter: number
  include_images: boolean
}

export type BidSettings = {
  bidTitle: string
  pageCount: number
  wordsPerPage?: number
  estimatedWordCount?: number
  bidType: 'open' | 'blind'
  selectedModel: string
  selectedProvider: string
}

export type ApiDirectoryRow = {
  id: string
  level: 1 | 2
  title: string
  subItems?: string[]
  status?: 'done' | 'pending'
  wordsDone?: number
  wordsTotal?: number
}

export type ApiProject = {
  id: string
  bidId?: number | null
  files?: Array<{ fileId: string; filename: string; size: number }>
  settings: BidSettings
  directory: ApiDirectoryRow[]
  createdAt: string
  updatedAt: string
}

export type ProjectListResponse = { success: true; projects: ApiProject[] }
export type ProjectDetailResponse = { success: true; project: ApiProject; bidId?: number }
export type ProjectDeleteResponse = {
  success: true
  projectId: string
  bidId: number | null
  deleted: true
  message: string
  warnings: string[]
  cleanup: { status: 'completed' | 'pending'; removed: number; preservedShared: number }
}

export type DuplicateBidParseMatch = {
  bid_id: number
  project_id: string
  project_title: string
  tender_file_name: string
  tender_file_size: number | null
  parse_status: string
  update_time: string | null
}

export type BidParseInfo = {
  bid_id: number
  current_step: number
  parse_id: number | null
  parse_mode: 'auto_upload' | 'paste_text' | null
  tender_file_path: string | null
  tender_file_name: string | null
  tender_file_size: number | null
  paste_content: string | null
  bid_category: 'service' | 'goods' | 'engineering' | 'other'
  parsed_project_overview: string | null
  parsed_score_requirements: string | null
  parsed_tech_scoring?: string | null
  parsed_biz_requirements?: string | null
  parsed_invalid_clauses?: string | null
  raw_full_text: string | null
  raw_markdown?: string | null
  full_markdown?: string | null
  chapter_blocks?: string | null
  project_basic_info?: string | null
  tech_scoring_items?: string | null
  biz_scoring_items?: string | null
  qualification_requirements?: string | null
  invalid_bid_clauses?: string | null
  bid_key_points?: string | null
  parse_current_step?: number | null
  parse_status: 'pending' | 'processing' | 'completed' | 'failed'
  parse_error: string | null
  parse_error_msg: string | null
  parse_warning_msg?: string | null
  update_time: string | null
  extraction_progress?: BidExtractionProgress | null
}

export type BidParseStatus = {
  bid_id: number
  parse_mode: 'auto_upload' | 'paste_text' | null
  parse_status: 'pending' | 'processing' | 'completed' | 'failed'
  parse_current_step?: number | null
  parse_step_label?: string
  tender_file_name: string | null
  tender_file_size: number | null
  parse_error_msg: string | null
  parse_warning_msg?: string | null
  current_step: number
  update_time?: string | null
  extraction_progress?: BidExtractionProgress | null
}

export type BidParseDetail = {
  bid_id: number
  parse_mode: 'auto_upload' | 'paste_text' | null
  parse_status: 'pending' | 'processing' | 'completed' | 'failed'
  tender_file_name?: string | null
  parsed_project_overview: string
  parsed_score_requirements: string
  project_basic_info?: Record<string, unknown>
  project_number?: string
  tech_scoring_items?: Array<Record<string, unknown>>
  biz_scoring_items?: Array<Record<string, unknown>>
  qualification_requirements?: Array<Record<string, unknown>>
  invalid_bid_clauses?: Array<Record<string, unknown>>
  bid_key_points?: Record<string, unknown>
  parse_warning_msg?: string | null
}

export type CatalogNode = {
  chapter_id: number
  title: string
  level: 1 | 2 | 3
  parent_id?: number
  sort_order?: number
  children: CatalogNode[]
}

export type CatalogVersion = {
  version_id: number
  bid_id: number
  version_name: string
  version_style: string
  version_type?: 'standard' | 'tech' | 'service'
  is_final?: boolean
  catalog_content: CatalogNode[]
  create_time: string
  update_time: string
}

export type BidCatalogResponse = {
  bid_id: number
  generation_status: 'idle' | 'processing' | 'completed' | 'failed'
  task_id: string | null
  message: string
  versions: CatalogVersion[]
  graphic_body_chapter_ids?: number[]
  context: {
    project_overview: string
    score_requirements: string
    bid_title: string
    page_count: number
    required_format_sections?: string[]
    required_fixed_forms?: string[]
    procurement_method?: string
    price_score_conflict?: string
  }
}

export type InvoiceOcrResult = {
  file_name: string
  source_type: 'image' | 'pdf'
  ocr_text: string
  document_type?: 'vat_invoice' | 'non_tax_receipt'
  receipt_fields?: {
    receipt_code?: string
    receipt_number?: string
    verification_code?: string
    payer?: string
    payer_tax_no?: string
    issue_date?: string
    total_amount?: string
    [key: string]: string | undefined
  }
  fields: {
    invoice_code?: string
    invoice_number?: string
    issue_date?: string
    amount_excluding_tax?: string
    amount?: string
    total_amount?: string
    tax_amount?: string
    title?: string
    tax_no?: string
    [key: string]: string | undefined
  }
  local_verification: {
    status: 'passed' | 'review' | 'high_risk'
    checks: Array<{ level: 'high' | 'medium'; message: string }>
    missing_fields: string[]
    official_ready?: boolean
    official_missing_fields?: string[]
  }
  status: 'ocr_completed' | 'ocr_unavailable'
  external_verification: {
    status: 'manual_pending' | 'passed' | 'failed' | 'review' | 'not_configured'
    message: string
    platform?: string
    platform_url?: string
    official_ready?: boolean
    official_missing_fields?: string[]
    checked_at?: string
  }
  official_verification_payload?: {
    invoice_code: string
    invoice_number: string
    issue_date: string
    amount_excluding_tax: string
  }
}

export type MermaidAsset = {
  id: number
  bid_id: number
  chart_title: string
  chart_type: string
  mermaid_code: string
  chart_image_data: string | null
  source_text: string | null
  saved_to_rag: boolean
  create_time: string
}

export type GraphicSection = {
  revision: number
  bid_id: number
  chapter_id: number
  chapter_title: string
  markdown: string
  ref_chart_ids: number[]
  charts: MermaidAsset[]
  ref_image_asset_ids?: number[]
  images?: KnowledgeAsset[]
  risks?: AssetRisk[]
  selected_asset_ids?: number[]
  selected_chunk_ids?: number[]
  selected_images?: KnowledgeAsset[]
  bid_scenario?: string | null
  length_level?: string | null
  image_match_meta?: Record<string, { match_score?: number; expire_status?: 'valid' | 'expiring' | 'expired' | 'unknown'; reuse_material?: boolean; match_scope?: string }>
  word_count?: number
  target_word_count?: number
  length_control?: { target_word_count: number; word_count: number; minimum_word_count: number; maximum_word_count: number; within_tolerance: boolean; adjustment_attempts: number; review_reason?: string | null } | null
  used_api?: boolean
  error?: string | null
}

export type GraphicSectionBatchResult = {
  bid_id: number
  chapters: Array<GraphicSection | { chapter_id: number; error: string }>
  success_count: number
  failure_count: number
  total_word_count: number
  estimated_pages: number
  within_tolerance?: boolean | null
}

export type ChapterMaterialSnippet = {
  chunk_id: number
  item_id: number
  title: string
  source_document: string
  section_title?: string | null
  page_number?: number | null
  content: string
  summary: string
  similarity?: number
}

export type ChapterMaterials = {
  bid_id: number
  chapter_id: number
  chapter_title: string
  images: KnowledgeAsset[]
  snippets: ChapterMaterialSnippet[]
  selected_asset_ids: number[]
  selected_chunk_ids: number[]
  bid_scenario?: string | null
  length_level?: string | null
  matching_context?: {
    chapter_path: string
    level_titles: string[]
    keywords: string[]
    exact_subtypes: string[]
    groups: string[]
  }
  total: number
}

export type AssetRiskSeverity = 'high' | 'medium' | 'low'
export type AssetRisk = {
  risk_id: string
  severity: AssetRiskSeverity
  issue: string
  rule: string
  chapter_id: number
  chapter_title: string
  asset_id: number
  asset?: KnowledgeAsset
  excerpt?: string
}

export type AssetAuditResult = {
  bid_id: number
  risks: AssetRisk[]
  assets: KnowledgeAsset[]
  summary: { high: number; medium: number; low: number; total: number }
}

export type BidTableType = 'qualification' | 'personnel' | 'vehicle' | 'equipment' | 'performance' | 'financial'
export type BidTableRow = Record<string, string | number | string[] | null> & {
  asset_id: number
  asset_sub_type: string
  source_name: string
  resource_path?: string | null
  missing_fields: string[]
  status: string
  verification_status: string
}
export type BidTableFillResult = {
  bid_id: number
  table_type: BidTableType
  embed_images: boolean
  fields: string[]
  rows: BidTableRow[]
  row_count: number
  complete_count: number
  pending_count: number
}
export type AssetVerificationResult = {
  bid_id: number
  asset_type: 'certificates' | 'invoices'
  results: Array<{ asset: KnowledgeAsset; asset_id: number; passed: boolean; risks: AssetRisk[] }>
  risks: AssetRisk[]
  summary: { high: number; medium: number; low: number; total: number; passed: number; abnormal: number }
}

export type KnowledgeCategory = {
  category_id: number
  id: number
  name: string
  parent_id: number | null
  sort_order: number
  item_count: number
  create_time: string
  update_time: string
}

export type KnowledgeAsset = {
  asset_id: number
  item_id: number
  asset_type: string
  resource_path: string | null
  category?: string | null
  tags: string[]
  bid_type?: string | null
  bid_scenario?: string | null
  length_level?: string | null
  dimension_tags?: Record<string, unknown> | null
  extend_tags?: string[]
  ai_description: string | null
  annotation?: string | null
  ocr_text?: string | null
  storage_location: string | null
  page_num?: number | null
  source_chapter?: string | null
  image_hash?: string | null
  duplicate_of_asset_id?: number | null
  deep_candidate?: boolean
  deep_status?: 'not_started' | 'pending' | 'processing' | 'completed' | 'failed' | string
  image_width?: number | null
  image_height?: number | null
  person_info?: Record<string, string> | null
  employee_info?: Record<string, string> | null
  employee_role?: string | null
  contract_info?: Record<string, string> | null
  archive_date?: string | null
  structured_info?: {
    category?: string | null
    asset_sub_type?: string | null
    confidence?: number | null
    evidence?: Record<string, string[]>
    fields?: Record<string, string>
    legacy?: Record<string, unknown>
  } | null
  asset_sub_type?: string | null
  expire_date?: string | null
  cert_no?: string | null
  holder_name?: string | null
  match_score?: number
  match_scope?: 'exact' | 'group' | 'category' | string
  expire_status?: 'valid' | 'expiring' | 'expired' | 'unknown'
  expire_label?: string
  reuse_material?: boolean
  item_title?: string | null
  source_name?: string | null
  item_category_id?: number | null
  item_category_name?: string | null
  item_status?: string | null
  create_time: string
}

export type KnowledgeChunk = {
  chunk_id: number
  section_title: string | null
  page_number: number | null
  content: string
}

export type KnowledgeEvidenceReview = {
  status: 'verified' | 'withdrawn'
  subject: string
  identifier: string
  valid_until: string | null
  reviewed_by: string
  reviewed_at: string
}

export type KnowledgeItem = {
  evidence_kind?: string | null
  evidence_review?: KnowledgeEvidenceReview | null
  evidence_suggestions?: Partial<Pick<KnowledgeEvidenceReview, 'subject' | 'identifier' | 'valid_until'>>
  item_id: number
  id: number
  category_id: number | null
  category_name: string | null
  title: string
  file_type: string
  source_name: string
  original_file_path: string | null
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'archived' | string
  process_progress: number
  process_error?: string | null
  version_no: number
  valid_until: string | null
  is_expired: boolean
  tags: string[]
  content_preview: string
  content_text?: string
  asset_count: number
  chunk_count: number
  total_pages?: number
  current_page?: number
  extracted_image_count?: number
  text_char_count?: number
  image_category_stats?: Record<string, number>
  text_page_count?: number
  scan_page_count?: number
  deep_status?: 'not_started' | 'pending' | 'processing' | 'completed' | 'failed' | string
  deep_progress?: number
  deep_total?: number
  deep_error?: string | null
  ocr_recognized_count?: number
  ocr_no_text_count?: number
  ocr_unprocessed_count?: number
  deep_completed_count?: number
  deep_failed_count?: number
  deep_processed_count?: number
  vector_status?: string
  vector_indexed_chunks?: number
  vector_error?: string | null
  vector_updated_at?: string | null
  thumbnail_path?: string | null
  assets?: KnowledgeAsset[]
  chunks?: KnowledgeChunk[]
  versions?: Array<{ version_id: number; item_id: number; title: string; version_no: number; original_file_path: string | null; create_time: string }>
  create_time: string
  update_time: string
}

export type KnowledgeProject = {
  workflow?: { status: string; phase: string; error?: string | null } | null
  project_id: number
  project_name: string
  tender_doc_id: number | null
  bid_doc_id: number | null
  status: string
  format_template?: KnowledgeProjectFormatTemplate
  tender_file_name?: string | null
  bid_file_name?: string | null
  tender_status?: string | null
  bid_status?: string | null
  requirement_count: number
  mapped_count: number
  tender_asset_count?: number
  bid_asset_count?: number
  documents?: {
    tender: KnowledgeProjectDocument
    bid: KnowledgeProjectDocument
  }
  create_time: string
  update_time: string
  maps?: Array<{
    map_id: number
    project_id: number
    requirement_id: number | null
    requirement_content: string
    solution_id: number | null
    solution_content: string | null
    match_score: number
    source_chapter: string | null
    create_time: string
  }>
}

export type KnowledgeProjectFormatTemplate = {
  id: string | null
  name: string | null
  status: 'not_started' | 'pending' | 'processing' | 'completed' | 'failed' | string
  error?: string | null
  rules?: Record<string, unknown>
  source_file_name?: string | null
  updated_at?: string | null
  is_default?: boolean
}

export type ReferenceWordTemplate = {
  id: string
  name: string
  file_name: string
  description: string
  scenario: string
  source_file_name: string
  size: number
  format_rules: Record<string, unknown>
}

export type KnowledgeProjectDocument = {
  processing?: Partial<KnowledgeItem>
  item_id: number | null
  file_name?: string | null
  status?: string | null
  process_error?: string | null
  content_preview?: string
  total_pages?: number
  text_char_count?: number
  chunk_count?: number
  asset_count?: number
  deep_status?: string | null
  update_time?: string | null
  ai_interpretation?: {
    coverage?: { status: string; completed_batches: number; total_batches: number; failed_batches: number; covered_chunks: number; total_chunks: number; covered_chars: number; total_chars: number; percent: number; scope: string } | null
    status?: string | null
    provider?: string | null
    model?: string | null
    summary?: string | null
    analysis?: Record<string, unknown>
    error?: string | null
    source_chunk_count?: number
    result_count?: number
  }
}

export type KnowledgeSearchResult = {
  chunk_id: number
  item_id: number
  title: string
  category_id: number | null
  category_name: string | null
  content: string
  section_title: string | null
  page_number: number | null
  similarity: number
  source_document: string
  tags: string[]
  valid_until: string | null
  assets: KnowledgeAsset[]
}

export type KnowledgeReextractResult = {
  scanned: number
  updated: number
  skipped: number
  failed: number
  details: Array<{
    asset_id?: number
    item_id?: number
    status: 'updated' | 'skipped' | 'failed' | string
    asset_sub_type?: string
    confidence?: number
    reason?: string
  }>
}

export type KnowledgeGraph = {
  nodes: Array<{ id: string; name: string; type: string; category_id: number | null; category_name: string | null; item_id: number | null }>
  links: Array<{ id: string; source: string; target: string; label: string; item_id: number | null }>
}

export type GraphicFormatTemplate = {
  id: string
  name: string
  description: string
  source: 'default' | 'history' | string
}

export type GraphicIllustrationUpload = {
  bid_id: number
  resource_path: string
  public_url: string
  markdown: string
  asset?: KnowledgeAsset | null
}

export type AuditIssue = {
  id: string
  category: 'rejection' | 'scoring' | 'qualification' | 'format' | 'business' | 'suggestion' | string
  severity: 'high' | 'medium' | 'low' | 'suggestion' | string
  title: string
  detail: string
  tender_basis: string
  bid_location: string
  suggestion: string
}

export type AuditResult = {
  audit_id: number
  bid_id: number | null
  tender_file_name: string | null
  bid_file_name: string | null
  status: string
  progress: number
  current_step: string | null
  checks: string[]
  summary: { high_count: number; medium_count: number; low_count: number; suggestion_count: number; total: number; by_category?: Record<string, number> }
  issues: AuditIssue[]
  used_ai: boolean
  ai_error?: string | null
  create_time: string
  update_time: string
  generated_time?: string | null
}

export type KnowledgeImportTask = {
  task_id: string
  dir_path: string
  status: 'pending' | 'processing' | 'completed' | 'completed_with_errors' | 'failed' | string
  total_files: number
  processed_files: number
  success_count: number
  failed_count: number
  skipped_count: number
  current_file: string | null
  failures: Array<{ file_path: string; file_name: string; reason: string }>
  files: Array<{
    import_file_id: number
    file_path: string
    file_name: string
    file_size: number
    project_name: string | null
    item_id: number | null
    status: string
    error_msg: string | null
    create_time: string
    update_time: string
  }>
  create_time: string
  update_time: string
}

type ProjectResponse = { success: true; project: ApiProject; bidId?: number }
type UploadAndParseResponse = ApiEnvelope<{ bid_id: number; file_path: string; file_name: string; file_size: number; parse_status: 'processing'; parse_info: BidParseInfo }>

export type VehicleRoutePlan = {
  city: string
  stops: Array<{ name: string; address: string; formatted_address: string; location: [number, number] }>
  distance_meters: number
  duration_seconds: number
  polyline: [number, number][]
  steps: Array<{ instruction: string; road: string }>
  provider: 'amap'
}

export type DeliveryNetworkPoint = {
  point_id: number
  name: string
  address: string
  city: string
  point_type: string
  enabled: boolean
  create_time: string
  update_time: string
}

export type DeliveryRoutePlan = VehicleRoutePlan & {
  company_name: string
  network_points: Array<DeliveryNetworkPoint & { location: [number, number]; formatted_address: string }>
  demand_points: Array<{ point_id: string; demand_type: string; name: string; address: string; formatted_address: string; location: [number, number] }>
  assignments: Array<{ demand_point_id: string; demand_name: string; network_point_id: string; network_point_name: string; distance_degrees: number }>
  start_point_id: string
  auto_order: boolean
  auto_nearest?: boolean
  nearest_comparison?: Array<{ point_id: string; name: string; distance_meters: number; duration_seconds: number }>
  return_to_start: boolean
  outbound_polyline?: [number, number][]
  return_polyline?: [number, number][]
  route_svg: string
}

export type TenderDemandPointCandidate = {
  point_id: string
  name: string
  address: string
  demand_type: string
  confidence: number
  source_excerpt: string
}

export type UploadProgress = {
  loaded: number
  total: number
  percent: number | null
  bytesPerSecond: number
  stage: 'uploading' | 'processing'
}
export type UploadOptions = {
  signal?: AbortSignal
  onTransferProgress?: (progress: UploadProgress) => void
}

function uploadWithProgress<T>(path: string, form: FormData, onProgress?: (percent: number) => void, options: UploadOptions = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    let settled = false
    const startedAt = performance.now()
    let loaded = 0
    let total = 0
    const abort = () => xhr.abort()
    const finish = (error?: Error, result?: T) => {
      if (settled) return
      settled = true
      options.signal?.removeEventListener('abort', abort)
      if (error) reject(error)
      else resolve(result as T)
    }
    const report = (stage: UploadProgress['stage'], known: boolean) => {
      const percent = stage === 'processing' ? 100 : known && total > 0 ? Math.min(100, Math.floor(loaded / total * 100)) : null
      if (percent !== null) onProgress?.(percent)
      options.onTransferProgress?.({ loaded, total, percent, stage, bytesPerSecond: loaded / Math.max(0.001, (performance.now() - startedAt) / 1000) })
    }
    if (options.signal?.aborted) { finish(new DOMException('上传已取消', 'AbortError')); return }
    xhr.open('POST', API_BASE + path)
    xhr.withCredentials = true
    const token = getAuthToken()
    if (token) xhr.setRequestHeader('Authorization', 'Bearer ' + token)
    xhr.upload.onprogress = event => {
      loaded = event.loaded
      total = event.total
      report('uploading', event.lengthComputable)
    }
    xhr.upload.onload = () => { if (total) loaded = total; report('processing', true) }
    xhr.onload = () => {
      let result: T
      try { result = JSON.parse(xhr.responseText || '{}') as T } catch {
        finish(new Error(xhr.status === 413 ? '文件超过服务器上传上限，请检查应用及代理的大小限制' : '服务器返回了无法识别的上传结果'))
        return
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const error = result as { message?: string; detail?: string }
        finish(new Error(error.message || error.detail || '上传请求失败（HTTP ' + xhr.status + '）'))
        return
      }
      report('processing', true)
      finish(undefined, result)
    }
    xhr.onerror = () => finish(new Error('上传连接中断，请检查网络或后端服务后重试'))
    xhr.onabort = () => finish(new DOMException('上传已取消', 'AbortError'))
    xhr.ontimeout = () => finish(new Error('上传连接超时，请重试'))
    options.signal?.addEventListener('abort', abort, { once: true })
    xhr.send(form)
  })
}

export const wordagentApi = {
  getCaseReference: (bidId: number) => request<ApiEnvelope<CaseReferenceSelection>>(`/bid/${bidId}/case-reference`),
  setCaseReference: (bidId: number, projectId: number | null) => request<ApiEnvelope<CaseReferenceSelection>>(`/bid/${bidId}/case-reference`, {
    method: 'PUT', body: JSON.stringify({ project_id: projectId }),
  }),
  getModelScenes: (signal?: AbortSignal) => request<{ success: true; scenes: ModelScene[] }>('/models/scenes', { signal }),
  setTenderTracking: (id: string, state: string) => request<{ success: true; state: string }>(`/tender-search/notices/${encodeURIComponent(id)}/tracking`, { method: 'PUT', body: JSON.stringify({ state }) }),
  getTenderNotices: (region = '', keyword = '', signal?: AbortSignal) => request<{ success: true; notices: TenderNotice[]; sources: (TenderSourceStatus & { checked_at: string })[]; coverage: string }>(`/tender-search/notices?${new URLSearchParams({ region, keyword })}`, { signal }),
  refreshTenderNotices: (signal?: AbortSignal) => request<{ success: true; sources: TenderSourceStatus[]; total: number; checked_at: string }>('/tender-search/refresh', { method: 'POST', signal }),
  getQwenCatalog: (signal?: AbortSignal) => request<{ success: true; models: QwenCatalogModel[] }>('/models/qwen-catalog', { signal }),
  getDeepseekCatalog: (signal?: AbortSignal) => request<{ success: true; models: QwenCatalogModel[] }>('/models/deepseek-catalog', { signal }),
  saveModelScene: (scene: string, model: string | null) => request<{ success: true; scene: string; model: string }>(`/models/scenes/${scene}`, {
    method: 'PUT', body: JSON.stringify({ model }),
  }),
  getPptSource: (bidId: number, versionId?: number) => request<ApiEnvelope<PptSource>>(`/bid/${bidId}/ppt/source${versionId ? `?version_id=${versionId}` : ''}`),
  generatePpt: (bidId: number, payload: PptGeneratePayload) => requestBlob(`/bid/${bidId}/ppt/generate`, { method: 'POST', body: JSON.stringify(payload) }),
  planVehicleRoute: (payload: { city: string; origin: { name: string; address: string }; stops: Array<{ name: string; address: string }>; destination: { name: string; address: string } }) => request<VehicleRoutePlan>('/maps/vehicle-route', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  listVehicleNetworkPoints: () => request<{ points: DeliveryNetworkPoint[] }>('/maps/network-points'),
  extractTenderDemandPoints: (bidId: number) => request<{ bid_id: number; tender_file_name: string | null; candidates: TenderDemandPointCandidate[]; requires_confirmation: boolean; message: string }>(`/maps/tenders/${bidId}/demand-points`),
  createVehicleNetworkPoint: (payload: { name: string; address: string; city: string; point_type?: string }) => request<{ point: DeliveryNetworkPoint }>('/maps/network-points', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateVehicleNetworkPoint: (pointId: number, payload: Partial<{ name: string; address: string; city: string; point_type: string; enabled: boolean }>) => request<{ point: DeliveryNetworkPoint }>(`/maps/network-points/${pointId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  deleteVehicleNetworkPoint: (pointId: number) => request<{ point_id: number; enabled: boolean }>(`/maps/network-points/${pointId}`, { method: 'DELETE' }),
  planDeliveryRoute: (payload: { company_name: string; city: string; network_points: Array<{ point_id: string; name: string; address: string; point_type: string; city?: string }>; demand_points: Array<{ point_id: string; name: string; address: string; demand_type: string }>; start_point_id?: string; return_to_start: boolean; auto_order: boolean; auto_nearest?: boolean }) => request<DeliveryRoutePlan>('/maps/delivery-route', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  getDeliveryRouteStaticMap: (payload: { center: [number, number]; zoom: number; locations: Array<[number, number]>; width?: number; height?: number }) => requestBlob('/maps/delivery-route/static-map', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  apiBase: API_BASE,
  listProjects: () => request<ProjectListResponse>('/projects'),
  getProject: (projectId: string) => request<ProjectDetailResponse>(`/projects/${projectId}`),
  deleteProject: (projectId: string) => request<ProjectDeleteResponse>(`/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  createProject: (settings: BidSettings) => request<ProjectResponse>('/projects', {
    method: 'POST',
    body: JSON.stringify({ settings }),
  }),
  getBidParseInfo: (bidId: number) => request<ApiEnvelope<BidParseInfo>>(`/bid/${bidId}/parse-info`),
  getBidParseStatus: (bidId: number) => request<ApiEnvelope<BidParseStatus>>(`/bid/${bidId}/parse-status`),
  getBidParseDetail: (bidId: number) => request<ApiEnvelope<BidParseDetail>>(`/bid/${bidId}/parse-detail`),
  updateBidParseInfo: (bidId: number, payload: { parse_mode?: 'auto_upload' | 'paste_text' | null; bid_category?: 'service' | 'goods' | 'engineering' | 'other' }) =>
    request<ApiEnvelope<BidParseInfo>>(`/bid/${bidId}/parse-info`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  savePasteParseContent: (bidId: number, payload: { project_overview: string; score_requirements: string }) =>
    request<ApiEnvelope<BidParseInfo>>(`/bid/${bidId}/paste-parse-content`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  saveParseResultEdit: (bidId: number, payload: { project_overview: string; score_requirements: string }) =>
    request<ApiEnvelope<BidParseInfo>>(`/bid/${bidId}/parse-result/edit`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  reparseBidFile: (bidId: number) => request<ApiEnvelope<{ bid_id: number; file_path: string; parse_status: 'processing'; parse_info: BidParseInfo }>>(`/bid/${bidId}/reparse`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  checkDuplicateBidParseFile: (bidId: number, payload: { tender_file_name: string; tender_file_size?: number | null }) => {
    const query = new URLSearchParams()
    query.set('tender_file_name', payload.tender_file_name)
    if (payload.tender_file_size) query.set('tender_file_size', String(payload.tender_file_size))
    return request<ApiEnvelope<{ matches: DuplicateBidParseMatch[] }>>(`/bid/${bidId}/duplicate-check?${query}`)
  },
  mergeDuplicateBidParse: (bidId: number, sourceBidId: number) => request<ApiEnvelope<BidParseInfo>>(`/bid/${bidId}/merge-parse/${sourceBidId}`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  uploadAndParseBidFileWithProgress: (bidId: number, file: File, onProgress?: (percent: number) => void, options: UploadOptions = {}) => {
    const form = new FormData()
    form.append('file', file)
    return uploadWithProgress<UploadAndParseResponse>(`/bid/${bidId}/upload-and-parse`, form, onProgress, options)
  },
  cleanupAutoParseContent: (bidId: number) => request<ApiEnvelope<BidParseInfo>>(`/bid/${bidId}/auto-parse-content/cleanup`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  cleanupParseContent: (bidId: number) => request<ApiEnvelope<BidParseInfo>>(`/bid/${bidId}/parse-content/cleanup`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  cleanupParseContentBeacon: (bidId: number) => {
    const payload = new Blob([JSON.stringify({})], { type: 'application/json' })
    return navigator.sendBeacon?.(`${API_BASE}/bid/${bidId}/parse-content/cleanup`, payload) ?? false
  },
  completeBidStep2: (bidId: number) => request<ApiEnvelope<{ bid_id: number; current_step: number; parse_info: BidParseInfo }>>(`/bid/${bidId}/step2/complete`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  getBidCatalog: (bidId: number, versionId?: number) => {
    const query = versionId ? `?version_id=${versionId}` : ''
    return request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog${query}`)
  },
  generateBidCatalogStreamUrl: (bidId: number, options: { force?: boolean; style?: string } = {}) => {
    const params = new URLSearchParams()
    const token = getAuthToken()
    if (token) params.set('access_token', token)
    if (options.force) params.set('force', 'true')
    if (options.style) params.set('style', options.style)
    const query = params.toString()
    return `${API_BASE}/bid/${bidId}/catalog/generate-stream${query ? `?${query}` : ''}`
  },
  generateBidCatalog: (bidId: number) => request<ApiEnvelope<{ task_id: string | null; status: string; versions?: CatalogVersion[] }>>(`/bid/${bidId}/catalog/generate`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  regenerateBidCatalog: (bidId: number, style?: string) => request<ApiEnvelope<{ task_id: string | null; status: string }>>(`/bid/${bidId}/catalog/regenerate`, {
    method: 'POST',
    body: JSON.stringify({ style }),
  }),
  updateCatalogChapter: (bidId: number, chapterId: number, chapterTitle: string) => request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/chapter/${chapterId}`, {
    method: 'PUT',
    body: JSON.stringify({ chapter_title: chapterTitle }),
  }),
  createCatalogChapter: (bidId: number, payload: { version_id: number; parent_id: number; chapter_title: string; level: 1 | 2 | 3; sort_order?: number }) =>
    request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/chapter`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteCatalogChapter: (bidId: number, chapterId: number) => request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/chapter/${chapterId}`, {
    method: 'DELETE',
  }),
  moveCatalogChapter: (bidId: number, chapterId: number, moveType: 'up' | 'down' | 'upgrade' | 'downgrade') =>
    request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/chapter/${chapterId}/move`, {
      method: 'PUT',
      body: JSON.stringify({ move_type: moveType }),
    }),
  setFinalCatalogVersion: (bidId: number, versionId: number) => request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/set-final`, {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  }),
  replaceCatalogVersionTree: (bidId: number, versionId: number, catalogContent: CatalogNode[]) =>
    request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/version/${versionId}/tree`, {
      method: 'PUT',
      body: JSON.stringify({ catalog_content: catalogContent }),
    }),
  expandCatalogChapter: (bidId: number, chapterId: number, instruction = '') => request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/chapter/${chapterId}/expand`, {
    method: 'POST',
    body: JSON.stringify({ instruction }),
  }),
  optimizeCatalogChapter: (bidId: number, chapterId: number, instruction = '') => request<ApiEnvelope<BidCatalogResponse>>(`/bid/${bidId}/catalog/chapter/${chapterId}/optimize`, {
    method: 'POST',
    body: JSON.stringify({ instruction }),
  }),
  downloadBidCatalogUrl: (bidId: number, versionId: number) => `${API_BASE}/bid/${bidId}/catalog/download?version_id=${versionId}`,
  generateMermaidCode: (bidId: number, payload: { source_text: string; chart_type: string; chart_title?: string | null }) => request<ApiEnvelope<{ bid_id: number; chart_title: string; chart_type: string; mermaid_code: string; used_api: boolean; provider: string; model: string; error?: string | null }>>(`/bid/${bidId}/mermaid/generate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  renderMermaidAsset: (bidId: number, payload: { chart_title: string; chart_type: string; mermaid_code: string; source_text?: string | null; chart_image_data?: string | null; save_to_rag?: boolean }) => request<ApiEnvelope<MermaidAsset>>(`/bid/${bidId}/mermaid/render`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  listMermaidAssets: (bidId: number) => request<ApiEnvelope<{ assets: MermaidAsset[]; total: number }>>(`/bid/${bidId}/mermaid/list`),
  saveMermaidToRag: (bidId: number, assetId: number) => request<ApiEnvelope<MermaidAsset>>(`/bid/${bidId}/mermaid/save-to-rag`, {
    method: 'POST',
    body: JSON.stringify({ asset_id: assetId }),
  }),
  generateGraphicSection: (bidId: number, payload: { chapter_id: number; chapter_ids?: number[]; word_limit: number; length_target?: number; length_mode?: 'shrink' | 'expand' | 'default'; rag_enable: boolean; image_strategy: 'none' | 'rag_auto' | 'project_only' | 'valid_only' | 'category_only'; asset_sub_type?: string[]; priority_expire?: boolean; exclude_ids?: number[]; chapter_theme?: string | null; format_template_id?: string | null; selected_asset_ids?: number[]; selected_chunk_ids?: number[]; bid_scenario?: string | null; length_level?: string | null }) => request<ApiEnvelope<GraphicSection | GraphicSectionBatchResult>>(`/bid/${bidId}/graphic-section/generate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  auditGraphicAssets: (bidId: number) => request<ApiEnvelope<AssetAuditResult>>(`/bid/${bidId}/audit-assets`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  fillBidTable: (bidId: number, payload: { table_type: BidTableType; embed_images?: boolean; asset_ids?: number[] }) => request<ApiEnvelope<BidTableFillResult>>(`/bid/${bidId}/table/fill`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  verifyCertificates: (bidId: number, assetIds: number[] = []) => request<ApiEnvelope<AssetVerificationResult>>(`/bid/${bidId}/certificates/verify`, {
    method: 'POST',
    body: JSON.stringify({ asset_ids: assetIds }),
  }),
  verifyInvoices: (bidId: number, assetIds: number[] = []) => request<ApiEnvelope<AssetVerificationResult>>(`/bid/${bidId}/invoices/verify`, {
    method: 'POST',
    body: JSON.stringify({ asset_ids: assetIds }),
  }),
  ocrInvoice: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ApiEnvelope<InvoiceOcrResult>>('/invoice/ocr', { method: 'POST', body: form })
  },
  getChapterMaterials: (bidId: number, chapterId: number, params: { bid_scenario?: string | null; length_level?: string | null; chapter_theme?: string | null } = {}) => {
    const query = new URLSearchParams()
    if (params.bid_scenario) query.set('bid_scenario', params.bid_scenario)
    if (params.length_level) query.set('length_level', params.length_level)
    if (params.chapter_theme) query.set('chapter_theme', params.chapter_theme)
    return request<ApiEnvelope<ChapterMaterials>>(`/bid/${bidId}/catalog/chapter/${chapterId}/materials${query.toString() ? `?${query}` : ''}`)
  },
  saveChapterMaterials: (bidId: number, chapterId: number, payload: { selected_asset_ids: number[]; selected_chunk_ids: number[]; bid_scenario?: string | null; length_level?: string | null }) => request<ApiEnvelope<ChapterMaterials>>(`/bid/${bidId}/catalog/chapter/${chapterId}/materials`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  matchAllChapterMaterials: (bidId: number, payload: { chapter_ids?: number[]; bid_scenario?: string | null; length_level?: string | null } = {}) => request<ApiEnvelope<{ chapters: ChapterMaterials[]; total_chapters: number; matched_chapters: number; total_materials: number }>>(`/bid/${bidId}/catalog/materials/match-all`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  generateGraphicSectionStreamUrl: (bidId: number, payload: { chapter_id: number; chapter_ids?: number[]; only_missing?: boolean; bidder_name?: string; word_limit: number; length_target?: number; length_mode?: 'shrink' | 'expand' | 'default'; rag_enable: boolean; image_strategy: 'none' | 'rag_auto' | 'project_only' | 'valid_only' | 'category_only'; asset_sub_type?: string[]; priority_expire?: boolean; exclude_ids?: number[]; chapter_theme?: string | null; format_template_id?: string | null; selected_asset_ids?: number[]; selected_chunk_ids?: number[]; bid_scenario?: string | null; length_level?: string | null }) => {
    const query = new URLSearchParams()
    const token = getAuthToken()
    if (token) query.set('access_token', token)
    query.set('chapter_id', String(payload.chapter_id))
    if (payload.bidder_name?.trim()) query.set('bidder_name', payload.bidder_name.trim())
    if (payload.chapter_ids?.length) query.set('chapter_ids', payload.chapter_ids.join(','))
    if (payload.only_missing) query.set('only_missing', 'true')
    query.set('word_limit', String(payload.word_limit))
    if (payload.length_target !== undefined) query.set('length_target', String(payload.length_target))
    if (payload.length_mode) query.set('length_mode', payload.length_mode)
    query.set('rag_enable', String(payload.rag_enable))
    query.set('image_strategy', payload.image_strategy)
    if (payload.asset_sub_type?.length) query.set('asset_sub_type', payload.asset_sub_type.join(','))
    if (payload.priority_expire) query.set('priority_expire', 'true')
    if (payload.exclude_ids?.length) query.set('exclude_ids', payload.exclude_ids.join(','))
    if (payload.chapter_theme) query.set('chapter_theme', payload.chapter_theme)
    if (payload.format_template_id) query.set('format_template_id', payload.format_template_id)
    if (payload.selected_asset_ids?.length) query.set('selected_asset_ids', payload.selected_asset_ids.join(','))
    if (payload.selected_chunk_ids?.length) query.set('selected_chunk_ids', payload.selected_chunk_ids.join(','))
    if (payload.bid_scenario) query.set('bid_scenario', payload.bid_scenario)
    if (payload.length_level) query.set('length_level', payload.length_level)
    return `${API_BASE}/bid/${bidId}/graphic-section/generate-stream?${query}`
  },
  listGraphicFormatTemplates: (bidId: number) => request<ApiEnvelope<{ templates: GraphicFormatTemplate[] }>>(`/bid/${bidId}/graphic-section/templates`),
  uploadGraphicIllustration: (bidId: number, file: File, payload: { caption?: string; category?: string; save_to_knowledge?: boolean }) => {
    const form = new FormData()
    form.append('file', file)
    if (payload.caption) form.append('caption', payload.caption)
    if (payload.category) form.append('category', payload.category)
    form.append('save_to_knowledge', String(Boolean(payload.save_to_knowledge)))
    return request<ApiEnvelope<GraphicIllustrationUpload>>(`/bid/${bidId}/graphic-section/upload-image`, { method: 'POST', body: form })
  },
  getGraphicSection: (bidId: number, chapterId: number, forExport = false, bidderName?: string) => {
    const query = new URLSearchParams()
    if (forExport) query.set('for_export', 'true')
    if (bidderName?.trim()) query.set('bidder_name', bidderName.trim())
    return request<ApiEnvelope<GraphicSection>>('/bid/' + bidId + '/graphic-section/' + chapterId + (query.size ? '?' + query.toString() : ''))
  },
  saveGraphicSection: (bidId: number, payload: { expected_revision: number; chapter_id: number; markdown: string; ref_chart_ids?: number[]; ref_image_asset_ids?: number[]; format_template_id?: string | null }) => request<ApiEnvelope<GraphicSection>>(`/bid/${bidId}/graphic-section/save`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  downloadGraphicDocxUrl: (bidId: number, bidderName?: string) => API_BASE + '/bid/' + bidId + '/graphic-section/download' + (bidderName?.trim() ? '?bidder_name=' + encodeURIComponent(bidderName.trim()) : ''),
  getBlindMode: (bidId: number) => request<ApiEnvelope<{ blind: boolean }>>('/bid/' + bidId + '/graphic-section/blind-mode'),
  checkBlindDocx: (bidId: number, blob: Blob, terms: string, force: boolean) => {
    const form = new FormData()
    form.append('file', blob, 'export.docx')
    form.append('terms', terms)
    form.append('force', String(force))
    return request<ApiEnvelope<BlindCheckReport>>('/bid/' + bidId + '/graphic-section/blind-check', { method: 'POST', body: form })
  },
  finalizeGraphicDocxToc: (bidId: number, blob: Blob) => requestBlob(`/bid/${bidId}/graphic-section/finalize-docx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
    body: blob,
  }),
  getKnowledgeDiagnostics: () => request<ApiEnvelope<{ paddleocr_available: boolean; chromadb_available: boolean; local_vector_fallback: boolean; vector_backend?: string; vector_store_enabled?: boolean; vector_store_available?: boolean; vector_store_error?: string | null; embedding_provider?: string; embedding_model?: string; embedding_dimension?: number; milvus_uri?: string; milvus_collection_knowledge?: string; ocr_status?: string; ocr_install_command?: string; ocr_install_note?: string }>>('/knowledge/diagnostics'),
  listKnowledgeCategories: () => request<ApiEnvelope<KnowledgeCategory[]>>('/knowledge/categories'),
  createKnowledgeCategory: (payload: { name: string; parent_id?: number | null; sort_order?: number }) => request<ApiEnvelope<KnowledgeCategory>>('/knowledge/categories', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateKnowledgeCategory: (categoryId: number, payload: { name?: string; parent_id?: number | null; sort_order?: number }) => request<ApiEnvelope<KnowledgeCategory>>(`/knowledge/categories/${categoryId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  deleteKnowledgeCategory: (categoryId: number) => request<ApiEnvelope<null>>(`/knowledge/categories/${categoryId}`, { method: 'DELETE' }),
  uploadKnowledgeItems: (files: File[], payload: { category_id?: number | null; tags?: string; valid_until?: string | null }) => {
    const form = new FormData()
    files.forEach(file => form.append('files', file))
    if (payload.category_id) form.append('category_id', String(payload.category_id))
    if (payload.tags) form.append('tags', payload.tags)
    if (payload.valid_until) form.append('valid_until', payload.valid_until)
    return request<ApiEnvelope<{ items: KnowledgeItem[]; failures: Array<{ file_name: string; reason: string }>; success_count: number; failure_count: number }>>('/knowledge/items/upload', {
      method: 'POST',
      body: form,
    })
  },
  uploadKnowledgeItemsWithProgress: (files: File[], payload: { category_id?: number | null; tags?: string; valid_until?: string | null }, onProgress?: (percent: number) => void) => {
    const form = new FormData()
    files.forEach(file => form.append('files', file))
    if (payload.category_id) form.append('category_id', String(payload.category_id))
    if (payload.tags) form.append('tags', payload.tags)
    if (payload.valid_until) form.append('valid_until', payload.valid_until)
    return uploadWithProgress<ApiEnvelope<{ items: KnowledgeItem[]; failures: Array<{ file_name: string; reason: string }>; success_count: number; failure_count: number }>>('/knowledge/items/upload', form, onProgress)
  },
  uploadKnowledgeEvidence: (files: File[], kind: string, onProgress?: (percent: number) => void) => {
    const form = new FormData()
    files.forEach(file => form.append('files', file))
    form.append('evidence_kind', kind)
    return uploadWithProgress<ApiEnvelope<{ items: KnowledgeItem[]; failures: Array<{ file_name: string; reason: string }>; success_count: number; failure_count: number }>>('/knowledge/evidence/upload', form, onProgress)
  },
  reviewKnowledgeEvidence: (itemId: number, payload: { verified: boolean; subject: string; identifier: string; valid_until: string | null }) => request<ApiEnvelope<KnowledgeItem>>(`/knowledge/evidence/${itemId}/review`, { method: 'PUT', body: JSON.stringify(payload) }),
  uploadKnowledgeProject: (projectName: string, tenderFile: File, bidFile: File, onProgress?: (percent: number) => void) => {
    const form = new FormData()
    form.append('project_name', projectName)
    form.append('tender_file', tenderFile)
    form.append('bid_file', bidFile)
    return uploadWithProgress<ApiEnvelope<KnowledgeProject>>('/knowledge/projects/upload', form, onProgress)
  },
  listKnowledgeProjects: (limit = 200) => request<ApiEnvelope<KnowledgeProject[]>>(`/knowledge/projects?limit=${limit}`),
  resumeKnowledgeProjectAnalysis: (projectId: number, signal?: AbortSignal) => request<ApiEnvelope<KnowledgeProject>>(`/knowledge/projects/${projectId}/interpret`, { method: 'POST', signal }),
  getKnowledgeProject: (projectId: number) => request<ApiEnvelope<KnowledgeProject>>(`/knowledge/projects/${projectId}`),
  extractKnowledgeProjectTemplate: (projectId: number) => request<ApiEnvelope<KnowledgeProject>>(`/knowledge/projects/${projectId}/template/extract`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  getKnowledgeProjectTemplate: (projectId: number) => request<ApiEnvelope<KnowledgeProjectFormatTemplate & { project_id: number }>>(`/knowledge/projects/${projectId}/template`),
  downloadKnowledgeProjectTemplateUrl: (projectId: number) => `${API_BASE}/knowledge/projects/${projectId}/template/download`,
  listReferenceWordTemplates: () => request<ApiEnvelope<{ templates: ReferenceWordTemplate[] }>>('/format/word/reference-templates'),
  downloadReferenceWordTemplateUrl: (templateId: string) => `${API_BASE}/format/word/reference-templates/${encodeURIComponent(templateId)}/download`,
  activateKnowledgeProjectTemplate: (projectId: number) => request<ApiEnvelope<KnowledgeProjectFormatTemplate & { project_id: number }>>(`/knowledge/projects/${projectId}/template/activate`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  matchKnowledgeProject: (projectId: number, payload: { min_score?: number; limit?: number } = {}) => request<ApiEnvelope<KnowledgeProject & { matched_count: number; matches: NonNullable<KnowledgeProject['maps']> }>>(`/knowledge/projects/${projectId}/match`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  renameKnowledgeProject: (projectId: number, projectName: string) => request<ApiEnvelope<KnowledgeProject>>(`/knowledge/projects/${projectId}`, {
    method: 'PUT',
    body: JSON.stringify({ project_name: projectName }),
  }),
  deleteKnowledgeProject: (projectId: number) => request<ApiEnvelope<null>>(`/knowledge/projects/${projectId}`, { method: 'DELETE' }),
  reuseKnowledgeProject: (projectId: number, payload: { target_bid_id?: number | null; target_project_id?: number | null; map_ids?: number[] } = {}) => request<ApiEnvelope<Record<string, unknown>>>(`/knowledge/projects/${projectId}/reuse`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  startKnowledgeLocalImport: (payload: { dir_path?: string | null } = {}) => request<ApiEnvelope<{ task_id: string; dir_path: string; total_files: number; status: string }>>('/knowledge/batch-import-local', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  getKnowledgeLocalImportStatus: (taskId: string) => request<ApiEnvelope<KnowledgeImportTask>>(`/knowledge/batch-import/${taskId}/status`),
  retryKnowledgeLocalImportFailed: (taskId: string) => request<ApiEnvelope<KnowledgeImportTask>>(`/knowledge/batch-import/${taskId}/retry-failed`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  listKnowledgeItems: (params: { category_id?: number | null; keyword?: string; status?: string; file_type?: string; bid_type?: string; bid_scenario?: string; length_level?: string; include_expired?: boolean; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.category_id) query.set('category_id', String(params.category_id))
    if (params.keyword) query.set('keyword', params.keyword)
    if (params.status) query.set('status', params.status)
    if (params.file_type) query.set('file_type', params.file_type)
    if (params.bid_type) query.set('bid_type', params.bid_type)
    if (params.bid_scenario) query.set('bid_scenario', params.bid_scenario)
    if (params.length_level) query.set('length_level', params.length_level)
    if (params.include_expired !== undefined) query.set('include_expired', String(params.include_expired))
    if (params.limit) query.set('limit', String(params.limit))
    return request<ApiEnvelope<{ items: KnowledgeItem[]; total: number }>>(`/knowledge/items${query.toString() ? `?${query}` : ''}`)
  },
  listKnowledgeAssets: (params: { category_id?: number | null; keyword?: string; image_category?: string; employee_role?: string; archive_year?: string; bid_type?: string; bid_scenario?: string; length_level?: string; asset_sub_type?: string; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.category_id) query.set('category_id', String(params.category_id))
    if (params.keyword) query.set('keyword', params.keyword)
    if (params.image_category) query.set('image_category', params.image_category)
    if (params.employee_role) query.set('employee_role', params.employee_role)
    if (params.archive_year) query.set('archive_year', params.archive_year)
    if (params.bid_type) query.set('bid_type', params.bid_type)
    if (params.bid_scenario) query.set('bid_scenario', params.bid_scenario)
    if (params.length_level) query.set('length_level', params.length_level)
    if (params.asset_sub_type) query.set('asset_sub_type', params.asset_sub_type)
    if (params.limit) query.set('limit', String(params.limit))
    return request<ApiEnvelope<{ assets: KnowledgeAsset[]; total: number }>>(`/knowledge/assets${query.toString() ? `?${query}` : ''}`)
  },
  getKnowledgeAsset: (assetId: number) => request<ApiEnvelope<KnowledgeAsset>>(`/knowledge/assets/${assetId}`),
  getKnowledgeDimensions: () => request<ApiEnvelope<{ dimensions: Array<{ key: string; name: string; options: Array<{ value: string; label: string; aliases?: string[] }> }>; legacy_dimensions: Array<{ key: string; alias_of?: string; options: Array<{ value: string; label: string }> }>; extensible: boolean }>>('/knowledge/dimensions'),
  batchTagKnowledgeAssets: (payload: { category?: string | null; asset_sub_type?: string | null; bid_scenario?: string | null; length_level?: string | null; source?: string | null; source_from?: string | null; source_to?: string | null; item_ids?: number[] | null; asset_ids?: number[] | null; overwrite_manual?: boolean; only_missing?: boolean; limit?: number } = {}) => request<ApiEnvelope<{ scanned: number; updated: number; success: number; skipped: number; failed: number; details: Array<Record<string, unknown>> }>>('/knowledge/assets/batch-tag', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateKnowledgeAssetDimensions: (assetId: number, payload: { bid_scenario?: string | null; length_level?: string | null; dimension_tags?: Record<string, unknown> | null }) => request<ApiEnvelope<KnowledgeAsset>>(`/knowledge/${assetId}/dimensions`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  updateKnowledgeAssetAnnotation: (assetId: number, annotation: string) => request<ApiEnvelope<KnowledgeAsset>>(`/knowledge/assets/${assetId}/annotation`, {
    method: 'PUT',
    body: JSON.stringify({ annotation }),
  }),
  updateKnowledgeAssetMetadata: (assetId: number, payload: { bid_type?: string | null; length_level?: string | null; extend_tags?: string[] }) => request<ApiEnvelope<KnowledgeAsset>>(`/knowledge/assets/${assetId}/metadata`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  regenerateKnowledgeAssetAnnotations: (payload: { asset_ids?: number[] | null; only_missing?: boolean; limit?: number } = {}) => request<ApiEnvelope<{ updated: number; scanned: number; qwen_ocr_requested?: number; qwen_ocr_succeeded?: number; qwen_ocr_failed?: number; qwen_ocr_model?: string }>>('/knowledge/assets/regenerate-annotations', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  cleanupKnowledgeAssets: () => request<ApiEnvelope<{ removed: number; linked_duplicates: number; updated_meta: number; cleared_bad_info?: number; scanned: number }>>('/knowledge/assets/cleanup', {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  reextractKnowledgeItemAssets: (itemId: number, payload: { asset_ids?: number[] | null; asset_sub_type?: string | null; only_missing?: boolean; limit?: number } = {}) => request<ApiEnvelope<KnowledgeReextractResult>>('/knowledge/' + itemId + '/reextract', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  reextractKnowledgeAssetsBatch: (payload: { item_ids?: number[] | null; asset_ids?: number[] | null; asset_sub_type?: string | null; only_missing?: boolean; limit?: number } = {}) => request<ApiEnvelope<KnowledgeReextractResult>>('/knowledge/reextract/batch', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  reidentifyPersonnelAssets: (limit = 2000) => request<ApiEnvelope<KnowledgeReextractResult & { recognized_count: number; category_corrected_count: number; structured_completed_count: number }>>('/knowledge/assets/reidentify-personnel', {
    method: 'POST',
    body: JSON.stringify({ limit, only_missing: false }),
  }),
  getKnowledgeItem: (itemId: number) => request<ApiEnvelope<KnowledgeItem>>(`/knowledge/items/${itemId}`),
  deepExtractKnowledgeItem: (itemId: number) => request<ApiEnvelope<Partial<KnowledgeItem> & { item_id: number; queued?: boolean; message?: string; action?: string }>>(`/knowledge/items/${itemId}/deep-extract`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  deepExtractKnowledgeItemsBatch: (itemIds: number[]) => request<ApiEnvelope<{
    total: number
    queued: number
    failed: number
    items: Array<Record<string, unknown>>
  }>>('/knowledge/items/deep-extract/batch', {
    method: 'POST',
    body: JSON.stringify({ item_ids: itemIds }),
  }),
  updateKnowledgeItem: (itemId: number, payload: { title?: string; category_id?: number | null; tags?: string[]; valid_until?: string | null; status?: string }) => request<ApiEnvelope<KnowledgeItem>>(`/knowledge/items/${itemId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  deleteKnowledgeItem: (itemId: number) => request<ApiEnvelope<null>>(`/knowledge/items/${itemId}`, { method: 'DELETE' }),
  retryKnowledgeItem: (itemId: number) => request<ApiEnvelope<KnowledgeItem>>(`/knowledge/items/${itemId}/retry`, {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  getKnowledgeItemStatus: (itemId: number) => request<ApiEnvelope<Pick<KnowledgeItem, 'item_id' | 'status' | 'process_progress' | 'process_error' | 'is_expired' | 'total_pages' | 'current_page' | 'extracted_image_count' | 'text_char_count' | 'image_category_stats' | 'text_page_count' | 'scan_page_count' | 'deep_status' | 'deep_progress' | 'deep_total' | 'deep_error' | 'deep_completed_count' | 'deep_failed_count' | 'deep_processed_count' | 'ocr_recognized_count' | 'ocr_no_text_count' | 'ocr_unprocessed_count' | 'vector_status' | 'vector_indexed_chunks' | 'vector_error' | 'vector_updated_at' | 'chunk_count' | 'asset_count' | 'update_time'>>>(`/knowledge/items/${itemId}/status`),
  searchKnowledge: (payload: { query: string; category_id?: number | null; image_category?: string | null; bid_type?: string | null; bid_scenario?: string | null; length_level?: string | null; include_images?: boolean; include_expired?: boolean; limit?: number }) => request<ApiEnvelope<{ query: string; total: number; results: KnowledgeSearchResult[]; strategy: string; chromadb_enabled: boolean; vector_backend?: string; vector_store_enabled?: boolean; embedding_provider?: string }>>('/knowledge/search', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  getKnowledgeGraph: (categoryId?: number | null) => request<ApiEnvelope<KnowledgeGraph>>(`/knowledge/graph${categoryId ? `?category_id=${categoryId}` : ''}`),
  batchKnowledgeItems: (payload: { item_ids: number[]; action: 'delete' | 'move' | 'expire' | 'restore'; category_id?: number | null }) => request<ApiEnvelope<{ affected: number }>>('/knowledge/batch', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  uploadAuditTender: (file: File, auditId?: number | null) => {
    const form = new FormData()
    form.append('file', file)
    if (auditId) form.append('audit_id', String(auditId))
    return request<ApiEnvelope<{ audit_id: number; file_name: string; file_size: number; role: string; status: string }>>('/audit/upload-tender', { method: 'POST', body: form })
  },
  uploadAuditBid: (file: File, auditId?: number | null) => {
    const form = new FormData()
    form.append('file', file)
    if (auditId) form.append('audit_id', String(auditId))
    return request<ApiEnvelope<{ audit_id: number; file_name: string; file_size: number; role: string; status: string }>>('/audit/upload-bid', { method: 'POST', body: form })
  },
  startAuditUrl: () => `${API_BASE}/audit/start`,
  getAuditResult: (auditId: number) => request<ApiEnvelope<AuditResult>>(`/audit/${auditId}/result`),
  exportAuditUrl: (auditId: number) => `${API_BASE}/audit/${auditId}/export`,
}
