import { useCallback, useEffect, useState } from 'react'
import { wordagentApi, type TenderNotice, type TenderSourceStatus } from './wordagentApi'
import { AccountNavActions } from './AccountNavActions'

function errorText(error: unknown) {
  if (error instanceof DOMException && (error.name === 'TimeoutError' || error.name === 'AbortError')) return '请求超时，后端可能繁忙，请稍后重试。'
  const text = error instanceof Error ? error.message : String(error)
  try { const value = JSON.parse(text); return value.detail || value.message || text } catch { return text }
}

export function TenderSearchWorkspace({ onHome }: { onHome: () => void }) {
  const [rows, setRows] = useState<TenderNotice[]>([])
  const [region, setRegion] = useState('')
  const [keyword, setKeyword] = useState('')
  const [statuses, setStatuses] = useState<TenderSourceStatus[]>([])
  const [checkedAt, setCheckedAt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(async (r: string, k: string) => {
    const result = await wordagentApi.getTenderNotices(r, k, AbortSignal.timeout(20000))
    setRows(result.notices)
    if (result.sources.length) { setStatuses(result.sources); setCheckedAt(result.sources[0].checked_at) }
  }, [])
  useEffect(() => {
    let mounted = true
    setBusy(true)
    void (async () => {
      try {
        await load('', '')
        const response = await wordagentApi.refreshTenderNotices(AbortSignal.timeout(25000))
        if (!mounted) return
        setStatuses(response.sources); setCheckedAt(response.checked_at)
        await load('', '')
      } catch (e) { if (mounted) setError(errorText(e) || '公告加载失败，请检查后端连接') }
      finally { if (mounted) setBusy(false) }
    })()
    return () => { mounted = false }
  }, [load])
  async function refresh() {
    setBusy(true); setError('')
    try {
      const result = await wordagentApi.refreshTenderNotices(AbortSignal.timeout(25000))
      setStatuses(result.sources); setCheckedAt(result.checked_at)
      await load(region, keyword)
    } catch (e) { setError(errorText(e) || '更新失败，请检查后端连接') }
    finally { setBusy(false) }
  }
  async function track(row: TenderNotice, state: string) {
    setError('')
    try {
      await wordagentApi.setTenderTracking(row.notice_id, state)
      setRows(current => current.map(item => item.notice_id === row.notice_id ? { ...item, follow_state: state } : item))
    } catch (e) { setError(errorText(e)) }
  }
  async function search() {
    setError('')
    try { await load(region, keyword) } catch (e) { setError(errorText(e)) }
  }
  return <div className="p1-app" style={{ minHeight: '100vh' }}>
    <header className="top-nav"><button type="button" className="logo-button" onClick={onHome}>示例公司智能标书助手</button><AccountNavActions onHome={onHome} /></header>
    <main className="module-page" style={{ maxWidth: 1120, margin: '0 auto' }}>
      <section className="module-header-card"><div><p>PROJECT DISCOVERY</p><h1>招标项目检索</h1><span>汇总食材、食品及食堂相关公开采购公告，按重点地区排序。</span></div></section>
      <section className="form-card" style={{ marginTop: 20, display: 'grid', gap: 14 }}>
        <strong>检索公告</strong>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <label>地区 <select value={region} onChange={e => setRegion(e.target.value)} style={{ padding: 10, marginLeft: 8 }}>{['','示例','示例甲','示例乙','华东其他','全国其他','待核实'].map(x => <option key={x} value={x}>{x || '全部地区'}</option>)}</select></label>
          <label>关键词 <input value={keyword} onChange={e => setKeyword(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void search() }} placeholder="项目名称或采购单位" style={{ padding: 10, marginLeft: 8 }} /></label>
          <button type="button" className="light-btn" onClick={() => void search()}>筛选</button>
          <button type="button" className="primary-btn" disabled={busy} onClick={() => void refresh()}>{busy ? '正在更新…' : '更新公告'}</button>
        </div>
        <span>地区排序：示例 → 示例甲 → 示例乙 → 华东其他 → 全国其他。地区依据公告列表信息推测，具体地点请查看原文。</span>
        <span>范围：仅抓取中国政府采购网四个地方公告栏目首页中匹配关键词的公告，进入页面时自动尝试更新，后端运行时约每 6 小时更新；覆盖不完整。</span>
      </section>
      <section className="form-card" style={{ marginTop: 20 }}>
        <h2 style={{ fontSize: 19 }}>数据来源状态</h2>
        {checkedAt && <p>本次检查时间：{new Date(checkedAt).toLocaleString()}</p>}
        {statuses.length === 0 && <p>{busy ? '正在检查公告来源…' : '尚未完成来源检查。'}</p>}
        {statuses.map(item => <p key={item.source} style={{ color: item.ok ? '#28643c' : '#a23422' }}>{item.source}：{item.ok ? `已获取，找到 ${item.count} 条相关公告` : `获取失败（${item.error || '连接失败'}），显示的可能是历史缓存`}</p>)}
        {error && <p className="graphic-error" role="alert">{error}</p>}
        <p>数据不保证完整或最新；截止时间、资格和更正情况均需在官方原文核实。</p>
        <p>官方平台： <a href="http://ggzy.hefei.gov.cn/" target="_blank" rel="noopener noreferrer">示例</a> · <a href="http://ggzyjy.ahsz.gov.cn/" target="_blank" rel="noopener noreferrer">示例甲</a> · <a href="https://ggzy.bengbu.gov.cn/" target="_blank" rel="noopener noreferrer">示例乙</a> · <a href="https://www.ccgp.gov.cn/" target="_blank" rel="noopener noreferrer">中国政府采购网</a></p>
      </section>
      <section className="form-card" style={{ marginTop: 20 }}>
        <h2 style={{ fontSize: 19 }}>公告列表 <small>（{rows.length} 条）</small></h2>
        {rows.length === 0 && <p>暂无已保存的匹配公告。若来源获取失败，请检查网络或前往官方平台查看。</p>}
        <div style={{ display: 'grid', gap: 12 }}>{rows.map(row => <article key={row.notice_id} style={{ border: '1px solid #dedbd3', borderRadius: 10, padding: 16 }}>
          <a href={row.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 16, fontWeight: 700, color: '#245f44' }}>{row.title}</a>
          <p style={{ color: '#62645e', margin: '8px 0' }}>地域：{row.region || '待核实'}　·　采购人：{row.buyer || '待核实'}　·　发布时间：{row.published_at || '待核实'}</p>
          <label>跟进状态： <select value={row.follow_state} onChange={e => void track(row, e.target.value)}>{['未标记','收藏','跟进中','不参与'].map(x => <option key={x} value={x}>{x}</option>)}</select></label>
          <p style={{ color: '#62645e', margin: 0 }}>来源：{row.source}　·　抓取时间：{new Date(row.fetched_at).toLocaleString()}　·　截止时间与资格：待核实</p>
        </article>)}</div>
      </section>
    </main>
  </div>
}
