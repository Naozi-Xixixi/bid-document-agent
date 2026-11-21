import { useEffect, useMemo, useState } from 'react'
import { useAuth } from './AuthWorkspace'
import { type ModelScene, type QwenCatalogModel, wordagentApi } from './wordagentApi'

function messageOf(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  try {
    const parsed = JSON.parse(raw)
    return parsed.detail || parsed.message || raw
  } catch { return raw }
}

async function withTimeout<T>(work: (signal: AbortSignal) => Promise<T>, label: string): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 20000)
  try { return await work(controller.signal) }
  catch (error) {
    if (controller.signal.aborted) throw new Error(`${label}超时，后端可能繁忙或网络不可用，请稍后重试`)
    throw error
  } finally { window.clearTimeout(timer) }
}

function relevantModels(models: QwenCatalogModel[], kind: ModelScene['kind']) {
  const capabilities = kind === 'embedding' ? ['TR', 'ME'] : kind === 'vision' ? ['VU'] : ['TG', 'Reasoning']
  return models.filter(model => model.capabilities.some(value => capabilities.includes(value)) ||
    (kind === 'embedding' && model.id.includes('embedding')) ||
    (kind === 'vision' && model.id.includes('ocr')))
}

export function ModelSettingsWorkspace({ onHome }: { onHome: () => void }) {
  const { user, company } = useAuth()
  const canEdit = user?.role === 'admin'
  const [scenes, setScenes] = useState<ModelScene[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [models, setModels] = useState<QwenCatalogModel[]>([])
  const [deepseekModels, setDeepseekModels] = useState<QwenCatalogModel[]>([])
  const [deepseekLoading, setDeepseekLoading] = useState(false)
  const [deepseekError, setDeepseekError] = useState('')
  const [loading, setLoading] = useState(true)
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalogError, setCatalogError] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState('')

  useEffect(() => {
    let active = true
    withTimeout(signal => wordagentApi.getModelScenes(signal), '读取场景配置').then(result => {
      if (!active) return
      setScenes(result.scenes)
      setDrafts(Object.fromEntries(result.scenes.map(item => [item.scene, item.model])))
    }).catch(reason => { if (active) setError(messageOf(reason)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  async function loadCatalog() {
    setCatalogLoading(true)
    setCatalogError('')
    try {
      const result = await withTimeout(signal => wordagentApi.getQwenCatalog(signal), '获取模型列表')
      setModels(result.models)
      if (!result.models.length) setCatalogError('百炼没有返回千问模型，请确认 API Key 所属地域和服务地址。')
    } catch (reason) { setCatalogError(messageOf(reason)) }
    finally { setCatalogLoading(false) }
  }

  useEffect(() => { void loadCatalog() }, [])

  async function loadDeepseekCatalog() {
    setDeepseekLoading(true)
    setDeepseekError('')
    try {
      const result = await withTimeout(signal => wordagentApi.getDeepseekCatalog(signal), '获取 DeepSeek 模型列表')
      setDeepseekModels(result.models)
      if (!result.models.length) setDeepseekError('DeepSeek 未返回模型，请检查账号权限。')
    } catch (reason) { setDeepseekError(messageOf(reason)) }
    finally { setDeepseekLoading(false) }
  }

  useEffect(() => { void loadDeepseekCatalog() }, [])


  async function save(item: ModelScene, reset = false) {
    const next = reset ? null : drafts[item.scene]?.trim() || null
    setSaving(item.scene)
    setError('')
    setNotice('')
    try {
      const result = await wordagentApi.saveModelScene(item.scene, next)
      setScenes(current => current.map(row => row.scene === item.scene ? { ...row, model: result.model } : row))
      setDrafts(current => ({ ...current, [item.scene]: result.model }))
      setNotice(`“${item.label}”已切换为 ${result.model}。后续模型调用将使用此配置。`)
    } catch (reason) { setError(messageOf(reason)) }
    finally { setSaving('') }
  }

  const grouped = useMemo(() => ({
    text: scenes.filter(item => item.kind === 'text'),
    vision: scenes.filter(item => item.kind === 'vision'),
    embedding: scenes.filter(item => item.kind === 'embedding'),
  }), [scenes])

  return <div style={{ minHeight: '100vh', background: 'var(--page-bg)' }}>
    <header className="top-nav">
      <button className="logo-button" type="button" onClick={onHome}>木石科技</button>
      <nav className="nav-actions"><span className="nav-company-name">{company?.company_name || '当前公司'}</span><button className="nav-btn nav-btn-a" type="button" onClick={onHome}>返回首页</button></nav>
    </header>
    <main className="module-page utility-workspace" style={{ maxWidth: 1040, margin: '0 auto' }}>
      <section className="module-header-card"><div><p>MODEL SETTINGS</p><h1>场景模型设置</h1><span>为每项任务分别选择模型。设置按公司保存，已开始的单次模型请求不会中断。</span></div></section>
      <section className="form-card" style={{ marginTop: 24, display: 'grid', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div><strong>千问模型目录</strong><p style={{ margin: '5px 0 0', color: 'var(--form-desc-color)', fontSize: 13 }}>从百炼官方接口获取；列表中出现不代表当前账号一定有调用权限。</p></div>
          <button className="light-btn" type="button" disabled={catalogLoading} onClick={() => void loadCatalog()}>{catalogLoading ? '正在获取…' : '刷新模型列表'}</button>
        </div>
        {catalogError && <div className="graphic-error" role="alert">{catalogError}。仍可手动填写准确的模型 ID。</div>}
        {!catalogError && <span style={{ color: 'var(--form-desc-color)', fontSize: 13 }}>{catalogLoading ? '正在获取模型…' : `已获取 ${models.length} 个千问模型`}</span>}
      </section>
      <section className="form-card" style={{ marginTop: 16, display: 'grid', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div><strong>DeepSeek 模型目录</strong><p style={{ margin: '5px 0 0', color: 'var(--form-desc-color)', fontSize: 13 }}>使用独立的 DeepSeek API Key；仅供文本生成场景选择，文档内容会发送给对应服务商。</p></div>
          <button className="light-btn" type="button" disabled={deepseekLoading} onClick={() => void loadDeepseekCatalog()}>{deepseekLoading ? '正在获取…' : '刷新 DeepSeek 列表'}</button>
        </div>
        {deepseekError && <div className="graphic-error" role="alert">{deepseekError}</div>}
        {!deepseekError && <span style={{ color: 'var(--form-desc-color)', fontSize: 13 }}>{deepseekLoading ? '正在获取模型…' : `已获取 ${deepseekModels.length} 个 DeepSeek 模型`}</span>}
      </section>
      {error && <div className="graphic-error" role="alert" style={{ marginTop: 20 }}>{error} <button className="light-btn" type="button" onClick={() => window.location.reload()}>重新加载页面</button></div>}
      {notice && <div className="knowledge-notice" role="status" style={{ marginTop: 20 }}>{notice}</div>}
      {loading && <p>正在读取当前配置…</p>}
      {([['text', '文本生成'], ['vision', '图片识别'], ['embedding', '向量化']] as const).map(([kind, title]) => grouped[kind].length > 0 &&
        <section className="form-card" key={kind} style={{ marginTop: 24 }}>
          <h2 style={{ margin: '0 0 8px', fontSize: 20 }}>{title}</h2>
          {kind === 'embedding' && <p style={{ color: 'var(--form-desc-color)', fontSize: 13 }}>仅当向量提供方设为 Qwen 时生效；切换向量模型后，已有向量索引需要重新构建。</p>}
          {kind === 'vision' && <p style={{ color: 'var(--form-desc-color)', fontSize: 13 }}>仅当知识库启用千问图片识别时生效。发票 OCR 使用独立的本地识别流程。</p>}
          {grouped[kind].map(item => {
            const options = relevantModels(item.kind === 'text' ? [...models, ...deepseekModels] : models, item.kind)
            return <div key={item.scene} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, alignItems: 'center', padding: '14px 0', borderTop: '1px solid var(--border-color, #e0ddd7)' }}>
              <div><strong>{item.label}</strong><small style={{ display: 'block', color: 'var(--form-desc-color)', marginTop: 4 }}>默认：{item.default}</small></div>
              <div><input aria-label={`${item.label}模型 ID`} list={`models-${item.scene}`} value={drafts[item.scene] ?? item.model} disabled={!canEdit || Boolean(saving)} onChange={event => setDrafts(current => ({ ...current, [item.scene]: event.target.value }))} placeholder="选择或输入模型 ID" style={{ width: '100%', boxSizing: 'border-box', padding: '11px 12px', border: '1px solid #d8d4cd', borderRadius: 7, background: '#fff' }} />
                <datalist id={`models-${item.scene}`}>{options.map(model => <option key={model.id} value={model.id} label={model.id.startsWith('deepseek/') ? `DeepSeek · ${model.name}` : model.name} />)}</datalist></div>
              <button className="light-btn" type="button" disabled={!canEdit || Boolean(saving) || drafts[item.scene]?.trim() === item.model} onClick={() => void save(item)}>{saving === item.scene ? '保存中…' : '保存'}</button>
              <button className="light-btn" type="button" disabled={!canEdit || Boolean(saving) || item.model === item.default} onClick={() => void save(item, true)}>恢复默认</button>
            </div>
          })}
        </section>)}
      {!canEdit && <p style={{ color: 'var(--form-desc-color)' }}>只有公司管理员可以修改模型设置。</p>}
    </main>
  </div>
}
