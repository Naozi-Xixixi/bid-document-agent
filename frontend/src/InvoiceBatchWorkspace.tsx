import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE, authRequest, getAuthToken } from './auth'
import type { InvoiceOcrResult } from './wordagentApi'

type Item = { id: string; batch_id: string; filename: string; ocr_status: 'pending' | 'processing' | 'completed' | 'failed'; error: string; result: InvoiceOcrResult; verification_status: 'manual_pending' | 'passed' | 'failed' | 'review'; verification_note: string; has_evidence: boolean; verified_by: string; verified_at: string; revision: number }
type Batch = { id: string; title: string; created_by: string; created_at: string; items: Item[]; summary: { total: number; pending: number; ocr_failed: number; passed: number; failed: number; review: number; unverified: number } }
type BatchListItem = Pick<Batch, 'id' | 'title' | 'created_by' | 'created_at'>
type History = { id: string; action: string; actor: string; created_at: string; snapshot: Item }
type Envelope<T> = { code: number; message: string; data: T }
const API = '/invoice/batches'
const statusText: Record<string, string> = { manual_pending: '待人工查验', passed: '人工记录：通过', failed: '人工记录：未通过', review: '人工记录：待复核' }
const eventText: Record<string, string> = { fields_before: '字段修改前', fields_changed: '字段修改后', verification_before: '查验记录修改前', manual_verification: '人工查验记录' }
const ocrText: Record<string, string> = { pending: '排队中', processing: '识别中', completed: '识别完成', failed: '识别失败' }
const vatFields = [['invoice_code','发票代码'],['invoice_number','发票号码'],['issue_date','开票日期'],['amount_excluding_tax','不含税金额'],['total_amount','价税合计'],['tax_amount','税额'],['title','发票抬头'],['tax_no','税号']]
const receiptFields = [['receipt_code','票据代码'],['receipt_number','票据号码'],['verification_code','校验码'],['issue_date','开票日期'],['total_amount','金额合计'],['payer','交款人'],['payer_tax_no','交款人统一社会信用代码']]
const displayDate = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const msg = (error: unknown) => error instanceof Error ? error.message : String(error)
const cleanFields = (value: Record<string, string | undefined> | undefined) => Object.fromEntries(Object.entries(value || {}).map(([key, text]) => [key, text || '']))

async function download(path: string, name: string) {
  const headers = new Headers(); const token = getAuthToken(); if (token) headers.set('Authorization', 'Bearer ' + token)
  const response = await fetch(API_BASE + path, { headers, credentials: 'include' })
  if (!response.ok) throw new Error('下载失败（' + response.status + '）')
  const blob = await response.blob()
  const ext: Record<string, string> = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/bmp': '.bmp' }
  if (!name.includes('.') && ext[blob.type]) name += ext[blob.type]
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export function InvoiceBatchWorkspace({ onHome }: { onHome: () => void }) {
  const [batches, setBatches] = useState<BatchListItem[]>([])
  const [selectedBatch, setSelectedBatch] = useState('')
  const [batch, setBatch] = useState<Batch | null>(null)
  const [selectedItem, setSelectedItem] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [editingRevision, setEditingRevision] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [evidence, setEvidence] = useState<File | null>(null)
  const [history, setHistory] = useState<History[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const selectedRef = useRef(''); selectedRef.current = selectedBatch
  const item = batch?.items.find(value => value.id === selectedItem)
  const receipt = item?.result?.document_type === 'non_tax_receipt'
  const itemBase = selectedBatch && selectedItem ? API + '/' + selectedBatch + '/items/' + selectedItem : ''

  const refreshList = useCallback(async () => {
    const res = await authRequest<Envelope<BatchListItem[]>>(API)
    setBatches(res.data)
  }, [])
  const refreshBatch = useCallback(async (id: string) => {
    const res = await authRequest<Envelope<Batch>>(API + '/' + id)
    if (selectedRef.current === id) setBatch(res.data)
    return res.data
  }, [])
  useEffect(() => { void refreshList().catch(e => setError(msg(e))) }, [refreshList])
  useEffect(() => {
    setBatch(null); setSelectedItem(''); setHistory([])
    if (!selectedBatch) return
    void refreshBatch(selectedBatch).catch(e => setError(msg(e)))
    const timer = window.setInterval(() => { void refreshBatch(selectedBatch).catch(e => setError(msg(e))) }, 3000)
    return () => window.clearInterval(timer)
  }, [selectedBatch, refreshBatch])
  useEffect(() => {
    const chosen = batch?.items.find(value => value.id === selectedItem)
    setFields(cleanFields(chosen?.result?.document_type === 'non_tax_receipt' ? chosen.result.receipt_fields : chosen?.result?.fields))
    setEditingRevision(chosen?.revision ?? null)
    setNote(chosen?.verification_note || '')
    setEvidence(null); setHistory([])
    // A background refresh must not overwrite unsaved edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBatch, selectedItem])
  // The selected item may be opened while its OCR is still running.
  useEffect(() => {
    if (item?.ocr_status === 'completed' && editingRevision !== null && editingRevision < item.revision && !Object.keys(fields).length) {
      setFields(cleanFields(receipt ? item.result.receipt_fields : item.result.fields))
      setEditingRevision(item.revision)
    }
  }, [item, editingRevision, fields, receipt])

  async function create() {
    if (!files.length || busy) return
    setBusy(true); setError(''); setNotice('正在上传文件…')
    try {
      const form = new FormData(); files.forEach(file => form.append('files', file))
      const res = await authRequest<Envelope<Batch>>(API, { method: 'POST', body: form })
      setFiles([]); if (fileInputRef.current) fileInputRef.current.value = ''; await refreshList(); setSelectedBatch(res.data.id)
      setNotice('批次已保存，后台逐张识别。可离开页面，稍后回来查看。')
    } catch (e) { setError(msg(e)); setNotice('') } finally { setBusy(false) }
  }
  async function resumeBatch() {
    setBusy(true); setError('')
    try { await authRequest(API + '/' + selectedBatch + '/resume', { method: 'POST' }); setNotice('已继续处理未完成的发票；无有效原文件的记录请重新上传。'); await refreshBatch(selectedBatch) }
    catch(e) { setError(msg(e)) } finally { setBusy(false) }
  }
  async function retry() {
    setBusy(true); setError('')
    try { await authRequest(itemBase + '/retry', { method: 'POST' }); setNotice('已请求重试识别。'); await refreshBatch(selectedBatch) }
    catch(e) { setError(msg(e)) } finally { setBusy(false) }
  }
  async function saveFields() {
    if (!item || editingRevision === null) return
    setBusy(true); setError('')
    try {
      const res = await authRequest<Envelope<Item>>(itemBase + '/fields', { method: 'PUT', body: JSON.stringify({ revision: editingRevision, fields }) })
      setEditingRevision(res.data.revision); setNote(''); setEvidence(null); setNotice('字段已保存；若内容发生变化，原人工查验结果已失效，历史记录仍保留。')
      await refreshBatch(selectedBatch)
    } catch(e) { setError(msg(e)) } finally { setBusy(false) }
  }
  async function verify(status: 'passed' | 'failed' | 'review') {
    if (!item || editingRevision === null) return
    setBusy(true); setError('')
    try {
      const form = new FormData(); form.append('status', status); form.append('note', note); form.append('revision', String(editingRevision)); if (evidence) form.append('evidence', evidence)
      const res = await authRequest<Envelope<Item>>(itemBase + '/verification', { method: 'POST', body: form })
      setEditingRevision(res.data.revision); setEvidence(null); setNotice('人工查验记录已保存。截图为用户提交的凭证，系统未自动验证截图真实性。')
      await refreshBatch(selectedBatch)
    } catch(e) { setError(msg(e)) } finally { setBusy(false) }
  }
  async function showHistory() {
    try { setHistory((await authRequest<Envelope<History[]>>(itemBase + '/history')).data) } catch(e) { setError(msg(e)) }
  }
  const modified = item && JSON.stringify(fields) !== JSON.stringify(cleanFields(receipt ? item.result.receipt_fields : item.result.fields))
  const stale = item && editingRevision !== null && item.revision !== editingRevision
  const disableRecord = busy || !item || item.ocr_status !== 'completed' || Boolean(modified) || Boolean(stale)
  const platform = receipt ? 'https://pjcy.mof.gov.cn/' : 'https://inv-veri.chinatax.gov.cn/'

  return <div className="invoice-workspace-shell invoice-batch-workspace">
    <header className="top-nav"><button className="logo-button" type="button" onClick={onHome}>示例公司智能标书助手</button><button className="light-btn" type="button" onClick={onHome}>返回首页</button></header>
    <main className="module-page invoice-module-page">
      <section className="module-header-card"><div><p>INVOICE CHECK</p><h1>发票批量核验与留存</h1><span>批量识别、人工官方查验、截图和操作记录按公司保存。</span></div></section>
      {error && <div className="graphic-error" role="alert">{error}</div>}{notice && <div className="invoice-notice">{notice}</div>}
      <section className="form-card invoice-upload-card">
        <h2>创建批次</h2><p>每批最多 30 个文件、合计 100 MB；单个不超过 20 MB。支持 JPG、PNG、WEBP、BMP、PDF。</p>
        <div className="batch-upload-controls"><label className="batch-file-picker">选择发票文件<input ref={fileInputRef} type="file" multiple accept=".jpg,.jpeg,.png,.webp,.bmp,.pdf" onChange={e => setFiles(Array.from(e.target.files || []))} /></label>
        <span>{files.length ? `已选择 ${files.length} 个文件：${files.map(file => file.name).join("、")}` : "尚未选择文件"}</span> <button className="dark-btn" disabled={!files.length || busy} onClick={() => void create()}>{busy ? '处理中…' : '上传并开始识别'}</button></div>
      </section>
      <section className="form-card invoice-result-card">
        <div className="invoice-card-heading"><h2>历史批次</h2><button className="light-btn" onClick={() => void refreshList().catch(e => setError(msg(e)))}>刷新列表</button></div>
        <select aria-label="选择批次" value={selectedBatch} onChange={e => setSelectedBatch(e.target.value)}><option value="">请选择批次</option>{batches.map(b => <option value={b.id} key={b.id}>{b.title} · {displayDate(b.created_at)} · {b.created_by}</option>)}</select>
        {batch && <><p className="batch-summary">共 {batch.summary.total} 张；待识别 {batch.summary.pending}，识别失败 {batch.summary.ocr_failed}；人工通过 {batch.summary.passed}，未通过 {batch.summary.failed}，待复核 {batch.summary.review}，未查验 {batch.summary.unverified}。</p>
        <button className="light-btn" onClick={() => void download(API + '/' + batch.id + '/report.csv', '发票批次汇总.csv').catch(e => setError(msg(e)))}>下载 CSV 汇总报告</button> <button className="light-btn" disabled={busy || (!batch.summary.pending && !batch.summary.ocr_failed)} onClick={() => void resumeBatch()}>继续未完成和失败项</button>
        <div className="invoice-batch-list">{batch.items.map(row => <button type="button" className={row.id === selectedItem ? 'dark-btn' : 'light-btn'} key={row.id} onClick={() => setSelectedItem(row.id)}>{row.filename} · {ocrText[row.ocr_status]} · {statusText[row.verification_status]}</button>)}</div></>}
      </section>
      {item && <section className="invoice-workspace-grid">
        <div className="form-card invoice-result-card">
          <h2>{item.filename}</h2><p>{ocrText[item.ocr_status]}{item.error ? '：' + item.error : ''}</p>
          <button className="light-btn" onClick={() => void download(itemBase + '/source', item.filename).catch(e => setError(msg(e)))}>下载原文件</button>
          {['failed','pending','processing'].includes(item.ocr_status) && <button className="light-btn" disabled={busy} onClick={() => void retry()}>重试识别</button>}
          {item.ocr_status === 'completed' && <>
            {stale && <div className="graphic-error">记录已被其他操作更新，当前编辑不会覆盖最新记录。<button type="button" onClick={() => { if (window.confirm('加载最新字段会丢弃未保存的修改，是否继续？')) { setFields(cleanFields(receipt ? item.result.receipt_fields : item.result.fields)); setEditingRevision(item.revision); setNote(item.verification_note); setEvidence(null) } }}>加载最新记录</button></div>}
            <p>{receipt ? '非税收入票据，请使用财政票据渠道核验。' : '发票字段请对照原件修正。修改后保存会使旧的人工查验结果失效。'}</p>
            <div className="invoice-field-grid">{(receipt ? receiptFields : vatFields).map(([key,label]) => <label key={key}>{label}<input value={fields[key] || ''} onChange={e => setFields(old => ({ ...old, [key]: e.target.value }))} /></label>)}</div>
            <button className="light-btn" disabled={busy || Boolean(stale) || !modified} onClick={() => void saveFields()}>保存字段修改</button>
            <p>本地检查：{item.result.local_verification?.status || '待复核'}（仅检查字段，不代表官方真伪结论）</p>{item.result.local_verification?.checks?.map((check, index) => <div className="invoice-check-row" key={index}>{check.message}</div>)}
            <details><summary>识别原文</summary><pre>{item.result.ocr_text || '暂无'}</pre></details>
          </>}
        </div>
        <aside className="form-card invoice-external-card">
          <h2>人工官方查验</h2><p>请自行在官方平台完成验证码和查验。系统只保存人工填写结果及上传截图，不自动连接官方查验接口。</p>
          <a className="batch-platform-link" href={platform} target="_blank" rel="noreferrer">{receipt ? '打开财政票据平台' : '打开税务发票平台'}</a>
          <button className="light-btn" disabled={disableRecord} onClick={() => { const values = (receipt ? receiptFields : vatFields).map(([key,label]) => label + '：' + (fields[key] || '未填写')).join('\n'); void navigator.clipboard.writeText(values).then(() => setNotice('查验字段已复制，请对照原件并在官方平台输入。')).catch(() => setError('复制失败，请手动填写。')) }}>复制查验字段</button>
          <p>当前：{statusText[item.verification_status]}<br />操作人：{item.verified_by || '—'}；记录时间：{displayDate(item.verified_at)}</p>
          {item.has_evidence && <button className="light-btn" onClick={() => void download(itemBase + '/evidence', '查验截图').catch(e => setError(msg(e)))}>下载当前截图</button>}
          <label className="batch-evidence">官方结果截图（记录通过或未通过时必填）<input key={item.revision} type="file" accept="image/jpeg,image/png,image/webp,image/bmp" onChange={e => setEvidence(e.target.files?.[0] || null)} /></label>
          <label className="batch-note">查验备注<textarea value={note} onChange={e => setNote(e.target.value)} placeholder="记录官方页面的实际结果或异常原因" /></label>
          {modified && <p>请先保存字段修改，再记录人工查验结果。</p>}
          <div className="invoice-result-actions"><button disabled={disableRecord || !evidence} onClick={() => void verify('passed')}>记录通过</button><button disabled={disableRecord || !evidence} onClick={() => void verify('failed')}>记录未通过</button><button disabled={disableRecord} onClick={() => void verify('review')}>记录待复核</button></div>
          <button className="light-btn" onClick={() => void showHistory()}>查看操作历史</button>
          {history.map(event => <div className="invoice-check-row" key={event.id}><span>{displayDate(event.created_at)} · {event.actor} · {eventText[event.action] || event.action} · {statusText[event.snapshot.verification_status]}</span>{event.snapshot.has_evidence && <button onClick={() => void download(itemBase + '/history/' + event.id + '/evidence', '历史查验截图').catch(e => setError(msg(e)))}>下载截图</button>}</div>)}
        </aside>
      </section>}
    </main>
  </div>
}
