import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'
const require=createRequire(import.meta.url)
const source=readFileSync(new URL('../src/docxExport.ts',import.meta.url),'utf8')+'\nexport {buildExportData};'
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText
const module={exports:{}}
new Function('require','module','exports',compiled)(name=>{
 if(name==='./auth')return {getAuthToken:()=>null}
 if(name==='./utils/bidTemplateCatalog')return {BID_TEMPLATE_CONFIGS:[],getBidTemplateById:id=>({id,filePath:''})}
 if(name==='./utils/customWordTemplateStore')return {}
 if(name==='./wordagentApi')return {wordagentApi:{
  getGraphicSection:async()=>({code:0,data:{markdown:'# 营业执照\n请附核实后的营业执照。',images:[],charts:[]}}),
  getBidParseDetail:async()=>({code:0,data:{qualification_requirements:[{title:'营业执照',criteria:'依法注册'}]}}),
 }}
 return require(name)
},module,module.exports)
const catalog={context:{project_overview:'',bid_title:'范围测试'},versions:[{version_id:1,is_final:true,catalog_content:[{chapter_id:1,title:'营业执照',children:[]}]}]}
test('standard and case templates do not auto-append generated evidence summary tables',async()=>{
 for(const id of ['default-business','custom:project-1']){
  const {data}=await module.exports.buildExportData(1,catalog,id,undefined,{includeImages:false})
  assert.ok(data.qualification_rows.length>0)
  assert.equal(data.has_qualification_rows,false)
  assert.equal(data.chapters.length,1)
 }
 const custom=await module.exports.buildExportData(1,catalog,'custom:user-authored',undefined,{includeImages:false})
 assert.equal(custom.data.has_qualification_rows,true)
})
