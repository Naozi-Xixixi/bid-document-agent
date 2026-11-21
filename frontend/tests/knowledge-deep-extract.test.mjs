import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const text=readFileSync(new URL('../src/KnowledgeWorkspace.tsx',import.meta.url),'utf8')
const source=ts.createSourceFile('KnowledgeWorkspace.tsx',text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
let handler
function visit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='deepExtractItem')handler=node.getText(source);ts.forEachChild(node,visit)}
visit(source)
assert.ok(handler)
const code=ts.transpileModule(handler,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText
function fixture(api){
 let detail={item_id:6,title:'正在编辑的标题',tags:['企业资料'],assets:[{asset_id:42}],content_text:'已有正文'}
 let notices=[]
 const execute=new Function('setAssetActionBusy','setNotice','wordagentApi','setDetail','refresh','window','apiErrorMessage',code+';return deepExtractItem')(
 ()=>{},message=>notices.push(message),{deepExtractKnowledgeItem:api},updater=>{detail=typeof updater==='function'?updater(detail):updater},async()=>{}, {alert:message=>notices.push(message)},(_,fallback)=>fallback)
 return {execute,notices,get detail(){return detail},replace:value=>{detail=value}}
}
test('OCR status response preserves full detail and unsaved edits',async()=>{
 const f=fixture(async()=>({code:0,data:{item_id:6,deep_status:'processing',deep_progress:12}}))
 await f.execute(6)
 assert.deepEqual(f.detail.tags,['企业资料'])
 assert.equal(f.detail.title,'正在编辑的标题')
 assert.equal(f.detail.content_text,'已有正文')
 assert.equal(f.detail.assets[0].asset_id,42)
 assert.equal(f.detail.deep_progress,12)
})
test('late OCR response cannot replace another open document',async()=>{
 let resolve
 const f=fixture(()=>new Promise(done=>{resolve=done}))
 const pending=f.execute(6)
 f.replace({item_id:7,title:'另一个资料',tags:[]})
 resolve({code:0,data:{item_id:6,deep_status:'processing'}})
 await pending
 assert.equal(f.detail.item_id,7)
 assert.equal(f.detail.title,'另一个资料')
})

test('completed OCR displays server no-op message instead of claiming a restart',async()=>{
 const message='深度识别已完成，没有待补识别的图片，无需重复处理'
 const f=fixture(async()=>({code:0,data:{item_id:6,queued:false,deep_status:'completed',message}}))
 await f.execute(6)
 assert.equal(f.notices.at(-1),message)
 assert.ok(f.notices.every(n=>!n.includes('已启动')))
})
