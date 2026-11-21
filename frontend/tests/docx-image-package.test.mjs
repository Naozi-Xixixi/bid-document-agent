import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import PizZip from 'pizzip'
import Docxtemplater from 'docxtemplater'
import ImageModule from 'docxtemplater-image-module-free'
const source = readFileSync(new URL('../src/docxExport.ts', import.meta.url), 'utf8')
const file = ts.createSourceFile('docxExport.ts', source, ts.ScriptTarget.Latest, true)
const declaration = file.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'normalizePackageNamespaces')
const compiled = ts.transpileModule(declaration.getText(file), {compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
const normalize = new Function(compiled + ';return normalizePackageNamespaces;')()
const template = readFileSync(new URL('../src/assets/bid-templates/standard-public.docx', import.meta.url))
const ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
function sample() {
 const zip = new PizZip(template)
 zip.file('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{%image}</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
 zip.file('word/_rels/document.xml.rels','<ns0:Relationships xmlns:ns0="'+ns+'"><ns0:Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></ns0:Relationships>')
 zip.file('[Content_Types].xml',zip.file('[Content_Types].xml').asText().replace(/xmlns=/,'xmlns:pkg=').replace(/<(\/?)(Types|Default|Override)\b/g,'<$1pkg:$2'))
 return zip
}
function render(zip) {
 const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jC1kAAAAASUVORK5CYII=','base64')
 const doc = new Docxtemplater(zip,{modules:[new ImageModule({getImage:()=>image,getSize:()=>[20,20]})]})
 doc.render({image:'test'});return doc.getZip()
}
test('images render in DOCX packages with prefixed relationships and content types',()=>{
 assert.throws(()=>render(sample()))
 const zip=sample();normalize(zip);const result=render(zip)
 const rels=result.file('word/_rels/document.xml.rels').asText()
 assert.match(rels,/Id="rId7"/);assert.match(rels,/Target="styles.xml"/)
 assert.match(rels,/Id="rId8"/);assert.match(rels,/relationships\/image/)
 assert.ok(Object.keys(result.files).some(n=>n.startsWith('word/media/') && /\.png$/.test(n)))
})
test('normal package XML is unchanged and normalization is idempotent',()=>{
 const zip=new PizZip(template);const original=zip.file('word/_rels/document.xml.rels').asText()
 normalize(zip);assert.equal(zip.file('word/_rels/document.xml.rels').asText(),original)
 const prefixed=sample();normalize(prefixed);const once=prefixed.file('word/_rels/document.xml.rels').asText();normalize(prefixed)
 assert.equal(prefixed.file('word/_rels/document.xml.rels').asText(),once)
})
