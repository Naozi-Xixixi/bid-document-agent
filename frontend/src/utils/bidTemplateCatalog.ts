import standardPublicTemplateUrl from '../assets/bid-templates/standard-public.docx?url'
import compactAnonymousTemplateUrl from '../assets/bid-templates/compact-anonymous.docx?url'
import foodDeliveryTemplateUrl from '../assets/bid-templates/food-delivery.docx?url'
import enterpriseHeaderTemplateUrl from '../assets/bid-templates/enterprise-header.docx?url'

export type BidTemplateId = 'default-business' | 'compact-technical' | 'formal-government' | 'enterprise-header'

export type BidTemplateConfig = {
  id: BidTemplateId
  name: string
  description: string
  scenario: string
  filePath: string
  font: string
  fontSize: string
  lineSpacing: string
  headingLevels: string
  structure: string
  placeholders: string[]
}

export const BID_TEMPLATE_CONFIGS: BidTemplateConfig[] = [
  {
    id: 'default-business',
    name: '标准明标版',
    description: '标准正式格式，适合公开招标项目和常规明标评审。',
    scenario: '公开招标、明标评审、商务与技术方案合并成册。',
    filePath: standardPublicTemplateUrl,
    font: '宋体',
    fontSize: '正文 11 磅，标题 12–16 磅',
    lineSpacing: '1.5 倍行距',
    headingLevels: '一级、二级、三级标题样式完整',
    structure: '封面、目录、图文正文、页眉、页脚、页码',
    placeholders: ['文本：{bid_title}、{project_number}、{template_name}', '目录循环：{#chapter_rows}...{/chapter_rows}', '章节循环：{#chapters}...{/chapters}', '图片：{%image}'],
  },
  {
    id: 'compact-technical',
    name: '精简暗标版',
    description: '隐去企业信息，正文紧凑，减少装饰元素，适合暗标评审。',
    scenario: '暗标评审、技术方案、实施组织和服务响应章节。',
    filePath: compactAnonymousTemplateUrl,
    font: '等线',
    fontSize: '正文 10.5 磅，标题 12–16 磅',
    lineSpacing: '1.15 倍行距',
    headingLevels: '一级、二级、三级标题样式完整',
    structure: '简洁封面、目录、紧凑正文、页眉、页脚、页码',
    placeholders: ['文本：{bid_title}、{bid_id}、{template_name}', '目录循环：{#chapter_rows}...{/chapter_rows}', '章节循环：{#chapters}...{/chapters}', '图片：{%image}'],
  },
  {
    id: 'formal-government',
    name: '食材配送专项版',
    description: '面向食材配送场景，内置资质、人员、车辆和设备表格样式。',
    scenario: '食材配送、餐饮供应链、冷链运输及履约能力响应。',
    filePath: foodDeliveryTemplateUrl,
    font: '宋体',
    fontSize: '正文 11 磅，标题 12–16 磅',
    lineSpacing: '1.35 倍行距',
    headingLevels: '一级、二级、三级标题样式完整',
    structure: '封面、目录、资质表、人员表、车辆表、设备表、图文正文、页眉页脚',
    placeholders: ['文本：{bid_title}、{bid_id}、{template_name}', '目录循环：{#chapter_rows}...{/chapter_rows}', '业务表循环：{#qualification_rows}、{#personnel_rows}、{#vehicle_rows}、{#equipment_rows}', '章节循环：{#chapters}...{/chapters}', '图片：{%image}'],
  },
  {
    id: 'enterprise-header',
    name: '示例公司企业页眉页脚版',
    description: '使用示例公司历史投标文件的品牌页眉、页脚与动态页码。',
    scenario: '企业明标、正式方案、企业资质与服务能力展示。',
    filePath: enterpriseHeaderTemplateUrl,
    font: '宋体',
    fontSize: '正文 11 磅，标题 12–16 磅',
    lineSpacing: '1.5 倍行距',
    headingLevels: '一级、二级、三级标题样式完整',
    structure: '企业页眉、正文、图片表格、绿色页脚、动态页码',
    placeholders: ['文本：{bid_title}、{project_number}、{template_name}', '目录循环：{#chapter_rows}...{/chapter_rows}', '章节循环：{#chapters}...{/chapters}', '图片：{%image}'],
  },
]

export function getBidTemplateList() {
  return BID_TEMPLATE_CONFIGS.map(template => ({ ...template, placeholders: [...template.placeholders] }))
}

export function getBidTemplateById(templateId: BidTemplateId) {
  return BID_TEMPLATE_CONFIGS.find(template => template.id === templateId) || BID_TEMPLATE_CONFIGS[0]
}

export function downloadBidTemplatePreview(template: BidTemplateConfig) {
  if (typeof document === 'undefined') return false
  const anchor = document.createElement('a')
  anchor.href = template.filePath
  anchor.download = `${template.name}.docx`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  return true
}
