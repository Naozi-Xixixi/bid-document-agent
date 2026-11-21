import { type ReactNode, useEffect, useId, useRef } from 'react'

export function ProjectDeleteDialog({ projectName, source, deleting, error, onCancel, onConfirm }: {
  projectName: string
  source?: ReactNode
  deleting: boolean
  error: string
  onCancel: () => void
  onConfirm: () => void
}) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    cancelRef.current?.focus()
    return () => {
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [])

  useEffect(() => {
    if (deleting) dialogRef.current?.focus()
  }, [deleting])

  return <div
    ref={dialogRef}
    role="dialog"
    aria-modal="true"
    aria-labelledby={titleId}
    aria-describedby={descriptionId}
    aria-busy={deleting}
    tabIndex={-1}
    style={{ position: 'fixed', inset: 0, zIndex: 10000, display: 'grid', placeItems: 'center', padding: 20, background: 'rgba(0, 0, 0, 0.4)' }}
    onKeyDown={event => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        if (!deleting) onCancel()
      }
      if (event.key === 'Tab') {
        const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
        const first = buttons[0]
        const last = buttons[buttons.length - 1]
        if (!first) event.preventDefault()
        else if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === event.currentTarget)) {
          event.preventDefault()
          first.focus()
        }
      }
    }}
  >
    <div style={{ width: 'min(520px, 100%)', borderRadius: 16, padding: 24, background: 'var(--page-bg, #fff)', boxShadow: '0 16px 60px rgba(0, 0, 0, 0.2)' }}>
      <h2 id={titleId} style={{ fontSize: 20, margin: '0 0 14px' }}>删除历史项目？</h2>
      <div id={descriptionId} style={{ fontSize: 14, lineHeight: 1.8 }}>
        <p style={{ margin: '0 0 10px', overflowWrap: 'anywhere' }}>项目：<strong>{projectName}</strong></p>
        {source && <div style={{ marginBottom: 10, fontSize: 13 }}>{source}</div>}
        <p style={{ margin: '0 0 10px', color: '#b42318' }}>将删除本项目的解析结果、大纲、正文及不再被使用的上传副本，删除后无法恢复。</p>
        <p style={{ margin: 0, color: 'var(--form-desc-color)' }}>电脑上的原始文件、企业知识库及其他项目正在使用的资料会保留。正在解析或生成中的项目暂时不能删除。</p>
      </div>
      {error && <p role="alert" style={{ color: '#b42318', whiteSpace: 'pre-line', overflowWrap: 'anywhere' }}>{error}</p>}
      {deleting && <p role="status" style={{ fontSize: 13 }}>正在删除，请稍候，完成前请勿关闭页面。</p>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
        <button ref={cancelRef} className="light-btn" type="button" disabled={deleting} onClick={onCancel}>取消</button>
        <button className="dark-btn" type="button" disabled={deleting} onClick={onConfirm} style={{ background: '#b42318', color: '#fff' }}>{deleting ? '删除中…' : '确认删除项目'}</button>
      </div>
    </div>
  </div>
}
