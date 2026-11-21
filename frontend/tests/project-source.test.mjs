import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const result = {}
const source = readFileSync(new URL('../src/components/ProjectSourceSummary.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText
new Function('require', 'exports', compiled)(createRequire(import.meta.url), result)
const render = (parse, props = {}) => renderToStaticMarkup(createElement(result.ProjectSourceSummary, { project: {}, parse, loadState: 'ready', ...props }))

test('uploaded source exposes original filename, size and parse status', () => {
  const html = render({ parse_mode: 'auto_upload', tender_file_name: '学校采购（定稿）.docx', tender_file_size: 104857600, parse_status: 'completed' })
  assert.match(html, /来源：上传招标文件 · 学校采购（定稿）.docx/)
  assert.match(html, /100.0 MiB · 解析完成/)
})

test('pasted information is identified as text rather than a stale uploaded filename', () => {
  const html = render({ parse_mode: 'paste_text', tender_file_name: '旧资料.docx', tender_file_size: 1024, parse_status: 'processing' })
  assert.match(html, /来源：手动粘贴的项目概述和评分要求/)
  assert.match(html, /解析中/)
  assert.doesNotMatch(html, /旧资料|KiB|上传招标文件/)
})

test('only a confirmed empty source is labelled as not yet supplied', () => {
  const html = render({ parse_mode: null, tender_file_name: null, parse_status: 'pending' })
  assert.match(html, /尚未上传招标文件或填写解析资料/)
  assert.doesNotMatch(html, /待解析/)
})

test('source failures and loading never misrepresent existing documents as absent', () => {
  const pending = render(undefined, { loadState: 'loading' })
  const unavailable = render(undefined, { loadState: 'unavailable' })
  assert.match(pending, /正在读取来源信息/)
  assert.match(unavailable, /来源信息暂不可用/)
  assert.doesNotMatch(pending + unavailable, /尚未上传/)
})

test('legacy reference attachments retain their original names and sizes', () => {
  const html = render({ parse_mode: null }, { project: { files: [{ filename: '企业资格.pdf', size: 2048 }, { filename: '历史方案.docx', size: 512 }] } })
  assert.match(html, /来源：历史项目上传的参考文件/)
  assert.match(html, /企业资格.pdf（2.0 KiB）/)
  assert.match(html, /历史方案.docx（512 B）/)
  assert.doesNotMatch(html, /尚未上传/)
})

test('an old attachment does not hide unavailable current parse metadata', () => {
  const html = render(undefined, { project: { files: [{ filename: '历史参考.pdf', size: 2048 }] }, loadState: 'unavailable' })
  assert.match(html, /来源信息暂不可用/)
  assert.match(html, /参考附件：历史参考.pdf/)
})

test('missing old filenames are marked unknown and failed parsing remains visible', () => {
  const html = render({ parse_mode: 'auto_upload', tender_file_name: null, tender_file_size: null, parse_status: 'failed' })
  assert.match(html, /原文件名未记录/)
  assert.match(html, /解析失败/)
  assert.doesNotMatch(html, /undefined|null|NaN/)
})

test('filenames are rendered as text with wrapping, never HTML', () => {
  const html = render({ parse_mode: 'auto_upload', tender_file_name: '<img src=x onerror=alert(1)>.docx', tender_file_size: -1, parse_status: 'pending' })
  assert.match(html, /&lt;img/)
  assert.doesNotMatch(html, /<img|NaN/)
  assert.match(html, /overflow-wrap:anywhere/)
})
