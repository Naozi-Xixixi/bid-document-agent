import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

type TextInputRequest = { title: string; initialValue: string }

function TextInputDialog({ request, onClose }: { request: TextInputRequest; onClose: (value: string | null) => void }) {
  const [value, setValue] = useState(request.initialValue)
  const inputRef = useRef<HTMLInputElement>(null)
  const titleId = useId()

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    inputRef.current?.focus()
    inputRef.current?.select()
    return () => {
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [])

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0, 0, 0, 0.4)', display: 'grid', placeItems: 'center', padding: 20 }}
      onMouseDown={event => { if (event.target === event.currentTarget) onClose(null) }}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing) {
          if (event.key === 'Enter') event.preventDefault()
          return
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          onClose(null)
        }
        if (event.key === 'Tab') {
          const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('input, button:not(:disabled)'))
          const first = controls[0]
          const last = controls[controls.length - 1]
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault()
            last?.focus()
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault()
            first?.focus()
          }
        }
      }}
    >
      <form
        onSubmit={event => { event.preventDefault(); if (value.trim()) onClose(value.trim()) }}
        style={{ width: 'min(440px, 100%)', padding: 24, borderRadius: 16, background: 'var(--page-bg, #fff)', boxShadow: '0 16px 60px rgba(0, 0, 0, 0.2)' }}
      >
        <label id={titleId} htmlFor={`${titleId}-input`} style={{ display: 'block', fontWeight: 700, marginBottom: 12 }}>{request.title}</label>
        <input
          id={`${titleId}-input`}
          ref={inputRef}
          value={value}
          onChange={event => setValue(event.target.value)}
          required
          style={{ boxSizing: 'border-box', width: '100%', padding: '10px 12px', border: '1px solid #cbc8c0', borderRadius: 8, font: 'inherit' }}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
          <button className="light-btn" type="button" onClick={() => onClose(null)}>取消</button>
          <button className="dark-btn" type="submit" disabled={!value.trim()}>确定</button>
        </div>
      </form>
    </div>,
    document.body,
  )
}

/** Resolve canceled input as null, including a project switch or component unmount. */
export function useTextInputDialog(scopeKey: string | number | null) {
  const [request, setRequest] = useState<TextInputRequest | null>(null)
  const resolveRef = useRef<((value: string | null) => void) | null>(null)
  const mountedRef = useRef(false)

  useEffect(() => {
    mountedRef.current = true
    setRequest(null)
    return () => {
      mountedRef.current = false
      const resolve = resolveRef.current
      resolveRef.current = null
      resolve?.(null)
    }
  }, [scopeKey])

  const close = useCallback((value: string | null) => {
    const resolve = resolveRef.current
    resolveRef.current = null
    if (mountedRef.current) setRequest(null)
    resolve?.(value)
  }, [])

  const requestText = useCallback((title: string, initialValue = ''): Promise<string | null> => {
    if (!mountedRef.current || resolveRef.current) return Promise.resolve(null)
    return new Promise(resolve => {
      resolveRef.current = resolve
      setRequest({ title, initialValue })
    })
  }, [])

  return { requestText, dialog: request ? <TextInputDialog request={request} onClose={close} /> : null }
}
