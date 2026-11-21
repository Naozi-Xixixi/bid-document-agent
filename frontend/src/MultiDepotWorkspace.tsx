import { useEffect, useState, type FormEvent } from 'react'
import { API_BASE, getAuthToken } from './auth'
import { wordagentApi, type DeliveryNetworkPoint } from './wordagentApi'
import { DeliveryRouteWorkspace } from './DeliveryRouteWorkspace'

type Props = { onHome: () => void; bidId?: number | null }
type Demand = { id: string; name: string; address: string; weight: string; start: string; end: string; service: string; source?: string }
type Vehicle = { id: string; name: string; networkId: string; capacity: string; departure: string }
type PlannedStop = { point_id: string; name: string; address: string; formatted_address?: string; location: number[]; weight_kg: number; window_start: string; window_end: string; service_minutes: number; arrival_time: string; service_time: string; departure_time: string; wait_minutes: number }
type Route = { origin: { address: string; formatted_address?: string; location: number[] }; vehicle_id: string; vehicle_name: string; network_point_name: string; capacity_kg: number; load_kg: number; departure_time: string; finish_time: string; distance_meters: number; travel_seconds: number; stops: PlannedStop[] }
type StoredInputs = { city: string; return_to_start: boolean; vehicles: Array<{ vehicle_id: string; name: string; network_point_id: string; capacity_kg: number; departure_time: string }>; demand_points: Array<{ point_id: string; name: string; address: string; weight_kg: number; window_start: string; window_end: string; service_minutes: number }> }
type SavedPlan = { id: string; title: string; created_by: string; created_at: string }
type Plan = { simulated: boolean; notice: string; complete: boolean; routes: Route[]; unassigned: Array<{ demand_point_id: string; name: string; reason: string }>; assignments: Array<{ demand_point_id: string; demand_name: string; network_point_name: string; vehicle_name: string }>; distance_meters: number; route_svg: string; return_to_start: boolean }
let localId = 0
const nextId = () => globalThis.crypto?.randomUUID?.() || `route-${Date.now()}-${++localId}`
const blankDemand = (): Demand => ({ id: nextId(), name: '', address: '', weight: '', start: '', end: '', service: '' })
const blankVehicle = (networkId = ''): Vehicle => ({ id: nextId(), name: '', networkId, capacity: '', departure: '' })
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error) }
async function api<T>(path: string, payload?: unknown): Promise<T> {
  const token = getAuthToken()
  const headers = new Headers(); if (token) headers.set('Authorization', 'Bearer ' + token)
  if (payload) headers.set('Content-Type', 'application/json')
  const response = await fetch(API_BASE + path, { method: payload ? 'POST' : 'GET', headers, credentials: 'include', body: payload ? JSON.stringify(payload) : undefined })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || data.message || '路线规划失败')
  return data as T
}
async function savePng(plan: Plan) {
  const url = URL.createObjectURL(new Blob([plan.route_svg], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const image = new Image(); image.src = url; await image.decode()
    const canvas = document.createElement('canvas'); canvas.width = 2400; canvas.height = 1440
    const context = canvas.getContext('2d'); if (!context) throw new Error('无法创建图片')
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('图片生成失败')), 'image/png'))
    const pngUrl = URL.createObjectURL(png); const link = document.createElement('a'); link.href = pngUrl
    link.download = plan.simulated ? '虚构测试-配送路线示意图.png' : '多网点配送路线示意图.png'; link.click()
    window.setTimeout(() => URL.revokeObjectURL(pngUrl), 5000)
  } finally { URL.revokeObjectURL(url) }
}

function saveCsv(plan: Plan) {
  const csv = (value: unknown) => {
    let text = String(value ?? '')
    if (/^[\s]*[=+@-]/.test(text)) text = "'" + text
    return '"' + text.replace(/"/g, '""') + '"'
  }
  const rows: unknown[][] = [['数据性质','网点','车辆','需求点','地址','重量(kg)','最早送达','最晚送达','预计到达','开始服务','离开','状态']]
  for (const route of plan.routes) for (const stop of route.stops) rows.push([plan.simulated ? '虚构测试，禁止用于实际配送' : '道路时间估算，需人工复核',route.network_point_name,route.vehicle_name,stop.name,stop.address,stop.weight_kg,stop.window_start,stop.window_end,stop.arrival_time,stop.service_time,stop.departure_time,'已安排'])
  for (const stop of plan.unassigned) rows.push([plan.simulated ? '虚构测试，禁止用于实际配送' : '道路时间估算，需人工复核','','',stop.name,'','','','','','','','未安排：' + stop.reason])
  const blob = new Blob(['\ufeff' + rows.map(row => row.map(csv).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url
  link.download = plan.simulated ? '虚构测试-配送明细.csv' : '配送路线明细.csv'; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export function MultiDepotWorkspace({ onHome, bidId }: Props) {
  const [legacy, setLegacy] = useState(false)
  const [network, setNetwork] = useState<DeliveryNetworkPoint[]>([])
  const [city, setCity] = useState('示例市')
  const [demands, setDemands] = useState<Demand[]>(() => [blankDemand()])
  const [vehicles, setVehicles] = useState<Vehicle[]>(() => [blankVehicle()])
  const [returnToStart, setReturnToStart] = useState(true)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [planInputs, setPlanInputs] = useState<Record<string, unknown> | null>(null)
  const [savedPlans, setSavedPlans] = useState<SavedPlan[]>([])
  const [selectedSaved, setSelectedSaved] = useState('')
  const [loadedTitle, setLoadedTitle] = useState('')
  const [planTitle, setPlanTitle] = useState('配送路线方案')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { void wordagentApi.listVehicleNetworkPoints().then(result => setNetwork(result.points)).catch(e => setError(errorText(e))); void api<{ plans: SavedPlan[] }>('/maps/multi-depot-route/plans').then(result => setSavedPlans(result.plans)).catch(e => setError(errorText(e))) }, [])
  if (legacy) return <DeliveryRouteWorkspace onHome={onHome} onBack={() => setLegacy(false)} bidId={bidId} />
  function updateDemand(id: string, value: Partial<Demand>) { setPlan(null); setDemands(old => old.map(item => item.id === id ? { ...item, ...value } : item)) }
  function updateVehicle(id: string, value: Partial<Vehicle>) { setPlan(null); setVehicles(old => old.map(item => item.id === id ? { ...item, ...value } : item)) }
  async function importTender() {
    if (!bidId) return setError('请先完成招标文件解读。')
    setBusy(true); setError('')
    try {
      const result = await wordagentApi.extractTenderDemandPoints(bidId)
      if (!result.candidates.length) return setError(result.message)
      if (!window.confirm('将替换当前需求点，请先保存需要保留的输入。继续导入？')) return
      setDemands(result.candidates.slice(0,14).map(item => ({ ...blankDemand(), id: item.point_id, name: item.name, address: item.address, source: item.source_excerpt })))
      setPlan(null); setPlanInputs(null); setLoadedTitle('')
      if (result.candidates.length > 14) setError('本次仅导入前 14 个候选点，请分批规划并核对原文。')
    } catch(e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  async function calculate(event: FormEvent) {
    event.preventDefault(); setError(''); setPlan(null); setPlanInputs(null); setLoadedTitle('')
    if (!network.length) return setError('请先维护并确认真实配送网点。')
    if (vehicles.some(v => !v.networkId || !network.some(n => String(n.point_id) === v.networkId) || !v.name.trim() || !v.capacity || Number(v.capacity) <= 0 || !v.departure)) return setError('请填写每辆车的名称、有效的所属网点、载重和发车时间。')
    if (demands.some(d => !d.name.trim() || !d.address.trim() || !d.weight || Number(d.weight) <= 0 || !d.start || !d.end || d.start > d.end || d.service === '' || Number(d.service) < 0)) return setError('请填写每个需求点的名称、地址、重量、同日送达时间窗和卸货时间。')
    setBusy(true)
    try {
      const payload = { city: city.trim(), network_points: network.filter(n => vehicles.some(v => v.networkId === String(n.point_id))).map(n => ({ point_id: String(n.point_id), name: n.name, address: n.address, city: n.city })),
        vehicles: vehicles.map(v => ({ vehicle_id: v.id, name: v.name.trim(), network_point_id: v.networkId, capacity_kg: Number(v.capacity), departure_time: v.departure })),
        demand_points: demands.map(d => ({ point_id: d.id, name: d.name.trim(), address: d.address.trim(), weight_kg: Number(d.weight), window_start: d.start, window_end: d.end, service_minutes: Number(d.service) })), return_to_start: returnToStart }
      const result = await api<Plan>('/maps/multi-depot-route', payload)
      setPlanInputs(payload); setPlan(result)
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  async function savePlan() {
    if (!plan || plan.simulated || !planInputs || !planTitle.trim()) return
    setBusy(true); setError('')
    try {
      await api('/maps/multi-depot-route/plans', { title: planTitle.trim(), inputs: planInputs, result: plan })
      setSavedPlans((await api<{ plans: SavedPlan[] }>('/maps/multi-depot-route/plans')).plans)
      setLoadedTitle('已保存：' + planTitle.trim())
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  async function loadPlan() {
    if (!selectedSaved) return
    if (!window.confirm('载入历史方案会替换当前未保存的规划输入，是否继续？')) return
    setBusy(true); setError('')
    try {
      const saved = await api<{ title: string; inputs: StoredInputs; result: Plan }>('/maps/multi-depot-route/plans/' + selectedSaved)
      setCity(saved.inputs.city); setReturnToStart(saved.inputs.return_to_start)
      setVehicles(saved.inputs.vehicles.map(v => ({ id: v.vehicle_id, name: v.name, networkId: v.network_point_id, capacity: String(v.capacity_kg), departure: v.departure_time })))
      setDemands(saved.inputs.demand_points.map(d => ({ id: d.point_id, name: d.name, address: d.address, weight: String(d.weight_kg), start: d.window_start, end: d.window_end, service: String(d.service_minutes) })))
      setPlan(saved.result); setPlanInputs(null); setLoadedTitle('历史快照：' + saved.title + '。已载入原输入，网点资料以当前网点库为准；请重新规划后再用于当前配送。')
    } catch(e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <main className="vehicle-route-page">
    <header className="top-nav"><button className="logo-button" onClick={onHome}>示例公司智能标书助手</button><button className="light-btn" onClick={onHome}>返回首页</button></header>
    <div className="vehicle-route-content utility-workspace multi-depot-workspace">
      <section className="module-header-card"><div><p>多网点配送规划</p><h1>车辆与时间窗路线规划</h1><span>根据各网点车辆、载重和送达时间窗规划；须先核实真实网点和需求数据。</span></div></section>
      <section className="form-card"><h2>配送网点</h2><p>当前保存的网点：{network.length ? network.map(x => x.name + '（' + x.address + '）').join('；') : '暂无网点'}。请先确认所有网点资料真实有效。</p>
        <button type="button" className="light-btn" onClick={() => setLegacy(true)}>维护网点 / 使用简单路线规划</button>
      </section>
      <section className="form-card"><h2>已保存的路线方案</h2><select aria-label="选择历史路线" value={selectedSaved} onChange={e => setSelectedSaved(e.target.value)}><option value="">选择历史方案</option>{savedPlans.map(saved => <option key={saved.id} value={saved.id}>{saved.title} · {saved.created_by} · {new Date(saved.created_at).toLocaleString('zh-CN')}</option>)}</select>{' '}<button type="button" className="light-btn" disabled={!selectedSaved || busy} onClick={() => void loadPlan()}>查看历史方案</button></section>
      <form className="form-card" onSubmit={event => void calculate(event)}>
        <fieldset disabled={busy}>
        <h2>真实规划输入</h2><p className="multi-route-intro">按顺序填写车辆、网点和配送需求，再生成路线。</p><label className="multi-route-city">规划城市 <input value={city} onChange={e => { setCity(e.target.value); setPlan(null) }} placeholder="示例市" /></label>
        <h3>车辆与发车网点</h3>
        {vehicles.map((v, i) => <div className="multi-route-row" key={v.id}>
          <label>车辆 {i + 1}<input aria-label={'车辆名称 ' + (i+1)} value={v.name} onChange={e => updateVehicle(v.id,{name:e.target.value})} placeholder="车辆名称" /></label>
          <label>所属网点<select aria-label={'发车网点 ' + (i+1)} value={v.networkId} onChange={e => updateVehicle(v.id,{networkId:e.target.value})}><option value="">请选择</option>{network.map(n => <option value={String(n.point_id)} key={n.point_id}>{n.name}</option>)}</select></label>
          <label>载重（公斤）<input type="number" min="0.01" step="0.01" value={v.capacity} onChange={e => updateVehicle(v.id,{capacity:e.target.value})} /></label>
          <label>发车时间<input type="time" value={v.departure} onChange={e => updateVehicle(v.id,{departure:e.target.value})} /></label>
          <button className="multi-route-remove" type="button" disabled={vehicles.length <= 1} onClick={() => { setVehicles(old => old.filter(x => x.id !== v.id)); setPlan(null) }}>删除</button>
        </div>)}
        <button className="light-btn" type="button" disabled={vehicles.length >= 10} onClick={() => { setVehicles(old => [...old,blankVehicle()]); setPlan(null) }}>添加车辆</button>
        <h3>配送需求与送达时间窗</h3><button className="light-btn" type="button" disabled={!bidId} onClick={() => void importTender()}>从当前招标文件导入位置候选</button><p>候选名称和地址必须核对原文；重量、时间窗、卸货时间需另行填写。</p>
        {demands.map((d,i) => <div className="multi-route-row" key={d.id}>
          <label>需求点 {i+1}<input aria-label={'需求点名称 '+(i+1)} value={d.name} onChange={e => updateDemand(d.id,{name:e.target.value})} placeholder="学校/单位名称" /></label>
          <label>地址<input aria-label={'需求点地址 '+(i+1)} value={d.address} onChange={e => updateDemand(d.id,{address:e.target.value})} placeholder="确认后的详细地址" /></label>
          <label>重量（公斤）<input type="number" min="0.01" step="0.01" value={d.weight} onChange={e => updateDemand(d.id,{weight:e.target.value})} /></label>
          <label>最早送达<input type="time" value={d.start} onChange={e => updateDemand(d.id,{start:e.target.value})} /></label>
          <label>最晚送达<input type="time" value={d.end} onChange={e => updateDemand(d.id,{end:e.target.value})} /></label>
          <label>卸货（分钟）<input type="number" min="0" max="1440" step="1" value={d.service} onChange={e => updateDemand(d.id,{service:e.target.value})} /></label>
          {d.source && <small>招标原文：{d.source}</small>}
          <button className="multi-route-remove" type="button" disabled={demands.length <= 1} onClick={() => { setDemands(old => old.filter(x => x.id !== d.id)); setPlan(null) }}>删除</button>
        </div>)}
        <button className="light-btn" type="button" disabled={demands.length >= 14} onClick={() => { setDemands(old => [...old,blankDemand()]); setPlan(null) }}>添加需求点</button>{' '}
        <label className="multi-route-return"><input type="checkbox" checked={returnToStart} onChange={e => { setReturnToStart(e.target.checked); setPlan(null) }} />车辆配送后返回各自发车网点</label>
        <p>单车只运行一趟，不拆分同一需求点；只使用已配置车辆的网点。时间窗按当天本地时间，请填写实际重量和时段，并复核道路情况。</p>
        <button className="dark-btn" disabled={busy} type="submit">{busy ? '逐段计算道路路线…' : '规划多网点路线'}</button>
        {error && <div role="alert" className="vehicle-route-error">{error}</div>}
        </fieldset>
      </form>
      {plan && <section className="form-card">{loadedTitle && <div className="invoice-notice">{loadedTitle}</div>}<h2>{plan.simulated ? '虚构网点测试结果' : '规划结果'}</h2><div className="invoice-notice">{plan.notice}</div>
        {!plan.complete && <div className="graphic-error">有 {plan.unassigned.length} 个需求点未安排；不能将本结果当作完整配送方案。</div>}
        <p>已安排 {plan.assignments.length} 点，使用 {plan.routes.length} 辆车，{plan.simulated ? '估算里程' : '道路里程'}合计 {(plan.distance_meters/1000).toFixed(1)} 公里。</p>
        {plan.route_svg && <><img className="multi-route-diagram" src={'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(plan.route_svg)} alt={plan.simulated ? '虚构测试配送路线示意图' : '多网点配送路线示意图'} /><button className="light-btn" type="button" onClick={() => void savePng(plan).catch(e => setError(errorText(e)))}>下载路线示意图 PNG</button>{' '}<button className="light-btn" type="button" onClick={() => saveCsv(plan)}>下载配送明细 CSV</button></>}
        {!plan.simulated && planInputs && <div><label>方案名称 <input value={planTitle} onChange={e => setPlanTitle(e.target.value)} /></label>{' '}<button type="button" className="light-btn" disabled={busy || !planTitle.trim()} onClick={() => void savePlan()}>保存路线方案</button></div>}
        {plan.routes.map(route => <article className="multi-route-result" key={route.vehicle_id}><h3>{route.network_point_name} → {route.vehicle_name}</h3><p>发车网点定位：{route.origin.formatted_address || route.origin.address}（{route.origin.location.join(', ')}）</p><p>发车 {route.departure_time}；{plan.return_to_start ? '返回网点' : '配送完成'} {route.finish_time}；载重 {route.load_kg} / {route.capacity_kg} 公斤；里程 {(route.distance_meters/1000).toFixed(1)} 公里；行驶约 {Math.ceil(route.travel_seconds/60)} 分钟。</p><ol>{route.stops.map(stop => <li key={stop.point_id}><b>{stop.name}</b>（输入地址：{stop.address}；高德定位：{stop.formatted_address || stop.address}；坐标：{stop.location.join(', ')}）：{stop.weight_kg} 公斤；送达窗 {stop.window_start}–{stop.window_end}；预计到达 {stop.arrival_time}，开始服务 {stop.service_time}，等待 {stop.wait_minutes} 分钟，离开 {stop.departure_time}。</li>)}</ol></article>)}
        {Boolean(plan.unassigned.length) && <><h3>未安排需求点</h3><ul>{plan.unassigned.map(item => <li key={item.demand_point_id}>{item.name}：{item.reason}</li>)}</ul></>}
      </section>}
    </div>
  </main>
}
