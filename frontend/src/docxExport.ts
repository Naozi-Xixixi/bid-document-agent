import Docxtemplater from 'docxtemplater'
import ImageModule from 'docxtemplater-image-module-free'
import PizZip from 'pizzip'
import { getAuthToken } from './auth'
import { BID_TEMPLATE_CONFIGS, getBidTemplateById, type BidTemplateId } from './utils/bidTemplateCatalog'
import { getCustomWordTemplate } from './utils/customWordTemplateStore'
import { type BlindCheckReport, type BidCatalogResponse, type BidParseDetail, type CatalogNode, type GraphicSection, type KnowledgeAsset, wordagentApi } from './wordagentApi'

export type DocxExportTemplateId = BidTemplateId | `custom:${string}`

export const DOCX_EXPORT_TEMPLATES = BID_TEMPLATE_CONFIGS

const TEMPLATE_URLS: Record<BidTemplateId, string> = {
  'default-business': getBidTemplateById('default-business').filePath,
  'compact-technical': getBidTemplateById('compact-technical').filePath,
  'formal-government': getBidTemplateById('formal-government').filePath,
  'enterprise-header': getBidTemplateById('enterprise-header').filePath,
}

type FlatChapter = CatalogNode & { number: string }
export type ExportProgressPhase = 'loading' | 'data' | 'images' | 'rendering' | 'complete' | 'fallback'
export type ExportProgress = { phase: ExportProgressPhase; message: string }
type ExportTextBlock = { text: string }
type ExportImage = { image: string; caption: string }
type ExportContentBlock = { heading1?: ExportTextBlock[]; heading2?: ExportTextBlock[]; heading3?: ExportTextBlock[]; body?: ExportTextBlock[]; image?: ExportImage[] }
type ExportChapter = { chapter_heading: string; chapter_outline_level: string; chapter_heading1: ExportTextBlock[]; chapter_heading2: ExportTextBlock[]; chapter_heading3: ExportTextBlock[]; content_blocks: ExportContentBlock[] }
type ExportChapterRow = { number: string; title: string; word_count: string; status: string }
type ExportTableRow = Record<string, string | ExportImage[]>
type ExportData = { bid_title: string; bid_id: string; project_number: string; bidder_name: string; bid_date: string; procurement_method?: string; document_kind?: string; price_score_conflict?: string; template_name: string; include_toc: boolean; chapter_rows: ExportChapterRow[]; qualification_rows: ExportTableRow[]; personnel_rows: ExportTableRow[]; vehicle_rows: ExportTableRow[]; equipment_rows: ExportTableRow[]; performance_rows: ExportTableRow[]; financial_rows: ExportTableRow[]; has_qualification_rows: boolean; has_personnel_rows: boolean; has_vehicle_rows: boolean; has_equipment_rows: boolean; has_performance_rows: boolean; has_financial_rows: boolean; chapters: ExportChapter[] }
type PreparedMedia = { key: string; source: string; dataUrl: string; width: number; height: number; caption: string; assetId?: number; chartId?: number; kind: 'image' | 'chart' }
type ExportRichRun = { text: string; bold?: boolean; italic?: boolean; underline?: boolean; strike?: boolean }
type ExportRichParagraph = { plain: string; runs: ExportRichRun[]; align?: 'left' | 'center' | 'right' }
type TenderCellParagraph = { runs: ExportRichRun[]; align: 'left' | 'center' | 'right' | 'both'; left_indent?: number; right_indent?: number }
type TenderCellLayout = {
  row: number
  col: number
  rowspan: number
  colspan: number
  source_text?: string
  paragraphs?: TenderCellParagraph[]
}
type TenderTableLayout = { widths: number[]; heights: number[]; cells: TenderCellLayout[]; header?: boolean; source_page?: number }
type ExportTableDefinition = { marker: string; rows: string[][]; layout?: TenderTableLayout }
type ExportBuildResult = { data: ExportData; sizes: Map<string, [number, number]>; richParagraphs: ExportRichParagraph[]; tables: ExportTableDefinition[] }

export function getTenderFormatGaps(catalog: BidCatalogResponse, versionId?: number): string[] {
  const required = catalog.context.required_format_sections || []
  if (!required.length) return []
  const version = resolveExportVersion(catalog, versionId)
  const titles = new Set((version.catalog_content || []).map(chapter => cleanChapterTitle(chapter.title).replace(/\s/g, '')))
  return required.filter(title => !titles.has(cleanChapterTitle(title).replace(/\s/g, '')))
}

const templateCache = new Map<string, ArrayBuffer>()

function report(onProgress: ((progress: ExportProgress) => void) | undefined, phase: ExportProgressPhase, message: string) {
  onProgress?.({ phase, message })
}

function safeText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(item => safeText(item)).filter(Boolean).join('、')
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}:${safeText(item)}`).filter(Boolean).join('；')
  return ''
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function readValue(source: unknown, keys: string[]): string {
  const record = asRecord(source)
  for (const key of keys) {
    const value = safeText(record[key]).trim()
    if (value) return value
  }
  return ''
}

function readAssetValue(asset: KnowledgeAsset, keys: string[]): string {
  return readValue(asset, keys)
    || readValue(asset.structured_info?.fields, keys)
    || readValue(asset.employee_info, keys)
    || readValue(asset.person_info, keys)
    || readValue(asset.contract_info, keys)
}

function toList(value: unknown): unknown[] {
  if (Array.isArray(value)) return value
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed : [parsed]
    } catch {
      return [value]
    }
  }
  return []
}

function assetCaption(asset: KnowledgeAsset): string {
  return asset.asset_sub_type && asset.asset_sub_type !== '其他附件'
    ? asset.asset_sub_type
    : asset.category || `知识库图片 #${asset.asset_id}`
}

function assetSearchText(asset: KnowledgeAsset): string {
  return [asset.category, asset.item_category_name, asset.employee_role, asset.item_title, asset.source_name, asset.ai_description, asset.annotation, asset.ocr_text, asset.asset_sub_type, asset.cert_no, asset.holder_name, JSON.stringify(asset.structured_info || {}), ...(asset.tags || [])].filter(Boolean).join(' ').toLowerCase()
}

function flattenCatalog(nodes: CatalogNode[], prefix: number[] = []): FlatChapter[] {
  return nodes.flatMap((node, index) => {
    const parts = [...prefix, index + 1]
    return [{ ...node, number: parts.join('.') }, ...flattenCatalog(node.children || [], parts)]
  })
}

export function selectExportChapters(nodes: CatalogNode[], selectedChapterIds?: number[]): FlatChapter[] {
  const allChapters = flattenCatalog(nodes)
  if (!selectedChapterIds?.length) return allChapters
  const selected = new Set(selectedChapterIds)
  const selectedNumbers = allChapters.filter(chapter => selected.has(chapter.chapter_id)).map(chapter => chapter.number)
  return allChapters.filter(chapter => selectedNumbers.some(number =>
    number === chapter.number || number.startsWith(chapter.number + '.') || chapter.number.startsWith(number + '.'),
  ))
}

export function resolveExportVersion(catalog: BidCatalogResponse, versionId?: number) {
  const version = versionId === undefined
    ? catalog.versions.find(item => item.is_final) || catalog.versions[0]
    : catalog.versions.find(item => item.version_id === versionId)
  if (!version) throw new Error('所选目录版本不存在，请刷新目录后重试')
  return version
}

function cleanChapterTitle(title: string) {
  return (title || '').replace(/^\s*(?:第[一二三四五六七八九十百千万\d]+[章节篇部分]\s*)+/, '').replace(/^\s*\d+(?:\.\d+)+\s*/, '').replace(/^\s*\d+(?:[、.．]\s*|\s+)/, '').trim()
}

function stripMarkdown(markdown: string) {
  return (markdown || '')
    .replace(/\[\[MERMAID_ASSET:\d+\]\]/g, '')
    .replace(/\[\[KNOWLEDGE_IMAGE:\d+\]\]/g, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '• ')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/\|/g, ' ｜ ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function normalizeInlineMarkdown(text: string, preserveBold = false) {
  return text
    .replace(/\$\\(?:rightarrow|to)\$/g, '→')
    .replace(/\\(?:rightarrow|to)\b/g, '→')
    .replace(/^\s*>\s?/gm, '')
    .replace(/\*\*([^*\n]+)\*\*|__([^_\n]+)__/g, (_match, stars, underscores) => {
      const content = stars ?? underscores
      return preserveBold ? '[[BID_BOLD_START]]' + content + '[[BID_BOLD_END]]' : content
    })
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s*[-*+]\s+/, '• ')
    .replace(/\|/g, ' ｜ ')
    .trim()
}

function parseMarkdownTable(value: string): string[][] | null {
  const lines = value.split('\n').map(line => line.trim()).filter(Boolean)
  if (lines.length < 2 || !lines[0].startsWith('|')) return null
  const logicalRows: string[] = []
  for (const line of lines) {
    if (line.startsWith('|') && (!logicalRows.length || logicalRows[logicalRows.length - 1].endsWith('|'))) logicalRows.push(line)
    else if (logicalRows.length) logicalRows[logicalRows.length - 1] += '\n' + line
  }
  const rows = logicalRows
    .filter(line => !/^\|?\s*:?-{3,}/.test(line))
    .map(line => line.replace(/^\||\|$/g, '').split('|').map(cell => normalizeInlineMarkdown(cell.trim(), true)))
  return rows.length >= 2 ? rows : null
}

function isPlaceholderImageUrl(value: string) {
  return /^https?:\/\/(?:via\.placeholder\.com|placehold\.co|dummyimage\.com)\//i.test(value.trim())
}

function richRuns(value: string): ExportRichRun[] {
  const runs: ExportRichRun[] = []
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|~~[^~]+~~|<u>[^<]+<\/u>)/g
  let cursor = 0
  for (const match of value.matchAll(pattern)) {
    const start = match.index || 0
    if (start > cursor) runs.push({ text: value.slice(cursor, start) })
    const token = match[0]
    const isBold = token.startsWith('**') || token.startsWith('__')
    const isItalic = token.startsWith('*') && !isBold
    const isStrike = token.startsWith('~~')
    const isUnderline = token.startsWith('<u>')
    const text = isUnderline ? token.slice(3, -4) : token.slice(isBold || isItalic || isStrike ? 2 : 0, isBold || isItalic || isStrike ? -2 : undefined)
    runs.push({ text, bold: isBold, italic: isItalic, strike: isStrike, underline: isUnderline })
    cursor = start + token.length
  }
  if (cursor < value.length) runs.push({ text: value.slice(cursor) })
  return runs.filter(run => run.text)
}

function buildRichParagraphs(markdown: string): ExportRichParagraph[] {
  return markdown.replace(/<!--TENDER_TABLE:[A-Za-z0-9+/=]+-->\n?/g, '').split(/\n{2,}/).map(block => block.trim()).filter(Boolean).flatMap(block => {
    if (parseMarkdownTable(block) || /^(?:\[\[(?:MERMAID_ASSET|KNOWLEDGE_IMAGE):\d+\]\]|!\[[^\]]*\]\([^)]*\)|<img\s)/i.test(block)) return []
    const aligned = block.match(/^<p\s+style=["']text-align:\s*(left|center|right);?["']>([\s\S]*)<\/p>$/i)
    const source = aligned ? aligned[2] : block.replace(/^#{1,6}\s+/, '')
    const runs = richRuns(source)
    const plain = runs.map(run => run.text).join('').trim()
    return plain ? [{ plain, runs, align: aligned?.[1] as 'left' | 'center' | 'right' | undefined }] : []
  })
}

function isSignatureField(text: string) {
  return /^(?:投标人(?:名称|全称|单位名称|电子签章)?|供应商(?:名称|全称|电子签章)?|响应人(?:名称|全称|电子签章)?|投标单位(?:名称)?|法定代表人(?:或(?:其)?(?:授权(?:委托)?|委托)?代表|签字|签章)?|授权(?:委托)?代表(?:签字|签章)?|签字|签章|盖章|(?:签署|投标|响应|填表)?日期)(?:\s*[（(][^()（）]{1,40}[）)])*\s*[：:]/.test(text.replace(/\[\[BID_BOLD_(?:START|END)\]\]|\s+/g, ''))
}

function createTextBlock(level: 1 | 2 | 3 | 0, text: string, flushLeft = false, sourceRight = false): ExportContentBlock {
  const normalized = normalizeInlineMarkdown(text)
  if (level === 1) return { heading1: [{ text: normalized }] }
  if (level === 2) return { heading2: [{ text: normalized }] }
  if (level === 3) return { heading3: [{ text: normalized }] }
  return { body: [{ text: (sourceRight ? '[[BID_BODY_SOURCE_RIGHT]]' : flushLeft ? '[[BID_BODY_FLUSH_LEFT]]' : '[[BID_BODY_INDENT]]') + normalized }] }
}

function setImageSize(sizes: Map<string, [number, number]>, media: PreparedMedia, maxWidth: number) {
  if (sizes.has(media.dataUrl)) return
  const scale = Math.min(maxWidth / Math.max(media.width, 1), 560 / Math.max(media.height, 1), 1)
  const width = Math.max(40, Math.round(media.width * scale))
  const height = Math.max(40, Math.round(media.height * scale))
  sizes.set(media.dataUrl, [width, height])
}

function createImageBlock(media: PreparedMedia, sizes: Map<string, [number, number]>, caption = media.caption, maxWidth = 460): ExportContentBlock {
  setImageSize(sizes, media, maxWidth)
  return { image: [{ image: media.dataUrl, caption: caption || media.caption }] }
}

function buildContentBlocks(markdown: string, mediaMap: Map<string, PreparedMedia>, sizes: Map<string, [number, number]>, onTable?: (rows: string[][], layout?: TenderTableLayout) => string, chapterTitle = '') {
  const blocks: ExportContentBlock[] = []
  let pendingText: string[] = []
  let pendingTable: string[] = []
  let pendingLayout: TenderTableLayout | undefined
  let lastImageBlockIndex = -1
  let hasBodyContent = false
  const flushText = () => {
    const value = pendingText.join('\n').trim()
    pendingText = []
    if (value) {
      const table = parseMarkdownTable(value)
      blocks.push(createTextBlock(0, table && onTable ? onTable(table) : value))
      hasBodyContent = true
    }
  }
  const flushTable = () => {
    if (!pendingTable.length) return
    const rows = parseMarkdownTable(pendingTable.join('\n'))
    if (rows && onTable) {
      blocks.push(createTextBlock(0, onTable(rows, pendingLayout)))
      hasBodyContent = true
    } else {
      pendingText.push(...pendingTable)
    }
    pendingTable = []
    pendingLayout = undefined
  }
  const addMedia = (key: string, alt = '') => {
    const media = mediaMap.get(key)
    if (!media) {
      if (alt) pendingText.push(alt)
      return
    }
    const block = createImageBlock(media, sizes, alt || media.caption)
    blocks.push(block)
    hasBodyContent = true
    lastImageBlockIndex = blocks.length - 1
  }

  for (const rawLine of (markdown || '').replace(/\r/g, '').split('\n')) {
    const line = rawLine.trim()
    if (line === '<!--TENDER_FORM:SOURCE-->') continue
    if (line.startsWith('<!--TENDER_ALIGN:RIGHT-->')) {
      flushTable(); flushText()
      blocks.push(createTextBlock(0, line.slice('<!--TENDER_ALIGN:RIGHT-->'.length), false, true))
      hasBodyContent = true
      lastImageBlockIndex = -1
      continue
    }
    if (line.startsWith('<!--TENDER_TABLE:')) {
      flushTable(); flushText()
      pendingLayout = parseTenderTableLayout(line)
      continue
    }
    if (!line) {
      if (pendingTable.length && !pendingTable[pendingTable.length - 1].endsWith('|')) pendingTable.push('')
      else {
        flushTable()
        flushText()
      }
      continue
    }
    if (pendingTable.length && !pendingTable[pendingTable.length - 1].endsWith('|')) {
      pendingTable.push(line)
      continue
    }
    if (line.startsWith('|') && (pendingTable.length > 0 || line.split('|').length >= 4)) {
      flushText()
      pendingTable.push(line)
      continue
    }
    flushTable()
    const caption = line.match(/^(?:图注|图|图片说明|说明|caption)[:：]\s*(.+)$/i)
    if (caption && lastImageBlockIndex >= 0 && !pendingText.length) {
      const image = blocks[lastImageBlockIndex]?.image?.[0]
      if (image) image.caption = normalizeInlineMarkdown(caption[1])
      lastImageBlockIndex = -1
      continue
    }
    lastImageBlockIndex = -1
    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/)
    if (headingMatch) {
      flushText()
      if (!hasBodyContent && cleanChapterTitle(headingMatch[2]).replace(/\s/g, '') === cleanChapterTitle(chapterTitle).replace(/\s/g, '')) continue
      blocks.push(createTextBlock(Math.min(3, headingMatch[1].length) as 1 | 2 | 3, headingMatch[2]))
      continue
    }
    const tokenPattern = /(\[\[(?:MERMAID_ASSET|KNOWLEDGE_IMAGE):\d+\]\]|!\[[^\]]*\]\([^)]+\))/g
    const parts = line.split(tokenPattern).filter(Boolean)
    for (const part of parts) {
      const markerMatch = part.match(/^\[\[(MERMAID_ASSET|KNOWLEDGE_IMAGE):(\d+)\]\]$/)
      if (markerMatch) {
        flushText()
        addMedia(markerMatch[1] + ':' + markerMatch[2])
        continue
      }
      const markdownImageMatch = part.match(/^!\[([^\]]*)\]\(([^)]+)\)$/)
      if (markdownImageMatch) {
        flushText()
        if (isPlaceholderImageUrl(markdownImageMatch[2])) continue
        addMedia('SOURCE:' + markdownImageMatch[2], markdownImageMatch[1])
        continue
      }
      const text = normalizeInlineMarkdown(part, true)
      if (isSignatureField(text)) {
        flushText()
        blocks.push(createTextBlock(0, text, true))
        hasBodyContent = true
      } else {
        pendingText.push(text)
      }
    }
  }
  flushTable()
  flushText()

  // Only explicit image markers belong in a formal response. Auto-appending
  // matched knowledge images can insert another bidder's historic evidence.
  return blocks
}

function assetUrl(path: string | null | undefined) {
  if (!path) return ''
  if (/^(https?:|data:|blob:)/i.test(path)) return path
  const normalized = path.replace(/\\/g, '/')
  const origin = wordagentApi.apiBase.replace(/\/api\/?$/, '')
  const uploadIndex = normalized.toLowerCase().lastIndexOf('data/uploads/')
  if (uploadIndex >= 0) return origin + '/uploads/' + normalized.slice(uploadIndex + 'data/uploads/'.length)
  if (/^[a-z]:\//i.test(normalized)) return ''
  if (normalized.startsWith('/')) return origin + normalized
  return origin + '/' + normalized
}

function dataUrlToBytes(dataUrl: string) {
  const payload = dataUrl.split(',', 2)[1]
  if (!payload) throw new Error('图片数据为空')
  const binary = atob(payload)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return bytes
}

async function imageSourceToPng(source: string) {
  const resolved = assetUrl(source)
  if (!resolved) throw new Error('图片路径不可用')
  let imageUrl = resolved
  let objectUrl = ''
  if (/^https?:/i.test(resolved) && new URL(resolved).origin === new URL(wordagentApi.apiBase).origin) {
    const headers = new Headers()
    const token = getAuthToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch(resolved, { credentials: 'include', headers })
    if (!response.ok) throw new Error(`图片读取失败（${response.status}）`)
    objectUrl = URL.createObjectURL(await response.blob())
    imageUrl = objectUrl
  }
  const image = new Image()
  image.crossOrigin = 'anonymous'
  try {
    image.src = imageUrl
    await image.decode()
    const maxWidth = 1400
    const naturalWidth = image.naturalWidth || image.width || 1
    const naturalHeight = image.naturalHeight || image.height || 1
    const scale = Math.min(1, maxWidth / Math.max(naturalWidth, 1))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(naturalHeight * scale))
    const context = canvas.getContext('2d')
    if (!context) throw new Error('浏览器不支持图片画布')
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    return { dataUrl: canvas.toDataURL('image/png'), width: canvas.width, height: canvas.height }
  } finally {
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  }
}

async function loadTemplate(templateId: DocxExportTemplateId) {
  if (templateId.startsWith('custom:')) {
    const customTemplate = await getCustomWordTemplate(templateId)
    if (!customTemplate) throw new Error('自定义模板不存在或已被删除')
    // Project templates can be re-extracted in the knowledge base while this
    // page remains open. Always read the current IndexedDB copy for custom
    // templates so a later export cannot use stale bytes.
    return customTemplate.content
  }
  const cached = templateCache.get(templateId)
  if (cached) return cached
  const response = await fetch(TEMPLATE_URLS[templateId as BidTemplateId])
  if (!response.ok) throw new Error('模板加载失败：' + response.status)
  const buffer = normalizeTemplateFonts(await response.arrayBuffer())
  templateCache.set(templateId, buffer)
  return buffer
}

// Word's default Heading styles may retain theme-font attributes alongside
// explicit Chinese font names. The theme wins during rendering and can mix
// fallback fonts within one heading. Keep bundled cover and response headings black;
// uploaded custom templates retain their own font and color choices.
export function normalizeTemplateFonts(template: ArrayBuffer): ArrayBuffer {
  const zip = new PizZip(template)
  const styles = zip.file('word/styles.xml')
  if (!styles) return template
  let changed = false
  let xml = styles.asText().replace(/<w:rFonts\b[^>]*\/?\s*>/g, tag => {
    let normalized = tag
    for (const [font, theme] of [
      ['ascii', 'asciiTheme'],
      ['hAnsi', 'hAnsiTheme'],
      ['eastAsia', 'eastAsiaTheme'],
      ['cs', 'csTheme'],
      ['cs', 'cstheme'],
    ]) {
      if (!new RegExp(`\\bw:${font}="[^"]*"`).test(normalized)) continue
      normalized = normalized.replace(new RegExp(`\\s+w:${theme}="[^"]*"`, 'g'), '')
    }
    if (normalized !== tag) changed = true
    return normalized
  })
  xml = xml.replace(/<w:style\b[^>]*\bw:styleId="(?:Title|Heading[1-6]|BidSubheading)"[^>]*>[\s\S]*?<\/w:style>/g, style => {
    const color = '<w:color w:val="000000"/>'
    let normalized = style.replace(/<w:rPr\b[^>]*>[\s\S]*?<\/w:rPr>/, runProperties => {
      if (/<w:color\b[^>]*\/?\s*>/.test(runProperties)) {
        return runProperties.replace(/<w:color\b[^>]*\/?\s*>/, color)
      }
      return runProperties.replace(/<w:rPr\b[^>]*>/, opening => opening + color)
    })
    if (normalized === style && !/<w:rPr\b/.test(style)) {
      normalized = style.replace('</w:style>', `<w:rPr>${color}</w:rPr></w:style>`)
    }
    if (/\bw:styleId="Title"/.test(style)) {
      normalized = normalized.replace(/<w:bottom\b[^>]*\/?\s*>/g, border => {
        const withoutTheme = border.replace(/\s+w:themeColor="[^"]*"/g, '')
        return /\bw:color="[^"]*"/.test(withoutTheme)
          ? withoutTheme.replace(/\bw:color="[^"]*"/, 'w:color="000000"')
          : withoutTheme.replace(/\/?\s*>$/, ' w:color="000000"/>')
      })
    }
    if (normalized !== style) changed = true
    return normalized
  })
  if (!changed) return template
  zip.file('word/styles.xml', xml)
  return zip.generate({ type: 'arraybuffer' })
}

async function templateName(templateId: DocxExportTemplateId) {
  if (templateId.startsWith('custom:')) return (await getCustomWordTemplate(templateId))?.name || templateId
  return getBidTemplateById(templateId as BidTemplateId).name
}

async function withConcurrency<T, R>(items: T[], limit: number, worker: (item: T) => Promise<R>) {
  const results = new Array<R>(items.length)
  let cursor = 0
  async function run() {
    while (cursor < items.length) {
      const index = cursor
      cursor += 1
      results[index] = await worker(items[index])
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, Math.max(items.length, 1)) }, () => run()))
  return results
}

async function loadSectionMedia(section: GraphicSection) {
  const markdown = section.markdown || ''
  const chartIds = Array.from(markdown.matchAll(/\[\[MERMAID_ASSET:(\d+)\]\]/g)).map(match => Number(match[1]))
  const imageIds = Array.from(markdown.matchAll(/\[\[KNOWLEDGE_IMAGE:(\d+)\]\]/g)).map(match => Number(match[1]))
  const candidates = new Map<string, { key: string; source: string; caption: string; assetId?: number; chartId?: number; kind: 'image' | 'chart' }>()
  for (const chart of section.charts || []) {
    if (chart.chart_image_data && (!chartIds.length || chartIds.includes(chart.id))) {
      const key = `MERMAID_ASSET:${chart.id}`
      candidates.set(key, { key, source: chart.chart_image_data, caption: `图表：${chart.chart_title}`, chartId: chart.id, kind: 'chart' })
    }
  }
  for (const image of section.images || []) {
    if (image.resource_path && imageIds.includes(image.asset_id)) {
      const key = `KNOWLEDGE_IMAGE:${image.asset_id}`
      candidates.set(key, { key, source: image.resource_path, caption: assetCaption(image), assetId: image.asset_id, kind: 'image' })
    }
  }
  for (const match of markdown.matchAll(/!\[([^\]]*)\]\(([^)]+)\)/g)) {
    if (isPlaceholderImageUrl(match[2])) continue
    const key = `SOURCE:${match[2]}`
    candidates.set(key, { key, source: match[2], caption: match[1] || '图片资料', kind: 'image' })
  }
  const failed: string[] = []
  const converted = await withConcurrency([...candidates.values()], 3, async candidate => {
    try {
      return { ...candidate, ...(await imageSourceToPng(candidate.source)) }
    } catch {
      failed.push(candidate.key)
      return null
    }
  })
  if (failed.length) throw new Error(`正文引用的图片无法读取：${failed.join('、')}`)
  return converted.filter((item): item is PreparedMedia => Boolean(item))
}

function mediaImage(media: PreparedMedia | undefined, sizes: Map<string, [number, number]>) {
  if (!media) return []
  setImageSize(sizes, media, 110)
  return [{ image: media.dataUrl, caption: media.caption }]
}

function findAssetByQuery(query: string, assets: KnowledgeAsset[]) {
  const normalized = query.toLowerCase()
  if (!normalized) return undefined
  return assets.find(asset => {
    const text = assetSearchText(asset)
    return text.includes(normalized) || normalized.includes(text)
  })
}

function uniqueRows(rows: ExportTableRow[]) {
  const seen = new Set<string>()
  return rows.filter(row => {
    const key = Object.entries(row).filter(([name]) => name !== 'image').map(([, value]) => safeText(value)).join('|')
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function buildQualificationRows(parseDetail: BidParseDetail | null, assets: KnowledgeAsset[], mediaByAssetId: Map<number, PreparedMedia>, sizes: Map<string, [number, number]>) {
  const rows = toList(parseDetail?.qualification_requirements).map(item => {
    const record = asRecord(item)
    const name = readValue(record, ['certificate_name', 'name', 'type', 'category', 'requirement']) || safeText(item)
    const detail = readValue(record, ['description', 'requirement', 'gap_note', 'detail'])
    const asset = findAssetByQuery(name, assets)
    return { certificate_name: name || '资格/资质要求', certificate_number: readValue(record, ['certificate_number', 'certificate_no', 'number']), valid_until: readValue(record, ['valid_until', 'expiry_date', 'expire_date']), remark: detail, image: mediaImage(asset ? mediaByAssetId.get(asset.asset_id) : undefined, sizes) }
  })
  const assetRows = assets.filter(asset => /资质|证书|许可证|营业执照|认证|荣誉/.test(assetSearchText(asset))).map(asset => ({ certificate_name: asset.asset_sub_type || asset.category || asset.item_title || '资质材料', issuing_authority: readAssetValue(asset, ['issuing_authority', 'issuer', 'authority']), certificate_number: asset.cert_no || readAssetValue(asset, ['certificate_no', 'certificate_number', 'id_number']), valid_until: asset.expire_date || readAssetValue(asset, ['valid_until', 'archive_date']), holder_name: asset.holder_name || readAssetValue(asset, ['holder_name', 'company_name']), remark: readAssetValue(asset, ['business_scope']) || assetCaption(asset), image: mediaImage(mediaByAssetId.get(asset.asset_id), sizes) }))
  return uniqueRows([...rows, ...assetRows]).slice(0, 80)
}

function buildPersonnelRows(assets: KnowledgeAsset[], mediaByAssetId: Map<number, PreparedMedia>, sizes: Map<string, [number, number]>) {
  return uniqueRows(assets.filter(asset => /人员|员工|健康证|驾驶证|从业/.test(assetSearchText(asset)) || Boolean(asset.employee_info || asset.person_info)).map(asset => ({ name: readAssetValue(asset, ['name', 'employee_name', '姓名']) || asset.item_title || asset.source_name || '人员资料', role: asset.employee_role || readAssetValue(asset, ['role', 'position', '岗位']) || asset.category || '', certificate: readAssetValue(asset, ['certificate_type', 'certificate', '证书类型']) || asset.asset_sub_type || asset.category || '', certificate_number: asset.cert_no || readAssetValue(asset, ['id_number', 'certificate_number']), valid_until: asset.expire_date || readAssetValue(asset, ['valid_until', 'archive_date']), years_experience: readAssetValue(asset, ['years_experience', 'experience_years']), image: mediaImage(mediaByAssetId.get(asset.asset_id), sizes) }))).slice(0, 80)
}

function buildVehicleRows(assets: KnowledgeAsset[], mediaByAssetId: Map<number, PreparedMedia>, sizes: Map<string, [number, number]>) {
  return uniqueRows(assets.filter(asset => /车辆|冷链|配送车|车牌|驾驶证/.test(assetSearchText(asset))).map(asset => ({ vehicle_type: asset.category || asset.asset_sub_type || '配送车辆', plate_number: readAssetValue(asset, ['plate_number', 'license_plate', 'vehicle_no', 'plate_no', '车牌号']), approved_load: readAssetValue(asset, ['approved_load', 'load', 'capacity']), quantity: readAssetValue(asset, ['quantity', '数量']) || '1', inspection_date: readAssetValue(asset, ['inspection_date', 'inspection_expire_date', 'valid_until', 'archive_date']), cold_chain_equipment: readAssetValue(asset, ['cold_chain_equipment', 'cold_chain', 'equipment']), remark: assetCaption(asset), image: mediaImage(mediaByAssetId.get(asset.asset_id), sizes) }))).slice(0, 80)
}

function buildEquipmentRows(assets: KnowledgeAsset[], mediaByAssetId: Map<number, PreparedMedia>, sizes: Map<string, [number, number]>) {
  return uniqueRows(assets.filter(asset => /设备|冷库|保鲜|检测|资产|信息化/.test(assetSearchText(asset))).map(asset => ({ name: asset.item_title || asset.category || '设备资料', model: readValue(asset, ['model', 'model_no', '型号']), quantity: readValue(asset, ['quantity', '数量']) || '1', condition: readValue(asset, ['condition', 'status', '状态']) || assetCaption(asset), image: mediaImage(mediaByAssetId.get(asset.asset_id), sizes) }))).slice(0, 80)
}

function buildPerformanceRows(assets: KnowledgeAsset[], mediaByAssetId: Map<number, PreparedMedia>, sizes: Map<string, [number, number]>) {
  return uniqueRows(assets.filter(asset => /业绩|案例|合同|项目/.test(assetSearchText(asset))).map(asset => ({
    project_name: readAssetValue(asset, ['project_name', 'name']) || asset.item_title || '业绩案例',
    client_name: readAssetValue(asset, ['client_name', 'customer', 'party_a', 'holder_name']),
    project_date: readAssetValue(asset, ['project_date', 'contract_date', 'archive_date']),
    project_scale: readAssetValue(asset, ['project_scale', 'amount', 'contract_amount']),
    remark: assetCaption(asset),
    image: mediaImage(mediaByAssetId.get(asset.asset_id), sizes),
  }))).slice(0, 80)
}

function buildFinancialRows(assets: KnowledgeAsset[], mediaByAssetId: Map<number, PreparedMedia>, sizes: Map<string, [number, number]>) {
  return uniqueRows(assets.filter(asset => /审计|财务|发票|收入|资产|利润/.test(assetSearchText(asset)) || asset.asset_sub_type === '增值税发票').map(asset => ({
    year: readAssetValue(asset, ['year', 'fiscal_year', 'archive_year']),
    revenue: readAssetValue(asset, ['revenue', 'operating_revenue', 'income']),
    total_assets: readAssetValue(asset, ['total_assets', 'assets']),
    net_profit: readAssetValue(asset, ['net_profit', 'profit']),
    audit_firm: readAssetValue(asset, ['audit_firm', 'auditor', 'issuing_authority']),
    verification: asset.structured_info?.confidence && asset.structured_info.confidence < 0.7 ? '待人工核验' : '待核验',
    image: mediaImage(mediaByAssetId.get(asset.asset_id), sizes),
  }))).slice(0, 80)
}

async function buildExportData(bidId: number, catalog: BidCatalogResponse, templateId: DocxExportTemplateId, onProgress?: (progress: ExportProgress) => void, options: { versionId?: number; selectedChapterIds?: number[]; includeImages?: boolean; includeToc?: boolean; selectedText?: string; bidderName?: string } = {}): Promise<ExportBuildResult> {
  report(onProgress, 'data', '填充数据')
  const version = resolveExportVersion(catalog, options.versionId)
  const chapters = selectExportChapters(version.catalog_content || [], options.selectedChapterIds)
  if (options.selectedChapterIds?.length && !chapters.length) throw new Error('所选章节不属于当前目录版本，请重新选择后导出')
  const leafChapters = chapters.filter(chapter => !chapter.children?.length)
  const [sectionResults, parseDetailResult] = await Promise.all([
    withConcurrency(leafChapters, 4, async chapter => {
      try {
        const response = await wordagentApi.getGraphicSection(bidId, chapter.chapter_id, true, options.bidderName)
        return response.code === 0 ? response.data : null
      } catch {
        return null
      }
    }),
    wordagentApi.getBidParseDetail(bidId).catch(() => null),
  ])
  if (options.selectedText && sectionResults[0]) {
    sectionResults[0] = { ...sectionResults[0], markdown: options.selectedText }
  }
  report(onProgress, 'images', '渲染图片')
  const mediaResults = options.includeImages === false
    ? sectionResults.map(() => [] as PreparedMedia[])
    : await withConcurrency(sectionResults, 3, async section => section ? loadSectionMedia(section) : Promise.resolve([]))
  const sectionMap = new Map<number, { section: GraphicSection; media: PreparedMedia[] }>()
  for (let index = 0; index < leafChapters.length; index += 1) {
    const section = sectionResults[index]
    if (section) sectionMap.set(leafChapters[index].chapter_id, { section, media: mediaResults[index] || [] })
  }
  const sizes = new Map<string, [number, number]>()
  const mediaByAssetId = new Map<number, PreparedMedia>()
  const rawAssets = new Map<number, KnowledgeAsset>()
  for (const loaded of sectionMap.values()) {
    for (const asset of loaded.section.images || []) rawAssets.set(asset.asset_id, asset)
    for (const media of loaded.media) if (media.assetId && !mediaByAssetId.has(media.assetId)) mediaByAssetId.set(media.assetId, media)
  }
  // Only assets explicitly referenced by this bid's saved chapters may enter
  // the generated attachment tables. A broad knowledge-library scan can pull
  // unrelated historical certificates and make the export much larger.
  const allAssets = [...rawAssets.values()]
  const tableImageCandidates = options.includeImages === false ? [] : allAssets.filter(asset =>
    !mediaByAssetId.has(asset.asset_id) &&
    /资质|证书|营业执照|许可证|认证|人员|员工|健康证|从业|车辆|冷链|车牌|驾驶证|设备/.test(assetSearchText(asset)) && Boolean(asset.resource_path)
  ).slice(0, 120)
  const tableImageResults = await withConcurrency(tableImageCandidates, 3, async asset => {
    try {
      return { asset, ...(await imageSourceToPng(asset.resource_path || '')), caption: assetCaption(asset) }
    } catch {
      return null
    }
  })
  for (const loaded of tableImageResults) {
    if (!loaded || mediaByAssetId.has(loaded.asset.asset_id)) continue
    const media: PreparedMedia = { key: `KNOWLEDGE_IMAGE:${loaded.asset.asset_id}`, source: loaded.asset.resource_path || '', dataUrl: loaded.dataUrl, width: loaded.width, height: loaded.height, caption: loaded.caption, assetId: loaded.asset.asset_id, kind: 'image' }
    mediaByAssetId.set(loaded.asset.asset_id, media)
  }
  const tables: ExportTableDefinition[] = []
  const chapterByNumber = new Map(chapters.map(chapter => [chapter.number, chapter]))
  const exportChapters: ExportChapter[] = chapters.map(chapter => {
    const loaded = sectionMap.get(chapter.chapter_id)
    const mediaMap = loaded ? new Map(loaded.media.map(media => [media.key, media])) : new Map<string, PreparedMedia>()
    const markdown = loaded?.section.markdown || ''
    const title = cleanChapterTitle(chapter.title)
    const ancestors = chapter.number.split('.').slice(0, -1)
    const duplicateAncestor = ancestors.some((_, index) => cleanChapterTitle(chapterByNumber.get(ancestors.slice(0, index + 1).join('.'))?.title || '').replace(/\s/g, '') === title.replace(/\s/g, ''))
    const heading = chapter.number + ' ' + title
    const depth = Math.min(chapter.number.split('.').length, 3)
    return {
      chapter_heading: heading,
      chapter_outline_level: String(depth - 1),
      chapter_heading1: !duplicateAncestor && depth === 1 ? [{ text: heading }] : [],
      chapter_heading2: !duplicateAncestor && depth === 2 ? [{ text: heading }] : [],
      chapter_heading3: !duplicateAncestor && depth === 3 ? [{ text: heading }] : [],
      content_blocks: !markdown.trim() && chapter.children?.length ? [] : buildContentBlocks(markdown, mediaMap, sizes, (rows, layout) => { const marker = '[[DOCX_TABLE:' + tables.length + ']]'; tables.push({ marker, rows, layout }); return marker }, title),
    }
  })
  const chapterRows = chapters.map(chapter => {
    const content = sectionMap.get(chapter.chapter_id)?.section.markdown || ''
    return {
      number: chapter.number,
      title: cleanChapterTitle(chapter.title),
      word_count: String(stripMarkdown(content).length),
      status: content.trim() ? '已生成' : '待生成',
    }
  })
  const parseDetail = parseDetailResult?.code === 0 ? parseDetailResult.data : null
  const parsedProjectName = readValue(parseDetail?.project_basic_info, ['project_name', '项目名称'])
  const overviewProjectName = catalog.context.project_overview.match(/(?:^|\n)项目名称[：:]\s*([^\n]+)/)?.[1]?.trim() || ''
  const baseTitle = parsedProjectName || overviewProjectName || catalog.context.bid_title || '标书项目 ' + bidId
  const titleKey = (value: string) => cleanChapterTitle(value).replace(/\s/g, '')
  const requiredForms = new Set((catalog.context.required_fixed_forms || []).map(titleKey))
  const missingSourceForm = leafChapters.some(chapter =>
    requiredForms.has(titleKey(chapter.title)) &&
    !/<!--TENDER_(?:FORM:SOURCE-->|TABLE:)/.test(sectionMap.get(chapter.chapter_id)?.section.markdown || '')
  )
  const priceScoreConflict = catalog.context.price_score_conflict || ''
  const isDraft = Boolean(
    options.selectedChapterIds?.length || options.selectedText || getTenderFormatGaps(catalog, options.versionId).length ||
    missingSourceForm || priceScoreConflict ||
    leafChapters.some(chapter => !sectionMap.get(chapter.chapter_id)?.section.markdown?.trim())
  )
  const bidTitle = isDraft ? `${baseTitle}（章节测试稿）` : baseTitle
  const today = new Date()
  // Standard and case-derived exports contain the selected chapter content only.
  // Legacy summary blocks can duplicate images and turn OCR descriptions into
  // narrow appendix tables. Only explicitly authored custom templates opt in.
  const includeSummaryTables = templateId.startsWith("custom:") && !templateId.startsWith("custom:project-")
  const qualificationRows = buildQualificationRows(parseDetail, allAssets, mediaByAssetId, sizes)
  const personnelRows = buildPersonnelRows(allAssets, mediaByAssetId, sizes)
  const vehicleRows = buildVehicleRows(allAssets, mediaByAssetId, sizes)
  const equipmentRows = buildEquipmentRows(allAssets, mediaByAssetId, sizes)
  const performanceRows = buildPerformanceRows(allAssets, mediaByAssetId, sizes)
  const financialRows = buildFinancialRows(allAssets, mediaByAssetId, sizes)
  return {
    data: {
      bid_title: bidTitle,
      bid_id: String(bidId),
      project_number: parseDetail?.project_number?.trim() || '【待填写项目编号】',
      bidder_name: options.bidderName?.trim() || (templateId === 'enterprise-header' ? '________________' : ''),
      procurement_method: catalog.context.procurement_method || '',
      document_kind: /磋商|谈判|询价|单一来源/.test(catalog.context.procurement_method || '') ? '响应文件' : '投标文件',
      price_score_conflict: priceScoreConflict,
      bid_date: templateId === 'enterprise-header' ? '______ 年 ____ 月 ____ 日' : `${today.getFullYear()} 年 ${today.getMonth() + 1} 月 ${today.getDate()} 日`,
      template_name: DOCX_EXPORT_TEMPLATES.find(item => item.id === (templateId as BidTemplateId))?.name || templateId,
      include_toc: options.includeToc !== false,
      chapter_rows: options.includeToc === false ? [] : chapterRows,
      qualification_rows: qualificationRows,
      personnel_rows: personnelRows,
      vehicle_rows: vehicleRows,
      equipment_rows: equipmentRows,
      performance_rows: performanceRows,
      financial_rows: financialRows,
      has_qualification_rows: includeSummaryTables && qualificationRows.length > 0,
      has_personnel_rows: includeSummaryTables && personnelRows.length > 0,
      has_vehicle_rows: includeSummaryTables && vehicleRows.length > 0,
      has_equipment_rows: includeSummaryTables && equipmentRows.length > 0,
      has_performance_rows: includeSummaryTables && performanceRows.length > 0,
      has_financial_rows: includeSummaryTables && financialRows.length > 0,
      chapters: exportChapters,
    },
    sizes,
    richParagraphs: sectionResults.flatMap(section => section ? buildRichParagraphs(section.markdown || '') : []),
    tables,
  }
}

function prepareProjectTemplate(zip: PizZip) {
  const file = zip.file('word/document.xml')
  if (!file) throw new Error('案例模板缺少 Word 正文')
  let xml = file.asText()
  const paragraphs = Array.from(xml.matchAll(/<w:p\b[^>]*>[\s\S]*?<\/w:p>/g))
  const start = paragraphs.findIndex(match => match[0].includes('{#chapter_rows}'))
  const end = paragraphs.findIndex((match, index) => index > start && match[0].includes('{/chapter_rows}'))
  if (start < 0 || end < 0 || paragraphs[start].index === undefined || paragraphs[end].index === undefined) {
    throw new Error('案例模板缺少目录占位符，请重新提取格式模板')
  }
  const from = paragraphs[start].index!
  const to = paragraphs[end].index! + paragraphs[end][0].length
  if (/<w:tbl\b/.test(xml.slice(from, to))) throw new Error('案例模板目录结构不受支持，请重新提取格式模板')
  xml = xml.slice(0, from) + '<w:p><w:r><w:t>[[BID_TOC_PLACEHOLDER]]</w:t></w:r></w:p>' + xml.slice(to)
  if (!xml.includes('{chapter_heading}')) throw new Error('案例模板缺少章节标题占位符，请重新提取格式模板')
  xml = xml.replace('{chapter_heading}', '[[BID_HEADING_LEVEL:{chapter_outline_level}]]{chapter_heading}')
  zip.file('word/document.xml', xml)
}

function normalizePackageNamespaces(zip: PizZip) {
  // The image module expects unprefixed package tags. Prefixes such as ns0
  // are valid OOXML but need an equivalent default-namespace representation.
  for (const name of Object.keys(zip.files)) {
    const relationships = name.endsWith('.rels')
    if (!relationships && name !== '[Content_Types].xml') continue
    const file = zip.file(name)
    if (!file) continue
    let xml = file.asText()
    const namespace = relationships
      ? 'http://schemas.openxmlformats.org/package/2006/relationships'
      : 'http://schemas.openxmlformats.org/package/2006/content-types'
    const declarations = [...xml.matchAll(/xmlns:([\w.-]+)\s*=\s*["']([^"']+)["']/g)]
      .filter(match => match[2] === namespace)
    for (const declaration of declarations) {
      const prefix = declaration[1].replace(/\./g, '\\.')
      xml = xml.replace(new RegExp('(<\\/?)' + prefix + ':', 'g'), '$1')
      const hasDefault = xml.includes('xmlns="' + namespace + '"') || xml.includes("xmlns='" + namespace + "'")
      xml = xml.replace(declaration[0], () => hasDefault ? '' : 'xmlns="' + namespace + '"')
    }
    if (declarations.length) zip.file(name, xml)
  }
}

function prepareEnterpriseTemplate(zip: PizZip, data: ExportData) {
  const document = zip.file('word/document.xml')
  if (document) {
    zip.file('word/document.xml', document.asText()
      .replace('标准明标版 · 公开招标项目', '{document_kind}' + (data.procurement_method ? ' · {procurement_method}' : ''))
      .replace('项目编号：{project_number}    模板：{template_name}', '项目编号：{project_number}')
      .replace('智能标书生成系统 · 本地模板导出', '供应商：{bidder_name}    日期：{bid_date}'))
  }
  for (const name of Object.keys(zip.files).filter(name => /^word\/footer\d+\.xml$/.test(name))) {
    const footer = zip.file(name)
    if (footer) zip.file(name, footer.asText().replace(/(<w:t\b[^>]*>)(?:— | —)(<\/w:t>)/g, '$1$2'))
  }
}

function renderDocx(template: ArrayBuffer, result: ExportBuildResult, sizes: Map<string, [number, number]>, projectTemplate = false, enterpriseTemplate = false) {
  const data = result.data
  const imageModule = new ImageModule({
    centered: true,
    fileType: 'docx',
    getImage: tagValue => dataUrlToBytes(String(tagValue)),
    getSize: (_image, tagValue) => sizes.get(String(tagValue)) || [460, 260],
  })
  const zip = new PizZip(template)
  if (projectTemplate) prepareProjectTemplate(zip)
  if (enterpriseTemplate) prepareEnterpriseTemplate(zip, data)
  normalizePackageNamespaces(zip)
  const doc = new Docxtemplater(zip, { paragraphLoop: true, linebreaks: true, modules: [imageModule] })
  doc.render(data)
  applyRichTextAndTables(doc.getZip(), result)
  return doc.getZip().generate({ type: 'blob', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
}

function escapeXml(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;')
}

function richRunXml(run: ExportRichRun) {
  const properties = [run.bold ? '<w:b/>' : '', run.italic ? '<w:i/>' : '', run.underline ? '<w:u w:val="single"/>' : '', run.strike ? '<w:strike/>' : ''].join('')
  const text = escapeXml(run.text).split(/([\n\t])/).map(part => part === '\n' ? '<w:br/>' : part === '\t' ? '<w:tab/>' : '<w:t xml:space="preserve">' + part + '</w:t>').join('')
  return '<w:r>' + (properties ? '<w:rPr>' + properties + '</w:rPr>' : '') + text + '</w:r>'
}

function parseTenderTableLayout(line: string): TenderTableLayout {
  const match = line.match(/^<!--TENDER_TABLE:([A-Za-z0-9+/=]+)-->$/)
  if (!match || match[1].length > 1000000) throw new Error('招标原表结构数据无效，请重新生成该表')
  const value = JSON.parse(atob(match[1])) as TenderTableLayout
  if (!Array.isArray(value.widths) || !value.widths.length || value.widths.length > 20 || value.widths.some(w => !Number.isFinite(w) || w <= 0)
    || !Array.isArray(value.heights) || value.heights.length > 100 || value.heights.some(h => !Number.isFinite(h) || h <= 0)
    || !Array.isArray(value.cells) || value.cells.some(c => ![c.row, c.col, c.rowspan, c.colspan].every(Number.isInteger) || c.row < 0 || c.col < 0 || c.rowspan < 1 || c.colspan < 1 || c.row + c.rowspan > value.heights.length || c.col + c.colspan > value.widths.length)) throw new Error('招标原表行列结构无效')
  for (const cell of value.cells) {
    if (cell.paragraphs !== undefined && (!Array.isArray(cell.paragraphs) || typeof cell.source_text !== 'string' ||
      cell.paragraphs.some(p => !p || !['left', 'center', 'right', 'both'].includes(p.align) ||
        [p.left_indent, p.right_indent].some(value => value !== undefined && (!Number.isInteger(value) || value < 0 || value > 5000)) ||
        !Array.isArray(p.runs) || p.runs.some(run => !run || typeof run.text !== 'string')))) {
      throw new Error('招标原表单元格版式数据无效')
    }
  }
  return value
}

export { parseTenderTableLayout }

function tableXml(rows: string[][], templateTableProperties: string, layout?: TenderTableLayout) {
  const columnCount = Math.max(...rows.map(row => row.length), 1)
  if (layout && (columnCount !== layout.widths.length || rows.length !== layout.heights.length)) throw new Error('招标原表行列已改变，请保留原表结构后再导出')
  const availableWidth = layout ? Math.min(9000, Math.round(layout.widths.reduce((sum, width) => sum + width * 20, 0))) : 9000
  const weights = layout?.widths || Array.from({length: columnCount}, (_, i) => Math.max(3, Math.min(16, Math.max(...rows.map(row => (row[i] || '').length)))))
  const total = weights.reduce((a,b) => a+b, 0)
  const widths = weights.map(w => Math.floor(availableWidth*w/total))
  widths[widths.length-1] += availableWidth - widths.reduce((a,b) => a+b,0)
  const grid = widths.map(width => '<w:gridCol w:w="' + width + '"/>').join('')
  const body = rows.map((row, rowIndex) => {
    let cells = ''
    for (let index = 0; index < columnCount; index++) {
      const spec = layout?.cells.find(c => c.col === index && c.row <= rowIndex && c.row + c.rowspan > rowIndex)
      if (layout && !spec) continue
      const span = spec?.colspan || 1
      const width = widths.slice(index, index + span).reduce((a,b) => a+b, 0)
      const continued = Boolean(spec && spec.row < rowIndex)
      const merge = spec && spec.rowspan > 1 ? '<w:vMerge w:val="' + (continued ? 'continue' : 'restart') + '"/>' : ''
      const value = continued ? '' : row[index] || ''
      const fontSize = columnCount > 6 ? 20 : columnCount > 4 ? 22 : 24
      const plain = value.replace(/\[\[BID_BOLD_(?:START|END)\]\]/g, '').trim()
      const sourceParagraphs = !continued && spec?.paragraphs && plain === spec.source_text ? spec.paragraphs : undefined
      const paragraphs = sourceParagraphs?.map(paragraph =>
        '<w:p><w:pPr><w:spacing w:before="0" w:after="0"/><w:ind w:left="' + (paragraph.left_indent || 0) + '" w:right="' + (paragraph.right_indent || 0) + '" w:firstLine="0"/>' +
        '<w:jc w:val="' + paragraph.align + '"/></w:pPr>' + paragraph.runs.map(run => richRunXml(run.underline && /^ +$/.test(run.text) ? { ...run, text: (/^费率[大小]写/.test(paragraph.runs[0]?.text || '') ? run.text.slice(0, 10) : run.text).replace(/ /g, '\u00a0') } : run)).join('') + '</w:p>'
      ).join('') || '<w:p><w:pPr><w:jc w:val="center"/><w:ind w:left="0" w:right="0" w:firstLine="0"/><w:spacing w:before="0" w:after="0"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/><w:color w:val="000000"/><w:sz w:val="' + fontSize + '"/>' + (rowIndex === 0 && layout?.header !== false ? '<w:b/>' : '') + '</w:rPr><w:t xml:space="preserve">' + escapeXml(value) + '</w:t></w:r></w:p>'
      cells += '<w:tc><w:tcPr><w:tcW w:w="' + width + '" w:type="dxa"/>' + (span > 1 ? '<w:gridSpan w:val="' + span + '"/>' : '') + merge + '<w:vAlign w:val="center"/></w:tcPr>' + paragraphs + '</w:tc>'
      index += span - 1
    }
    const height = layout ? '<w:trHeight w:val="'+Math.round(Math.min(450,layout.heights[rowIndex])*20)+'" w:hRule="atLeast"/>' : ''
    const rowProperties = '<w:trPr><w:cantSplit/>' + height + (rowIndex === 0 && layout?.header !== false ? '<w:tblHeader/>' : '') + '</w:trPr>'
    return '<w:tr>' + rowProperties + cells + '</w:tr>'
  }).join('')
  const halfLineAfter = '<w:p><w:pPr><w:keepNext/><w:spacing w:before="0" w:after="0" w:line="120" w:lineRule="exact"/></w:pPr></w:p>'
  // Explicit geometry prevents template autofit or theme borders changing a source form.
  const properties = layout ? '<w:tblPr><w:tblW w:w="' + availableWidth + '" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblBorders>' + ['top','left','bottom','right','insideH','insideV'].map(side=>'<w:'+side+' w:val="single" w:sz="4" w:color="000000"/>').join('') + '</w:tblBorders></w:tblPr>' : templateTableProperties
  return '<w:tbl>' + properties + '<w:tblGrid>' + grid + '</w:tblGrid>' + body + '</w:tbl>' + halfLineAfter
}

function tocFieldXml(rows: ExportChapterRow[]) {
  const entries = rows.length ? rows : [{ number: '', title: '暂无章节', word_count: '', status: '' }]
  const paragraphs = entries.map((row, index) => {
    const level = Math.min(Math.max(row.number.split('.').filter(Boolean).length, 1), 3)
    const indent = (level - 1) * 360
    const content = escapeXml(`${row.number} ${row.title}`.trim())
    const fieldStart = index === 0
      ? '<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r><w:r><w:instrText xml:space="preserve"> TOC \\o &quot;1-3&quot; \\h \\z \\u </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
      : ''
    const fieldEnd = index === entries.length - 1 ? '<w:r><w:fldChar w:fldCharType="end"/></w:r>' : ''
    return `<w:p><w:pPr><w:ind w:left="${indent}"/><w:spacing w:before="0" w:after="80"/></w:pPr>${fieldStart}<w:r><w:rPr><w:color w:val="000000"/></w:rPr><w:t xml:space="preserve">${content}</w:t></w:r>${fieldEnd}</w:p>`
  })
  return paragraphs.join('')
}

function applyRichTextAndTables(zip: PizZip, result: ExportBuildResult) {
  const file = zip.file('word/document.xml')
  if (!file) return
  let xml = file.asText()
  xml = xml.replace(/<w:p\b[^>]*>[\s\S]*?<\/w:p>/g, paragraph => {
    if (paragraph.includes('[[BID_TOC_PLACEHOLDER]]')) return result.data.include_toc ? paragraph : ''
    const match = paragraph.match(/\[\[BID_HEADING_LEVEL:([0-2])\]\]/)
    if (!match) return paragraph
    let updated = paragraph.replace(match[0], '')
    updated = updated.replace(/<w:outlineLvl\b[^>]*\/?\s*>/g, '')
    const outline = `<w:outlineLvl w:val="${match[1]}"/>`
    return /<w:pPr\b[^>]*>/.test(updated)
      ? updated.replace(/<w:pPr\b[^>]*>/, value => value + outline)
      : updated.replace(/<w:p\b[^>]*>/, value => value + `<w:pPr>${outline}</w:pPr>`)
  })
  if (result.data.include_toc) {
    // A bare TOC field has no cached result. Word does not always refresh it
    // when opening a downloaded document, leaving an otherwise empty page.
    xml = xml.replace(/<w:p\b[\s\S]*?<\/w:p>/g, paragraph =>
      paragraph.includes('[[BID_TOC_PLACEHOLDER]]') || /<w:instrText\b[^>]*>\s*TOC\s/.test(paragraph)
        ? tocFieldXml(result.data.chapter_rows)
        : paragraph,
    )
    const settingsFile = zip.file('word/settings.xml')
    if (settingsFile) {
      let settings = settingsFile.asText()
      if (/<w:updateFields\b/.test(settings)) {
        settings = settings.replace(/<w:updateFields\b[^>]*\/?\s*>/, '<w:updateFields w:val="true"/>')
      } else {
        settings = settings.replace('</w:settings>', '<w:updateFields w:val="true"/></w:settings>')
      }
      zip.file('word/settings.xml', settings)
    }
  }
  const templateTableProperties = xml.match(/<w:tblPr\b[\s\S]*?<\/w:tblPr>/)?.[0]
    || '<w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
  for (const table of result.tables) {
    const marker = escapeXml(table.marker)
    xml = xml.replace(/<w:p\b[\s\S]*?<\/w:p>/g, paragraph => paragraph.includes(marker) ? tableXml(table.rows, templateTableProperties, table.layout) : paragraph)
  }
  // Generated body blocks carry their paragraph formatting through the template.
  xml = xml.replace(/<w:p\b[^>]*>[\s\S]*?<\/w:p>/g, paragraph => {
    const marker = paragraph.match(/\[\[BID_BODY_(INDENT|FLUSH_LEFT|SOURCE_RIGHT)\]\]/)
    if (!marker) return paragraph
    const signature = marker[1] !== 'INDENT'
    const indent = signature
      ? '<w:ind w:left="0" w:leftChars="0" w:firstLine="0" w:firstLineChars="0"/>'
      : '<w:ind w:firstLineChars="200"/>'
    const updated = paragraph.replace(marker[0], '')
    const align = signature ? '<w:jc w:val="' + (marker[1] === 'SOURCE_RIGHT' ? 'right' : 'left') + '"/>' : ''
    const properties = indent + align
    if (/<w:pPr\s*\/>/.test(updated)) return updated.replace(/<w:pPr\s*\/>/, '<w:pPr>' + properties + '</w:pPr>')
    if (!/<w:pPr\b[^>]*>/.test(updated)) return updated.replace(/<w:p\b[^>]*>/, value => value + '<w:pPr>' + properties + '</w:pPr>')
    return updated.replace(/(<w:pPr\b[^>]*>)([\s\S]*?)(<\/w:pPr>)/, (_match, open, format: string, close) => {
      if (/<w:ind\b[^>]*\/>/.test(format)) {
        format = format.replace(/<w:ind\b([^>]*)\/>/, (_indent, attrs: string) => {
          if (signature) return indent
          const kept = attrs.replace(/\s+w:(?:firstLine|firstLineChars|hanging|hangingChars)="[^"]*"/g, '')
          return '<w:ind' + kept + ' w:firstLineChars="200"/>'
        })
      } else {
        const afterIndent = /<w:(?:contextualSpacing|mirrorIndents|suppressOverlap|jc|textDirection|textAlignment|textboxTightWrap|outlineLvl|divId|cnfStyle|rPr|sectPr|pPrChange)\b/
        format = afterIndent.test(format) ? format.replace(afterIndent, tag => indent + tag) : format + indent
      }
      if (signature) {
        if (/<w:jc\b[^>]*\/>/.test(format)) format = format.replace(/<w:jc\b[^>]*\/>/, align)
        else {
          const afterAlignment = /<w:(?:textDirection|textAlignment|textboxTightWrap|outlineLvl|divId|cnfStyle|rPr|sectPr|pPrChange)\b/
          format = afterAlignment.test(format) ? format.replace(afterAlignment, tag => align + tag) : format + align
        }
      }
      return open + format + close
    })
  })
  xml = xml.replace(/<w:r\b[^>]*>[\s\S]*?<\/w:r>/g, run => {
    if (!run.includes('[[BID_BOLD_START]]')) return run
    return run.replace(/^(<w:r\b[^>]*>)(<w:rPr\b[^>]*\/>|<w:rPr\b[^>]*>[\s\S]*?<\/w:rPr>)?<w:t\b[^>]*>([\s\S]*?)<\/w:t><\/w:r>$/,
      (_match, open, properties: string | undefined, value: string) => {
        const boldProperties = properties?.startsWith('<w:rPr') && !properties.endsWith('/>')
          ? properties.replace(/<w:b(?:Cs)?\b[^>]*\/>/g, '').replace(/<w:rPr\b[^>]*>(?:<w:rStyle\b[^>]*\/>|<w:rFonts\b[^>]*\/>)*\s*/, tag => tag + '<w:b/><w:bCs/>')
          : '<w:rPr><w:b/><w:bCs/></w:rPr>'
        return value.split(/\[\[BID_BOLD_START\]\]([\s\S]*?)\[\[BID_BOLD_END\]\]/).map((text, index) => text
          ? open + ((index % 2 ? boldProperties : properties) || '') + '<w:t xml:space="preserve">' + text + '</w:t></w:r>'
          : '').join('')
      })
  })
  // Do not replace paragraphs by substring. A short body fragment such as
  // “投标函” also occurs inside catalog headings; replacing the whole heading
  // corrupts both the document and its Word table of contents.
  if (result.data.price_score_conflict) {
    const note = '<w:p><w:r><w:t>' + escapeXml(result.data.price_score_conflict) + '</w:t></w:r></w:p>'
    const cover = [...xml.matchAll(/<w:p\b[^>]*>[\s\S]*?<\/w:p>/g)].find(match => match[0].includes('供应商：') && match[0].includes('日期：'))
    if (cover?.index !== undefined) {
      const end = cover.index + cover[0].length
      xml = xml.slice(0, end) + note + xml.slice(end)
    } else {
      xml = xml.replace(/(<w:body\b[^>]*>)/, (_match, body) => body + note)
    }
  }
  zip.file('word/document.xml', xml)
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

class TocPaginationError extends Error {}
class BlindCheckError extends Error {}

async function clearBlindMetadata(blob: Blob): Promise<Blob> {
  const zip = new PizZip(await blob.arrayBuffer())
  for (const name of ['docProps/core.xml', 'docProps/app.xml']) {
    const file = zip.file(name)
    if (!file) continue
    const xml = new DOMParser().parseFromString(file.asText(), 'application/xml')
    if (xml.getElementsByTagName('parsererror').length) throw new BlindCheckError('无法清理 Word 文件属性，请更换模板。')
    for (const element of Array.from(xml.getElementsByTagName('*'))) {
      if (['creator', 'lastmodifiedby', 'manager', 'company'].includes(element.localName.toLowerCase())) element.textContent = ''
    }
    zip.file(name, new XMLSerializer().serializeToString(xml))
  }
  return zip.generate({ type: 'blob', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
}

export async function checkBlindExport(bidId: number, blob: Blob, terms: string, review: (report: BlindCheckReport) => Promise<boolean>) {
  let report: BlindCheckReport
  try {
    const response = await wordagentApi.checkBlindDocx(bidId, blob, terms, true)
    if (response.code !== 0 || !response.data?.required) throw new Error(response.message || '检查未完成')
    report = response.data
  } catch (error) {
    throw new BlindCheckError('暗标检查未完成，已阻止导出：' + (error instanceof Error ? error.message : String(error)))
  }
  const confirmed = await review(report)
  if (report.blocked) throw new BlindCheckError('暗标检查发现问题，请按清单修改后重新导出。')
  if (!confirmed) throw new BlindCheckError('尚未完成人工复核，已取消导出。')
}


async function withUpdatedTocPageNumbers(bidId: number, blob: Blob, includeToc: boolean) {
  if (!includeToc) return blob
  try {
    return await wordagentApi.finalizeGraphicDocxToc(bidId, blob)
  } catch (error) {
    const raw = error instanceof Error ? error.message : String(error)
    let detail = raw
    try { detail = JSON.parse(raw).message || raw } catch { /* Preserve non-JSON errors. */ }
    throw new TocPaginationError(`目录页码计算失败：${detail}`)
  }
}

function triggerBackendFallback(bidId: number, bidderName?: string) {
  const url = wordagentApi.downloadGraphicDocxUrl(bidId, bidderName)
  if (typeof document === 'undefined') throw new Error('当前环境不支持原生导出降级')
  const link = document.createElement('a')
  link.href = url
  link.target = '_blank'
  link.rel = 'noopener noreferrer'
  document.body.appendChild(link)
  link.click()
  link.remove()
  return url
}

export async function exportGraphicBidDocx(options: { bidId: number; catalog: BidCatalogResponse; templateId: DocxExportTemplateId; onProgress?: (progress: ExportProgress) => void; versionId?: number; selectedChapterIds?: number[]; includeImages?: boolean; includeToc?: boolean; selectedText?: string; bidderName?: string; blindCheck?: boolean; blindTerms?: string; onBlindReview: (report: BlindCheckReport) => Promise<boolean> }) {
  let blind: boolean
  try {
    const mode = await wordagentApi.getBlindMode(options.bidId)
    blind = mode.data.blind || Boolean(options.blindCheck) || options.templateId === 'compact-technical'
  } catch (error) {
    throw new BlindCheckError('无法确认项目的暗标状态，已阻止导出。')
  }
  const templateId = blind && options.templateId === 'enterprise-header' ? 'compact-technical' : options.templateId
  try {
    report(options.onProgress, 'loading', '加载模板')
    const template = await loadTemplate(templateId)
    const result = await buildExportData(options.bidId, options.catalog, templateId, options.onProgress, { versionId: options.versionId, selectedChapterIds: options.selectedChapterIds, includeImages: options.includeImages, includeToc: options.includeToc, selectedText: options.selectedText, bidderName: options.bidderName })
    result.data.template_name = await templateName(templateId)
    report(options.onProgress, 'rendering', '生成文件')
    const rendered = renderDocx(template, result, result.sizes, templateId.startsWith('custom:project-'), templateId === 'enterprise-header')
    report(options.onProgress, 'rendering', '计算目录页码')
    let blob = await withUpdatedTocPageNumbers(options.bidId, rendered, result.data.include_toc)
    if (blind) {
      report(options.onProgress, 'rendering', '检查暗标身份信息与图片')
      blob = await clearBlindMetadata(blob)
      await checkBlindExport(options.bidId, blob, [options.bidderName, options.blindTerms].filter(Boolean).join('\n'), options.onBlindReview)
    }
    const fileName = '图文标书_' + result.data.bid_id + '_' + templateId + (result.data.bid_title.endsWith('（章节测试稿）') ? '_章节测试稿' : '') + '.docx'
    downloadBlob(blob, fileName)
    report(options.onProgress, 'complete', '导出完成')
    return { mode: 'template' as const, fileName }
  } catch (error) {
    if (error instanceof TocPaginationError || error instanceof BlindCheckError || blind) throw error
    if (templateId.startsWith('custom:project-') || templateId === 'enterprise-header') {
      const reason = error instanceof Error ? error.message : String(error)
      throw new Error(`所选 Word 模板导出失败：${reason}。请检查模板或改选其他模板后重试。`)
    }
    if (templateId.startsWith('custom:')) {
      report(options.onProgress, 'fallback', '自定义模板渲染失败，切换标准模板')
      try {
        const standardTemplate = await loadTemplate('default-business')
        const result = await buildExportData(options.bidId, options.catalog, 'default-business', options.onProgress, { versionId: options.versionId, selectedChapterIds: options.selectedChapterIds, includeImages: options.includeImages, includeToc: options.includeToc, selectedText: options.selectedText, bidderName: options.bidderName })
        result.data.template_name = getBidTemplateById('default-business').name
        const rendered = renderDocx(standardTemplate, result, result.sizes)
        report(options.onProgress, 'rendering', '计算目录页码')
        const blob = await withUpdatedTocPageNumbers(options.bidId, rendered, result.data.include_toc)
        const fileName = '图文标书_' + result.data.bid_id + '_default-business' + (result.data.bid_title.endsWith('（章节测试稿）') ? '_章节测试稿' : '') + '.docx'
        downloadBlob(blob, fileName)
        report(options.onProgress, 'complete', '已使用标准模板完成导出')
        return { mode: 'fallback' as const, fallbackKind: 'default-template' as const, fileName, error }
      } catch (error) {
        if (error instanceof TocPaginationError) throw error
        // Continue to the historical backend fallback if the standard client export also fails.
      }
    }
    report(options.onProgress, 'fallback', '模板导出失败，切换原生导出')
    try {
      const fallbackUrl = triggerBackendFallback(options.bidId, options.bidderName)
      return { mode: 'fallback' as const, fallbackUrl, error }
    } catch (fallbackError) {
      const primaryMessage = error instanceof Error ? error.message : String(error)
      const fallbackMessage = fallbackError instanceof Error ? fallbackError.message : String(fallbackError)
      throw new Error('模板导出失败：' + primaryMessage + '；原生导出也失败：' + fallbackMessage)
    }
  }
}
