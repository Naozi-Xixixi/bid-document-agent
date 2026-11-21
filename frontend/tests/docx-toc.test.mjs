import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import PizZip from 'pizzip'

const source = readFileSync(new URL('../src/docxExport.ts', import.meta.url), 'utf8')
const file = ts.createSourceFile('docxExport.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = new Set(['escapeXml', 'tocFieldXml', 'prepareProjectTemplate', 'applyRichTextAndTables', 'buildContentBlocks', 'normalizeInlineMarkdown', 'parseMarkdownTable', 'isSignatureField', 'createTextBlock', 'cleanChapterTitle'])
const declarations = file.statements
  .filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
  .map(node => node.getText(file))
assert.equal(declarations.length, names.size)
const compiled = ts.transpileModule(declarations.join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const { tocFieldXml, prepareProjectTemplate, applyRichTextAndTables, buildContentBlocks } = new Function(`${compiled}; return { tocFieldXml, prepareProjectTemplate, applyRichTextAndTables, buildContentBlocks };`)()

test('downloaded Word caches visible TOC entries until Word refreshes page numbers', () => {
  const xml = tocFieldXml([
    { number: '1', title: '项目理解', word_count: '0', status: '待生成' },
    { number: '1.2.1', title: '源头直采', word_count: '1200', status: '已生成' },
  ])
  assert.match(xml, /1 项目理解/)
  assert.match(xml, /1\.2\.1 源头直采/)
  assert.match(xml, /<w:ind w:left="720"\/>/)
  assert.match(xml, /<w:instrText[^>]*> TOC \\o &quot;1-3&quot;/)
  assert.equal((xml.match(/<w:fldChar w:fldCharType="begin"/g) || []).length, 1)
  assert.equal((xml.match(/<w:fldChar w:fldCharType="end"/g) || []).length, 1)
  assert.doesNotMatch(xml, /待生成|已生成|1200/)
})

test('case template replaces its static list with a Word TOC and assigns heading levels', () => {
  const zip = new PizZip()
  zip.file('word/document.xml', '<w:document><w:body>' +
    '<w:p><w:r><w:t>目录</w:t></w:r></w:p>' +
    '<w:p><w:r><w:t>{#chapter_rows}</w:t></w:r></w:p>' +
    '<w:p><w:r><w:t>{number} {title}</w:t></w:r></w:p>' +
    '<w:p><w:r><w:t>{/chapter_rows}</w:t></w:r></w:p>' +
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{chapter_heading}</w:t></w:r></w:p>' +
    '</w:body></w:document>')
  prepareProjectTemplate(zip)
  let xml = zip.file('word/document.xml').asText()
  assert.doesNotMatch(xml, /chapter_rows/)
  assert.match(xml, /\[\[BID_TOC_PLACEHOLDER\]\]/)
  xml = xml.replace('{chapter_outline_level}', '1').replace('{chapter_heading}', '1.1 项目方案')
  zip.file('word/document.xml', xml)
  applyRichTextAndTables(zip, { data: { include_toc: true, chapter_rows: [{ number: '1.1', title: '项目方案' }] }, tables: [] })
  xml = zip.file('word/document.xml').asText()
  assert.equal((xml.match(/<w:instrText[^>]*> TOC /g) || []).length, 1)
  assert.match(xml, /<w:outlineLvl w:val="1"\/>/)
  assert.match(xml, /<w:jc w:val="center"\/>/)
  assert.doesNotMatch(xml, /BID_(TOC|HEADING)/)
})

test('exported signature fields are flush left and only explicit emphasis becomes bold', () => {
  const blocks = buildContentBlocks([
    '请核对**证明材料**。',
    '**投标人名称（加盖电子签章）**：[待核实]',
    '法定代表人或其授权代表（签字或电子签章）：[待核实]',
    '日期：[待核实]',
  ].join('\n'), new Map(), new Map())
  assert.equal(blocks.length, 4)
  const zip = new PizZip()
  const xml = blocks.map(({body}) => '<w:p><w:pPr><w:ind w:left="420" w:firstLine="440"/><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:rFonts w:eastAsia="宋体"/><w:b w:val="0"/><w:sz w:val="22"/></w:rPr><w:t>' + body[0].text + '</w:t></w:r></w:p>').join('')
  zip.file('word/document.xml', '<w:document><w:body>' + xml + '</w:body></w:document>')
  applyRichTextAndTables(zip, { data: { include_toc: false }, tables: [] })
  const rendered = zip.file('word/document.xml').asText()
  assert.doesNotMatch(rendered, /\[\[BID_/)
  assert.equal((rendered.match(/w:firstLineChars="0"/g) || []).length, 3)
  assert.equal((rendered.match(/w:jc w:val="left"/g) || []).length, 3)
  assert.match(rendered, /w:firstLineChars="200"/)
  assert.match(rendered, /<w:b\/><w:bCs\/><w:sz w:val="22"\/><\/w:rPr><w:t[^>]*>证明材料<\/w:t>/)
  assert.match(rendered, /<w:b w:val="0"\/><w:sz w:val="22"\/><\/w:rPr><w:t[^>]*>请核对<\/w:t>/)
})


test('source forms flow after parent headings while keeping template page breaks', () => {
  const zip = new PizZip()
  zip.file('word/document.xml', '<w:document><w:body>' +
    '<w:p><w:pPr><w:pageBreakBefore/></w:pPr><w:r><w:t>投标文件正文</w:t></w:r></w:p>' +
    '<w:p><w:r><w:t>1 报价文件</w:t></w:r></w:p>' +
    '<w:p><w:r><w:t>1.1 报价表</w:t></w:r></w:p>' +
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>费率大写</w:t></w:r></w:p></w:tc></w:tr></w:tbl>' +
    '</w:body></w:document>')
  applyRichTextAndTables(zip, {
    data: {
      include_toc: false,
      chapters: [
        { chapter_heading: '1 报价文件' },
        { chapter_heading: '1.1 报价表', source_form: true },
      ],
    },
    tables: [],
  })
  const rendered = zip.file('word/document.xml').asText()
  assert.equal((rendered.match(/<w:pageBreakBefore\b/g) || []).length, 1)
  assert.match(rendered, /<w:pageBreakBefore\/><\/w:pPr><w:r><w:t>投标文件正文/)
  assert.match(rendered, /<w:p><w:r><w:t>1\.1 报价表<\/w:t><\/w:r><\/w:p><w:tbl>/)
})
