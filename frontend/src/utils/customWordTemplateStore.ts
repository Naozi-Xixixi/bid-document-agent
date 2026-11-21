import PizZip from 'pizzip'
import { companyStorageKey } from '../auth'

const DB_NAME = 'bidagent-word-template-library'
const STORE_NAME = 'templates'
const DB_VERSION = 1
const FALLBACK_KEY = 'bidagent.custom-word-templates'

export type WordTemplateFormatRules = {
  page: {
    paper: string
    orientation: string
    margins: string
    headerFooter: string
  }
  styles: {
    body: string
    heading1: string
    heading2: string
    heading3: string
  }
  table: string
  lists: string
  fixedElements: string
}

export type CustomWordTemplate = {
  id: string
  name: string
  fileName: string
  description: string
  scenario: string
  uploadedAt: string
  updatedAt: string
  size: number
  placeholders: string[]
  formatRules: WordTemplateFormatRules
  isDefault: boolean
  content: ArrayBuffer
}

export type CustomWordTemplateSummary = Omit<CustomWordTemplate, 'content'>

type StoredTemplate = CustomWordTemplate
type FallbackTemplate = Omit<StoredTemplate, 'content'> & { content: string }

function encodeArrayBuffer(content: ArrayBuffer): string {
  const bytes = new Uint8Array(content)
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000))
  }
  return btoa(binary)
}

function decodeArrayBuffer(content: string): ArrayBuffer {
  const binary = atob(content)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return bytes.buffer
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('当前浏览器不支持本地模板存储'))
      return
    }
    const request = indexedDB.open(companyStorageKey(DB_NAME), DB_VERSION)
    request.onerror = () => reject(request.error || new Error('本地模板数据库打开失败'))
    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains(STORE_NAME)) database.createObjectStore(STORE_NAME, { keyPath: 'id' })
    }
    request.onsuccess = () => resolve(request.result)
  })
}

function indexedRequest<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error || new Error('本地模板操作失败'))
  })
}

async function readFallback(): Promise<StoredTemplate[]> {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(companyStorageKey(FALLBACK_KEY))
    if (!raw) return []
    const templates = JSON.parse(raw) as FallbackTemplate[]
    return templates.flatMap(template => {
      if (typeof template.content !== 'string') return []
      try {
        return [{ ...template, content: decodeArrayBuffer(template.content) }]
      } catch {
        return []
      }
    })
  } catch {
    return []
  }
}

async function writeFallback(templates: StoredTemplate[]) {
  if (typeof localStorage !== 'undefined') {
    const serialized: FallbackTemplate[] = templates.map(template => ({ ...template, content: encodeArrayBuffer(template.content) }))
    localStorage.setItem(companyStorageKey(FALLBACK_KEY), JSON.stringify(serialized))
  }
}

async function listStoredTemplates(): Promise<StoredTemplate[]> {
  try {
    const database = await openDatabase()
    const transaction = database.transaction(STORE_NAME, 'readonly')
    const result = await indexedRequest(transaction.objectStore(STORE_NAME).getAll())
    database.close()
    return result || []
  } catch {
    return readFallback()
  }
}

async function putStoredTemplate(template: StoredTemplate) {
  try {
    const database = await openDatabase()
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    await indexedRequest(transaction.objectStore(STORE_NAME).put(template))
    database.close()
  } catch {
    const templates = await readFallback()
    const index = templates.findIndex(item => item.id === template.id)
    if (index >= 0) templates[index] = template
    else templates.push(template)
    await writeFallback(templates)
  }
}

async function deleteStoredTemplate(id: string) {
  try {
    const database = await openDatabase()
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    await indexedRequest(transaction.objectStore(STORE_NAME).delete(id))
    database.close()
  } catch {
    await writeFallback((await readFallback()).filter(item => item.id !== id))
  }
}

function xmlAttribute(tag: string, name: string): string {
  const match = tag.match(new RegExp(`(?:^|\\s)${name}="([^"]*)"`))
  return match?.[1] || ''
}

function xmlText(xml: string): string {
  return xml.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'")
}

function styleRule(stylesXml: string, styleId: string, label: string): string {
  const styleMatch = stylesXml.match(new RegExp(`<w:style\\b[^>]*w:styleId="${styleId}"[\\s\\S]*?<\\/w:style>`))
  if (!styleMatch) return `${label}：未识别`
  const style = styleMatch[0]
  const fontMatch = style.match(/<w:rFonts\b[^>]*>/)
  const sizeMatch = style.match(/<w:sz\b[^>]*>/)
  const spacingMatch = style.match(/<w:spacing\b[^>]*>/)
  const font = fontMatch ? xmlAttribute(fontMatch[0], 'w:eastAsia') || xmlAttribute(fontMatch[0], 'w:ascii') : ''
  const size = sizeMatch ? xmlAttribute(sizeMatch[0], 'w:val') : ''
  const line = spacingMatch ? xmlAttribute(spacingMatch[0], 'w:line') : ''
  return `${label}：${font || '继承字体'}${size ? `，字号 ${Number(size) / 2} 磅` : ''}${line ? `，行距 ${line}` : ''}`
}

function extractFormatRules(documentXml: string, stylesXml: string, settingsXml: string): WordTemplateFormatRules {
  const section = documentXml.match(/<w:sectPr[\s\S]*?<\/w:sectPr>/)?.[0] || ''
  const pageSize = section.match(/<w:pgSz\b[^>]*>/)?.[0] || ''
  const pageMargin = section.match(/<w:pgMar\b[^>]*>/)?.[0] || ''
  const width = xmlAttribute(pageSize, 'w:w')
  const height = xmlAttribute(pageSize, 'w:h')
  const orientation = xmlAttribute(pageSize, 'w:orient') || 'portrait'
  const top = xmlAttribute(pageMargin, 'w:top')
  const right = xmlAttribute(pageMargin, 'w:right')
  const bottom = xmlAttribute(pageMargin, 'w:bottom')
  const left = xmlAttribute(pageMargin, 'w:left')
  const header = section.includes('<w:headerReference')
  const footer = section.includes('<w:footerReference')
  const table = documentXml.includes('<w:tblBorders') || documentXml.includes('<w:tblStyle') ? '已检测到模板表格边框/样式' : '未检测到固定表格样式'
  const ordered = documentXml.includes('<w:numPr')
  const fixedElements = [header ? '页眉' : '', footer ? '页脚' : '', settingsXml.includes('w:mirrorMargins') ? '镜像页边距' : ''].filter(Boolean)
  return {
    page: {
      paper: width && height ? `${width} × ${height} twip` : '未识别',
      orientation: orientation === 'landscape' ? '横向' : '纵向',
      margins: top && right && bottom && left ? `上 ${top} / 右 ${right} / 下 ${bottom} / 左 ${left} twip` : '未识别',
      headerFooter: header || footer ? `保留${header ? '页眉' : ''}${header && footer ? '、' : ''}${footer ? '页脚' : ''}` : '未检测到页眉页脚引用',
    },
    styles: {
      body: styleRule(stylesXml, 'Normal', '正文'),
      heading1: styleRule(stylesXml, 'Heading1', '一级标题'),
      heading2: styleRule(stylesXml, 'Heading2', '二级标题'),
      heading3: styleRule(stylesXml, 'Heading3', '三级标题'),
    },
    table,
    lists: ordered ? '已检测到有序/无序列表编号定义' : '未检测到列表编号定义',
    fixedElements: fixedElements.length ? fixedElements.join('、') : '封面、水印等固定元素随模板原样保留',
  }
}

export function inspectWordTemplate(content: ArrayBuffer) {
  let zip: PizZip
  try {
    zip = new PizZip(content)
  } catch {
    throw new Error('文件不是可读取的 DOCX 格式')
  }
  const documentXml = zip.file('word/document.xml')?.asText() || ''
  if (!documentXml) throw new Error('DOCX 缺少正文文件，模板格式不兼容')
  const stylesXml = zip.file('word/styles.xml')?.asText() || ''
  const settingsXml = zip.file('word/settings.xml')?.asText() || ''
  const plainText = xmlText(documentXml)
  const placeholders = Array.from(plainText.matchAll(/\{[#\/]?([A-Za-z0-9_.-]+)\}/g)).map(match => match[1]).filter((value, index, values) => values.indexOf(value) === index)
  const hasTitle = placeholders.includes('bid_title')
  const hasChapters = placeholders.includes('chapters') || placeholders.includes('chapter_rows')
  if (!hasTitle || !hasChapters) throw new Error('模板缺少必要占位符：至少需要 {bid_title} 和 {#chapters}（或 {#chapter_rows}）')
  return { placeholders, formatRules: extractFormatRules(documentXml, stylesXml, settingsXml) }
}

/**
 * Reads a completed Word document as a format reference. Reference files do
 * not need docxtemplater placeholders; they only contribute inspectable layout
 * rules to the format center.
 */
export function inspectWordFormatReference(content: ArrayBuffer) {
  let zip: PizZip
  try {
    zip = new PizZip(content)
  } catch {
    throw new Error('文件不是可读取的 DOCX 格式')
  }
  const documentXml = zip.file('word/document.xml')?.asText() || ''
  if (!documentXml) throw new Error('DOCX 缺少正文文件，无法提取格式')
  const stylesXml = zip.file('word/styles.xml')?.asText() || ''
  const settingsXml = zip.file('word/settings.xml')?.asText() || ''
  return { formatRules: extractFormatRules(documentXml, stylesXml, settingsXml) }
}

function summary(template: StoredTemplate): CustomWordTemplateSummary {
  const { content: _content, ...rest } = template
  return rest
}

export async function listCustomWordTemplates(): Promise<CustomWordTemplateSummary[]> {
  const templates = await listStoredTemplates()
  return templates.sort((a, b) => Number(b.isDefault) - Number(a.isDefault) || b.updatedAt.localeCompare(a.updatedAt)).map(summary)
}

export async function getCustomWordTemplate(id: string): Promise<CustomWordTemplate | null> {
  if (!id.startsWith('custom:')) return null
  const templates = await listStoredTemplates()
  return templates.find(template => template.id === id) || null
}

export async function saveCustomWordTemplate(template: CustomWordTemplate): Promise<CustomWordTemplateSummary> {
  const templates = await listStoredTemplates()
  const next = { ...template, isDefault: templates.length ? template.isDefault : true }
  if (next.isDefault) {
    for (const item of templates) if (item.isDefault) await putStoredTemplate({ ...item, isDefault: false })
  }
  await putStoredTemplate(next)
  return summary(next)
}

export async function updateCustomWordTemplate(id: string, patch: Partial<Pick<CustomWordTemplate, 'name' | 'description' | 'scenario' | 'isDefault'>>): Promise<CustomWordTemplateSummary | null> {
  const template = await getCustomWordTemplate(id)
  if (!template) return null
  if (patch.isDefault) {
    for (const item of await listStoredTemplates()) if (item.id !== id && item.isDefault) await putStoredTemplate({ ...item, isDefault: false })
  }
  const next = { ...template, ...patch, updatedAt: new Date().toISOString() }
  await putStoredTemplate(next)
  return summary(next)
}

export async function removeCustomWordTemplate(id: string) {
  const template = await getCustomWordTemplate(id)
  await deleteStoredTemplate(id)
  if (template?.isDefault) {
    const next = (await listStoredTemplates()).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0]
    if (next) await putStoredTemplate({ ...next, isDefault: true })
  }
}

export async function importCustomWordTemplate(file: File, metadata: { name?: string; description?: string; scenario?: string } = {}) {
  if (!/\.docx$/i.test(file.name)) throw new Error('仅支持上传 .docx 文件')
  const content = await file.arrayBuffer()
  const inspection = inspectWordTemplate(content)
  const now = new Date().toISOString()
  const template: CustomWordTemplate = {
    id: `custom:${crypto.randomUUID()}`,
    name: metadata.name?.trim() || file.name.replace(/\.docx$/i, ''),
    fileName: file.name,
    description: metadata.description?.trim() || '用户上传的 Word 标书模板',
    scenario: metadata.scenario?.trim() || '自定义招标项目',
    uploadedAt: now,
    updatedAt: now,
    size: file.size,
    placeholders: inspection.placeholders,
    formatRules: inspection.formatRules,
    isDefault: false,
    content,
  }
  return saveCustomWordTemplate(template)
}

export async function importRemoteWordTemplate(content: ArrayBuffer, metadata: { id: string; name: string; fileName?: string; description?: string; scenario?: string; isDefault?: boolean }) {
  if (!metadata.id.startsWith('custom:')) throw new Error('远程模板 ID 无效')
  const inspection = inspectWordTemplate(content)
  const existing = await getCustomWordTemplate(metadata.id)
  const stored = await listStoredTemplates()
  const now = new Date().toISOString()
  const isDefault = metadata.isDefault ?? existing?.isDefault ?? stored.length === 0
  if (isDefault) {
    for (const item of stored) if (item.id !== metadata.id && item.isDefault) await putStoredTemplate({ ...item, isDefault: false })
  }
  const template: CustomWordTemplate = {
    id: metadata.id,
    name: metadata.name.trim() || existing?.name || '项目格式模板',
    fileName: metadata.fileName || existing?.fileName || `${metadata.name || '项目格式模板'}.docx`,
    description: metadata.description?.trim() || existing?.description || '从已上传投标文件中提取的版式模板',
    scenario: metadata.scenario?.trim() || existing?.scenario || '当前招投标项目',
    uploadedAt: existing?.uploadedAt || now,
    updatedAt: now,
    size: content.byteLength,
    placeholders: inspection.placeholders,
    formatRules: inspection.formatRules,
    isDefault,
    content,
  }
  await putStoredTemplate(template)
  return summary(template)
}

export async function downloadCustomWordTemplate(id: string) {
  const template = await getCustomWordTemplate(id)
  if (!template || typeof document === 'undefined') return false
  const url = URL.createObjectURL(new Blob([template.content], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = template.fileName || `${template.name}.docx`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  return true
}
