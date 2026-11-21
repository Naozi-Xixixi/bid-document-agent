import { useEffect, useMemo, useState } from 'react'
import { type ApiProject, type PptSource, wordagentApi } from './wordagentApi'

function readableError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  try {
    const parsed = JSON.parse(raw)
    return parsed.detail || parsed.message || raw
  } catch { return raw }
}

const fieldStyle = { width: '100%', boxSizing: 'border-box' as const, border: '1px solid #d8d4cd', borderRadius: 7, padding: '11px 12px', background: '#fff', color: '#222' }

export function PptWorkspace({ onHome }: { onHome: () => void }) {
  const [projects, setProjects] = useState<ApiProject[]>([])
  const [sourceNames, setSourceNames] = useState<Record<string, string>>({})
  const [bidId, setBidId] = useState<number | null>(null)
  const [versionId, setVersionId] = useState<number | undefined>()
  const [source, setSource] = useState<PptSource | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [title, setTitle] = useState('')
  const [subtitle, setSubtitle] = useState('')
  const [theme, setTheme] = useState<'green' | 'blue'>('green')
  const [pagesPerChapter, setPagesPerChapter] = useState(3)
  const [includeImages, setIncludeImages] = useState(true)
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [loadingSource, setLoadingSource] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    wordagentApi.listProjects().then(result => {
      if (!active) return
      const withBid = result.projects.filter(project => Boolean(project.bidId))
      setProjects(withBid)
      if (withBid.length) {
        setBidId(withBid[0].bidId || null)
        setTitle(withBid[0].settings.bidTitle || '')
      }
      withBid.forEach(project => {
        if (!project.bidId) return
        void wordagentApi.getBidParseStatus(project.bidId).then(status => {
          if (active && status.data.tender_file_name) setSourceNames(current => ({ ...current, [project.id]: status.data.tender_file_name || '' }))
        }).catch(() => undefined)
      })
    }).catch(reason => { if (active) setError(readableError(reason)) })
      .finally(() => { if (active) setLoadingProjects(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!bidId) { setSource(null); return }
    let active = true
    setLoadingSource(true)
    setError('')
    setSource(null)
    wordagentApi.getPptSource(bidId, versionId).then(result => {
      if (!active) return
      setSource(result.data)
      setSelected(result.data.chapters.filter(chapter => chapter.available).slice(0, 40).map(chapter => chapter.chapter_id))
    }).catch(reason => { if (active) setError(readableError(reason)) })
      .finally(() => { if (active) setLoadingSource(false) })
    return () => { active = false }
  }, [bidId, versionId])

  const available = useMemo(() => source?.chapters.filter(chapter => chapter.available) || [], [source])

  function chooseProject(nextBidId: number) {
    setBidId(nextBidId)
    setVersionId(undefined)
    setSelected([])
    setNotice('')
    const project = projects.find(item => item.bidId === nextBidId)
    setTitle(project?.settings.bidTitle || '')
  }

  function toggleChapter(chapterId: number) {
    setSelected(current => current.includes(chapterId) ? current.filter(id => id !== chapterId) : current.length < 40 ? [...current, chapterId] : current)
  }

  async function generate() {
    if (!bidId || !source || !selected.length || generating) return
    setGenerating(true)
    setError('')
    setNotice('')
    try {
      const blob = await wordagentApi.generatePpt(bidId, {
        version_id: source.version_id,
        chapter_ids: selected,
        title: title.trim(),
        subtitle: subtitle.trim(),
        theme,
        max_slides_per_chapter: pagesPerChapter,
        include_images: includeImages,
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${(title.trim() || source.project_title).replace(/[\\/:*?"<>|]/g, '_')}-项目汇报.pptx`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 60000)
      setNotice(`PPT 已生成并开始下载（${(blob.size / 1024).toFixed(0)} KiB）。可以在 PowerPoint 中继续编辑文字、表格和图片位置。`)
    } catch (reason) { setError(readableError(reason)) }
    finally { setGenerating(false) }
  }

  return <div style={{ minHeight: '100vh', background: 'var(--page-bg)' }}>
    <header className="top-nav">
      <button className="logo-button" type="button" onClick={onHome}>木石科技</button>
      <nav className="nav-actions"><button className="nav-btn nav-btn-a" type="button" onClick={onHome}>返回首页</button></nav>
    </header>
    <main className="module-page utility-workspace" style={{ maxWidth: 1080, margin: '0 auto' }}>
      <section className="module-header-card"><div><p>PRESENTATION</p><h1>制作项目汇报 PPT</h1><span>从已保存的标书目录和正文生成可编辑 PPTX。当前使用内置版式，尚非示例公司官方模板。</span></div></section>
      <section className="form-card" style={{ marginTop: 24, display: 'grid', gap: 18 }}>
        <div><strong>1. 选择项目与目录版本</strong><p style={{ color: 'var(--form-desc-color)', margin: '5px 0 0', fontSize: 13 }}>至少完成目录和一个章节正文后才能制作。</p></div>
        {loadingProjects ? <p>正在读取项目…</p> : projects.length === 0 ? <p>暂无可用标书项目，请先到“招标文件和大纲生成”新建项目。</p> : <>
          <label style={{ display: 'grid', gap: 7 }}>标书项目
            <select style={fieldStyle} value={bidId || ''} onChange={event => chooseProject(Number(event.target.value))}>
              {projects.map(project => <option key={project.id} value={project.bidId || ''}>{sourceNames[project.id] || project.settings.bidTitle || '未命名项目'} · #{project.bidId}</option>)}
            </select>
          </label>
          {source && <label style={{ display: 'grid', gap: 7 }}>目录版本
            <select style={fieldStyle} value={source.version_id} onChange={event => setVersionId(Number(event.target.value))}>
              {source.versions.map(version => <option key={version.version_id} value={version.version_id}>{version.version_name}{version.is_final ? ' · 最终版' : ''}</option>)}
            </select>
          </label>}
        </>}
        {loadingSource && <p>正在检查章节内容…</p>}
      </section>
      {source && <>
        <section className="form-card" style={{ marginTop: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}><div><strong>2. 选择汇报章节</strong><p style={{ color: 'var(--form-desc-color)', margin: '5px 0', fontSize: 13 }}>已选择 {selected.length} / 40 章；未生成正文的章节暂不可选。</p></div><div style={{ display: 'flex', gap: 8 }}><button className="light-btn" type="button" onClick={() => setSelected(available.slice(0, 40).map(item => item.chapter_id))}>全选可用</button><button className="light-btn" type="button" onClick={() => setSelected([])}>清空</button></div></div>
          {!available.length && <p>这个目录版本还没有已保存的章节正文。请先在“图文标书正文”生成或编辑章节。</p>}
          <div style={{ maxHeight: 400, overflowY: 'auto', marginTop: 14, borderTop: '1px solid #e1ddd5' }}>
            {source.chapters.map(chapter => <label key={chapter.chapter_id} style={{ display: 'flex', gap: 12, alignItems: 'start', padding: '11px 8px', paddingLeft: 8 + Math.max(0, chapter.level - 1) * 18, borderBottom: '1px solid #ebe7df', opacity: chapter.available ? 1 : 0.55 }}>
              <input type="checkbox" checked={selected.includes(chapter.chapter_id)} disabled={!chapter.available || (!selected.includes(chapter.chapter_id) && selected.length >= 40)} onChange={() => toggleChapter(chapter.chapter_id)} style={{ marginTop: 4 }} />
              <span style={{ display: 'grid', gap: 3 }}><strong>{chapter.chapter_title}</strong><small style={{ color: 'var(--form-desc-color)' }}>{chapter.source_kind}{chapter.image_count ? ` · ${chapter.image_count} 张引用图片` : ''}{chapter.preview ? ` · ${chapter.preview}` : ''}</small></span>
            </label>)}
          </div>
        </section>
        <section className="form-card" style={{ marginTop: 24, display: 'grid', gap: 16 }}>
          <strong>3. 设置封面与版式</strong>
          <label style={{ display: 'grid', gap: 7 }}>封面标题<input style={fieldStyle} maxLength={160} value={title} onChange={event => setTitle(event.target.value)} placeholder={source.project_title} /></label>
          <label style={{ display: 'grid', gap: 7 }}>副标题<input style={fieldStyle} maxLength={200} value={subtitle} onChange={event => setSubtitle(event.target.value)} placeholder="例如：项目技术方案汇报" /></label>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(210px,1fr))', gap: 16 }}>
            <label style={{ display: 'grid', gap: 7 }}>内置版式<select style={fieldStyle} value={theme} onChange={event => setTheme(event.target.value as 'green' | 'blue')}><option value="green">清新绿</option><option value="blue">商务蓝</option></select></label>
            <label style={{ display: 'grid', gap: 7 }}>每章节最多页数<select style={fieldStyle} value={pagesPerChapter} onChange={event => setPagesPerChapter(Number(event.target.value))}><option value={1}>1 页</option><option value={2}>2 页</option><option value={3}>3 页</option><option value={4}>4 页</option></select></label>
          </div>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}><input type="checkbox" checked={includeImages} onChange={event => setIncludeImages(event.target.checked)} />包含已引用的图片和图表</label>
          <p style={{ margin: 0, color: 'var(--form-desc-color)', fontSize: 13 }}>正文按原文摘取重点；长章节会按页数上限精简。完整原文仍保留在标书中。表格文字可编辑，图片可移动和替换。</p>
        </section>
        {error && <div className="graphic-error" role="alert" style={{ marginTop: 20 }}>{error}</div>}
        {notice && <div className="knowledge-notice" role="status" style={{ marginTop: 20 }}>{notice}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '24px 0 60px' }}><button className="dark-btn" type="button" disabled={generating || !selected.length} onClick={() => void generate()}>{generating ? '正在制作 PPT…' : '生成并下载 PPTX'}</button></div>
      </>}
      {!source && error && <div className="graphic-error" role="alert" style={{ marginTop: 20 }}>{error}</div>}
    </main>
  </div>
}
