import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'
const source=readFileSync(new URL('../src/MultiDepotWorkspace.tsx',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText
const require=createRequire(import.meta.url)
const nodes=node=>!node||typeof node!=='object'?[]:[node,...[node.props?.children].flat(Infinity).flatMap(nodes)]
const text=node=>typeof node==='string'||typeof node==='number'?String(node):!node||typeof node!=='object'?'':[node.props?.children].flat(Infinity).map(text).join('')
const tick=()=>new Promise(r=>setImmediate(r))
function harness(t){
  const values=[];let index=0;const hooks={useState(initial){const slot=index++;if(!(slot in values))values[slot]=typeof initial==='function'?initial():initial;return [values[slot],v=>{values[slot]=typeof v==='function'?v(values[slot]):v}]},useEffect(){}}
  const demo={simulated:true,notice:'模拟数据与直线估算，仅用于测试，不能用于实际配送或投标。',complete:false,routes:[],unassigned:[{demand_point_id:'d5',name:'虚构超载测试点',reason:'载重不足'}],assignments:[],distance_meters:0,route_svg:'<svg xmlns="http://www.w3.org/2000/svg"/>',return_to_start:true}
  const previousFetch=globalThis.fetch;let calls=[];globalThis.fetch=async url=>{calls.push(url);return {ok:true,json:async()=>demo}};t.after(()=>{globalThis.fetch=previousFetch})
  const exports={};new Function('require','exports',compiled)(name=>name==='react'?hooks:name==='./auth'?{API_BASE:'/api',getAuthToken:()=>''}:name==='./wordagentApi'?{wordagentApi:{}}:name==='./DeliveryRouteWorkspace'?{DeliveryRouteWorkspace:()=>null}:name==='./AdvancedDispatchWorkspace'?{AdvancedDispatchWorkspace:()=>null}:require(name),exports)
  const render=()=>{index=0;return exports.MultiDepotWorkspace({onHome(){}})};const button=name=>nodes(render()).find(n=>n.type==='button'&&text(n)===name)
  return {render,button,calls}
}
test('fictional demo visibly identifies unassigned demand and cannot be saved as a real plan',async t=>{
  const h=harness(t);h.button('运行虚构网点测试').props.onClick();await tick();await tick()
  assert.equal(h.calls.length,1);assert.match(h.calls[0],/\/multi-depot-route\/demo$/)
  const view=text(h.render());assert.match(view,/虚构网点测试结果/);assert.match(view,/虚构超载测试点/);assert.match(view,/不能用于实际配送或投标/)
  assert.equal(h.button('保存路线方案'),undefined)
})
test('real planning without a network reports missing input without contacting the provider',async t=>{
  const h=harness(t);nodes(h.render()).find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await tick()
  assert.match(text(h.render()),/请先维护并确认真实配送网点/);assert.equal(h.calls.length,0)
})
