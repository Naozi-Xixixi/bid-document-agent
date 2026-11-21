import type { ApiProject, BidParseStatus } from '../wordagentApi'

export type ProjectSourceLoadState = 'loading' | 'ready' | 'unavailable'

function fileSizeLabel(size: number | null | undefined) {
  if (!Number.isFinite(size) || !size || size < 0) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`
}

export function ProjectSourceSummary({ project, parse, loadState }: {
  project: Pick<ApiProject, 'files'>
  parse?: BidParseStatus
  loadState: ProjectSourceLoadState
}) {
  const files = project.files?.filter(file => file.filename?.trim()) ?? []
  const hasParseSource = Boolean(parse?.parse_mode || parse?.tender_file_name)
  let source: string
  if (loadState === 'loading') source = '来源：正在读取来源信息…'
  else if (loadState === 'unavailable') source = '来源信息暂不可用，请重新打开列表重试'
  else if (parse?.parse_mode === 'paste_text') source = '来源：手动粘贴的项目概述和评分要求'
  else if (parse?.tender_file_name || parse?.parse_mode === 'auto_upload') source = `来源：上传招标文件 · ${parse.tender_file_name || '原文件名未记录'}`
  else if (files.length) source = '来源：历史项目上传的参考文件'
  else source = '尚未上传招标文件或填写解析资料'

  const parseLabels: Record<BidParseStatus['parse_status'], string> = {
    pending: '待解析', processing: '解析中', completed: '解析完成', failed: '解析失败',
  }
  const details = loadState === 'ready' && hasParseSource && parse
    ? [parse.parse_mode !== 'paste_text' ? fileSizeLabel(parse.tender_file_size) : '', parseLabels[parse.parse_status]].filter(Boolean).join(' · ')
    : ''

  return <span className="p1-history-source" style={{ display: 'grid', gap: 3, overflowWrap: 'anywhere', whiteSpace: 'normal' }}>
    <span style={{ color: 'var(--form-label-color)' }}>{source}</span>
    {details && <span>{details}</span>}
    {files.length > 0 && <span>参考附件：{files.map(file => [file.filename, fileSizeLabel(file.size)].filter(Boolean).join('（') + (fileSizeLabel(file.size) ? '）' : '')).join('；')}</span>}
  </span>
}
