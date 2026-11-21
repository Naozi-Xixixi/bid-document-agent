import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { wordagentApi, type VehicleRoutePlan } from './wordagentApi'

type Props = { onHome: () => void }
type RoutePoint = { id: number; name: string; address: string }

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
        return
      }
      const script = document.createElement('script')
      script.dataset.amapJs = 'true'
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(amapJsKey)}&plugin=AMap.ToolBar`
      script.async = true
      script.onload = () => window.AMap ? resolve(window.AMap) : reject(new Error('高德地图脚本加载失败'))
      script.onerror = () => reject(new Error('高德地图脚本加载失败，请检查网络和 JS API Key'))
      document.head.appendChild(script)
    }).catch(error => {
      amapLoader = null
      throw error
    })
  }
  return amapLoader
}

function routeError(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error || '')
  try {
    const parsed = JSON.parse(raw) as { detail?: string; message?: string }
    return parsed.detail || parsed.message || raw
  } catch {
    return raw || '路线规划失败，请检查地址后重试。'
  }
}

function formatDistance(meters: number) {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} 公里` : `${meters} 米`
}

function formatDuration(seconds: number) {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60), remaining = total % 60
  return `${hours ? `${hours} 小时 ` : ''}${minutes} 分 ${remaining} 秒`
}

export function VehicleRouteWorkspace({ onHome }: Props) {
  const mapElement = useRef<HTMLDivElement>(null)
  const mapInstance = useRef<any>(null)
  const [city, setCity] = useState('')
  const [origin, setOrigin] = useState({ name: '配送中心', address: '' })
  const [points, setPoints] = useState<RoutePoint[]>([{ id: 1, name: '配送网点 1', address: '' }])
  const [returnToOrigin, setReturnToOrigin] = useState(true)
  const [plan, setPlan] = useState<VehicleRoutePlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [mapError, setMapError] = useState('')
  const [mapReady, setMapReady] = useState(false)

  const validPoints = useMemo(() => points.filter(point => point.address.trim()), [points])

  useEffect(() => {
    let cancelled = false
    if (!amapJsKey) {
      setMapError('地图显示需要配置 VITE_AMAP_JS_KEY。路线计算仍会通过后端高德服务完成。')
      return
    }
    loadAmap().then(AMap => {
      if (cancelled || !mapElement.current) return
      mapInstance.current = new AMap.Map(mapElement.current, {
        zoom: 10,
        center: [116.397428, 39.90923],
        resizeEnable: true,
        mapStyle: 'amap://styles/normal',
      })
      mapInstance.current.addControl(new AMap.ToolBar({ position: 'RB' }))
      setMapReady(true)
      setMapError('')
    }).catch(reason => {
      if (!cancelled) setMapError(reason instanceof Error ? reason.message : '高德地图加载失败')
    })
    return () => {
      cancelled = true
      if (mapInstance.current) {
        mapInstance.current.destroy()
        mapInstance.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!mapReady || !plan || !mapInstance.current || !window.AMap) return
    const AMap = window.AMap
    mapInstance.current.clearMap()
    const markers = plan.stops.map((stop, index) => new AMap.Marker({
      position: stop.location,
      label: { content: `<span class="vehicle-map-label">${index === 0 ? '起' : index === plan.stops.length - 1 ? '终' : index}</span>`, direction: 'top' },
      title: `${stop.name} · ${stop.formatted_address}`,
    }))
    const overlays = [...markers]
    if (plan.polyline.length > 1) {
      overlays.push(new AMap.Polyline({ path: plan.polyline, strokeColor: '#3388ff', strokeWeight: 6, strokeOpacity: 0.88, lineJoin: 'round', lineCap: 'round' }))
    }
    mapInstance.current.add(overlays)
    mapInstance.current.setFitView(overlays, false, [64, 64, 64, 64])
  }, [mapReady, plan])

  function updatePoint(id: number, patch: Partial<RoutePoint>) {
    setPoints(current => current.map(point => point.id === id ? { ...point, ...patch } : point))
  }

  async function calculateRoute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    if (!origin.address.trim()) return setError('请填写配送中心地址。')
    if (!validPoints.length) return setError('请至少填写一个配送网点地址。')
    if (validPoints.length > 14) return setError('当前最多支持 14 个配送网点。')
    setBusy(true)
    try {
      const originPoint = { name: origin.name.trim() || '配送中心', address: origin.address.trim() }
      const destinationPoint = returnToOrigin
        ? originPoint
        : { name: validPoints[validPoints.length - 1].name.trim() || `配送网点 ${validPoints.length}`, address: validPoints[validPoints.length - 1].address.trim() }
      const intermediate = returnToOrigin ? validPoints : validPoints.slice(0, -1)
      const result = await wordagentApi.planVehicleRoute({
        city: city.trim(),
        origin: originPoint,
        stops: intermediate.map((point, index) => ({ name: point.name.trim() || `配送网点 ${index + 1}`, address: point.address.trim() })),
        destination: destinationPoint,
      })
      setPlan(result)
    } catch (reason) {
      setError(routeError(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="vehicle-route-page">
      <header className="module-topbar">
        <div className="module-topbar-title"><button className="admin-logo" type="button" onClick={onHome}>示例公司智能标书助手</button><span>车辆网点路线图</span></div>
        <button className="light-btn" type="button" onClick={onHome}>返回首页</button>
      </header>
      <div className="vehicle-route-content">
        <section className="module-header-card vehicle-route-heading">
          <div><p>配送线路规划</p><h1>车辆网点路线图</h1><span>输入配送中心和网点地址，查看驾车路线、里程与预计时间。</span></div>
          <span className="vehicle-provider-badge">高德地图</span>
        </section>
        <div className="vehicle-route-layout">
          <form className="form-card vehicle-route-form" onSubmit={event => void calculateRoute(event)}>
            <div className="vehicle-route-section-head"><div><strong>路线信息</strong><span>按列表顺序依次经过网点</span></div><label>城市<input value={city} onChange={event => setCity(event.target.value)} placeholder="例如：杭州市" /></label></div>
            <label className="vehicle-route-field"><span className="vehicle-route-index start">起</span><span><strong>{origin.name}</strong><input value={origin.address} onChange={event => setOrigin(current => ({ ...current, address: event.target.value }))} placeholder="输入仓库或配送中心详细地址" required /></span></label>
            <div className="vehicle-route-stops-head"><strong>配送网点</strong><button className="light-btn" type="button" onClick={() => setPoints(current => [...current, { id: Date.now(), name: `配送网点 ${current.length + 1}`, address: '' }])} disabled={points.length >= 14}>＋ 添加网点</button></div>
            <div className="vehicle-route-stop-list">
              {points.map((point, index) => <div className="vehicle-route-field vehicle-route-stop" key={point.id}>
                <span className="vehicle-route-index">{index + 1}</span><span><input aria-label={`网点 ${index + 1} 名称`} value={point.name} onChange={event => updatePoint(point.id, { name: event.target.value })} placeholder={`网点 ${index + 1} 名称`} /><input aria-label={`网点 ${index + 1} 地址`} value={point.address} onChange={event => updatePoint(point.id, { address: event.target.value })} placeholder="输入网点详细地址" /></span>
                <button className="vehicle-remove-stop" type="button" aria-label="删除网点" onClick={() => setPoints(current => current.filter(item => item.id !== point.id))} disabled={points.length <= 1}>×</button>
              </div>)}
            </div>
            <label className="vehicle-return-toggle"><input type="checkbox" checked={returnToOrigin} onChange={event => setReturnToOrigin(event.target.checked)} /><span>配送完成后返回配送中心</span></label>
            <button className="dark-btn vehicle-calculate-btn" type="submit" disabled={busy}>{busy ? '正在规划路线…' : '规划驾车路线'}</button>
            {error && <div className="vehicle-route-error" role="alert">{error}</div>}
            <p className="vehicle-route-note">地址将发送到高德地图进行地理编码和路线计算。一次最多规划 14 个配送网点。</p>
          </form>
          <section className="form-card vehicle-map-panel">
            <div className="vehicle-map-summary"><div><strong>路线地图</strong><span>{plan ? `${plan.stops.length} 个路线点` : '规划后显示地图和路线'}</span></div>{plan && <div className="vehicle-route-metrics"><strong>{formatDistance(plan.distance_meters)}</strong><span>约 {formatDuration(plan.duration_seconds)}</span></div>}</div>
            <div className={`vehicle-map-canvas ${mapReady ? '' : 'is-placeholder'}`} ref={mapElement}>
              {!mapReady && <div className="vehicle-map-empty"><span className="vehicle-map-pin">⌖</span><strong>{mapError || '地图加载中…'}</strong><small>请在浏览器端配置高德 JS API Key</small></div>}
            </div>
            {plan && <div className="vehicle-route-results">
              <strong>途经顺序</strong>
              <ol>{plan.stops.map((stop, index) => <li key={`${stop.address}-${index}`}><span>{index === 0 ? '起点' : index === plan.stops.length - 1 ? '终点' : `网点 ${index}`}</span><div><b>{stop.name}</b><small>{stop.formatted_address}</small></div></li>)}</ol>
              <details><summary>查看路线指引（{plan.steps.length} 段）</summary><ol className="vehicle-route-instructions">{plan.steps.map((step, index) => <li key={`${step.instruction}-${index}`}>{step.instruction}</li>)}</ol></details>
            </div>}
            {mapError && amapJsKey && <p className="vehicle-map-warning">{mapError}</p>}
          </section>
        </div>
      </div>
    </main>
  )
}
