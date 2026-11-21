import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'
const source=readFileSync(new URL('../src/AdvancedDispatchWorkspace.tsx',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText
const require=createRequire(import.meta.url)
const nodes=node=>!node||typeof node!=='object'?[]:[node,...[node.props?.children].flat(Infinity).flatMap(nodes)]
const text=node=>typeof node==='string'||typeof node==='number'?String(node):!node||typeof node!=='object'?'':[node.props?.children].flat(Infinity).map(text).join('')
const tick=()=>new Promise(r=>setImmediate(r))
function harness(t){
 let index=0;const values=[];const hooks={useState(initial){const slot=index++;if(!(slot in values))values[slot]=initial;return [values[slot],v=>{values[slot]=typeof v==='function'?v(values[slot]):v}]},useEffect(){}}
 const scenario={vehicles:[{id:'自定义车',depot:'仓库'}],orders:[{id:'订单'}],roads:[{origin:'仓库',destination:'学校',distance_m:1000,duration_s:600,forbidden_vehicles:[]}]}
 const result={simulation:true,status:'optimal_for_model',search_states:4,search_limit:100,objective:'最多完成',total_distance_m:2000,notice:'仅模拟',unassigned:[],routes:[]}
 const calls=[];const original=globalThis.fetch;globalThis.fetch=async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>url.endsWith('/demo')?{scenario,result}:result}};t.after(()=>globalThis.fetch=original)
 const exports={};new Function('require','exports',compiled)(name=>name==='react'?hooks:name==='./auth'?{API_BASE:'/api',getAuthToken:()=>''}:require(name),exports)
 const render=()=>{index=0;return exports.AdvancedDispatchWorkspace({onBack(){}})};const button=label=>nodes(render()).find(n=>n.type==='button'&&text(n)===label)
 return {button,render,calls}
}
test('demo is explicitly simulation and unverified truck query is not run',async t=>{
 const h=harness(t);h.button('重载虚构样例').props.onClick();await tick();await tick()
 assert.match(text(h.render()),/默认使用虚构样例/);assert.match(text(h.render()),/模型内最优/);assert.equal(h.calls.length,1);assert.match(h.calls[0].url,/\/demo$/)
})
test('simulated road closure uses the actual scenario vehicle IDs',async t=>{
 const h=harness(t);h.button('重载虚构样例').props.onClick();await tick();await tick();h.button('模拟车辆禁行并重新计算').props.onClick();await tick();await tick()
 const call=h.calls.find(x=>x.url.endsWith('/simulate'));assert.ok(call);assert.deepEqual(JSON.parse(call.opts.body).roads[0].forbidden_vehicles,['自定义车'])
})


test('replan passes completed units, prior trips and depot positions',async t=>{
 const h=harness(t);h.button('重载虚构样例').props.onClick();await tick();await tick();h.button('模拟完成一件并返仓后重排').props.onClick();await tick();await tick()
 const call=h.calls.find(x=>x.url.endsWith('/simulate-replan'));assert.ok(call)
 const payload=JSON.parse(call.opts.body);assert.deepEqual(payload.completed_units,{'订单':1});assert.deepEqual(payload.completed_trips,{'自定义车':1});assert.deepEqual(payload.vehicle_positions,{'自定义车':'仓库'})
})


test('truck planning removes simulated roads and uses only the explicit real configuration',async t=>{
 const h=harness(t);h.button('重载虚构样例').props.onClick();await tick();await tick()
 const area=nodes(h.render()).find(n=>n.type==='textarea' && n.props.placeholder?.includes('node_locations'))
 area.props.onChange({target:{value:JSON.stringify({node_locations:{'仓库':[117,31]},trucks:{'自定义车':{size:2}}})}})
 h.button('查询货车道路并规划').props.onClick();await tick();await tick()
 const call=h.calls.find(x=>x.url.endsWith('/truck-plan'));assert.ok(call)
 const body=JSON.parse(call.opts.body);assert.deepEqual(body.scenario.roads,[]);assert.equal(body.scenario.use_vehicle_costs,false);assert.deepEqual(body.node_locations,{'仓库':[117,31]})
})
