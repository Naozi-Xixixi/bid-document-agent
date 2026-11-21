import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'

const source = readFileSync(new URL('../src/InvoiceVerificationWorkspace.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText
const require = createRequire(import.meta.url)
const invoice = {
  file_name: 'test-invoice.png', status: 'ocr_completed', ocr_text: 'offline test',
  fields: { invoice_number: '12345678', issue_date: '2026-10-20', amount_excluding_tax: '100.00', title: '测试单位', tax_no: 'TEST1234' },
  external_verification: { status: 'manual_pending' },
}
const tick = () => new Promise(resolve => setImmediate(resolve))
function nodes(node) {
  if (!node || typeof node !== 'object') return []
  return [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]
}
function text(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (!node || typeof node !== 'object') return ''
  return [node?.props?.children].flat(Infinity).map(child => text(child)).join('')
}
function harness(t, response = invoice) {
  const values = []
  let index = 0
  const hooks = {
    useState(initial) {
      const slot = index++
      if (!(slot in values)) values[slot] = initial
      return [values[slot], update => { values[slot] = typeof update === 'function' ? update(values[slot]) : update }]
    },
    useRef(initial) {
      const slot = index++
      if (!(slot in values)) values[slot] = { current: initial }
      return values[slot]
    },
    useMemo: fn => fn(),
    useEffect: () => {},
  }
  const api = { ocrInvoice: async () => ({ code: 0, data: structuredClone(response) }) }
  const opened = []
  const previousWindow = globalThis.window
  globalThis.window = { open: (...args) => { opened.push(args); return null } }
  t.after(() => { if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow })
  t.mock.method(URL, 'createObjectURL', () => 'blob:offline-preview')
  t.mock.method(URL, 'revokeObjectURL', () => {})
  const exports = {}
  new Function('require', 'exports', compiled)(name => name === 'react' ? hooks : name === './wordagentApi' ? { wordagentApi: api } : require(name), exports)
  const render = () => { index = 0; return exports.InvoiceVerificationWorkspace({ onHome() {} }) }
  const button = name => nodes(render()).find(node => node.type === 'button' && text(node) === name)
  const field = label => nodes(nodes(render()).find(node => node.type === 'label' && text(node) === label)).find(node => node.type === 'input')
  const upload = () => nodes(render()).find(node => node.type === 'input' && node.props.type === 'file').props.onChange({ target: { files: [{ type: 'image/png', name: 'offline.png' }] } })
  return { render, button, field, upload, api, opened }
}

test('opening official verification never invents a passed result or result image', async t => {
  const h = harness(t)
  h.upload()
  await tick()
  h.button('打开官方平台查验').props.onClick()
  assert.deepEqual(h.opened, [['https://inv-veri.chinatax.gov.cn/', '_blank', 'noopener,noreferrer']])
  const output = text(h.render())
  assert.match(output, /等待官方查验/)
  assert.doesNotMatch(output, /人工记录：查验通过/)
  assert.equal(nodes(h.render()).filter(node => node.type === 'img' && node.props.alt === '发票详情').length, 0)
})

test('only a manual result action records passed and edited fields invalidate it', async t => {
  const h = harness(t)
  h.upload()
  await tick()
  h.button('查验通过').props.onClick()
  assert.match(text(h.render()), /人工记录：查验通过/)
  assert.match(text(h.render()), /记录时间/)
  h.field('发票号码').props.onChange({ target: { value: '87654321' } })
  const output = text(h.render())
  assert.match(output, /等待官方查验/)
  assert.doesNotMatch(output, /人工记录：查验通过|记录时间/)
})

test('a failed second upload cannot reuse the previous invoice fields or result', async t => {
  const h = harness(t)
  h.upload()
  await tick()
  h.button('查验通过').props.onClick()
  let reject
  h.api.ocrInvoice = () => new Promise((_, fail) => { reject = fail })
  h.upload()
  assert.equal(h.button('打开官方平台查验').props.disabled, true)
  assert.equal(h.field('发票号码'), undefined)
  reject(new Error('offline OCR failure'))
  await tick()
  assert.equal(h.button('打开官方平台查验').props.disabled, true)
  assert.equal(h.field('发票号码'), undefined)
  assert.match(text(h.render()), /offline OCR failure/)
  assert.doesNotMatch(text(h.render()), /人工记录：查验通过/)
})

test('incomplete verification fields do not open an external page', async t => {
  const h = harness(t, { ...invoice, fields: { ...invoice.fields, invoice_number: '' } })
  h.upload()
  await tick()
  h.button('打开官方平台查验').props.onClick()
  assert.equal(h.opened.length, 0)
  assert.match(text(h.render()), /请先补齐/)
})

test('impossible calendar dates cannot pass the local field check', async t => {
  const h = harness(t, { ...invoice, fields: { ...invoice.fields, issue_date: '2026-02-30' } })
  h.upload()
  await tick()
  h.button('打开官方平台查验').props.onClick()
  assert.equal(h.opened.length, 0)
  assert.match(text(h.render()), /开票日期格式需要人工确认/)
})

test('records explicitly disclose their page-only lifetime', async t => {
  const h = harness(t)
  h.upload()
  await tick()
  h.button('查验通过').props.onClick()
  assert.match(text(h.render()), /刷新或离开页面后不保留/)
})
