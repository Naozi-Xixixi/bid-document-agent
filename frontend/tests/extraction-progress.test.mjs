import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

// Render the actual component without a browser, network requests or extra dependencies.
const source = readFileSync(new URL('../src/components/ExtractionProgress.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText
const componentModule = {}
new Function('require', 'exports', compiled)(createRequire(import.meta.url), componentModule)
const { ExtractionProgress } = componentModule

const fixture = (changes = {}) => ({
  status: 'running',
  completed: 3,
  total: 8,
  percent: 37,
  current_key: 'qualification',
  current_label: '资格要求',
  message: '正在提取资格要求',
  started_at: '2026-10-20T01:00:00+00:00',
  updated_at: '2026-10-20T01:00:10+00:00',
  items: [
    { key: 'prepare', label: '准备', status: 'completed', attempt: 1 },
    { key: 'project_overview', label: '项目概况', status: 'completed', attempt: 1 },
    { key: 'scoring', label: '评分要求', status: 'completed', attempt: 1 },
    { key: 'qualification', label: '资格要求', status: 'running', attempt: 1 },
    { key: 'commercial', label: '商务要求', status: 'pending', attempt: 0 },
    { key: 'invalid', label: '否决条款', status: 'pending', attempt: 0 },
    { key: 'summary', label: '汇总', status: 'pending', attempt: 0 },
    { key: 'save', label: '保存', status: 'pending', attempt: 0 },
  ],
  ...changes,
})
const render = (progress, options = {}) => renderToStaticMarkup(createElement(ExtractionProgress, {
  progress, parseStatus: 'processing', elapsedSeconds: 12, ...options,
}))
const progressTag = html => html.match(/<progress\b[^>]*>/)?.[0] ?? ''

test('legacy tasks remain indeterminate without an invented zero percent', () => {
  const html = render(null, { elapsedFromServer: false })
  assert.ok(progressTag(html))
  assert.doesNotMatch(progressTag(html), /\bvalue=/)
  assert.doesNotMatch(html, /\b0%/)
  assert.match(html, /等待模型返回/)
  assert.match(html, /这个任务暂无分项进度/)
  assert.match(html, /已用时间（本次页面）/)
})

test('partial extraction displays server percent, completed count and all eight items', () => {
  const html = render(fixture())
  assert.match(progressTag(html), /\bvalue="37"/)
  assert.match(html, /已完成 3\/8 项/)
  assert.match(html, /当前：资格要求/)
  assert.equal((html.match(/<li\b/g) ?? []).length, 8)
  assert.match(html, /不代表预计耗时/)
})

test('retries are visible for the active task and in the item list', () => {
  const progress = fixture()
  progress.items[3].attempt = 2
  const html = render(progress)
  assert.match(html, /重试第 1 次/)
  assert.match(html, /第 2 次尝试/)
  assert.match(progressTag(html), /\bvalue="37"/)
})

test('failed or still-running tasks never display completed 100 percent', () => {
  for (const options of [
    { progress: fixture({ status: 'failed', percent: 100 }), parseStatus: 'failed' },
    { progress: fixture({ status: 'running', percent: 100 }), parseStatus: 'processing' },
    { progress: fixture({ status: 'completed', percent: 100 }), parseStatus: 'failed' },
  ]) {
    const html = render(options.progress, { parseStatus: options.parseStatus })
    assert.match(progressTag(html), /\bvalue="99"/)
    assert.doesNotMatch(html.replace(/<[^>]*>/g, ''), /100%/)
    if (options.parseStatus === 'failed') assert.match(html, /信息提取未完成/)
  }
})

test('saved completed extraction displays 100 percent and eight completed items', () => {
  const progress = fixture({ status: 'completed', completed: 8, percent: 100, current_key: 'save', current_label: '保存' })
  progress.items = progress.items.map(item => ({ ...item, status: 'completed', attempt: 1 }))
  const html = render(progress, { parseStatus: 'completed' })
  assert.match(progressTag(html), /\bvalue="100"/)
  assert.match(html, /已完成 8\/8 项/)
  assert.match(html, /提取结果已保存/)
})

test('fallback results are explicitly identified in the expandable list', () => {
  const progress = fixture()
  progress.items[2].status = 'fallback'
  const html = render(progress)
  assert.match(html, /<details>/)
  assert.match(html, /已完成（备用方式）/)
  assert.match(html, /已完成 3\/8 项/)
})

test('an unknown denominator cannot produce a determinate percentage', () => {
  for (const total of [0, Number.NaN]) {
    const html = render(fixture({ total, percent: 50 }))
    assert.doesNotMatch(progressTag(html), /\bvalue=/)
    assert.doesNotMatch(html, /50%/)
  }
})

test('elapsed time changes never advance the reported completion percentage', () => {
  const progress = fixture()
  const first = render(progress, { elapsedSeconds: 1 })
  const later = render(progress, { elapsedSeconds: 125 })
  assert.equal(progressTag(first), progressTag(later))
  assert.match(first, /1秒/)
  assert.match(later, /2分 5秒/)
  assert.match(later, /已完成 3\/8 项/)
})
