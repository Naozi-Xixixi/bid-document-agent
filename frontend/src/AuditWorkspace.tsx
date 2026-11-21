import { AccountNavActions } from './AccountNavActions'
import { useMemo, useState } from 'react'
import { getAuthToken } from './auth'
import { type AuditIssue, type AuditResult, wordagentApi } from './wordagentApi'

const CHECKS = [
  ['rejection', '废标风险'],
  ['scoring', '评分响应'],
  ['qualification', '资质校验'],
  ['format', '格式规范'],
  ['business', '商务一致性'],
] as const

const TABS = [
  ['all', '全部问题'],
  ['rejection', '废标风险'],
  ['scoring', '评分响应'],
  ['qualification', '资质校验'],
  ['format', '格式规范'],
  ['business', '商务一致性'],
] as const

function apiErrorMessage(error: unknown, fallback: string) {
  const raw = error instanceof Error ? error.message : String(error ?? '')
  try {
    const parsed = JSON.parse(raw)
    return parsed?.message || parsed?.detail || fallback
  } catch {
    return raw || fallback
  }
}

export function AuditWorkspace({ onHome }: { onHome: () => void }) {
  const [auditId, setAuditId] = useState<number | null>(null)
  const [tenderName, setTenderName] = useState('')
  const [bidName, setBidName] = useState('')
  const [checks, setChecks] = useState<string[]>(CHECKS.map(([key]) => key))
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [step, setStep] = useState('等待上传文件')
  const [result, setResult] = useState<AuditResult | null>(null)
  const [activeTab, setActiveTab] = useState('all')
  const [error, setError] = useState('')

  const visibleIssues = useMemo(() => {
    const issues = result?.issues || []
    return activeTab === 'all' ? issues : issues.filter(issue => issue.category === activeTab)
  }, [activeTab, result])

  async function upload(role: 'tender' | 'bid', file?: File | null) {
    if (!file) return
    setError('')
    try {
      const res = role === 'tender'
        ? await wordagentApi.uploadAuditTender(file, auditId)
        : await wordagentApi.uploadAuditBid(file, auditId)
      if (res.code === 0) {
        setAuditId(res.data.audit_id)
        if (role === 'tender') setTenderName(res.data.file_name)
        else setBidName(res.data.file_name)
        setStep('文件已上传，等待开始审核')
        setProgress(0)
        setResult(null)
      } else {
        setError(res.message || '文件上传失败')
      }
    } catch (err) {
      setError(apiErrorMessage(err, '文件上传失败'))
    }
  }

  function toggleCheck(key: string) {
    setChecks(prev => prev.includes(key) ? prev.filter(item => item !== key) : [...prev, key])
  }

  async function startAudit() {
    if (!auditId || !tenderName || !bidName || running) return
    setRunning(true)
    setError('')
    setProgress(5)
    setStep('准备审核')
    setResult(null)
    try {
      const headers = new Headers({ 'Content-Type': 'application/json' })
      const token = getAuthToken()
      if (token) headers.set('Authorization', `Bearer ${token}`)
      const response = await fetch(wordagentApi.startAuditUrl(), {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify({ audit_id: auditId, checks }),
      })
      if (!response.ok) throw new Error(await response.text())
      const reader = response.body?.getReader()
      if (!reader) throw new Error('浏览器不支持流式审核进度')
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''
        parts.forEach(processSseBlock)
      }
      if (buffer.trim()) processSseBlock(buffer)
    } catch (err) {
      setError(apiErrorMessage(err, '审核失败'))
    } finally {
      setRunning(false)
    }
  }

  function processSseBlock(block: string) {
    const dataLine = block.split('\n').find(line => line.startsWith('data:'))
    if (!dataLine) return
    try {
      const payload = JSON.parse(dataLine.replace(/^data:\s*/, ''))
      if (payload.type === 'progress') {
        setProgress(Number(payload.progress || 0))
        setStep(String(payload.step || '审核中'))
      } else if (payload.type === 'done') {
        setProgress(100)
        setStep('审核完成')
        setResult(payload.result as AuditResult)
      } else if (payload.type === 'server_error') {
        setError(payload.message || '审核失败')
      }
    } catch {
      // Ignore partial SSE frame.
    }
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--page-bg)' }}>
      <header className="top-nav">
        <button className="logo-button" type="button" onClick={onHome}>木石科技</button>
        <AccountNavActions onHome={onHome} />
      </header>
      <main className="module-page audit-module-page">
        <section className="module-header-card">
          <div>
            <p>BID AUDIT</p>
            <h1>标书智能审核</h1>
            <span>上传招标文件与待审核投标文件，自动检查废标风险、评分覆盖、资质与商务一致性。</span>
          </div>
          <button className="light-btn" type="button" onClick={onHome}>返回首页</button>
        </section>

        <section className="form-card audit-upload-card">
          <UploadBox title="上传招标文件" fileName={tenderName} onFile={file => void upload('tender', file)} />
          <UploadBox title="上传待审核投标文件" fileName={bidName} onFile={file => void upload('bid', file)} />
          <div className="audit-config-row">
            <div>
              {CHECKS.map(([key, label]) => (
                <label key={key} className="graphic-check">
                  <input type="checkbox" checked={checks.includes(key)} onChange={() => toggleCheck(key)} />
                  {label}
                </label>
              ))}
            </div>
            <button className="dark-btn" type="button" disabled={!auditId || !tenderName || !bidName || running || !checks.length} onClick={() => void startAudit()}>
              {running ? '审核中...' : '开始智能审核'}
            </button>
          </div>
          {(running || progress > 0) && (
            <div className="audit-progress">
              <div><span>{step}</span><strong>{progress}%</strong></div>
              <span className="audit-progress-track"><i style={{ width: `${progress}%` }} /></span>
            </div>
          )}
          {error && <div className="graphic-error">{error}</div>}
        </section>

        {result && (
          <section className="form-card audit-result-card">
            <div className="audit-result-head">
              <div className="audit-stats">
                <Stat label="高风险" value={result.summary.high_count} />
                <Stat label="中风险" value={result.summary.medium_count} />
                <Stat label="低风险" value={result.summary.low_count} />
                <Stat label="优化建议" value={result.summary.suggestion_count} />
              </div>
              <div className="graphic-header-actions">
                <button className="light-btn" type="button" onClick={() => window.open(wordagentApi.exportAuditUrl(result.audit_id), '_blank')}>导出审核报告</button>
                <button className="light-btn" type="button" onClick={() => void startAudit()}>重新审核</button>
              </div>
            </div>
            <div className="audit-tabs">
              {TABS.map(([key, label]) => (
                <button key={key} className={activeTab === key ? 'active' : ''} type="button" onClick={() => setActiveTab(key)}>
                  {label}<span>{key === 'all' ? result.issues.length : result.issues.filter(issue => issue.category === key).length}</span>
                </button>
              ))}
            </div>
            <div className="audit-issue-list">
              {visibleIssues.length
                ? visibleIssues.map(issue => <IssueCard key={issue.id} issue={issue} />)
                : <div className="knowledge-empty">当前分类暂未发现问题。</div>}
            </div>
          </section>
        )}
      </main>
    </div>
  )
}

function UploadBox({ title, fileName, onFile }: { title: string; fileName: string; onFile: (file: File | null) => void }) {
  return (
    <label
      className="audit-upload-box"
      onDragOver={event => event.preventDefault()}
      onDrop={event => { event.preventDefault(); onFile(event.dataTransfer.files?.[0] || null) }}
    >
      <input type="file" accept=".pdf,.doc,.docx" onChange={event => onFile(event.target.files?.[0] || null)} />
      <strong>{title}</strong>
      <span>{fileName || '点击或拖拽上传 PDF / DOCX'}</span>
    </label>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return <span><strong>{value}</strong><em>{label}</em></span>
}

function IssueCard({ issue }: { issue: AuditIssue }) {
  return (
    <article className="audit-issue-card">
      <div><span className={`audit-risk-tag ${issue.severity}`}>{severityLabel(issue.severity)}</span><strong>{issue.title}</strong></div>
      <p>{issue.detail}</p>
      <dl>
        <dt>招标文件原文依据</dt><dd>{issue.tender_basis || '-'}</dd>
        <dt>标书对应位置</dt><dd>{issue.bid_location || '-'}</dd>
        <dt>整改建议</dt><dd>{issue.suggestion || '-'}</dd>
      </dl>
      <button className="plain-btn" type="button" onClick={() => window.alert(issue.bid_location || '请在投标文件中检索对应关键词定位。')}>快速定位</button>
    </article>
  )
}

function severityLabel(value: string) {
  return { high: '高风险', medium: '中风险', low: '低风险', suggestion: '优化建议' }[value] || '问题'
}
