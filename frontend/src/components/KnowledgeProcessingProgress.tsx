import type { KnowledgeItem } from '../wordagentApi'

export type KnowledgeProgressSource = Partial<KnowledgeItem>
type StageState = 'pending' | 'running' | 'completed' | 'failed' | 'inactive'
export type KnowledgeStage = { key: string; label: string; state: StageState; percent: number | null; detail: string; error?: string }
const bounded = (value: number | undefined) => Math.max(0, Math.min(100, Number.isFinite(value) ? value! : 0))
const active = (status?: string) => status === 'pending' || status === 'processing' || status === 'indexing'

export function knowledgeProgressState(item: KnowledgeProgressSource) {
  const primaryDone = item.status === 'completed'
  const primaryFailed = item.status === 'failed'
  const primary: KnowledgeStage = {
    key: 'text', label: '正文解析',
    state: primaryDone ? 'completed' : primaryFailed ? 'failed' : active(item.status) ? (item.status === 'pending' ? 'pending' : 'running') : 'inactive',
    percent: primaryDone ? 100 : primaryFailed ? null : bounded(item.process_progress),
    detail: primaryDone ? `已提取 ${(item.text_char_count || 0).toLocaleString()} 字` : primaryFailed ? '解析失败' : item.status === 'pending' ? '等待解析' : '读取正文、目录和表格',
    error: item.process_error || undefined,
  }
  const deepActive = active(item.deep_status)
  const deepFailed = item.deep_status === 'failed' || (item.deep_failed_count || 0) > 0 || (!deepActive && (item.ocr_unprocessed_count || 0) > 0)
  const deepDone = item.deep_status === 'completed'
  const deep: KnowledgeStage = {
    key: 'deep', label: '图片文字识别（OCR）',
    state: deepActive ? (item.deep_status === 'pending' ? 'pending' : 'running') : deepFailed ? 'failed' : deepDone ? 'completed' : 'inactive',
    percent: deepDone && !deepFailed ? 100 : deepActive && ((item.deep_total || 0) > 0 || (item.deep_progress || 0) > 0) ? bounded(item.deep_progress) : null,
    detail: deepActive ? ((item.deep_total || 0) > 0 && item.deep_processed_count != null ? `已处理 ${item.deep_processed_count} / ${item.deep_total} 项` : (item.deep_progress || 0) > 0 ? '正在识别图片文字与信息' : '准备图片，等待识别') : deepFailed ? `待补识别 ${item.ocr_unprocessed_count ?? item.deep_failed_count ?? 0} 张` : deepDone ? `识别出文字 ${item.ocr_recognized_count || 0} 张 · 未检测到可读文字 ${item.ocr_no_text_count || 0} 张` : primaryFailed ? '正文解析失败，未开始' : '未启动',
    error: item.deep_error || ((item.deep_failed_count || 0) > 0 ? `${item.deep_failed_count} 项识别失败` : undefined),
  }
  const vectorActive = primaryDone && active(item.vector_status)
  const vectorDone = item.vector_status === 'completed'
  const vectorFailed = item.vector_status === 'failed' || item.vector_status === 'not_configured'
  const indexed = Math.max(0, item.vector_indexed_chunks || 0)
  const vector: KnowledgeStage = {
    key: 'vector', label: '检索索引',
    state: primaryFailed ? 'inactive' : vectorActive ? (item.vector_status === 'pending' ? 'pending' : 'running') : vectorFailed ? 'failed' : vectorDone ? 'completed' : 'inactive',
    // The server writes indexed_chunks at the end of a batch, not once per chunk.
    percent: vectorDone ? 100 : null,
    detail: primaryFailed ? '正文解析失败，未开始' : vectorActive ? item.vector_status === 'pending' ? '等待建立索引' : '正在建立索引' : vectorFailed ? item.vector_status === 'not_configured' ? '索引服务未配置' : '索引建立失败' : vectorDone ? `已索引 ${indexed} 个片段` : primaryDone ? '索引状态待确认' : '等待正文解析',
    error: item.vector_error || undefined,
  }
  const stages = [primary, deep, vector]
  const running = stages.some(stage => stage.state === 'running' || stage.state === 'pending')
  const hasIssue = primaryFailed || deepFailed || vectorFailed || Boolean(primary.error || deep.error || vector.error)
  const complete = primaryDone && (deepDone || !item.deep_status || item.deep_status === 'not_started') && vectorDone && !hasIssue
  const current = stages.find(stage => stage.state === 'running') || stages.find(stage => stage.state === 'pending')
  const label = running ? `${current?.label || '文件解析'}${current?.state === 'pending' ? '等待中' : '中'}` : hasIssue ? '处理结束，有异常' : complete ? '全部处理完成' : '等待状态确认'
  return { stages, running, hasIssue, complete, label }
}

export function isKnowledgeProcessingActive(item: KnowledgeProgressSource) {
  return knowledgeProgressState(item).running
}

export function KnowledgeProgressBar({ label, percent, running = false, failed = false }: { label: string; percent: number | null; running?: boolean; failed?: boolean }) {
  return <div className={`knowledge-task-bar ${running && percent === null ? 'is-indeterminate' : ''} ${failed ? 'is-failed' : ''}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent == null ? undefined : bounded(percent)} aria-valuetext={percent == null ? (running ? '处理中' : '暂无进度') : `${bounded(percent)}%`}><div style={percent == null ? undefined : { width: `${bounded(percent)}%` }} /></div>
}

export function KnowledgeProcessingProgress({ item, compact = false }: { item: KnowledgeProgressSource; compact?: boolean }) {
  const result = knowledgeProgressState(item)
  return <div className={`knowledge-processing-progress ${compact ? 'is-compact' : ''}`} aria-label="文件解析进度">
    <div className={`knowledge-processing-summary ${result.hasIssue ? 'has-error' : ''}`}>{result.label}</div>
    {result.stages.map(stage => <div className="knowledge-processing-stage" key={stage.key}>
      <div className="knowledge-processing-label"><span>{stage.label}</span><strong>{stage.state === 'completed' ? '完成' : stage.state === 'failed' ? '异常' : stage.state === 'inactive' ? '—' : stage.percent == null ? (stage.state === 'running' ? '处理中' : '等待中') : `${stage.percent}%`}</strong></div>
      <KnowledgeProgressBar label={stage.label} percent={stage.percent} running={stage.state === 'running' || stage.state === 'pending'} failed={stage.state === 'failed'} />
      {!compact && <small>{stage.detail}</small>}
      {stage.error && <small className="knowledge-processing-error">{stage.error}</small>}
    </div>)}
    {!compact && result.running && <small className="knowledge-processing-hint">各阶段进度按实际处理状态更新；正文完成后即可查看，图片和索引会继续处理。</small>}
  </div>
}

export type KnowledgeUploadProgressState = { total: number; uploadPercent: number; items: KnowledgeItem[]; failures: Array<{ file_name: string; reason: string }>; startedAt: number; phase: 'uploading' | 'accepted' | 'failed'; error?: string }
export function KnowledgeUploadProgressPanel({ progress, onClose, onRetry }: { progress: KnowledgeUploadProgressState; onClose: () => void; onRetry: (item: KnowledgeItem) => void }) {
  const accepted = progress.phase === 'accepted'
  const states = progress.items.map(knowledgeProgressState)
  const running = progress.phase === 'uploading' || states.some(state => state.running)
  const failures = progress.failures.length + states.filter(state => state.hasIssue).length
  const completed = states.filter(state => state.complete).length
  const allComplete = accepted && completed === progress.total && failures === 0
  const label = progress.phase === 'failed' ? '上传未完成' : !accepted ? progress.uploadPercent >= 100 ? '文件已发送，等待服务器接收' : '文件上传中' : allComplete ? '全部处理完成' : running ? '文件解析处理中' : failures ? '处理结束，有异常' : '等待状态确认'
  return <section className={`knowledge-upload-progress ${failures || progress.phase === 'failed' ? 'has-error' : ''}`}>
    <div className="knowledge-upload-progress-head"><strong>{label}</strong><span>{accepted ? `全部处理完成 ${completed} / ${progress.total} 个文件` : `文件传输 ${bounded(progress.uploadPercent)}%`}</span>{!running && <button type="button" onClick={onClose}>收起</button>}</div>
    {!accepted && <KnowledgeProgressBar label="文件上传" percent={progress.uploadPercent >= 100 && progress.phase === 'uploading' ? null : bounded(progress.uploadPercent)} running={progress.phase === 'uploading'} failed={progress.phase === 'failed'} />}
    {progress.error && <p className="knowledge-processing-error">{progress.error}</p>}
    <div className="knowledge-upload-task-files">{progress.items.map(item => <article key={item.item_id}><strong className="knowledge-upload-task-title">{item.title}</strong><KnowledgeProcessingProgress item={item} />{item.status === 'failed' && <button type="button" className="light-btn" onClick={() => onRetry(item)}>重试解析</button>}</article>)}</div>
    {progress.failures.length > 0 && <div className="knowledge-import-failures">{progress.failures.map((failure, index) => <p key={index}><strong>{failure.file_name}</strong><span>{failure.reason}</span></p>)}</div>}
  </section>
}
