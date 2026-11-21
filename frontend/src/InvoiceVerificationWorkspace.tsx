import { useEffect, useMemo, useRef, useState } from 'react'
import { type InvoiceOcrResult, wordagentApi } from './wordagentApi'

type Props = { onHome: () => void }
type ExternalStatus = 'manual_pending' | 'passed' | 'failed' | 'review'

const OFFICIAL_PLATFORM_URL = 'https://inv-veri.chinatax.gov.cn/'
const FISCAL_RECEIPT_PLATFORM_URL = 'https://pjcy.mof.gov.cn/'
const FISCAL_RECEIPT_GUIDE_URL = 'https://www.mof.gov.cn/zaixianfuwu/zxcx/czpjcy/'

const FIELD_LABELS: Array<{ key: string; label: string; placeholder: string }> = [
  { key: 'invoice_code', label: '发票代码', placeholder: '官方平台可选字段' },
  { key: 'invoice_number', label: '发票号码', placeholder: '识别后可修改' },
  { key: 'issue_date', label: '开票日期', placeholder: 'YYYY-MM-DD' },
  { key: 'amount_excluding_tax', label: '不含税金额', placeholder: '官方查验必填' },
  { key: 'total_amount', label: '价税合计', placeholder: '识别后可修改' },
  { key: 'tax_amount', label: '税额', placeholder: '识别后可修改' },
  { key: 'title', label: '发票抬头', placeholder: '购买方名称' },
  { key: 'tax_no', label: '税号', placeholder: '统一社会信用代码或税号' },
]

const RECEIPT_FIELD_LABELS = [
  { key: 'receipt_code', label: '票据代码' },
  { key: 'receipt_number', label: '票据号码' },
  { key: 'verification_code', label: '校验码' },
  { key: 'payer', label: '交款人' },
  { key: 'payer_tax_no', label: '交款人统一社会信用代码' },
  { key: 'issue_date', label: '开票日期' },
  { key: 'total_amount', label: '金额合计' },
] as const

function fieldValue(fields: InvoiceOcrResult['fields'], key: string) {
  if (key === 'amount_excluding_tax') {
    if (fields.amount_excluding_tax) return fields.amount_excluding_tax
    const total = Number(String(fields.total_amount || '').replace(/[,，]/g, ''))
    const tax = Number(String(fields.tax_amount || '').replace(/[,，]/g, ''))
    if (fields.total_amount && fields.tax_amount && Number.isFinite(total) && Number.isFinite(tax) && total >= tax) {
      return (total - tax).toFixed(2)
    }
  }
  return fields[key] || ''
}

function localVerify(fields: InvoiceOcrResult['fields']): InvoiceOcrResult['local_verification'] {
  const required = [
    ['invoice_number', '发票号码'],
    ['issue_date', '开票日期'],
    ['amount_excluding_tax', '不含税金额'],
    ['title', '发票抬头'],
    ['tax_no', '税号'],
  ] as const
  const missingFields = required.filter(([key]) => !fieldValue(fields, key).trim()).map(([, label]) => label)
  const checks: InvoiceOcrResult['local_verification']['checks'] = []
  if (missingFields.length) checks.push({ level: missingFields.length >= 2 ? 'high' : 'medium', message: `缺少：${missingFields.join('、')}` })

  const date = fieldValue(fields, 'issue_date').trim()
  const parsedDate = new Date(`${date}T00:00:00Z`)
  if (date && (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== date)) checks.push({ level: 'medium', message: '开票日期格式需要人工确认' })
  const amount = fieldValue(fields, 'amount_excluding_tax').replace(/[,，]/g, '').trim()
  if (amount && !/^[+-]?\d+(?:\.\d{1,2})?$/.test(amount)) checks.push({ level: 'medium', message: '不含税金额格式需要人工确认' })
  const invoiceCode = fieldValue(fields, 'invoice_code').trim()
  if (invoiceCode && !/^[0-9A-Za-z-]{8,30}$/.test(invoiceCode)) checks.push({ level: 'medium', message: '发票代码格式需要人工确认' })

  const officialRequired = [
    ['invoice_number', '发票号码'],
    ['issue_date', '开票日期'],
    ['amount_excluding_tax', '开具金额（不含税）'],
  ] as const
  const officialMissing = officialRequired.filter(([key]) => !fieldValue(fields, key).trim()).map(([, label]) => label)
  const officialReady = !officialMissing.length && !checks.some(check => check.message === '开票日期格式需要人工确认' || check.message === '不含税金额格式需要人工确认')
  return {
    status: checks.some(item => item.level === 'high') ? 'high_risk' : checks.length ? 'review' : 'passed',
    checks,
    missing_fields: missingFields,
    official_ready: officialReady,
    official_missing_fields: officialMissing,
  }
}

function statusText(status: InvoiceOcrResult['local_verification']['status']) {
  if (status === 'passed') return '字段检查通过'
  if (status === 'high_risk') return '存在高风险项'
  return '待补充或人工确认'
}

function externalStatusText(status: ExternalStatus) {
  if (status === 'passed') return '人工记录：查验通过'
  if (status === 'failed') return '人工记录：未通过'
  if (status === 'review') return '人工记录：待复核'
  return '等待官方查验'
}

function InvoiceIcon() {
  return (
    <svg className="invoice-feature-icon" viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <path d="M15 9h27l7 7v39H15V9Z" stroke="currentColor" strokeWidth="3" />
      <path d="M42 9v9h9M22 28h20M22 36h20M22 44h13" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      <circle cx="46" cy="47" r="8" fill="var(--page-bg)" stroke="currentColor" strokeWidth="3" />
      <path d="m42.5 47 2.4 2.4 4.6-5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function compactDate(value: string) {
  return value.replace(/\D/g, '').slice(0, 8)
}

async function copyToClipboard(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return true
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  return copied
}

export function InvoiceVerificationWorkspace({ onHome }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const fieldRefs = useRef<Record<string, HTMLInputElement | null>>({})
  const [result, setResult] = useState<InvoiceOcrResult | null>(null)
  const [fields, setFields] = useState<InvoiceOcrResult['fields']>({})
  const [previewUrl, setPreviewUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [externalStatus, setExternalStatus] = useState<ExternalStatus>('manual_pending')
  const [externalNote, setExternalNote] = useState('')
  const [externalCheckedAt, setExternalCheckedAt] = useState('')
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])

  const isNonTaxReceipt = result?.document_type === 'non_tax_receipt'
  const digitalInvoiceWithoutCode = !isNonTaxReceipt && Boolean(result?.ocr_text?.includes('电子发票（增值税') && !fields.invoice_code)
  const verification = useMemo(() => localVerify(fields), [fields])
  const officialPayload = useMemo(() => ({
    invoice_code: fieldValue(fields, 'invoice_code'),
    invoice_number: fieldValue(fields, 'invoice_number'),
    issue_date: compactDate(fieldValue(fields, 'issue_date')),
    amount_excluding_tax: fieldValue(fields, 'amount_excluding_tax'),
  }), [fields])

  const officialCopyText = useMemo(() => [
    `发票代码：${officialPayload.invoice_code || '（可选，未识别）'}`,
    `发票号码：${officialPayload.invoice_number || '（待补充）'}`,
    `开票日期：${officialPayload.issue_date || '（待补充，格式 YYYYMMDD）'}`,
    `开具金额（不含税）：${officialPayload.amount_excluding_tax || '（待补充）'}`,
    '验证码：请在国家税务总局查验平台页面输入',
  ].join('\n'), [officialPayload])

  async function handleFile(file?: File | null) {
    if (!file || busy) return
    setBusy(true)
    setError('')
    setNotice('正在上传并执行 OCR 识别…')
    setExternalStatus('manual_pending')
    setExternalNote('')
    setExternalCheckedAt('')
    // A failed second upload must not leave the first invoice's fields actionable.
    setResult(null)
    setFields({})
    setPreviewUrl(file.type.startsWith('image/') ? URL.createObjectURL(file) : '')
    try {
      const response = await wordagentApi.ocrInvoice(file)
      if (response.code !== 0) throw new Error(response.message)
      setResult(response.data)
      setFields(response.data.fields || {})
      setExternalStatus(response.data.external_verification.status === 'manual_pending' ? 'manual_pending' : 'review')
      setNotice(response.data.status !== 'ocr_completed' ? 'OCR 当前不可用，请检查 PaddleOCR 环境。' : response.data.document_type === 'non_tax_receipt' ? '已识别为非税收入票据。请核对票据字段，并通过票据开具方指定的渠道核验。' : 'OCR 识别完成，请先核对字段，再打开官方平台查验。')
    } catch (err) {
      setError(err instanceof Error ? err.message : '发票识别失败，请稍后重试')
      setNotice('')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  function updateField(key: string, value: string) {
    setFields(prev => ({ ...prev, [key]: value }))
    setExternalStatus('manual_pending')
    setExternalCheckedAt('')
    setExternalNote('')
    setResult(current => current ? {
      ...current,
      fields: { ...current.fields, [key]: value },
      external_verification: { ...current.external_verification, status: 'manual_pending', checked_at: undefined, message: '字段已修改，需要重新查验' },
    } : current)
    setNotice('字段已修改，本地核验结果已实时更新；如已查验，请重新核对官方平台字段。')
  }

  async function copyOfficialValues() {
    if (!result || busy || isNonTaxReceipt) return
    try {
      const copied = await copyToClipboard(officialCopyText)
      setNotice(copied ? '官方查验字段已复制，可粘贴到查验记录或逐项填入官方页面。' : '复制失败，请直接参考下方字段填写官方页面。')
    } catch {
      setNotice('复制失败，请直接参考下方字段填写官方页面。')
    }
  }

  async function copyReceiptValues() {
    if (!result || busy || !isNonTaxReceipt) return
    const receipt = result.receipt_fields || {}
    const copyText = [
      `票据代码：${receipt.receipt_code || '（未识别）'}`,
      `票据号码：${receipt.receipt_number || '（未识别）'}`,
      `校验码：${receipt.verification_code || '（未识别）'}`,
      `票据金额：${receipt.total_amount || '（未识别）'}`,
      `开票日期：${receipt.issue_date || '（未识别）'}`,
    ].join('\n')
    try {
      const copied = await copyToClipboard(copyText)
      setNotice(copied ? '财政电子票据查验字段已复制，请对照原件后填入财政部平台。' : '复制失败，请直接参考识别结果填写财政部平台。')
    } catch {
      setNotice('复制失败，请直接参考识别结果填写财政部平台。')
    }
  }

  function recordExternalResult(nextStatus: ExternalStatus) {
    if (!result || busy || isNonTaxReceipt) return
    const checkedAt = new Date().toLocaleString('zh-CN', { hour12: false })
    const message = nextStatus === 'passed' ? '用户已根据官方页面结果记录为查验通过。' : nextStatus === 'failed' ? '用户已根据官方页面结果记录为查验未通过。' : '用户已记录为需要人工复核。'
    setExternalStatus(nextStatus)
    setExternalCheckedAt(checkedAt)
    setResult(current => current ? {
      ...current,
      external_verification: {
        ...current.external_verification,
        status: nextStatus,
        message,
        platform: current.external_verification.platform || '国家税务总局全国增值税发票查验平台',
        platform_url: current.external_verification.platform_url || OFFICIAL_PLATFORM_URL,
        checked_at: checkedAt,
      },
    } : current)
    setNotice('已在本次页面中记录人工查验结果，刷新或离开页面后不保留。')
  }

  function handleOfficialCheck() {
    if (!result || busy || isNonTaxReceipt) return
    if (!verification.official_ready) {
      setNotice(`请先补齐：${verification.official_missing_fields?.join('、') || '官方查验字段'}`)
      const missingKey = !fieldValue(fields, 'invoice_number')
        ? 'invoice_number'
        : !fieldValue(fields, 'issue_date')
          ? 'issue_date'
          : 'amount_excluding_tax'
      fieldRefs.current[missingKey]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      fieldRefs.current[missingKey]?.focus()
      return
    }
    window.open(OFFICIAL_PLATFORM_URL, '_blank', 'noopener,noreferrer')
    setNotice(result.source_type === 'pdf'
      ? '官网打开后点“导入”，选择刚才上传的原始 PDF；核对官网带出的字段，再输入验证码并查验。'
      : '请在官方平台完成查验，再手动记录实际结果。若新窗口未打开，可点击下方平台链接。')
  }

  return (
    <div className="invoice-workspace-shell">
      <header className="top-nav">
        <button className="logo-button" type="button" onClick={onHome}>示例公司智能标书助手</button>
        <button className="light-btn" type="button" onClick={onHome}>返回首页</button>
      </header>
      <main className="module-page invoice-module-page">
        <section className="module-header-card">
          <div>
            <p>INVOICE CHECK</p>
            <h1>发票智能核验</h1>
            <span>先用 OCR 读取发票字段，再一键打开国家税务总局查验平台完成官方核对。</span>
          </div>
          <div className="invoice-header-mark"><InvoiceIcon /><span>识别 · 核对 · 留痕</span></div>
        </section>

        <section className="invoice-workspace-grid">
          <div className="invoice-main-column">
            <div className="form-card invoice-upload-card">
              <div className="invoice-card-heading"><div><p>STEP 01</p><h2>上传发票</h2></div><span>支持 JPG、PNG、WEBP、BMP、PDF</span></div>
              <button className="invoice-dropzone" type="button" onClick={() => inputRef.current?.click()} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); void handleFile(event.dataTransfer.files?.[0]) }} disabled={busy}>
                <InvoiceIcon />
                <strong>{busy ? '正在识别发票…' : '点击选择发票文件'}</strong>
                <span>{busy ? 'PaddleOCR 正在读取图片信息' : '也可以将文件拖入此区域'}</span>
              </button>
              <input ref={inputRef} type="file" accept=".jpg,.jpeg,.png,.webp,.bmp,.pdf,image/*" hidden onChange={event => void handleFile(event.target.files?.[0])} />
              {notice && <div className="invoice-notice">{notice}</div>}
              {error && <div className="graphic-error">{error}</div>}
            </div>

            <div className="form-card invoice-result-card">
              <div className="invoice-card-heading"><div><p>STEP 02</p><h2>识别结果</h2></div><span>{result?.file_name || '尚未上传文件'}</span></div>
              {!result && <div className="invoice-empty">上传发票或票据后，识别字段会显示在这里。</div>}
              {result && <>
                {isNonTaxReceipt && <div className="invoice-notice">文件类型：中央非税收入统一票据（电子）。原件未填写的字段会显示为“原件为空”；票据金额不是增值税发票的不含税金额。</div>}
                {digitalInvoiceWithoutCode && <div className="invoice-notice">这张数电发票原件未印发票代码，留空即可；发票号码、购买方名称和金额请对照原件核对。</div>}
                <div className="invoice-result-layout">
                  <div className="invoice-preview-panel">
                    {previewUrl ? <img src={previewUrl} alt="发票预览" /> : <div className="invoice-pdf-placeholder">PDF<br /><small>已接收文件</small></div>}
                  </div>
                  <div className="invoice-field-grid">
                    {isNonTaxReceipt
                      ? RECEIPT_FIELD_LABELS.map(item => <label key={item.key}>{item.label}<input value={result.receipt_fields?.[item.key] || ''} placeholder="原件为空或未识别" readOnly /></label>)
                      : FIELD_LABELS.map(item => <label key={item.key}>{item.label}<input ref={element => { fieldRefs.current[item.key] = element }} value={fieldValue(fields, item.key)} placeholder={item.placeholder} onChange={event => updateField(item.key, event.target.value)} /></label>)}
                  </div>
                </div>
                <details className="invoice-ocr-details"><summary>查看原始识别文本</summary><pre>{result.ocr_text || '暂无识别文本'}</pre></details>
              </>}
            </div>
          </div>

          <aside className="invoice-side-column">
            <div className="form-card invoice-check-card">
              <div className="invoice-card-heading"><div><p>STEP 03</p><h2>本地核验</h2></div><span className={`invoice-status ${result ? isNonTaxReceipt ? 'review' : verification.status : 'pending'}`}>{result ? isNonTaxReceipt ? '票据类型已识别' : statusText(verification.status) : '待上传文件'}</span></div>
              {!result && <div className="invoice-empty">等待 OCR 结果</div>}
              {result && isNonTaxReceipt && <div className="invoice-result-banner review"><strong>!</strong><span>非税收入票据<small>已识别票据字段；票据真伪尚未核验。交款人统一社会信用代码在原件上为空。</small></span></div>}
              {result && !isNonTaxReceipt && <>
                <div className={`invoice-result-banner ${verification.status}`}><strong>{verification.status === 'passed' ? '✓' : '!'}</strong><span>{statusText(verification.status)}<small>检查字段完整性与格式，不代表税务平台真伪结论</small></span></div>
                <div className="invoice-check-list">
                  {verification.checks.length ? verification.checks.map((check, index) => <div className={`invoice-check-row ${check.level}`} key={`${check.message}-${index}`}><b>{check.level === 'high' ? '高风险' : '待确认'}</b><span>{check.message}</span></div>) : <div className="invoice-check-row passed"><b>通过</b><span>本地已识别发票号码、日期、金额、抬头和税号。</span></div>}
                </div>
              </>}
            </div>

            <div className="form-card invoice-external-card">
              <div className="invoice-card-heading"><div><p>STEP 04</p><h2>官方平台核验</h2></div><span className={`invoice-status ${externalStatus}`}>{isNonTaxReceipt ? '需按票据渠道核验' : externalStatusText(externalStatus)}</span></div>
              {isNonTaxReceipt ? <>
                <p>这类票据可以在财政部全国财政电子票据查验平台核验。请核对票据代码、票据号码、校验码、金额和开票日期，再按平台提示完成查验；本页面尚未得到真伪结论。</p>
                <div className="invoice-official-fields">
                  <div><span>票据代码</span><b>{result?.receipt_fields?.receipt_code || '待核对'}</b></div>
                  <div><span>票据号码</span><b>{result?.receipt_fields?.receipt_number || '待核对'}</b></div>
                  <div><span>校验码</span><b>{result?.receipt_fields?.verification_code || '待核对'}</b></div>
                  <div><span>票据金额</span><b>{result?.receipt_fields?.total_amount || '待核对'}</b></div>
                  <div><span>开票日期</span><b>{result?.receipt_fields?.issue_date || '待核对'}</b></div>
                </div>
                <div className="invoice-external-actions">
                  <button className="light-btn" type="button" onClick={() => void copyReceiptValues()}>复制查验信息</button>
                  <button className="dark-btn" type="button" onClick={() => window.open(FISCAL_RECEIPT_PLATFORM_URL, '_blank', 'noopener,noreferrer')}>打开财政部平台</button>
                </div>
                <a className="invoice-platform-link" href={FISCAL_RECEIPT_GUIDE_URL} target="_blank" rel="noreferrer">财政部官网查验入口</a>
              </> : <>
              <p>官方平台会要求输入验证码，最终结果以国家税务总局页面为准。</p>
              {result?.source_type === 'pdf' && <div className="invoice-notice">省去逐项输入：在官网点击“导入”，选择这份原始 PDF；官网读取字段后，请核对并输入验证码。</div>}
              <div className="invoice-official-fields">
                <div><span>发票号码</span><b>{officialPayload.invoice_number || '待补充'}</b></div>
                <div><span>开票日期</span><b>{officialPayload.issue_date || '待补充'}</b></div>
                <div><span>不含税金额</span><b>{officialPayload.amount_excluding_tax || '待补充'}</b></div>
                <div><span>发票代码</span><b>{officialPayload.invoice_code || '可不填'}</b></div>
              </div>
              <div className="invoice-external-actions">
                <button className="light-btn" type="button" disabled={!result} onClick={() => void copyOfficialValues()}>复制查验信息</button>
                <button className="dark-btn" type="button" disabled={!result || busy} onClick={handleOfficialCheck}>打开官方平台查验</button>
              </div>
              {result && !verification.official_ready && <small>补齐 {verification.official_missing_fields?.join('、') || '官方查验字段'} 后可打开平台。</small>}
              <a className="invoice-platform-link" href={OFFICIAL_PLATFORM_URL} target="_blank" rel="noreferrer">国家税务总局全国增值税发票查验平台</a>
              <div className={`invoice-official-record ${externalStatus}`}>
                <strong>人工记录查验结果</strong>
                <span>请根据官方页面的实际结果填写。此记录仅保留在本次页面中，刷新或离开后不保留。</span>
                <div className="invoice-result-actions">
                  <button className={externalStatus === 'passed' ? 'selected' : ''} type="button" disabled={!result} onClick={() => recordExternalResult('passed')}>查验通过</button>
                  <button className={externalStatus === 'failed' ? 'selected' : ''} type="button" disabled={!result} onClick={() => recordExternalResult('failed')}>未通过</button>
                  <button className={externalStatus === 'review' ? 'selected' : ''} type="button" disabled={!result} onClick={() => recordExternalResult('review')}>待复核</button>
                </div>
                {result && <textarea value={externalNote} placeholder="可记录官方返回的异常说明或查验备注" onChange={event => setExternalNote(event.target.value)} />}
                {externalCheckedAt && <small>记录时间：{externalCheckedAt}{externalNote ? ` · ${externalNote}` : ''}</small>}
              </div>
              </>}
            </div>
          </aside>
        </section>
      </main>
    </div>
  )
}

export default InvoiceVerificationWorkspace
