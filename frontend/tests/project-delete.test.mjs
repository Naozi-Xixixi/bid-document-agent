import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const workspace = readFileSync(new URL('../src/P1Workspace.tsx', import.meta.url), 'utf8')
const sourceFile = ts.createSourceFile('P1Workspace.tsx', workspace, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const declaration = name => {
  let result
  const visit = node => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) result = node
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  assert.ok(result, `Expected ${name} in the actual workspace`)
  return result.getText(sourceFile)
}
const compile = source => ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText
const flush = () => new Promise(resolve => setImmediate(resolve))
const project = (id, title) => ({ id, bidId: id === 'one' ? 11 : 22, settings: { bidTitle: title, bidType: 'open' }, updatedAt: '2026-10-20T00:00:00Z' })
const sampleProjects = [project('one', '项目一'), project('two', '项目二')]
const deletedResult = { success: true, deleted: true, projectId: 'one', bidId: 11, warnings: [], cleanup: { status: 'completed', removed: 1, preservedShared: 0 } }
const nodes = tree => {
  if (!tree || typeof tree !== 'object') return []
  const children = tree.props?.children
  return [tree, ...(Array.isArray(children) ? children.flat(Infinity) : [children]).flatMap(nodes)]
}

// The real list component runs against an in-memory hook harness and fake APIs.
// Nothing in these interaction tests can reach a project server.
async function historyFixture(deleteProject) {
  const slots = []
  const effects = []
  let cursor = 0
  const selected = []
  const deleted = []
  const calls = []
  const useState = initial => {
    const index = cursor++
    if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial
    return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next }]
  }
  const useRef = initial => {
    const index = cursor++
    if (!(index in slots)) slots[index] = { current: initial }
    return slots[index]
  }
  const useEffect = (effect, dependencies) => {
    const index = cursor++
    const previous = slots[index]
    if (!previous || dependencies.some((value, i) => value !== previous.dependencies[i])) {
      effects.push(() => {
        previous?.cleanup?.()
        slots[index] = { dependencies, cleanup: effect() }
      })
    }
  }
  const jsx = (type, props, key) => ({ type, props, key })
  const api = {
    listProjects: async () => ({ projects: sampleProjects }),
    getProject: async () => { throw new Error('History must not load the full project body') },
    getBidParseStatus: async bidId => ({ code: 0, data: { bid_id: bidId, parse_status: 'completed' } }),
    deleteProject: async id => { calls.push(id); return deleteProject(id) },
  }
  const errorText = error => {
    try { return JSON.parse(error.message).detail } catch { return error.message }
  }
  const Component = new Function(
    'useState', 'useRef', 'useEffect', 'wordagentApi', 'apiErrorMessage', 'formatProjectDate',
    'TopNav', 'ProjectDeleteDialog', 'ProjectSourceSummary', 'BID_CATEGORY_FROM_API', 'require', 'exports',
    compile(declaration('P1ProjectSelect')) + '; return P1ProjectSelect;',
  )(useState, useRef, useEffect, api, errorText, value => value, 'TopNav', 'ProjectDeleteDialog', 'ProjectSourceSummary', {}, () => ({ jsx, jsxs: jsx }), {})
  const render = () => {
    cursor = 0
    const tree = Component({ onHome() {}, onCreate() {}, onSelect: id => selected.push(id), onDeleted: (...args) => deleted.push(args) })
    effects.splice(0).forEach(effect => effect())
    return tree
  }
  render()
  await flush()
  const find = predicate => nodes(render()).find(predicate)
  const dialog = () => find(node => node.type === 'ProjectDeleteDialog')
  const rows = () => nodes(render()).filter(node => node.props?.className === 'p1-history-row')
  const openDelete = () => {
    let stopped = false
    find(node => node.props?.['aria-label'] === '删除项目：项目一').props.onClick({ stopPropagation() { stopped = true } })
    assert.equal(stopped, true)
    return dialog()
  }
  return { render, find, dialog, rows, openDelete, selected, deleted, calls }
}

test('opening and cancelling deletion neither opens the project nor sends DELETE', async () => {
  const state = await historyFixture(async () => { throw new Error('DELETE must not be called') })
  state.openDelete().props.onCancel()
  assert.equal(state.dialog(), undefined)
  assert.equal(state.rows().length, 2)
  assert.deepEqual(state.selected, [])
  assert.deepEqual(state.calls, [])
})

test('pending deletion blocks repeats and closing; success removes only that project and keeps warnings', async () => {
  let resolve
  const pending = new Promise(done => { resolve = done })
  const state = await historyFixture(() => pending)
  const dialog = state.openDelete()
  dialog.props.onConfirm()
  dialog.props.onConfirm()
  state.dialog().props.onCancel()
  assert.deepEqual(state.calls, ['one'])
  assert.equal(state.dialog().props.deleting, true)
  assert.ok(state.find(node => node.props?.inert === true))
  assert.equal(state.rows().length, 2)
  resolve({ ...deletedResult, warnings: ['部分副本待清理'], cleanup: { status: 'pending', removed: 0, preservedShared: 1 } })
  await flush()
  assert.equal(state.dialog(), undefined)
  assert.equal(state.rows().length, 1)
  assert.deepEqual(state.deleted, [['one', 11]])
  assert.deepEqual(state.selected, [])
  assert.ok(state.find(node => node.props?.children === '部分副本待清理'))
})

test('a busy-project conflict leaves the project and confirmation dialog available', async () => {
  const state = await historyFixture(async () => { throw new Error(JSON.stringify({ detail: '项目正在生成，暂时不能删除' })) })
  state.openDelete().props.onConfirm()
  await flush()
  assert.equal(state.rows().length, 2)
  assert.equal(state.dialog().props.deleting, false)
  assert.match(state.dialog().props.error, /项目正在生成/)
  assert.deepEqual(state.deleted, [])
  state.dialog().props.onCancel()
  assert.equal(state.dialog(), undefined)
})

const clearSelection = (selectedBidId, cachedBidId, deletedBidId) => {
  const state = { project: undefined, bidId: undefined, phase: undefined, removed: [] }
  const action = new Function('project', 'bidId', 'lastBidIdKey', 'window', 'setProject', 'setBidId', 'setPhase',
    compile(declaration('handleProjectDeleted')) + '; return handleProjectDeleted;')(
    null, selectedBidId, 'last-bid', { localStorage: { getItem: () => String(cachedBidId), removeItem: key => state.removed.push(key) } },
    value => { state.project = value }, value => { state.bidId = value }, value => { state.phase = value },
  )
  action('one', deletedBidId)
  return state
}

test('deleting the remembered project clears selection even if its project object was never loaded', () => {
  assert.deepEqual(clearSelection(11, 11, 11), { project: null, bidId: null, phase: 0, removed: ['last-bid'] })
})

test('deleting another project preserves the current project selection', () => {
  assert.deepEqual(clearSelection(22, 22, 11), { project: undefined, bidId: undefined, phase: undefined, removed: [] })
})

test('confirmation describes irreversible scope and disables both controls while deleting', () => {
  const result = {}
  new Function('require', 'exports', compile(readFileSync(new URL('../src/components/ProjectDeleteDialog.tsx', import.meta.url), 'utf8')))(createRequire(import.meta.url), result)
  const html = renderToStaticMarkup(createElement(result.ProjectDeleteDialog, { projectName: '测试项目', deleting: true, error: '', onCancel() {}, onConfirm() {} }))
  assert.match(html, /删除后无法恢复/)
  assert.match(html, /不再被使用的上传副本/)
  assert.match(html, /电脑上的原始文件、企业知识库及其他项目正在使用的资料会保留/)
  assert.match(html, /aria-busy="true"/)
  assert.equal((html.match(/disabled=""/g) ?? []).length, 2)
})

test('project deletion uses DELETE with an encoded project identifier', async () => {
  const apiSource = readFileSync(new URL('../src/wordagentApi.ts', import.meta.url), 'utf8')
  const apiFile = ts.createSourceFile('wordagentApi.ts', apiSource, ts.ScriptTarget.Latest, true)
  let method
  const visit = node => {
    if (ts.isPropertyAssignment(node) && node.name.getText(apiFile) === 'deleteProject') method = node.initializer.getText(apiFile)
    ts.forEachChild(node, visit)
  }
  visit(apiFile)
  assert.ok(method)
  const calls = []
  const deleteProject = new Function('request', compile(`const deleteProject = ${method}`) + '; return deleteProject;')(
    async (...args) => { calls.push(args); return deletedResult },
  )
  assert.deepEqual(await deleteProject('project /?一'), deletedResult)
  assert.deepEqual(calls, [[`/projects/${encodeURIComponent('project /?一')}`, { method: 'DELETE' }]])
})
