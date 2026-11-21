import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const result = {}
const source = readFileSync(new URL('../src/components/KnowledgeProcessingProgress.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText
new Function('require', 'exports', compiled)(createRequire(import.meta.url), result)
const { knowledgeProgressState, isKnowledgeProcessingActive } = result
const base = {
  item_id: 1, title: '大文件', status: 'completed', process_progress: 100,
  text_char_count: 140861, deep_status: 'completed', deep_progress: 100,
  deep_failed_count: 0, extracted_image_count: 12, chunk_count: 30,
  vector_status: 'completed', vector_indexed_chunks: 30,
}
const renderItem = (item, props = {}) => renderToStaticMarkup(createElement(result.KnowledgeProcessingProgress, { item, ...props }))
const renderUpload = (progress) => renderToStaticMarkup(createElement(result.KnowledgeUploadProgressPanel, {
  progress: { total: 1, uploadPercent: 100, items: [], failures: [], startedAt: 0, phase: 'accepted', ...progress },
  onClose() {}, onRetry() {},
}))

test('text at 100 percent does not finish ongoing image recognition', () => {
  const item = { ...base, deep_status: 'processing', deep_progress: 47, deep_processed_count: 47, deep_total: 100 }
  const state = knowledgeProgressState(item)
  assert.equal(state.complete, false)
  assert.equal(state.running, true)
  assert.equal(isKnowledgeProcessingActive(item), true)
  assert.equal(state.stages.find(stage => stage.key === 'text').state, 'completed')
  assert.equal(state.stages.find(stage => stage.key === 'deep').percent, 47)
  const html = renderItem(item)
  assert.match(html, /图片文字识别（OCR）中/)
  assert.ok(html.includes("已处理 47 / 100 项"))
  assert.match(html, /aria-label="图片文字识别（OCR）"[^>]*aria-valuenow="47"/)
  assert.doesNotMatch(html, /全部处理完成/)
})

test('vector indexing keeps processing active after text and images finish', () => {
  for (const vector_status of ['pending', 'indexing', 'processing']) {
    const item = { ...base, vector_status, vector_indexed_chunks: 0 }
    const state = knowledgeProgressState(item)
    assert.equal(state.running, true, vector_status)
    assert.equal(isKnowledgeProcessingActive(item), true, vector_status)
    assert.equal(state.complete, false, vector_status)
    assert.equal(state.stages.find(stage => stage.key === 'vector').percent, null)
    const html = renderItem(item)
    const bar = html.match(/<div[^>]*role="progressbar"[^>]*aria-label="检索索引"[^>]*>/)?.[0]
    assert.ok(bar)
    assert.doesNotMatch(bar, /aria-valuenow/)
    assert.match(bar, /is-indeterminate/)
  }
})

test('an unknown index status cannot claim all processing is complete', () => {
  for (const vector_status of [undefined, '', 'unknown']) {
    const item = { ...base, vector_status }
    assert.equal(knowledgeProgressState(item).complete, false)
    const html = renderItem(item)
    assert.match(html, /索引状态待确认/)
    assert.doesNotMatch(html, /全部处理完成/)
    assert.ok(!renderUpload({ items: [item] }).includes("<strong>全部处理完成</strong>"))
  }
})

test('only fully completed stages produce a successful summary', () => {
  const state = knowledgeProgressState(base)
  assert.equal(state.complete, true)
  assert.equal(state.running, false)
  assert.equal(state.hasIssue, false)
  assert.match(renderItem(base), /全部处理完成/)
  assert.ok(renderUpload({ items: [base] }).includes("全部处理完成 1 / 1 个文件"))
})

test('partial image failures remain visible even when the worker finished', () => {
  const item = { ...base, deep_failed_count: 2 }
  const state = knowledgeProgressState(item)
  assert.equal(state.complete, false)
  assert.equal(state.hasIssue, true)
  assert.equal(state.stages.find(stage => stage.key === 'deep').state, 'failed')
  const html = renderItem(item)
  assert.match(html, /处理结束，有异常/)
  assert.match(html, /2 项识别失败/)
  assert.doesNotMatch(html, /全部处理完成/)
})

test('bytes at 100 percent still wait for server acceptance', () => {
  const html = renderUpload({ phase: 'uploading' })
  assert.match(html, /文件已发送，等待服务器接收/)
  assert.match(html, /文件传输 100%/)
  assert.match(html, /is-indeterminate/)
  assert.doesNotMatch(html, /aria-valuenow|全部处理完成|收起/)
})

test('upload failures display the actual error and never show success', () => {
  const html = renderUpload({ phase: 'failed', uploadPercent: 38, error: '连接已断开，请重试' })
  assert.match(html, /上传未完成/)
  assert.match(html, /连接已断开，请重试/)
  assert.match(html, /aria-label="文件上传"[^>]*aria-valuenow="38"/)
  assert.doesNotMatch(html, /全部处理完成/)
})

test('primary parse failures expose errors and retry without implying success', () => {
  const item = { ...base, status: 'failed', process_error: '文件结构损坏', deep_status: 'not_started', vector_status: 'pending' }
  const state = knowledgeProgressState(item)
  assert.equal(state.hasIssue, true)
  assert.equal(state.complete, false)
  assert.equal(state.running, false)
  const html = renderUpload({ items: [item] })
  assert.match(html, /文件结构损坏/)
  assert.match(html, /重试解析/)
  assert.match(html, /处理结束，有异常/)
})

test('failed or unconfigured index service cannot be reported as completed', () => {
  for (const vector_status of ['failed', 'not_configured']) {
    const item = { ...base, vector_status, vector_error: '索引服务不可用' }
    const state = knowledgeProgressState(item)
    assert.equal(state.complete, false)
    assert.equal(state.hasIssue, true)
    assert.equal(state.stages.find(stage => stage.key === 'vector').state, 'failed')
    assert.match(renderItem(item), /索引服务不可用/)
  }
})

test('numeric progress is bounded for both bar size and accessibility', () => {
  for (const [value, expected] of [[-8, 0], [143, 100], [NaN, 0], [Infinity, 0]]) {
    const item = { ...base, status: 'processing', process_progress: value, deep_status: 'processing', deep_progress: value, deep_total: 100 }
    const state = knowledgeProgressState(item)
    assert.equal(state.stages.find(stage => stage.key === 'text').percent, expected)
    assert.equal(state.stages.find(stage => stage.key === 'deep').percent, expected)
    const html = renderItem(item)
    assert.ok(html.includes('aria-valuenow="' + expected + '"'))
    assert.ok(html.includes('width:' + expected + '%'))
    assert.doesNotMatch(html, /NaN|Infinity|width:-|aria-valuenow="143"/)
    assert.match(html, /aria-valuemin="0" aria-valuemax="100"/)
  }
})

test('batch progress preserves each file stage rather than averaging away slow files', () => {
  const html = renderUpload({ total: 3, items: [
    { ...base, item_id: 1, title: '已完成文件' },
    { ...base, item_id: 2, title: '等待图片文件', deep_status: 'processing', deep_progress: 47 },
    { ...base, item_id: 3, title: '等待索引文件', vector_status: 'indexing', vector_indexed_chunks: 0 },
  ] })
  assert.match(html, /文件解析处理中/)
  assert.ok(html.includes("全部处理完成 1 / 3 个文件"))
  const articles = [...html.matchAll(/<article>(.*?)<\/article>/g)].map(match => match[1])
  assert.equal(articles.length, 3)
  assert.match(articles[0], /已完成文件/)
  assert.match(articles[0], /全部处理完成/)
  assert.match(articles[1], /等待图片文件/)
  assert.match(articles[1], /aria-label="图片文字识别（OCR）"[^>]*aria-valuenow="47"/)
  assert.doesNotMatch(articles[1], /全部处理完成/)
  assert.match(articles[2], /等待索引文件/)
  assert.match(articles[2], /检索索引中/)
  assert.doesNotMatch(articles[2], /全部处理完成/)
})

test('rejected files retain their filenames and failure explanations', () => {
  const html = renderUpload({ total: 2, items: [base], failures: [{ file_name: '失败文件.docx', reason: '无法读取文件' }] })
  assert.match(html, /处理结束，有异常/)
  assert.ok(html.includes("全部处理完成 1 / 2 个文件"))
  assert.match(html, /失败文件.docx/)
  assert.match(html, /无法读取文件/)
})

test('file titles and server errors render as escaped text', () => {
  const html = renderUpload({ items: [{ ...base, title: '<img src=x>', status: 'failed', process_error: '<script>bad()</script>' }] })
  assert.match(html, /&lt;img src=x&gt;/)
  assert.ok(html.includes("&lt;script&gt;bad()&lt;/script&gt;"))
  assert.doesNotMatch(html, /<script>|<img src=/)
})


test('a previous index count does not manufacture a current indexing percentage', () => {
  const item = { ...base, vector_status: 'indexing', vector_indexed_chunks: 15, chunk_count: 30 }
  const state = knowledgeProgressState(item)
  assert.equal(state.stages.find(stage => stage.key === 'vector').percent, null)
  const bar = renderItem(item).match(/<div[^>]*role="progressbar"[^>]*aria-label="检索索引"[^>]*>/)?.[0]
  assert.ok(bar)
  assert.doesNotMatch(bar, /aria-valuenow/)
})

test('partial failures do not stop tracking the remaining active recognition work', () => {
  const item = { ...base, deep_status: 'processing', deep_progress: 47, deep_failed_count: 2 }
  const state = knowledgeProgressState(item)
  assert.equal(state.complete, false)
  assert.equal(state.hasIssue, true)
  assert.equal(isKnowledgeProcessingActive(item), true)
  assert.match(renderItem(item), /2 项识别失败/)
})

test('image preparation without a known total uses an indeterminate bar', () => {
  const item = { ...base, deep_status: 'processing', deep_progress: 0, deep_total: 0 }
  assert.equal(knowledgeProgressState(item).stages.find(stage => stage.key === 'deep').percent, null)
  const html = renderItem(item)
  const bar = html.match(/<div[^>]*role="progressbar"[^>]*aria-label="图片文字识别（OCR）"[^>]*>/)?.[0]
  assert.ok(bar)
  assert.doesNotMatch(bar, /aria-valuenow/)
  assert.match(bar, /is-indeterminate/)
  assert.match(html, /准备图片，等待识别/)
})


test('legacy completion cannot hide unprocessed OCR images', () => {
  const item = { ...base, ocr_unprocessed_count: 8 }
  assert.equal(knowledgeProgressState(item).complete, false)
  assert.match(renderItem(item), /待补识别 8 张/)
})

test('recognized and no-readable-text counts are distinct', () => {
  const html = renderItem({ ...base, ocr_recognized_count: 9, ocr_no_text_count: 3, ocr_unprocessed_count: 0 })
  assert.match(html, /识别出文字 9 张/)
  assert.match(html, /未检测到可读文字 3 张/)
})
