import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const source = readFileSync(new URL('../src/docxExport.ts', import.meta.url), 'utf8')
const file = ts.createSourceFile('docxExport.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const pieces = file.statements.filter(node => (ts.isFunctionDeclaration(node) && node.name?.text === 'checkBlindExport') || (ts.isClassDeclaration(node) && node.name?.text === 'BlindCheckError')).map(node => node.getText(file)).join('\n')
const compiled = ts.transpileModule(pieces, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
function setup(report) {
  const api = { checkBlindDocx: async () => report }
  const exports = new Function('wordagentApi', 'exports', compiled + '; return exports')(api, {})
  return { check: exports.checkBlindExport, api }
}
const blob = new Blob(['final word'])
test('blocked findings cannot be overridden by a review callback', async () => {
  const { check } = setup({ code: 0, data: { required: true, blocked: true, issues: [{ detail: '命中' }] } })
  await assert.rejects(check(1, blob, '企业', async () => true), /发现问题/)
})
test('cancelled manual review prevents export', async () => {
  const { check } = setup({ code: 0, data: { required: true, blocked: false, images: [{}] } })
  await assert.rejects(check(1, blob, '企业', async () => false), /取消导出/)
})
test('an unavailable check never silently passes or falls back', async () => {
  const { check, api } = setup(null)
  api.checkBlindDocx = async () => { throw new Error('offline') }
  await assert.rejects(check(1, blob, '企业', async () => true), /已阻止导出/)
})
test('clean report with explicit review permits export', async () => {
  const { check } = setup({ code: 0, data: { required: true, blocked: false, images: [] } })
  let called = false
  await check(1, blob, '企业', async () => { called = true; return true })
  assert.equal(called, true)
})
