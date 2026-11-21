import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

globalThis.window = { location: { href: 'http://localhost:5181/' } }
const moduleOf = (source) => {
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const result = {}
  new Function('require', 'exports', compiled)(() => ({ getAuthToken: () => 'test-token' }), result)
  return result
}
const { readEventStream } = moduleOf(readFileSync(new URL('../src/utils/eventStream.ts', import.meta.url), 'utf8'))
const encoder = new TextEncoder()
const sse = (chunks) => new Response(new ReadableStream({ start(c) { for (const x of chunks) c.enqueue(x); c.close() } }), { headers: { 'content-type': 'text/event-stream' } })

test('UTF-8, CRLF, heartbeat and multiline JSON survive byte-sized network chunks', async () => {
  const bytes = encoder.encode(': keepalive\r\nevent: delta\r\ndata: {"content":\r\ndata: "中文正文"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n')
  const events = []
  globalThis.fetch = async (url, options) => {
    assert.equal(url.searchParams.has('access_token'), false)
    assert.equal(options.headers.Authorization, 'Bearer test-token')
    return sse(Array.from(bytes, byte => new Uint8Array([byte])))
  }
  await readEventStream('http://localhost/api/stream?access_token=secret', { onEvent: (name, payload) => events.push([name, payload]) })
  assert.deepEqual(events, [['delta', { content: '中文正文' }], ['done', {}]])
})

test('first preview is delivered while the response is still open', async () => {
  let controller
  let first
  const preview = new Promise(resolve => { first = resolve })
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c) { controller = c; c.enqueue(encoder.encode('event: delta\ndata: {"preview":"第一章"}\n\n')) } }), { headers: { 'content-type': 'text/event-stream' } })
  let finished = false
  const request = readEventStream('http://localhost/stream', { onEvent: name => { if (name === 'delta') first() } }).then(() => { finished = true })
  await preview
  assert.equal(finished, false)
  controller.enqueue(encoder.encode('event: done\ndata: {}\n\n'))
  await request
})

test('EOF without terminal event rejects without restarting the request', async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; return sse([encoder.encode('event: delta\ndata: {"content":"半截"}\n\n')]) }
  await assert.rejects(readEventStream('http://localhost/stream', { onEvent() {} }), /尚未完成/)
  assert.equal(calls, 1)
})

test('batch stream waits through chapter_done until batch_done', async () => {
  globalThis.fetch = async () => sse([encoder.encode('event: chapter_done\ndata: {"chapter_id":1}\n\nevent: batch_done\ndata: {"success_count":1}\n\n')])
  const seen = []
  await readEventStream('http://localhost/stream', { terminalEvents: ['batch_done'], onEvent: name => seen.push(name) })
  assert.deepEqual(seen, ['chapter_done', 'batch_done'])
})

test('server errors reject instead of being treated as success', async () => {
  globalThis.fetch = async () => sse([encoder.encode('event: error\ndata: {"message":"上游服务失败"}\n\n')])
  await assert.rejects(readEventStream('http://localhost/stream', { onEvent() {} }), /上游服务失败/)
})

test('cancelled request never reports completion', async () => {
  const abort = new AbortController()
  globalThis.fetch = async (_url, options) => new Response(new ReadableStream({ start(c) {
    options.signal.addEventListener('abort', () => c.error(new DOMException('cancelled', 'AbortError')), { once: true })
    c.enqueue(encoder.encode('event: delta\ndata: {"content":"部分"}\n\n'))
  } }), { headers: { 'content-type': 'text/event-stream' } })
  await assert.rejects(readEventStream('http://localhost/stream', { signal: abort.signal, onEvent: () => abort.abort() }), { name: 'AbortError' })
})

const api = readFileSync(new URL('../src/wordagentApi.ts', import.meta.url), 'utf8')
const uploadSource = "const API_BASE='http://localhost/api'; const getAuthToken=()=>null;\n" + api.slice(api.indexOf('export type UploadProgress'), api.indexOf('export const wordagentApi')) + '\nexport { uploadWithProgress };'
const { uploadWithProgress } = moduleOf(uploadSource)
let xhr
class MockXHR {
  constructor() { xhr = this; this.upload = {} }
  open() {}
  setRequestHeader() {}
  send() {}
  abort() { this.onabort?.() }
}
globalThis.XMLHttpRequest = MockXHR

test('upload bytes progress is visible before the server acknowledges or parses', async () => {
  const states = []
  let resolved = false
  const result = uploadWithProgress('/upload', new FormData(), undefined, { onTransferProgress: state => states.push(state) }).then(() => { resolved = true })
  xhr.upload.onprogress({ loaded: 50, total: 100, lengthComputable: true })
  assert.equal(states.at(-1).percent, 50)
  xhr.upload.onload()
  assert.equal(states.at(-1).percent, 100)
  assert.equal(states.at(-1).stage, 'processing')
  assert.equal(resolved, false)
  xhr.status = 200; xhr.responseText = '{"code":0,"data":{"parse_status":"processing"}}'; xhr.onload()
  await result
})

test('upload cancellation rejects and never produces successful completion', async () => {
  const controller = new AbortController()
  const request = uploadWithProgress('/upload', new FormData(), undefined, { signal: controller.signal })
  controller.abort()
  await assert.rejects(request, { name: 'AbortError' })
})

test('proxy 413 upload failure is readable even when the body is HTML', async () => {
  const request = uploadWithProgress('/upload', new FormData())
  xhr.status = 413; xhr.responseText = '<html>413</html>'; xhr.onload()
  await assert.rejects(request, /上传上限/)
})
