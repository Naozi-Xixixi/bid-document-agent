import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'

const source = readFileSync(new URL('../src/InvoiceBatchWorkspace.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText
const require = createRequire(import.meta.url)
function nodes(node) { if (!node || typeof node !== 'object') return []; return [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)] }
function text(node) { if (typeof node === 'string' || typeof node === 'number') return String(node); if (!node || typeof node !== 'object') return ''; return [node.props?.children].flat(Infinity).map(text).join('') }
const fields = { invoice_number:'12345678', issue_date:'2026-10-20', amount_excluding_tax:'100.00' }
const item = { id:'item', batch_id:'batch', filename:'invoice.pdf', ocr_status:'completed', error:'', result:{ document_type:'vat_invoice', fields, local_verification:{status:'review',checks:[]} }, verification_status:'manual_pending', verification_note:'', has_evidence:false, verified_by:'', verified_at:'', revision:3 }
const batch = { id:'batch', title:'批次', created_by:'alice', created_at:'2026-10-30T00:00:00Z', items:[item], summary:{total:1,pending:0,ocr_failed:0,passed:0,failed:0,review:0,unverified:1} }
function harness() {
  const values = [[batch], 'batch', batch, 'item', [], {...fields}, 3, '', null, [], false, '', '']
  let index = 0, calls = []
  const hooks = { useState(initial) { const slot = index++; if (!(slot in values)) values[slot] = initial; return [values[slot], v => { values[slot] = typeof v === 'function' ? v(values[slot]) : v }] }, useRef(value) { const slot = index++; if (!(slot in values)) values[slot] = {current:value}; return values[slot] }, useEffect(){}, useCallback(fn){return fn} }
  const authRequest = async (path, options) => { calls.push({path,options}); if (path.endsWith('/verification')) return {data:{...item,verification_status:'passed',revision:4}}; return {data:batch} }
  const exports = {}
  new Function('require','exports',compiled)(name => name === 'react' ? hooks : name === './auth' ? {authRequest,API_BASE:'/api',getAuthToken:()=>''} : require(name), exports)
  const render = () => {index=0;return exports.InvoiceBatchWorkspace({onHome(){}})}
  const button = name => nodes(render()).find(n=>n.type==='button' && text(n)===name)
  return {render,button,calls}
}
test('passed and failed cannot be recorded without a screenshot', () => {
  const h=harness()
  assert.equal(h.button('记录通过').props.disabled,true)
  assert.equal(h.button('记录未通过').props.disabled,true)
  assert.equal(h.button('记录待复核').props.disabled,false)
})
test('unsaved field edits prevent recording and preserve the old result', () => {
  const h=harness(); const input=nodes(h.render()).find(n=>n.type==='input' && n.props.value==='12345678')
  input.props.onChange({target:{value:'87654321'}})
  assert.equal(h.button('记录待复核').props.disabled,true)
  assert.equal(h.button('保存字段修改').props.disabled,false)
  assert.equal(h.calls.length,0)
})
test('manual result submission includes the selected screenshot and revision', async () => {
  const h=harness(); const input=nodes(h.render()).find(n=>n.type==='input' && n.props.accept==='image/jpeg,image/png,image/webp,image/bmp')
  const screenshot=new Blob(['fake'],{type:'image/png'}); input.props.onChange({target:{files:[screenshot]}})
  assert.equal(h.button('记录通过').props.disabled,false)
  await h.button('记录通过').props.onClick()
  const call=h.calls.find(x=>x.path.endsWith('/verification'))
  assert.equal(call.options.body.get('status'),'passed'); assert.equal(call.options.body.get('revision'),'3'); assert.ok(call.options.body.get('evidence'))
})
