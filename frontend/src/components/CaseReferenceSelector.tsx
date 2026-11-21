import { useEffect, useState } from 'react'
import { type CaseReferenceSelection, wordagentApi } from '../wordagentApi'

export function CaseReferenceSelector({ bidId, onSelectionChange }: { bidId: number | null; onSelectionChange?: (projectId: number | null) => void }) {
  const [selection, setSelection] = useState<CaseReferenceSelection | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setSelection(null)
    setError('')
    if (bidId) wordagentApi.getCaseReference(bidId)
      .then(response => { if (active) { setSelection(response.data); onSelectionChange?.(response.data.project_id) } })
      .catch(() => { if (active) setError('认可案例列表加载失败') })
    return () => { active = false }
  }, [bidId])

  async function change(value: string) {
    if (!bidId) return
    setBusy(true)
    setError('')
    try {
      const response = await wordagentApi.setCaseReference(bidId, value ? Number(value) : null)
      setSelection(response.data)
      onSelectionChange?.(response.data.project_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存案例选择失败')
    } finally {
      setBusy(false)
    }
  }

  return <div className="case-reference-selector" style={{ padding: '10px 12px', border: '1px solid #ddd8ce', borderRadius: 8, background: '#faf9f6' }}>
    <label style={{ display: 'block', fontWeight: 600, fontSize: 13 }}>甲方认可标书案例
      <select aria-label="甲方认可标书案例" disabled={!bidId || busy || !selection} value={selection?.project_id ?? ''}
        onChange={event => void change(event.target.value)} style={{ display: 'block', width: '100%', marginTop: 6, padding: 8 }}>
        <option value="">不指定案例</option>
        {selection?.options.map(item => <option key={item.project_id} value={item.project_id}>{item.project_name} · {item.bid_file_name}</option>)}
      </select>
    </label>
    <small style={{ display: 'block', marginTop: 6, color: '#766f65', lineHeight: 1.5 }}>
      {selection?.options.length ? '生成目录和正文时参考所选案例的结构与写法；当前招标要求优先。已生成内容需重新生成才会更新。Word 外观请另选导出模板。' : '知识库中暂无已完成解析的成套投标案例，请先在企业知识库上传。'}
    </small>
    {selection?.selected && <small style={{ display: 'block', marginTop: 5, color: '#766f65' }}>案例 Word 格式：{selection.selected.format_template_status === 'completed' ? '已提取，图文导出页可选' : '尚未完成提取；可在企业知识库的项目中提取格式模板'}</small>}
    {selection?.project_id && !selection.selected && <small role="alert" style={{ color: '#b42318' }}>已选案例失效，请重新选择。</small>}
    {error && <small role="alert" style={{ color: '#b42318' }}>{error}</small>}
  </div>
}
