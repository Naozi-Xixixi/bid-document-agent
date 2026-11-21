import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import PizZip from 'pizzip'

const source = readFileSync(new URL('../src/docxExport.ts', import.meta.url), 'utf8')
const file = ts.createSourceFile('docxExport.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = new Set(['flattenCatalog', 'selectExportChapters', 'resolveExportVersion', 'normalizeTemplateFonts'])
const declarations = file.statements
  .filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
  .map(node => node.getText(file))
assert.equal(declarations.length, names.size)
const compiled = ts.transpileModule(declarations.join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const { selectExportChapters, resolveExportVersion, normalizeTemplateFonts } = new Function('PizZip', 'exports', `${compiled}; return exports;`)(PizZip, {})

const nodes = [
  { chapter_id: 1, title: '第一部分', children: [
    { chapter_id: 2, title: '方案', children: [
      { chapter_id: 3, title: '采购', children: [] },
      { chapter_id: 4, title: '配送', children: [] },
    ] },
  ] },
  { chapter_id: 5, title: '第二部分', children: [
    { chapter_id: 6, title: '验收', children: [] },
  ] },
]

test('exporting selected leaves retains their ancestor headings but excludes siblings', () => {
  assert.deepEqual(selectExportChapters(nodes, [4, 6]).map(item => [item.chapter_id, item.number]), [
    [1, '1'], [2, '1.1'], [4, '1.1.2'], [5, '2'], [6, '2.1'],
  ])
})

test('exporting a selected parent includes its descendants', () => {
  assert.deepEqual(selectExportChapters(nodes, [2]).map(item => item.chapter_id), [1, 2, 3, 4])
})

test('export version follows the page selection, including a non-final version', () => {
  const catalog = { versions: [
    { version_id: 14, is_final: true, catalog_content: nodes },
    { version_id: 15, is_final: false, catalog_content: [{ chapter_id: 9, children: [] }] },
  ] }
  assert.equal(resolveExportVersion(catalog, 15).version_id, 15)
  assert.equal(resolveExportVersion(catalog).version_id, 14)
  assert.throws(() => resolveExportVersion(catalog, 99), /所选目录版本不存在/)
})

test('bundled template headings and cover title use explicit fonts and black text', () => {
  const zip = new PizZip()
  zip.file('word/styles.xml', '<w:styles><w:style w:styleId="Title"><w:pPr><w:pBdr><w:bottom w:color="4F81BD" w:themeColor="accent1"/></w:pBdr></w:pPr><w:rPr><w:color w:val="1F4E79"/></w:rPr></w:style><w:style w:styleId="Heading3"><w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia" w:ascii="宋体" w:eastAsia="宋体"/><w:color w:val="1F4E79"/></w:rPr></w:style><w:rFonts w:asciiTheme="minorHAnsi"/></w:styles>')
  const output = new PizZip(normalizeTemplateFonts(zip.generate({ type: 'arraybuffer' }))).file('word/styles.xml').asText()
  assert.match(output, /w:ascii="宋体" w:eastAsia="宋体"/)
  assert.doesNotMatch(output, /majorHAnsi|majorEastAsia/)
  assert.match(output, /w:color w:val="000000"/)
  assert.doesNotMatch(output, /1F4E79|4F81BD|themeColor="accent1"/)
  assert.match(output, /<w:bottom w:color="000000"\/>/)
  assert.match(output, /w:asciiTheme="minorHAnsi"/)
})

test('the standard built-in template exports black heading styles', () => {
  const file = readFileSync(new URL('../src/assets/bid-templates/standard-public.docx', import.meta.url))
  const template = file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength)
  const xml = new PizZip(normalizeTemplateFonts(template)).file('word/styles.xml').asText()
  for (const name of ['Title', 'Heading1', 'Heading2', 'Heading3']) {
    const style = xml.match(new RegExp(`<w:style\\b[^>]*w:styleId="${name}"[^>]*>[\\s\\S]*?<\\/w:style>`))?.[0]
    assert.ok(style, `${name} style exists`)
    assert.match(style, /<w:color w:val="000000"\/>/)
    assert.doesNotMatch(style, /w:(?:ascii|eastAsia|hAnsi)Theme=/)
    if (name === 'Title') assert.match(style, /<w:bottom[^>]*w:color="000000"[^>]*\/>/)
  }
})

test('built-in exports use a Word table of contents instead of the editing status table', () => {
  for (const name of ['standard-public', 'compact-anonymous', 'food-delivery', 'enterprise-header']) {
    const file = readFileSync(new URL(`../src/assets/bid-templates/${name}.docx`, import.meta.url))
    const xml = new PizZip(file).file('word/document.xml').asText()
    assert.match(xml, /TOC \\o &quot;1-3&quot;|TOC \\o "1-3"/, `${name} has a Word TOC field`)
    assert.match(xml, /\{#include_toc\}/, `${name} honors the TOC option`)
    assert.doesNotMatch(xml, /\{#chapter_rows\}|目标字数/, `${name} has no status table in its TOC`)
    for (const level of [1, 2, 3]) assert.match(xml, new RegExp(`\\{#chapter_heading${level}\\}`))
  }
})
