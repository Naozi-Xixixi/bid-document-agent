import Docxtemplater from 'docxtemplater'
import ImageModule from 'docxtemplater-image-module-free'
import JSZipUtils from 'jszip-utils'
import PizZip from 'pizzip'

const DOCX_MIME_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

export class WordTemplateExportError extends Error {
  constructor(message, { cause, phase = 'render' } = {}) {
    super(message)
    this.name = 'WordTemplateExportError'
    this.phase = phase
    this.cause = cause
  }
}

function toError(error) {
  if (error instanceof Error) return error
  return new Error(String(error || '未知导出错误'))
}

function loadBinaryContent(templateUrl) {
  return new Promise((resolve, reject) => {
    try {
      JSZipUtils.getBinaryContent(templateUrl, (error, data) => {
        if (error) {
          reject(error)
          return
        }
        resolve(data)
      })
    } catch (error) {
      reject(error)
    }
  })
}

function triggerBrowserDownload(blob, fileName) {
  if (typeof document === 'undefined' || typeof URL === 'undefined') {
    throw new WordTemplateExportError('当前环境不支持浏览器文件下载', { phase: 'download' })
  }
  const downloadUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = downloadUrl
  anchor.download = fileName
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000)
}

function createImageModule(imageModuleOptions) {
  if (!imageModuleOptions) return null
  return new ImageModule({
    centered: true,
    fileType: 'docx',
    ...imageModuleOptions,
  })
}

export function createBackendFallback({ url, target = '_blank', open } = {}) {
  if (!url) throw new WordTemplateExportError('缺少后端 Word 导出地址', { phase: 'fallback' })
  const openWindow = open || ((fallbackUrl, fallbackTarget, features) => {
    if (typeof window === 'undefined' || typeof window.open !== 'function') {
      throw new WordTemplateExportError('当前环境不支持调用后端导出接口', { phase: 'fallback' })
    }
    return window.open(fallbackUrl, fallbackTarget, features)
  })
  return () => {
    openWindow(url, target, 'noopener,noreferrer')
    return { fallback: true, url }
  }
}

export class WordTemplateExport {
  constructor({ loadBinary = loadBinaryContent, download = triggerBrowserDownload } = {}) {
    this.loadBinary = loadBinary
    this.download = download
  }

  async loadTemplate(templateUrl) {
    if (!templateUrl) {
      throw new WordTemplateExportError('缺少本地 DOCX 模板地址', { phase: 'load' })
    }
    try {
      return await this.loadBinary(templateUrl)
    } catch (error) {
      throw new WordTemplateExportError('DOCX 模板加载失败', { cause: toError(error), phase: 'load' })
    }
  }

  renderTemplate(templateBinary, data, { imageModuleOptions } = {}) {
    try {
      const imageModule = createImageModule(imageModuleOptions)
      const modules = imageModule ? [imageModule] : []
      const zip = new PizZip(templateBinary)
      const doc = new Docxtemplater(zip, {
        paragraphLoop: true,
        linebreaks: true,
        modules,
      })
      doc.render(data || {})
      return doc.getZip().generate({ type: 'blob', mimeType: DOCX_MIME_TYPE })
    } catch (error) {
      throw new WordTemplateExportError('DOCX 模板渲染失败', { cause: toError(error), phase: 'render' })
    }
  }

  async export({ templateUrl, data, fileName = 'export.docx', imageModuleOptions, fallbackExport, fallbackUrl, fallbackTarget = '_blank', onError } = {}) {
    try {
      const templateBinary = await this.loadTemplate(templateUrl)
      const blob = this.renderTemplate(templateBinary, data, { imageModuleOptions })
      this.download(blob, fileName)
      return { fallback: false, blob, fileName }
    } catch (error) {
      const exportError = error instanceof WordTemplateExportError
        ? error
        : new WordTemplateExportError('Word 导出失败', { cause: toError(error), phase: 'export' })
      onError?.(exportError)
      const fallbackHandler = fallbackExport || (fallbackUrl
        ? createBackendFallback({ url: fallbackUrl, target: fallbackTarget })
        : null)
      if (fallbackHandler) {
        const fallbackResult = await fallbackHandler(exportError)
        return { fallback: true, error: exportError, result: fallbackResult }
      }
      throw exportError
    }
  }
}

export const wordTemplateExport = new WordTemplateExport()

export async function exportWordTemplate(options) {
  return wordTemplateExport.export(options)
}
