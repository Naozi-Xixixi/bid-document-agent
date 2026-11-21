import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const exports = {}
const source = readFileSync(new URL('../src/components/KnowledgeAnalysisProgress.tsx', import.meta.url), 'utf8')
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText
new Function('require', 'exports', code)(createRequire(import.meta.url), exports)
const render = analysis => renderToStaticMarkup(createElement(exports.KnowledgeAnalysisProgress, { analysis }))
const coverage = {
  status: 'processing', completed_batches: 2, total_batches: 4, failed_batches: 0,
  covered_chunks: 60, total_chunks: 130, covered_chars: 40000, total_chars: 80000,
  percent: 50, scope: '已提取的正文文本（不包含未识别的扫描图片）',
}

test('actual coverage and batch count are visible during interpretation', () => {
  const html = render({ status: 'processing', coverage })
  assert.match(html, /value="50" max="100"/)
  assert.match(html, /2 \/ 4 批/)
  assert.match(html, /60 \/ 130 个片段/)
  assert.doesNotMatch(html, /AI 已完成全部文本批次/)
})

test('partial failure preserves coverage and explains resumable retry', () => {
  const html = render({ status: 'failed', error: '第 3 批超时', coverage: { ...coverage, failed_batches: 1 } })
  assert.match(html, /AI 解读未完成/)
  assert.match(html, /重试将保留已完成结果/)
  assert.match(html, /第 3 批超时/)
})

test('legacy complete results never imply full coverage', () => {
  assert.match(render({ status: 'completed' }), /尚未记录全文覆盖率/)
  assert.doesNotMatch(render({ status: 'completed' }), /100%/)
})

test('full text coverage is explicitly scoped and requires completion', () => {
  const html = render({ status: 'completed', coverage: { ...coverage, percent: 100 } })
  assert.match(html, /AI 已完成全部文本批次/)
  assert.match(html, /不包含未识别的扫描图片/)
  assert.doesNotMatch(render({ status: 'pending', coverage: { ...coverage, percent: 100 } }), /AI 已完成全部文本批次/)
})
