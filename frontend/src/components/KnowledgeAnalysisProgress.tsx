import type { KnowledgeProjectDocument } from '../wordagentApi'

export function KnowledgeAnalysisProgress({ analysis }: { analysis?: KnowledgeProjectDocument['ai_interpretation'] }) {
  const coverage = analysis?.coverage
  if (!coverage) return analysis?.status === 'completed'
    ? <small>历史解读尚未记录全文覆盖率，可点击“补全 / 重试 AI 解读”。</small> : null
  const percent = Math.max(0, Math.min(100, coverage.percent))
  const complete = analysis?.status === 'completed' && percent === 100
  return <div className="knowledge-analysis-progress">
    <strong>{complete ? 'AI 已完成全部文本批次' : analysis?.status === 'failed' ? 'AI 解读未完成' : 'AI 分批解读中'} · {percent}%</strong>
    <progress aria-label="AI 文本解读覆盖率" value={percent} max={100} />
    <small>已完成 {coverage.completed_batches} / {coverage.total_batches} 批 · {coverage.covered_chunks} / {coverage.total_chunks} 个片段 · {coverage.covered_chars.toLocaleString()} / {coverage.total_chars.toLocaleString()} 字</small>
    <small>{coverage.scope}</small>
    {!!coverage.failed_batches && <small role="status">{coverage.failed_batches} 批失败，重试将保留已完成结果。</small>}
    {analysis?.error && <small role="status">{analysis.error}</small>}
  </div>
}
