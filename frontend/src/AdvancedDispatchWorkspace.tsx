import { useEffect, useState } from 'react'
import { API_BASE, getAuthToken } from './auth'

type Event = { order_id: string; destination: string; units: number; weight_kg: number; volume_m3: number; trip: number; reload_before: boolean; arrival: string; service_start: string; depart: string }
type Result = { road_checks?: Array<{origin:string;destination:string;vehicle_id:string;restriction_code:number;usable:boolean}>; simulation: boolean; status: string; search_states: number; search_limit: number; objective: string; total_distance_m: number; notice: string; unassigned: Array<{order_id:string;unassigned_units:number}>; routes: Array<{vehicle_id:string;depot:string;trips:number;events:Event[];return_at:string;distance_m:number}> }
type Scenario = { vehicles: Array<{id:string;depot:string}>; orders: Array<{id:string}>; roads: Array<{ origin:string;destination:string;distance_m:number;duration_s:number;forbidden_vehicles:string[] }>; [key:string]: unknown }
function format(value: string) { return new Date(value).toLocaleString('zh-CN', { timeZone:'Asia/Shanghai', hour12:false }) }
async function request<T>(path:string, body?:unknown):Promise<T> {
  const token=getAuthToken();const headers=new Headers();if(token)headers.set('Authorization','Bearer '+token);if(body)headers.set('Content-Type','application/json')
  const response=await fetch(API_BASE+path,{method:body?'POST':'GET',headers,credentials:'include',body:body?JSON.stringify(body):undefined})
  const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail || value.message || '请求失败'))
  return value as T
}
export function AdvancedDispatchWorkspace({onBack}:{onBack:()=>void}) {
  const [input,setInput]=useState('')
  const [result,setResult]=useState<Result|null>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [truckInput,setTruckInput]=useState('')
  const [realConfig,setRealConfig]=useState('')
  const [truckResult,setTruckResult]=useState<{usable:boolean;restriction_code:number;distance_meters:number;duration_seconds:number;notice:string}|null>(null)
  async function loadDemo() {
    setBusy(true);setError('')
    try { const data=await request<{scenario:Scenario;result:Result}>('/maps/advanced-dispatch/demo');setInput(JSON.stringify(data.scenario,null,2));setResult(data.result) }
    catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}
  }
  useEffect(()=>{void loadDemo()},[])
  async function simulate(variation?:'congested'|'blocked') {
    setBusy(true);setError('');setResult(null)
    try {
      const scenario=JSON.parse(input) as Scenario
      if(variation==='congested') scenario.roads=scenario.roads.map(road=>({...road,duration_s:road.duration_s*3}))
      if(variation==='blocked') scenario.roads=scenario.roads.map(road=>({...road,forbidden_vehicles:scenario.vehicles.map(vehicle=>vehicle.id)}))
      if(variation)setInput(JSON.stringify(scenario,null,2))
      setResult(await request<Result>('/maps/advanced-dispatch/simulate',scenario))
    } catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}
  }
  async function replanSample(){
    setBusy(true);setError('');setResult(null)
    try {
      const scenario=JSON.parse(input) as Scenario
      if(!scenario.orders?.[0] || !scenario.vehicles?.[0]) throw new Error('样例需要至少一个订单和车辆')
      const payload={ scenario, now:'2026-10-31T00:20:00+08:00', completed_units:{[scenario.orders[0].id]:1},
        completed_trips:{[scenario.vehicles[0].id]:1}, vehicle_positions:Object.fromEntries(scenario.vehicles.map(v=>[v.id,v.depot])) }
      setResult(await request<Result>('/maps/advanced-dispatch/simulate-replan',payload))
    }catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}
  }
  async function realPlan(){
    setBusy(true);setError('');setResult(null)
    try {
      const scenario=JSON.parse(input) as Scenario
      const config=JSON.parse(realConfig) as {node_locations:unknown;trucks:unknown}
      setResult(await request<Result>('/maps/advanced-dispatch/truck-plan',{scenario:{...scenario,roads:[],use_vehicle_costs:false},node_locations:config.node_locations,trucks:config.trucks}))
    }catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}
  }
  async function checkTruck(){
    setBusy(true);setError('');setTruckResult(null)
    try {setTruckResult(await request('/maps/advanced-dispatch/truck-leg',JSON.parse(truckInput)))}
    catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}
  }
  return <main className="vehicle-route-page"><header className="top-nav"><strong>配送约束模拟实验室</strong><button type="button" className="light-btn" onClick={onBack}>返回配送规划</button></header>
    <div className="vehicle-route-content utility-workspace">
      <section className="module-header-card"><div><h1>拆单、多趟与跨日约束测试</h1><span>默认使用虚构样例；真实货车排线须填写实际订单、车辆和坐标并核实权限。</span></div></section>
      <section className="form-card"><p>测试按不可拆分的包装单元分配；车辆按固定设定温度匹配订单温度范围，每趟同时检查重量和体积，返回所属网点后才能装载下一趟。时间请填写带时区的日期时间，例如 2026-10-30T23:00:00+08:00。</p>
        <p>穷举搜索限 8 个拆分单元、4 辆车；最多搜索 5 秒或设定节点数。只有显示“模型内最优”时才表示已穷举这个简化模型，不代表真实业务全局最优。</p>
        <button className="light-btn" type="button" disabled={busy} onClick={() => void loadDemo()}>重载虚构样例</button>{' '}
        <button className="light-btn" type="button" disabled={busy || !input} onClick={() => void simulate('congested')}>模拟拥堵并重新计算</button>{' '}
        <button className="light-btn" type="button" disabled={busy || !input} onClick={() => void simulate('blocked')}>模拟车辆禁行并重新计算</button>{' '}<button className="light-btn" type="button" disabled={busy || !input} onClick={() => void replanSample()}>模拟完成一件并返仓后重排</button>
        <label style={{display:'block'}}>订单、车辆与道路输入（JSON；默认数据为虚构样例）<textarea style={{width:'100%',minHeight:360,fontFamily:'monospace'}} value={input} onChange={e=>{setInput(e.target.value);setResult(null)}} /></label>
        <button className="dark-btn" type="button" disabled={busy || !input} onClick={() => void simulate()}>{busy?'计算中…':'重新计算虚构场景'}</button>
        {error&&<div role="alert" className="graphic-error">{error}</div>}
      </section>
      {result&&<section className="form-card"><h2>{result.simulation ? '模拟结果' : '货车规划结果（当前路况估算）'}</h2><p>{result.notice}</p>{Boolean(result.road_checks?.some(check=>!check.usable)) && <div className="graphic-error">部分车型路段限行或状态未知，未纳入规划；请核查车辆和道路条件。</div>}<p>状态：{result.status==='optimal_for_model'?'模型内最优（已穷举）':result.status==='complete'?'已全部完成':result.status==='no_available_vehicles'?'无可用车辆':'搜索受限，仅为当前找到的方案'}{result.search_states !== undefined ? `；已搜索 ${result.search_states} 个状态` : ''}；距离 {(result.total_distance_m/1000).toFixed(1)} 公里。</p>
        {result.routes.map(route=><article key={route.vehicle_id}><h3>{route.vehicle_id} · {route.depot} · {route.trips} 趟</h3><ol>{route.events.map((e,i)=><li key={i}>第 {e.trip} 趟{e.reload_before?'（返回网点重新装载）':''}：{e.order_id} / {e.units} 件 / {e.weight_kg} kg / {e.volume_m3} m³；到达 {format(e.arrival)}，服务 {format(e.service_start)}，离开 {format(e.depart)}。</li>)}</ol><p>预计返回：{format(route.return_at)}；里程 {(route.distance_m/1000).toFixed(1)} 公里。</p></article>)}
        {result.unassigned.length>0&&<div className="graphic-error">未安排：{result.unassigned.map(x=>x.order_id+' '+x.unassigned_units+' 件').join('；')}</div>}
      </section>}
      <section className="form-card"><h2>货车约束排线（有限规模）</h2><p>使用上方订单及车辆参数和下方真实坐标、车辆信息，逐段调用高德货车接口。最多 4 个地点、2 辆车、24 次请求；可能收费。请先替换全部虚构样例。使用当前道路快照，不预测未来时段，不等于可实际通行。</p>
        <label>真实坐标与货车参数 JSON<textarea style={{width:'100%',minHeight:180,fontFamily:'monospace'}} value={realConfig} onChange={e=>{setRealConfig(e.target.value);setResult(null)}} placeholder={'{"node_locations":{"地点编号":[经度,纬度]},"trucks":{"车辆编号":{"size":2,"height_m":2.5,"width_m":2,"total_weight_t":4,"rated_load_t":1.5,"axles":2,"province":"皖","plate_number":"真实车牌后6或7位","cartype":0}}}'} /></label>
        <button type="button" className="light-btn" disabled={!realConfig.trim()||!input||busy} onClick={()=>void realPlan()}>查询货车道路并规划</button>
      </section>
      <section className="form-card"><h2>货车真实路段查询</h2><p>这是独立查询接口，需要已授权的高德货车服务。填写真实车型尺寸、总重、核定载重、轴数、车牌和坐标；查询可能产生服务费用。未知限行状态不视为可通行。当前查询只覆盖一个路段，尚未与上方模拟排线自动联动。</p>
        <label>货车查询参数 JSON<textarea style={{width:'100%',minHeight:150,fontFamily:'monospace'}} value={truckInput} onChange={e=>{setTruckInput(e.target.value);setTruckResult(null)}} placeholder={'{"origin":[经度,纬度],"destination":[经度,纬度],"size":2,"height_m":2.5,"width_m":2,"total_weight_t":4,"rated_load_t":1.5,"axles":2,"province":"皖","plate_number":"真实车牌后6或7位","cartype":0}'} /></label>
        <button type="button" className="light-btn" disabled={!truckInput.trim()||busy} onClick={()=>void checkTruck()}>查询货车路段</button>
        {truckResult&&<p>{truckResult.usable?'高德返回未限行或已规避限行':'未能确认可通行，请勿作为通行依据'}；限行状态码 {truckResult.restriction_code}；{(truckResult.distance_meters/1000).toFixed(1)} 公里，约 {Math.ceil(truckResult.duration_seconds/60)} 分钟。{truckResult.notice}</p>}
      </section>
      <section className="form-card"><h2>调度说明</h2><p>上面的拥堵和禁行按钮仅模拟道路数据变化并重新求解，并未接入 GPS、订单状态或实际交通流；示例重排会扣除已完成数量及已使用趟数，但只接受所有车辆已返仓且无在途货物的状态；车辆在途和已装货状态不能自动继承。真实实时调度仍需订单、车辆定位、车辆在途载货和道路事件数据源，不能凭模拟数据上线。</p></section>
    </div></main>
}
