import { getAuthToken } from '../auth'

type StreamOptions = {
  signal?: AbortSignal
  terminalEvents?: string[]
  onEvent: (eventName: string, payload: Record<string, any>) => void | Promise<void>
}

/** Read one SSE request without reconnecting or silently restarting generation. */
export async function readEventStream(url: string, options: StreamOptions): Promise<void> {
  const endpoint = new URL(url, window.location.href)
  endpoint.searchParams.delete('access_token')
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const token = getAuthToken()
  if (token) headers.Authorization = 'Bearer ' + token
  const response = await fetch(endpoint, { headers, credentials: 'include', signal: options.signal })
  if (!response.ok) {
    const raw = await response.text()
    let message = '生成请求失败（HTTP ' + response.status + '）'
    try { const data = JSON.parse(raw); message = data.message || data.detail || message } catch { /* Non-JSON proxy response. */ }
    throw new Error(message)
  }
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('服务器未返回实时生成数据，请检查后端连接')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const terminals = new Set(options.terminalEvents ?? ['done'])
  let pending = ''
  let eventName = 'message'
  let dataLines: string[] = []
  let completed = false
  const dispatch = async () => {
    if (!dataLines.length) { eventName = 'message'; return }
    const raw = dataLines.join('\n')
    const name = eventName
    dataLines = []
    eventName = 'message'
    let payload: Record<string, any>
    try { payload = JSON.parse(raw) } catch { throw new Error('实时生成数据格式不完整，请重试') }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('实时生成数据格式不正确')
    await options.onEvent(name, payload)
    if (name === 'error') throw new Error(payload.message || payload.error || '生成失败，请重试')
    if (terminals.has(name)) completed = true
  }
  const line = async (text: string) => {
    if (!text) { await dispatch(); return }
    if (text.startsWith(':')) return
    const colon = text.indexOf(':')
    const field = colon < 0 ? text : text.slice(0, colon)
    let value = colon < 0 ? '' : text.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') eventName = value
    if (field === 'data') dataLines.push(value)
  }
  try {
    while (!completed) {
      options.signal?.throwIfAborted()
      const { value, done } = await reader.read()
      pending += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let match: RegExpExecArray | null
      while ((match = /\r\n|\n|\r/.exec(pending))) {
        if (match[0] === '\r' && match.index === pending.length - 1 && !done) break
        const text = pending.slice(0, match.index)
        pending = pending.slice(match.index + match[0].length)
        await line(text)
        if (completed) break
      }
      if (done) break
    }
    options.signal?.throwIfAborted()
    if (!completed) throw new Error('连接已中断，生成尚未完成；已显示的内容仅为预览，请重试')
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
