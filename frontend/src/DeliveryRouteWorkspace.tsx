import { Component, useEffect, useMemo, useRef, useState, type ErrorInfo, type FormEvent, type ReactNode } from 'react'
import { toPng } from 'html-to-image'
import { wordagentApi, type DeliveryRoutePlan, type DeliveryNetworkPoint, type TenderDemandPointCandidate } from './wordagentApi'

type Props = { onHome: () => void; onBack?: () => void; bidId?: number | null }
type DemandPoint = { id: string; name: string; address: string; demand_type: string; confidence?: number; source_excerpt?: string }

type RouteErrorBoundaryProps = { children: ReactNode; onHome: () => void }
type RouteErrorBoundaryState = { hasError: boolean }

class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): RouteErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[DeliveryRouteWorkspace]', error, info)
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return <main className="vehicle-route-page"><section className="form-card vehicle-route-error-boundary" role="alert">
      <h2>配送路线模块暂时无法显示</h2>
      <p>地图数据或路线结果出现异常，请返回后重新进入。已填写的配送网点仍会保存在系统中。</p>
      <button className="dark-btn" type="button" onClick={this.props.onHome}>返回首页</button>
    </section></main>
  }
}

declare global {
  interface Window {
    AMap?: any
    _AMapSecurityConfig?: { securityJsCode: string }
  }
}

const amapJsKey = import.meta.env.VITE_AMAP_JS_KEY?.trim() || ''
const amapSecurityCode = import.meta.env.VITE_AMAP_SECURITY_CODE?.trim() || ''
let amapLoader: Promise<any> | null = null

function loadAmap() {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (!amapJsKey) return Promise.reject(new Error('请在 frontend/.env.local 配置 VITE_AMAP_JS_KEY'))
  if (!amapLoader) {
    if (amapSecurityCode) window._AMapSecurityConfig = { securityJsCode: amapSecurityCode }
    amapLoader = new Promise((resolve, reject) => {
      const existing = document.querySelector<HTMLScriptElement>('script[data-amap-js="true"]')
      if (existing) {
        existing.addEventListener('load', () => window.AMap ? resolve(window.AMap) : reject(new Error('高德地图脚本加载失败')), { once: true })
        existing.addEventListener('error', () => reject(new Error('高德地图脚本加载失败')), { once: true })
        if (window.AMap) resolve(window.AMap)
        return
      }
      const script = document.createElement('script')
      script.dataset.amapJs = 'true'
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(amapJsKey)}&plugin=AMap.ToolBar`
      script.async = true
      script.onload = () => window.AMap ? resolve(window.AMap) : reject(new Error('高德地图脚本加载失败'))
      script.onerror = () => reject(new Error('高德地图脚本加载失败，请检查网络和 JS API Key'))
      document.head.appendChild(script)
    }).catch(error => { amapLoader = null; throw error })
  }
  return amapLoader
}

function routeError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error || '')
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown; message?: unknown }
    const detail = parsed.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      const detailObject = detail as { message?: unknown; detail?: unknown; address?: unknown }
      const message = detailObject.message || detailObject.detail
      if (typeof message === 'string') return message
      return JSON.stringify(detail)
    }
    return typeof parsed.message === 'string' ? parsed.message : raw
  } catch {
    return raw || '路线规划失败，请检查地址后重试。'
  }
}

function distanceText(meters: number) {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} 公里` : `${meters} 米`
}

function durationText(seconds: number) {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60), remaining = total % 60
  return `${hours ? `${hours} 小时 ` : ''}${minutes} 分 ${remaining} 秒`
}

function newDemand(index: number): DemandPoint {
  return { id: `demand-${Date.now()}-${index}`, name: '', address: '', demand_type: '学校' }
}

function stopDisplayName(stop: { name?: string; address?: string; formatted_address?: string }, index?: number) {
  const name = stop.name?.trim() || ''
  const isGenericName = /^招标服务点\s*\d*$/.test(name)
  if (name && !isGenericName) return name
  return stop.formatted_address?.trim() || stop.address?.trim() || (index === undefined ? '配送地点' : `配送地点 ${index + 1}`)
}

function DeliveryRouteWorkspaceContent({ onHome, onBack, bidId }: Props) {
  const mapElement = useRef<HTMLDivElement>(null)
  const mapInstance = useRef<any>(null)
  const [city, setCity] = useState('示例市')
  const [network, setNetwork] = useState<DeliveryNetworkPoint[]>([])
  const [demands, setDemands] = useState<DemandPoint[]>([newDemand(1)])
  const [startPointId, setStartPointId] = useState('')
  const [autoNearest, setAutoNearest] = useState(false)
  const [returnToStart, setReturnToStart] = useState(true)
  const [autoOrder, setAutoOrder] = useState(true)
  const [plan, setPlan] = useState<DeliveryRoutePlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadingNetwork, setLoadingNetwork] = useState(true)
  const [error, setError] = useState('')
  const [mapError, setMapError] = useState('')
  const [networkForm, setNetworkForm] = useState({ name: '', address: '', city: '', point_type: '配送网点' })
  const [editingPointId, setEditingPointId] = useState<number | null>(null)
  const [extractingDemand, setExtractingDemand] = useState(false)
  const [extractMessage, setExtractMessage] = useState('')

  const validDemands = useMemo(() => demands.filter(item => item.address.trim()), [demands])

  useEffect(() => {
    void wordagentApi.listVehicleNetworkPoints().then(result => {
      setNetwork(result.points)
      if (!startPointId && result.points[0]) setStartPointId(String(result.points[0].point_id))
    }).catch(reason => setError(routeError(reason))).finally(() => setLoadingNetwork(false))
  }, [])

  useEffect(() => {
    let cancelled = false
    if (!amapJsKey) { setMapError('地图显示需要配置 VITE_AMAP_JS_KEY。路线计算仍可由后端完成。'); return }
    loadAmap().then(AMap => {
      if (cancelled || !mapElement.current) return
      mapInstance.current = new AMap.Map(mapElement.current, { zoom: 10, center: [116.397428, 39.90923], resizeEnable: true })
      mapInstance.current.addControl(new AMap.ToolBar({ position: 'RB' }))
      setMapError('')
    }).catch(reason => { if (!cancelled) setMapError(reason instanceof Error ? reason.message : '高德地图加载失败') })
    return () => { cancelled = true; mapInstance.current?.destroy(); mapInstance.current = null }
  }, [])

  useEffect(() => {
    if (!plan || !mapInstance.current || !window.AMap) return
    try {
      const AMap = window.AMap
      const stops = Array.isArray(plan.stops) ? plan.stops : []
      const polyline = Array.isArray(plan.outbound_polyline) ? plan.outbound_polyline : Array.isArray(plan.polyline) ? plan.polyline : []
      const returnLine = Array.isArray(plan.return_polyline) ? plan.return_polyline : []
      if (!stops.length) {
        setMapError('路线已返回，但没有可显示的路线点。')
        return
      }
      mapInstance.current.clearMap()
      const markers = stops
        .filter(stop => Array.isArray(stop.location) && stop.location.length === 2)
        .map((stop, index) => new AMap.Marker({
          position: stop.location,
          label: { content: `<span class="vehicle-map-label">${stopDisplayName(stop, index)}</span>`, direction: 'top' },
          title: `${stopDisplayName(stop, index)} · ${stop.formatted_address || stop.address || ''}`,
        }))
      const overlays: any[] = [...markers]
      if (polyline.length > 1) overlays.push(new AMap.Polyline({ path: polyline, strokeColor: '#3b7f55', strokeWeight: 7, strokeOpacity: 1, isOutline: true, outlineColor: '#ffffff', borderWeight: 2, lineJoin: 'round', lineCap: 'round' }))
      if (returnLine.length > 1) overlays.push(new AMap.Polyline({ path: returnLine, strokeColor: '#1746a2', strokeWeight: 5, strokeOpacity: 1, isOutline: true, outlineColor: '#ffffff', borderWeight: 1, strokeStyle: 'dashed', strokeDasharray: [12, 8], lineJoin: 'round', lineCap: 'round', zIndex: 60 }))
      if (!overlays.length) {
        setMapError('路线点坐标无效，无法在地图上显示。')
        return
      }
      mapInstance.current.add(overlays)
      mapInstance.current.setFitView(overlays, false, [64, 64, 64, 64])
      setMapError('')
    } catch (reason) {
      setMapError(routeError(reason))
    }
  }, [plan])

  function updateDemand(id: string, patch: Partial<DemandPoint>) { setDemands(current => current.map(item => item.id === id ? { ...item, ...patch } : item)) }

  async function addNetworkPoint(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('')
    try {
      if (editingPointId !== null) {
        const result = await wordagentApi.updateVehicleNetworkPoint(editingPointId, networkForm)
        setNetwork(current => current.map(item => item.point_id === editingPointId ? result.point : item))
      } else {
        const result = await wordagentApi.createVehicleNetworkPoint(networkForm)
        setNetwork(current => [result.point, ...current]); setStartPointId(String(result.point.point_id))
      }
      setEditingPointId(null)
      setNetworkForm({ name: '', address: '', city: '', point_type: '配送网点' })
    } catch (reason) { setError(routeError(reason)) }
  }

  function editNetworkPoint(point: DeliveryNetworkPoint) {
    setEditingPointId(point.point_id)
    setNetworkForm({ name: point.name, address: point.address, city: point.city, point_type: point.point_type })
  }

  async function removeNetworkPoint(pointId: number) {
    try { await wordagentApi.deleteVehicleNetworkPoint(pointId); setNetwork(current => current.filter(item => item.point_id !== pointId)) } catch (reason) { setError(routeError(reason)) }
  }

  async function importTenderDemandPoints() {
    if (!bidId) return setError('请先完成招标文件解读，再导入学校或单位位置。')
    setExtractingDemand(true); setError(''); setExtractMessage('')
    try {
      const result = await wordagentApi.extractTenderDemandPoints(bidId)
      const imported = result.candidates.map((item: TenderDemandPointCandidate) => ({ id: item.point_id, name: item.name, address: item.address, demand_type: item.demand_type, confidence: item.confidence, source_excerpt: item.source_excerpt }))
      if (imported.length) setDemands(imported)
      setExtractMessage(result.message)
    } catch (reason) { setError(routeError(reason)) } finally { setExtractingDemand(false) }
  }

  async function calculateRoute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('')
    if (!network.length) return setError('请先添加至少一个示例集团配送网点。')
    if (!autoNearest && !startPointId) return setError('请选择配送起点。')
    if (autoNearest && validDemands.length !== 1) return setError('自动选择最近网点需要填写且仅填写一个需求点。')
    if (!validDemands.length) return setError('请至少填写一个招标学校或单位地址。')
    const invalidDemand = validDemands.find(item => !item.name.trim() || !item.address.trim())
    if (invalidDemand) return setError('请补全每个招标服务点的名称和详细地址。')
    setBusy(true)
    try {
      setPlan(await wordagentApi.planDeliveryRoute({
        company_name: '示例配送公司', city: city.trim() || '示例市', start_point_id: startPointId,
        network_points: network.map(item => ({ point_id: String(item.point_id), name: item.name, address: item.address, city: item.city, point_type: item.point_type })),
        demand_points: validDemands.map(item => ({ point_id: item.id, name: item.name.trim() || item.address.trim(), address: item.address.trim(), demand_type: item.demand_type })),
        return_to_start: returnToStart, auto_order: autoOrder, auto_nearest: autoNearest,
      }))
    } catch (reason) { setError(routeError(reason)) } finally { setBusy(false) }
  }

  async function downloadRouteImageLegacy() {
    if (!plan || !mapElement.current || !mapInstance.current) return
    try {
      // Export the same AMap DOM that the user sees. This keeps the base map,
      // road labels, route polyline, markers and zoom controls together.
      const dataUrl = await toPng(mapElement.current, {
        cacheBust: true,
        pixelRatio: 2,
        backgroundColor: '#f5f7f3',
      })
      const anchor = document.createElement('a')
      anchor.href = dataUrl
      anchor.download = '示例配送公司配送路线地图.png'
      anchor.click()
    } catch (reason) {
      setError(`地图图片导出失败：${routeError(reason)}。请等待地图底图加载完成后重试。`)
    }
  }

  async function downloadRouteImage() {
    if (!plan || !mapElement.current || !mapInstance.current || !window.AMap) return
    let imageUrl = ''
    try {
      const map = mapInstance.current
      const bounds = mapElement.current.getBoundingClientRect()
      const viewportWidth = Math.max(1, bounds.width)
      const viewportHeight = Math.max(1, bounds.height)
      const exportScale = Math.min(1, 1024 / viewportWidth, 1024 / viewportHeight)
      const width = Math.max(320, Math.round(viewportWidth * exportScale))
      const height = Math.max(240, Math.round(viewportHeight * exportScale))
      const centerValue = map.getCenter?.()
      const center: [number, number] = [
        Number(centerValue?.lng ?? centerValue?.getLng?.() ?? plan.stops[0]?.location?.[0]),
        Number(centerValue?.lat ?? centerValue?.getLat?.() ?? plan.stops[0]?.location?.[1]),
      ]
      const zoom = Math.max(3, Math.min(17, Math.floor(Number(map.getZoom?.() ?? 11) + Math.log2(exportScale))))
      const locations = plan.stops
        .filter(stop => Array.isArray(stop.location) && stop.location.length === 2)
        .map(stop => stop.location as [number, number])
      const staticMapBlob = await wordagentApi.getDeliveryRouteStaticMap({ center, zoom, locations, width, height })
      imageUrl = URL.createObjectURL(staticMapBlob)
      const image = new Image()
      image.src = imageUrl
      await image.decode()

      const pixelRatio = 2
      const canvas = document.createElement('canvas')
      canvas.width = width * pixelRatio
      canvas.height = height * pixelRatio
      const context = canvas.getContext('2d')
      if (!context) throw new Error('无法生成路线图图片')
      context.drawImage(image, 0, 0, canvas.width, canvas.height)
      context.scale(pixelRatio, pixelRatio)

      // Static-map zoom is integral; project onto its viewport, not the live map's
      // potentially fractional zoom, otherwise exported routes drift off the roads.
      const worldPixel = ([lng, lat]: [number, number]): [number, number] => {
        const size = 256 * 2 ** zoom
        const sine = Math.sin(Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI / 180)
        return [(lng + 180) / 360 * size, (0.5 - Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI)) * size]
      }
      const centerPixel = worldPixel(center)
      const project = (location: [number, number]): [number, number] => {
        const point = worldPixel(location)
        return [point[0] - centerPixel[0] + width / 2, point[1] - centerPixel[1] + height / 2]
      }
      const drawRoute = (points: [number, number][], color: string, dashed = false) => {
        const polyline = points.filter(item => Array.isArray(item) && item.length === 2)
        if (polyline.length < 2) return
        context.beginPath()
        polyline.forEach((location, index) => {
          const [x, y] = project(location)
          if (index === 0) context.moveTo(x, y)
          else context.lineTo(x, y)
        })
        context.setLineDash(dashed ? [12, 8] : [])
        context.lineJoin = 'round'
        context.lineCap = 'round'
        context.strokeStyle = '#ffffff'
        context.lineWidth = dashed ? 7 : 10
        context.stroke()
        context.lineJoin = 'round'
        context.lineCap = 'round'
        context.strokeStyle = color
        context.lineWidth = dashed ? 5 : 7
        context.stroke()
        context.setLineDash([])
      }
      drawRoute(plan.outbound_polyline ?? plan.polyline ?? [], '#3b7f55')
      drawRoute(plan.return_polyline ?? [], '#1746a2', true)

      if (plan.return_polyline?.length) {
        context.fillStyle = 'rgba(255,255,255,0.94)'
        context.fillRect(10, 10, 230, 38)
        context.font = '13px Microsoft YaHei, Arial, sans-serif'
        context.fillStyle = '#3b7f55'; context.fillText('━ 配送路线', 20, 34)
        context.fillStyle = '#1746a2'; context.fillText('┅ 返程路线', 128, 34)
      }

      const drawLabel = (text: string, x: number, y: number) => {
        const label = text || '配送地点'
        context.font = '14px Microsoft YaHei, Arial, sans-serif'
        const labelWidth = Math.min(width - 24, Math.max(96, context.measureText(label).width + 24))
        const labelX = Math.max(8, Math.min(width - labelWidth - 8, x + 18))
        const labelY = Math.max(28, Math.min(height - 12, y - 12))
        context.fillStyle = 'rgba(255,255,255,0.94)'
        context.strokeStyle = '#dfe8df'
        context.lineWidth = 1
        context.beginPath()
        context.roundRect(labelX, labelY - 22, labelWidth, 30, 6)
        context.fill()
        context.stroke()
        context.fillStyle = '#26352b'
        context.fillText(label, labelX + 12, labelY - 2)
      }

      plan.stops.forEach((stop, index) => {
        if (!Array.isArray(stop.location) || stop.location.length !== 2) return
        const [x, y] = project(stop.location as [number, number])
        const isStart = index === 0
        const isReturn = index === plan.stops.length - 1 && plan.return_to_start
        context.beginPath()
        context.arc(x, y, 13, 0, Math.PI * 2)
        context.fillStyle = isStart ? '#2c8551' : isReturn ? '#bd6534' : '#356ba9'
        context.fill()
        context.strokeStyle = '#ffffff'
        context.lineWidth = 3
        context.stroke()
        drawLabel(stopDisplayName(stop, index), x, y)
      })

      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(value => value ? resolve(value) : reject(new Error('无法编码路线图图片')), 'image/png')
      })
      const downloadUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = downloadUrl
      anchor.download = '示例配送公司配送地图路线.png'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 60_000)
    } catch (reason) {
      setError(`地图图片导出失败：${routeError(reason)}。请确认高德地图已加载完成后重试。`)
    } finally {
      if (imageUrl) URL.revokeObjectURL(imageUrl)
    }
  }

  return <main className="vehicle-route-page">
    <header className="module-topbar"><div className="module-topbar-title"><button className="admin-logo" type="button" onClick={onHome}>示例公司智能标书助手</button><span>示例配送配送路线图</span></div><div className="nav-actions">{onBack && <button className="light-btn" type="button" onClick={onBack}>返回配送规划</button>}<button className="light-btn" type="button" onClick={onHome}>返回首页</button></div></header>
    <div className="vehicle-route-content">
      <section className="module-header-card vehicle-route-heading"><div><p>招标配送方案</p><h1>示例配送公司配送路线图</h1><span>默认配送中心：示例市示例区示例路 1 号。先解读招标文件，再确认本次学校或单位位置。</span></div><span className="vehicle-provider-badge">高德地图</span></section>
      <div className="delivery-route-layout">
        <section className="form-card delivery-network-card"><div className="delivery-card-title"><div><strong>示例集团配送网点</strong><span>这里保存集团自有的配送中心和送菜网点，后续项目可重复使用。</span></div><b>{network.length} 个</b></div>
          <form className="delivery-network-form" onSubmit={event => void addNetworkPoint(event)}><input value={networkForm.name} onChange={event => setNetworkForm({ ...networkForm, name: event.target.value })} placeholder="网点名称" required /><input value={networkForm.address} onChange={event => setNetworkForm({ ...networkForm, address: event.target.value })} placeholder="网点详细地址" required /><input value={networkForm.city} onChange={event => setNetworkForm({ ...networkForm, city: event.target.value })} placeholder="城市" /><button className="light-btn" type="submit">{editingPointId !== null ? '保存修改' : '保存网点'}</button>{editingPointId !== null && <button className="delivery-cancel-edit" type="button" onClick={() => { setEditingPointId(null); setNetworkForm({ name: '', address: '', city: '', point_type: '配送网点' }) }}>取消</button>}</form>
          <div className="delivery-network-list">{loadingNetwork ? <span className="delivery-muted">正在加载网点…</span> : network.length ? network.map(item => <div className="delivery-network-item" key={item.point_id}><span className="delivery-point-dot">●</span><div><b>{item.name}</b><small>{item.city ? `${item.city} · ` : ''}{item.address}</small></div><button type="button" onClick={() => editNetworkPoint(item)} aria-label={`编辑${item.name}`}>编辑</button><button type="button" onClick={() => void removeNetworkPoint(item.point_id)} aria-label={`停用${item.name}`}>停用</button></div>) : <span className="delivery-muted">还没有保存网点，请先在上方添加。</span>}</div>
        </section>
        <form className="form-card delivery-demand-card" onSubmit={event => void calculateRoute(event)}><div className="delivery-card-title"><div><strong>当前招标配送需求</strong><span>填写学校、医院或其他供货单位的地址。地址确认后才会发送到高德地图。</span></div><b>{validDemands.length} 个需求点</b></div>
          <div className="delivery-tender-import"><button className="light-btn" type="button" onClick={() => void importTenderDemandPoints()} disabled={extractingDemand || !bidId}>{extractingDemand ? '正在读取招标解读…' : '从当前招标文件提取位置'}</button><span>{bidId ? '读取当前项目已完成的招标文件解读结果' : '请先进入招标文件解读流程'}</span></div>
          {extractMessage && <div className="delivery-extract-message">{extractMessage}</div>}
          <div className="delivery-route-options"><label>规划城市<input value={city} onChange={event => setCity(event.target.value)} placeholder="例如：杭州市" /></label><label>配送起点<select value={autoNearest ? "auto" : startPointId} onChange={event => { const value = event.target.value; setAutoNearest(value === "auto"); if (value !== "auto") setStartPointId(value); setPlan(null) }}><option value="auto">自动选择道路距离最近的网点（仅一个需求点）</option><option value="">选择自有网点</option>{network.map(item => <option key={item.point_id} value={item.point_id}>{item.name}</option>)}</select></label></div>
          <div className="delivery-demand-list">{demands.map((item, index) => <div className="delivery-demand-row" key={item.id}><div className="delivery-demand-item"><span>{index + 1}</span><input value={item.name} onChange={event => updateDemand(item.id, { name: event.target.value })} placeholder="学校/单位名称" required /><input value={item.address} onChange={event => updateDemand(item.id, { address: event.target.value })} placeholder="详细地址（请核对）" required /><select value={item.demand_type} onChange={event => updateDemand(item.id, { demand_type: event.target.value })}><option>学校</option><option>医院</option><option>机关单位</option><option>其他单位</option></select><button type="button" onClick={() => setDemands(current => current.filter(point => point.id !== item.id))} disabled={demands.length <= 1}>×</button></div>{item.source_excerpt && <small className="delivery-source-excerpt">招标文件原文：{item.source_excerpt}{item.confidence !== undefined && item.confidence < .8 ? ' · 地址未能从原文确认，请手动补全' : ''}</small>}</div>)}</div>
          <div className="delivery-demand-actions"><button className="light-btn" type="button" onClick={() => setDemands(current => [...current, newDemand(current.length + 1)])} disabled={demands.length >= 14}>＋ 添加招标单位</button><label><input type="checkbox" checked={autoOrder} onChange={event => setAutoOrder(event.target.checked)} />按距离自动排序</label><label><input type="checkbox" checked={returnToStart} onChange={event => setReturnToStart(event.target.checked)} />返回配送起点</label></div>
          <button className="dark-btn vehicle-calculate-btn" type="submit" disabled={busy}>{busy ? '正在规划路线…' : '生成配送路线图'}</button>{error && <div className="vehicle-route-error" role="alert">{error}</div>}<p className="vehicle-route-note">路线依据当前自有网点和招标需求点生成。正式投标时还应结合车辆容量、配送时段和实际道路情况复核。</p>
        </form>
      </div>
      <section className="form-card vehicle-map-panel delivery-result-panel"><div className="vehicle-map-summary"><div><strong>路线图预览</strong><span>{plan ? `${plan.stops.length} 个路线点 · ${plan.auto_nearest ? "已按道路距离选择最近网点" : "使用指定配送起点"}` : '生成后显示地图和路线图'}</span></div>{plan && <div className="vehicle-route-metrics"><strong>{distanceText(plan.distance_meters)}</strong><span>约 {durationText(plan.duration_seconds)}</span><button className="light-btn" type="button" onClick={() => void downloadRouteImage()}>下载地图路线图 PNG</button></div>}</div><div className={`vehicle-map-canvas ${amapJsKey ? '' : 'is-placeholder'}`} ref={mapElement}>{!amapJsKey && <div className="vehicle-map-empty"><span className="vehicle-map-pin">⌖</span><strong>{mapError}</strong><small>路线计算仍可使用，配置 JS Key 后显示交互地图。</small></div>}</div>{plan && <div className="delivery-route-legend" aria-label="路线图例"><span><i className="delivery-legend-outbound" />配送路线</span>{Boolean(plan.return_polyline?.length) && <span><i className="delivery-legend-return" />返程路线</span>}</div>}{plan && <div className="delivery-result-grid"><div><strong>本次配送起点</strong>{plan.assignments.map(item => <div className="delivery-assignment" key={item.demand_point_id}><span>{item.demand_name}</span><b>→ {item.network_point_name}</b></div>)}</div><div><strong>配送顺序</strong><ol className="delivery-order-list">{plan.stops.map((item, index) => <li key={`${item.name}-${index}`}><span>{index === 0 ? '起点' : index === plan.stops.length - 1 && plan.return_to_start ? '返回' : index}</span><b>{stopDisplayName(item, index)}</b></li>)}</ol></div></div>}{mapError && amapJsKey && <p className="vehicle-map-warning">{mapError}</p>}</section>
    </div>
  </main>
}

export function DeliveryRouteWorkspace(props: Props) {
  return <RouteErrorBoundary onHome={props.onHome}><DeliveryRouteWorkspaceContent {...props} /></RouteErrorBoundary>
}
