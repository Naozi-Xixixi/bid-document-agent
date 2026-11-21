export type BidExtractionProgress = {
  status: 'running' | 'completed' | 'failed'
  completed: number
  total: number
  percent: number
  current_key: string | null
  current_label: string | null
  message: string
  started_at: string | null
  updated_at: string | null
  items: Array<{
    key: string
    label: string
    status: 'pending' | 'running' | 'completed' | 'fallback' | 'failed'
    attempt: number
  }>
}

type ExtractionProgressProps = {
  progress?: BidExtractionProgress | null
  parseStatus: 'pending' | 'processing' | 'completed' | 'failed'
  elapsedSeconds: number | null
  elapsedFromServer?: boolean
}

const itemStatusLabels = {
  pending: '等待处理',
  running: '处理中',
  completed: '已完成',
  fallback: '已完成（备用方式）',
  failed: '失败',
} as const

function formatElapsed(seconds: number) {
  const value = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor(value % 3600 / 60)
  const remainder = value % 60
  return hours ? `${hours}小时 ${minutes}分 ${remainder}秒` : minutes ? `${minutes}分 ${remainder}秒` : `${remainder}秒`
}

/** All completion values come from the server; elapsed time never advances them. */
export function ExtractionProgress({ progress, parseStatus, elapsedSeconds, elapsedFromServer = true }: ExtractionProgressProps) {
  const status = parseStatus === 'failed' ? 'failed' : progress?.status ?? (parseStatus === 'completed' ? 'completed' : 'running')
  const hasCount = Boolean(progress && Number.isFinite(progress.total) && progress.total > 0 && Number.isFinite(progress.completed))
  const completed = hasCount ? Math.max(0, Math.min(progress!.total, progress!.completed)) : null
  const hasPercent = hasCount && Number.isFinite(progress?.percent)
  const percent = hasPercent ? status === 'completed' ? 100 : Math.max(0, Math.min(99, Math.floor(progress!.percent))) : undefined
  const currentItem = progress?.items.find(item => item.key === progress.current_key)
  const currentLabel = progress?.current_label || currentItem?.label
  const retryLabel = currentItem && currentItem.attempt > 1 ? `（重试第 ${currentItem.attempt - 1} 次）` : ''
  const heading = status === 'completed' ? '信息提取完成' : status === 'failed' ? '信息提取未完成' : '正在提取信息'
  const statusText = status === 'running'
    ? currentLabel ? `当前：${currentLabel}${retryLabel}` : '等待模型返回，页面会继续更新。'
    : status === 'failed' ? `${currentLabel ? `停止于：${currentLabel}${retryLabel}。` : ''}请检查错误后重试。`
      : '提取结果已保存。'
  const elapsedText = elapsedSeconds !== null && Number.isFinite(elapsedSeconds) ? formatElapsed(elapsedSeconds) : null

  return <section aria-label="信息提取进度" style={{ display: 'grid', gap: 8, marginTop: 12 }}>
    <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }}>
      <strong>{heading}</strong>
      <span>{percent === undefined ? status === 'completed' ? '已完成' : status === 'failed' ? '未完成' : '处理中' : `${percent}%`}{completed !== null ? ` · 已完成 ${completed}/${progress!.total} 项` : ''}</span>
    </div>
    <progress
      aria-label="信息提取完成进度"
      aria-valuetext={percent === undefined ? heading : `${percent}%，已完成 ${completed}/${progress!.total} 项`}
      max={100}
      value={percent ?? (status === 'completed' ? 100 : undefined)}
      style={{ width: '100%', height: 14, accentColor: status === 'failed' ? '#b42318' : 'var(--form-cta-bg)' }}
    />
    <p role="status" style={{ margin: 0, fontSize: 13, color: status === 'failed' ? '#b42318' : 'var(--form-label-color)' }}>{statusText}</p>
    {progress?.message && <p style={{ margin: 0, fontSize: 12, color: 'var(--form-desc-color)' }}>{progress.message}</p>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 12, color: 'var(--form-desc-color)' }}>
      {elapsedText && <span>{elapsedFromServer ? '已用时间' : '已用时间（本次页面）'}：{elapsedText}</span>}
      <span>百分比按已完成事项计算，不代表预计耗时。</span>
    </div>
    {!progress && status === 'running' && <p style={{ margin: 0, fontSize: 12, color: 'var(--form-desc-color)' }}>这个任务暂无分项进度，正在等待模型返回；无需重复提交。</p>}
    {Boolean(progress?.items.length) && <details>
      <summary style={{ cursor: 'pointer', fontSize: 13 }}>查看提取事项</summary>
      <ol style={{ margin: '8px 0 0', paddingLeft: 24, display: 'grid', gap: 6 }}>
        {progress!.items.map(item => <li key={item.key} style={{ fontSize: 12, color: item.status === 'failed' ? '#b42318' : 'var(--form-desc-color)' }}>
          <span>{item.label}</span> · <span>{itemStatusLabels[item.status]}</span>{item.attempt > 1 ? ` · 第 ${item.attempt} 次尝试` : ''}
        </li>)}
      </ol>
    </details>}
  </section>
}
import { useEffect, useRef, useState } from 'react'


function timestamp(value: string | null | undefined) {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

/** A local clock updates elapsed time only; polling supplies the actual progress. */
export function LiveExtractionProgress({ progress, parseStatus }: Pick<ExtractionProgressProps, 'progress' | 'parseStatus'>) {
  const [now, setNow] = useState(Date.now)
  const observedAtRef = useRef(now)
  const running = parseStatus === 'processing' && (!progress || progress.status === 'running')
  const observedRunningRef = useRef(running)
  const serverStart = timestamp(progress?.started_at)
  const serverEnd = !running ? timestamp(progress?.updated_at) : null

  useEffect(() => {
    setNow(Date.now())
    if (!running) return
    observedRunningRef.current = true
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running, progress?.started_at])

  const elapsedSeconds = serverStart !== null
    ? Math.max(0, ((serverEnd ?? now) - serverStart) / 1000)
    : observedRunningRef.current ? Math.max(0, (now - observedAtRef.current) / 1000) : null
  return <ExtractionProgress progress={progress} parseStatus={parseStatus} elapsedSeconds={elapsedSeconds} elapsedFromServer={serverStart !== null} />
}
