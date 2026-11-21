import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

// Exercise the actual resolver without mounting React or making network requests.
const workspaceSource = readFileSync(new URL('../src/P1Workspace.tsx', import.meta.url), 'utf8')
const sourceFile = ts.createSourceFile('P1Workspace.tsx', workspaceSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const resolverDeclaration = sourceFile.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'resolveDuplicateFileDecision')
assert.ok(resolverDeclaration, 'P1Workspace must define the duplicate-file resolver')
const compiled = ts.transpileModule(resolverDeclaration.getText(sourceFile), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const resolverWith = wordagentApi => new Function('wordagentApi', compiled + '; return resolveDuplicateFileDecision;')(wordagentApi)

const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

const fixture = (pauseAt) => {
  const reached = deferred()
  const release = deferred()
  const calls = []
  const changed = []
  const controller = new AbortController()
  const match = { bid_id: 22, project_id: 11, project_title: 'Mock project' }
  const step = async (name, value) => {
    calls.push(name)
    if (pauseAt === name) {
      reached.resolve()
      await release.promise
    }
    return value
  }
  const api = {
    checkDuplicateBidParseFile: () => step('check', { code: 0, data: { matches: [match] } }),
    createProject: () => step('create', { bidId: 33, project: { id: 44 } }),
    getProject: () => step('project', { bidId: 22, project: { id: 11 } }),
    mergeDuplicateBidParse: () => step('merge', { code: 0 }),
  }
  const choice = pauseAt === 'create' ? 'new' : pauseAt === 'merge' ? 'merge' : 'overwrite'
  const request = resolverWith(api)(
    5,
    { name: 'fixture.docx', size: 123 },
    operation => operation(),
    (...args) => changed.push(args),
    controller.signal,
    () => step('choose', choice),
  )
  return { reached, release, calls, changed, controller, request }
}

const cancellationCases = [
  ['check', 'cancelling during duplicate lookup never opens a chooser or mutates a project', ['check']],
  ['choose', 'cancelling during duplicate choice never starts a project mutation', ['check', 'choose']],
  ['create', 'cancelling while creating a project never navigates after the response', ['check', 'choose', 'create']],
  ['project', 'cancelling while loading an existing project never navigates after the response', ['check', 'choose', 'project']],
  ['merge', 'cancelling while merging never reports a successful merge after the response', ['check', 'choose', 'merge']],
]

for (const [pauseAt, name, expectedCalls] of cancellationCases) {
  test(name, async () => {
    const state = fixture(pauseAt)
    await state.reached.promise
    const rejected = assert.rejects(state.request, { name: 'AbortError' })
    state.controller.abort()
    state.release.resolve()
    await rejected
    assert.deepEqual(state.calls, expectedCalls)
    assert.deepEqual(state.changed, [])
  })
}

test('an uninterrupted overwrite switches to the matched project and returns its bid ID', async () => {
  const state = fixture()
  assert.equal(await state.request, 22)
  assert.deepEqual(state.calls, ['check', 'choose', 'project'])
  assert.deepEqual(state.changed, [[{ id: 11 }, 22]])
})
