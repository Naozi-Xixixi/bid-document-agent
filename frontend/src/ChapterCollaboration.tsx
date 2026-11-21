import { useCallback, useEffect, useState } from 'react'
import { authRequest } from './auth'
import { wordagentApi, type GraphicSection } from './wordagentApi'

type Member = { user_id: number; display_name: string; username: string }
type State = {
  revision: number; unresolved_count: number; assigned_user_id: number | null; reviewer_user_id: number | null; state: string
  permissions: { can_assign: boolean; can_edit: boolean; can_submit: boolean; can_review: boolean; can_reopen: boolean }
  versions: Array<{ revision: number; actor_name: string; action: string; note: string; created_at: string }>
  comments: Array<{ id: number; revision: number; actor_name: string; body: string; quote: string; resolved: number; resolved_by: string | null; created_at: string }>
}
const actions: Record<string, string> = { baseline: '接入前内容', save: '保存正文', assign: '分配章节', submit: '提交审核', approve: '审批定稿', reject: '退回修改', reopen: '重新打开', restore: '版本回退', comment: '添加批注', resolve_comment: '解决批注' }
const statusNames: Record<string, string> = { draft: '草稿', in_review: '审核中（正文锁定）', approved: '已定稿（正文锁定）' }
const date = (value: string) => new Date(value).toLocaleString('zh-CN')

export function ChapterCollaboration({ bidId, chapterId, revision, dirty, generating, markdown, onRevision, onLoad, onEditable, onBusy }: {
  bidId: number; chapterId: number; revision: number | null; dirty: boolean; generating: boolean; markdown: string
  onRevision: (revision: number) => void; onLoad: (section: GraphicSection) => void; onEditable: (editable: boolean) => void; onBusy: (busy: boolean) => void
}) {
  const [state, setState] = useState<State | null>(null)
  const [members, setMembers] = useState<Member[]>([])
  const [assignee, setAssignee] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [body, setBody] = useState('')
  const [quote, setQuote] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState('')
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')
  const [diff, setDiff] = useState<{ left: string; right: string; diff: string } | null>(null)
  const path = '/bid/' + bidId + '/chapters/' + chapterId + '/collaboration'
  const refresh = useCallback(async () => {
    const result = await authRequest<{ data: State }>(path)
    setState(result.data)
    return result.data
  }, [path])
  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const [result, people] = await Promise.all([authRequest<{ data: State }>(path), authRequest<{ data: Member[] }>('/bid/collaboration/members')])
        if (!active) return
        setState(result.data); setMembers(people.data)
        setAssignee(String(result.data.assigned_user_id || '')); setReviewer(String(result.data.reviewer_user_id || ''))
      } catch (e) { if (active) setError(String(e)) }
    }
    void load()
    const timer = window.setInterval(() => { if (active) void authRequest<{ data: State }>(path).then(r => { if (active) setState(r.data) }).catch(() => {}) }, 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [path])
  useEffect(() => { if (revision !== null) void refresh().catch(() => {}) }, [revision, refresh])
  useEffect(() => { onEditable(Boolean(state?.permissions.can_edit)) }, [state?.permissions.can_edit, onEditable])
  useEffect(() => {
    setAssignee(String(state?.assigned_user_id || ''))
    setReviewer(String(state?.reviewer_user_id || ''))
  }, [state?.assigned_user_id, state?.reviewer_user_id])
  useEffect(() => { onBusy(busy); return () => onBusy(false) }, [busy, onBusy])
  const stale = state !== null && revision !== null && state.revision !== revision
  const blocked = busy || generating || dirty || revision === null || stale
  async function mutate(suffix: string, payload: object, method = 'POST') {
    if (blocked) return
    setBusy(true); setError('')
    try {
      const result = await authRequest<{ data: State }>(path + suffix, { method, body: JSON.stringify({ ...payload, expected_revision: revision }) })
      setState(result.data); onRevision((revision ?? 0) + 1); setConfirm(''); setNote(''); setBody(''); setQuote('')
      if (suffix === '/restore') onLoad((await wordagentApi.getGraphicSection(bidId, chapterId)).data)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); await refresh().catch(() => {}) }
    finally { setBusy(false) }
  }
  function downloadDraft() {
    const url = URL.createObjectURL(new Blob([markdown], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a'); link.href = url; link.download = '章节-' + chapterId + '-本地草稿.md'; link.click(); URL.revokeObjectURL(url)
  }
  async function loadLatest() {
    setBusy(true); setError('')
    try { onLoad((await wordagentApi.getGraphicSection(bidId, chapterId)).data); await refresh(); setConfirm('') }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  async function compareVersions() {
    setBusy(true); setError('')
    try { setDiff((await authRequest<{ data: { left: string; right: string; diff: string } }>(path + '/compare?left=' + left + '&right=' + right)).data) }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  async function compareLocal() {
    setBusy(true)
    try { const latest = (await wordagentApi.getGraphicSection(bidId, chapterId)).data; setDiff({ left: markdown, right: latest.markdown, diff: '左侧是未丢弃的本地草稿，右侧是服务器最新正文。可复制需要保留的文字，加载最新版本后再合并保存。' }) }
    catch(e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <details className="chapter-collaboration" open>
    <summary><strong>章节协作与留痕</strong> · {state ? statusNames[state.state] : '加载中'} · 版本 {revision ?? '…'}</summary>
    {error && <div role="alert" className="graphic-error">{error}</div>}
    {stale && <div role="alert" className="graphic-error">服务器已更新至版本 {state?.revision}，当前编辑基于版本 {revision}。保存会被拦截，本地草稿仍在。请先对比再合并。</div>}
    <div className="collab-actions"><button type="button" disabled={busy || generating} onClick={() => void compareLocal()}>本地与最新版本对比</button><button type="button" onClick={downloadDraft}>下载本地草稿</button><button type="button" disabled={busy || generating} onClick={() => setConfirm('reload')}>加载最新章节</button></div>
    {confirm === 'reload' && <div className="knowledge-notice">加载将替换编辑器里的本地文字，请先复制或下载需要保留的草稿。<button type="button" onClick={() => void loadLatest()}>确认加载</button><button type="button" onClick={() => setConfirm('')}>取消</button></div>}
    {dirty && <p>请先保存正文，再分工、批注或提交审核。</p>}
    {state && <>
      <div className="collab-grid">
        <label>章节负责人<select aria-label="章节负责人" value={assignee} disabled={!state.permissions.can_assign || blocked || state.state !== 'draft'} onChange={e => setAssignee(e.target.value)}><option value="">未分配（同公司可编辑）</option>{members.map(m => <option key={m.user_id} value={m.user_id}>{m.display_name}（{m.username}）</option>)}</select></label>
        <label>审核人<select aria-label="章节审核人" value={reviewer} disabled={!state.permissions.can_assign || blocked || state.state !== 'draft'} onChange={e => setReviewer(e.target.value)}><option value="">请选择另一位成员</option>{members.map(m => <option key={m.user_id} value={m.user_id}>{m.display_name}（{m.username}）</option>)}</select></label>
      </div>
      <button type="button" disabled={!state.permissions.can_assign || blocked || state.state !== 'draft'} onClick={() => void mutate('/assignment', { assigned_user_id: Number(assignee) || null, reviewer_user_id: Number(reviewer) || null }, 'PUT')}>保存分工</button>
      <p>管理员分工；负责人编写；另一位审核人审批。审核中和定稿后正文锁定，重新打开会记录原因。</p>
      <label>审核意见 / 重新打开原因<input value={note} maxLength={2000} onChange={e => setNote(e.target.value)} /></label>
      <div className="collab-actions"><button type="button" disabled={blocked || !state.permissions.can_submit} onClick={() => void mutate('/workflow', { action: 'submit', note })}>提交审核</button><button type="button" disabled={blocked || !state.permissions.can_review} onClick={() => void mutate('/workflow', { action: 'approve', note })}>审批定稿</button><button type="button" disabled={blocked || !state.permissions.can_review || !note.trim()} onClick={() => void mutate('/workflow', { action: 'reject', note })}>退回修改</button><button type="button" disabled={blocked || !state.permissions.can_reopen || !note.trim()} onClick={() => void mutate('/workflow', { action: 'reopen', note })}>重新打开</button></div>
      <details><summary>评审批注（{state.unresolved_count} 条未解决）</summary>
        <label>引用原文（可选）<input value={quote} maxLength={2000} onChange={e => setQuote(e.target.value)} /></label>
        <label>批注内容<textarea value={body} maxLength={4000} onChange={e => setBody(e.target.value)} /></label>
        <button type="button" disabled={blocked || !body.trim() || state.state === 'approved'} onClick={() => void mutate('/comments', { body, quote })}>添加批注</button>
        {state.comments.map(c => <article key={c.id} className="collab-comment"><strong>{c.actor_name}</strong> · {date(c.created_at)} · 基于版本 {c.revision} · {c.resolved ? '已解决：' + c.resolved_by : '待处理'}{c.quote && <blockquote>{c.quote}</blockquote>}<p>{c.body}</p>{!c.resolved && <button type="button" disabled={blocked || state.state === 'approved'} onClick={() => void mutate('/comments/' + c.id + '/resolve', {})}>标记解决</button>}</article>)}
        <small>优先显示待处理批注，最多 200 条。未解决批注会阻止定稿；作者、审核人或管理员可以标记解决。</small>
      </details>
      <details><summary>版本历史与操作记录</summary>
        <div className="collab-grid"><label>对比旧版本<select value={left} onChange={e => setLeft(e.target.value)}><option value="">选择版本</option>{state.versions.map(v => <option key={v.revision} value={v.revision}>版本 {v.revision} · {v.actor_name} · {actions[v.action] || v.action}</option>)}</select></label><label>对比新版本<select value={right} onChange={e => setRight(e.target.value)}><option value="">选择版本</option>{state.versions.map(v => <option key={v.revision} value={v.revision}>版本 {v.revision} · {v.actor_name} · {actions[v.action] || v.action}</option>)}</select></label></div>
        <div className="collab-actions"><button type="button" disabled={busy || left === '' || right === ''} onClick={() => void compareVersions()}>查看版本差异</button><button type="button" disabled={blocked || !state.permissions.can_edit || left === ''} onClick={() => setConfirm('restore')}>回退到所选旧版本</button></div>
        {confirm === 'restore' && <p>回退会创建新版本，所有历史仍保留。<button type="button" onClick={() => void mutate('/restore', { revision: Number(left) })}>确认回退</button><button type="button" onClick={() => setConfirm('')}>取消</button></p>}
        <ol className="collab-history">{state.versions.map(v => <li key={v.revision}>版本 {v.revision} · {actions[v.action] || v.action} · {v.actor_name} · {date(v.created_at)} {v.note && <span> · {v.note}</span>}</li>)}</ol><small>显示最近 100 个版本；历史不因回退而删除。</small>
      </details>
    </>}
    {diff && <section><h4>内容对比</h4><pre className="collab-diff">{diff.diff || '正文内容相同（本次可能只修改了分工或审核状态）'}</pre><div className="collab-grid"><pre className="collab-diff">{diff.left}</pre><pre className="collab-diff">{diff.right}</pre></div><button type="button" onClick={() => setDiff(null)}>收起对比</button></section>}
  </details>
}
