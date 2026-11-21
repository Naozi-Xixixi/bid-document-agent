# Data Model: 食材配送 AI 投标系统

**Feature**: `001-food-delivery-bid-ai` | **Date**: 2026-08-24 | 源: [spec.md](./spec.md) Key Entities + FR

存储：PostgreSQL 16（结构化）+ pgvector（`KnowledgeItem.embedding`）+ MinIO（`*_object_key` 指向对象）。所有实体含 `id (uuid)`、`created_at`、`updated_at`、`created_by`；受控实体含 `project_id` 用于 RBAC 行级隔离。

---

## 1. 项目与用户域

### BidProject（投标项目）
| 字段 | 类型 | 约束/说明 |
|------|------|-----------|
| name | text | 必填 |
| purchaser | text | 采购人 |
| industry | enum | 学校/医院/企业/机关/超市/其他 |
| region | text | |
| deadline | timestamptz | 投标截止时间 |
| status | enum | draft/parsing/drafting/reviewing/finalized/archived |

关系：1─* TenderDocument, 1─1 Outline, 1─* AuditFinding / Presentation / RedactionRule。

### User / Role
- **User**: username, password_hash, display_name, active。
- **Role**: enum `bid_specialist`(投标专员) / `bid_manager`(投标经理·审核) / `kb_admin`(知识库管理员) / `pricing`(商务定价) / `sys_admin`(系统管理员)。
- User *─* Role；RBAC 决定模块操作权限（FR-013/039/040）。

### AuditLog（审计日志，横切）
actor_id, action(login/upload/export/delete/redact_confirm/…), target_type, target_id, ip, detail(jsonb), at。可按用户/时间/操作类型检索（FR-039）。

---

## 2. 招标解读域（M1/M2）

### TenderDocument（招标文件）
file_object_key, format(pdf/docx/doc/image/zip), pages, is_ocr(bool), **parse_status**(uploaded/parsing/parsed/failed), fail_reason(加密/损坏/超限)。1─* 下列抽取实体。

### QualificationRequirement（资格要求）FR-002/006
description, category(营业执照/许可/体系认证/业绩/财务/人员/设备), **page_anchor**(page+bbox), **match_result**(satisfied/unsatisfied/need_supplement), gap_note, matched_knowledge_ids[]。

### ScoringItem（评分项）FR-003/007
code, name, score(numeric), criteria(text), page_anchor, responded_by_node_ids[]（*─* OutlineNode，用于覆盖率校验）。

### DisqualificationClause（废标条款）FR-004
description, page_anchor, avoidance_advice, avoidance_status(open/handled)。

### PolicyHint（政策提示）FR-005（R-1：仅来源于上传文件/知识库政策条目）
description, source_ref(必填：文件名+条款)。无 source 不落库。

---

## 3. 大纲与正文域（M4）

### Outline / OutlineNode
Outline: project_id, template_ref(可另存模板)。
OutlineNode: parent_id, level, title, order, scoring_item_codes[](末级关联评分项), **gen_status**(pending/generating/generated/reviewed)。未被任何节点覆盖的评分项由校验查询显式列出（FR-007）。

### ProposalSection（标书章节）
node_id(1─1), body(prosemirror json), export_html。1─* ImageAnnotation / TableBlock / Diagram / SourceRef。

### ImageAnnotation（图片标注）FR-019/020
image_object_key, **highlight_boxes**(jsonb: [{x,y,w,h,color}]), callout(jsonb: {title,body}), caption(题注), layout(right_box/below_caption/cert_grid), cert_grid_cols(N 可配置)。

### TableBlock（表格块）FR-021
kind(compare/schedule/config), header[], rows[][], style_template, source_paragraph_ref。

### Diagram（图示）FR-022/023
kind(flow/mindmap/site_map/route_map), dot_or_spec(text), rendered_object_key(png/svg), legend。

### SourceRef（引用溯源）FR-016
section_id, knowledge_item_id, snippet, file_name。任一生成段落/图片可回溯。

---

## 4. 知识库域（M3）

### KnowledgeItem（知识条目）FR-009~016
| 字段 | 类型 | 说明 |
|------|------|------|
| type | enum | cert/scene_image/proposal/case/resume/equipment/quotation/policy |
| title | text | |
| object_key | text | 文件/图片 |
| industry, region, year, project_type | | 元数据 |
| tags | text[] | |
| **valid_until** | date | 证书类必填（保存时校验，FR-010/015） |
| **status** | enum | active/expired（默认检索仅 active，FR-014） |
| **superseded_by** | uuid | 指向新版条目（版本替代，US10） |
| version | int | |
| embedding | vector(1024) | pgvector 语义检索（BGE-M3） |

**状态机**：`active → expired`（上传新版指定替代 → 旧版 expired 并写 superseded_by；或 valid_until 到期由 `expiry_scan` 自动过期）。T-30 天进提醒列表（FR-015）。规则：生成引用过期条目处数必须为 0（SC-008）——检索层强制 `status='active'`。

---

## 5. 审核 / 优化 / 暗标域（M5/M6）

### AuditFinding（审核问题）FR-025/026
category(资质/废标/评分覆盖/格式), **severity**(fatal/important/hint), description, location(章节+页码), evidence(招标文件原文引用), suggestion, **status**(open/fixed/ignored)。复审后 fixed 项不再出现。

### RedactionRule（脱敏规则）FR-030
pattern(词/正则), replacement, scope(body/caption/header_footer/image_text), enabled。初始词典：示例公司及关联词（全称/简称/品牌/母公司/网址/商标/人名）。

### RedactionReport / ResidualScan（暗标报告）FR-031
per-image 处理方式列表；residual_findings[]（含图片内文字）;  **residual_confirmed**(bool)。`residual_confirmed=false` → 导出被阻断（fatal，SC-005）。

### EvidenceShot（佐证截图）FR-029（R-3：用户上传，系统归档匹配）
object_key, kind(credit/invoice_tax/cert_query), linked_entity_type, linked_entity_id, query_date, caption。

---

## 6. 报价分析域（M7）

### QuotationRecord（报价记录）FR-032
project_name, date, region, platform, industry, category(品类), quote(numeric), award_price(numeric,可空), won(bool,可空)。导入经字段映射 + 校验报告（成功/失败条数+原因）。

分析（FR-033/034）：多维筛选聚合视图输出均价/最高/最低/中标价区间/样本数；样本数<5 标注「样本不足，仅供参考」；参考区间附依据项目清单 + 免责声明（非持久化，查询时计算）。

---

## 7. 演示域（M8）

### Presentation（答辩 PPT）FR-035/036
project_id, template_style, **sync_state**(in_sync/stale/conflict), pptx_object_key。1─* PresentationSlide。

### PresentationSlide
order, layout, content(jsonb), **source_section_id**(引用来源), **user_edited**(bool)。同步时未改页刷新，user_edited 页给冲突提示而非覆盖。

---

## 关系总览（ER 摘要）

```
User *──* Role                         AuditLog(全局)
BidProject 1──* TenderDocument 1──* {Qualification|Scoring|Disqual|Policy}
ScoringItem *──* OutlineNode           BidProject 1──1 Outline 1──* OutlineNode 1──1 ProposalSection
ProposalSection 1──* {ImageAnnotation|TableBlock|Diagram|SourceRef}
KnowledgeItem 0..1 ──superseded_by──> KnowledgeItem   SourceRef *──1 KnowledgeItem
BidProject 1──* {AuditFinding|RedactionRule|EvidenceShot|Presentation}
Presentation 1──* PresentationSlide *──1 ProposalSection
QuotationRecord（独立分析域，按元数据聚合）
```
