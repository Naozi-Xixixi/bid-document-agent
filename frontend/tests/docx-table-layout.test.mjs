import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const source = readFileSync(new URL('../src/docxExport.ts', import.meta.url), 'utf8')
const file = ts.createSourceFile('docxExport.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = new Set(['escapeXml', 'tableXml', 'parseTenderTableLayout'])
const declarations = file.statements
  .filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
  .map(node => node.getText(file))
assert.equal(declarations.length, names.size)
const compiled = ts.transpileModule(declarations.join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const { tableXml, parseTenderTableLayout } = new Function(`${compiled}; return { tableXml, parseTenderTableLayout };`)()

test('body table centers every cell vertically and leaves half a line after the table', () => {
  const xml = tableXml([['资源类别', '拟配置情况'], ['车辆', '两辆冷藏车']], '<w:tblPr/>')
  assert.equal((xml.match(/<w:vAlign w:val="center"\/>/g) || []).length, 4)
  assert.equal((xml.match(/<w:jc w:val="center"\/>/g) || []).length, 4)
  assert.equal((xml.match(/<w:spacing w:before="0" w:after="0"\/>/g) || []).length, 4)
  assert.equal((xml.match(/<w:cantSplit\/>/g) || []).length, 2)
  assert.equal((xml.match(/<w:tblHeader\/>/g) || []).length, 1)
  assert.match(xml, /<\/w:tbl><w:p><w:pPr><w:keepNext\/><w:spacing w:before="0" w:after="0" w:line="120" w:lineRule="exact"\/>/)
})


test('wide quote tables retain unconfirmed blank price fields', () => {
  const xml = tableXml([['序号','品名','单位','数量','单价（元）','合价（元）'], ['1','鲜肉','斤','按需','','']], '<w:tblPr/>')
  assert.match(xml, /单价（元）/)
  assert.match(xml, /合价（元）/)
  assert.equal((xml.match(/<w:tr>/g) || []).length, 2)
  assert.equal((xml.match(/<w:gridCol /g) || []).length, 6)
})


test('source form retains widths, vertical merges and has no invented header', () => {
  const layout={widths:[100,300],heights:[30,30],header:false,cells:[{row:0,col:0,rowspan:2,colspan:1},{row:0,col:1,rowspan:1,colspan:1},{row:1,col:1,rowspan:1,colspan:1}]}
  const xml=tableXml([['报价',''],['','']],'<w:tblPr/>',layout)
  assert.match(xml, /gridCol w:w="2250"/)
  assert.match(xml, /gridCol w:w="6750"/)
  assert.match(xml, /vMerge w:val="restart"/)
  assert.match(xml, /vMerge w:val="continue"/)
  assert.doesNotMatch(xml, /tblHeader/)
  assert.throws(()=>tableXml([['报价']],'<w:tblPr/>',layout), /行列已改变/)
  assert.throws(()=>parseTenderTableLayout('<!--TENDER_TABLE:!!!!-->'), /结构数据无效/)
})
