# Implementation Plan: 食材配送 AI 投标系统

**Branch**: `001-food-delivery-bid-ai` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-food-delivery-bid-ai/spec.md`

**Related**: [research.md](./research.md)（技术选型与 NEEDS CLARIFICATION 决议）、[data-model.md](./data-model.md)、[contracts/](./contracts/)、[quickstart.md](./quickstart.md)

---

## Summary

为示例配送投标团队构建一套**单企业内网部署的 Web 应用**：投标专员上传招标文件后，系统自动完成「解读 → 大纲 → 图文标书生成 → 智能审核 → 暗标脱敏 → 导出 Word/PDF → 答辩 PPT」全链路，并提供企业知识库（图文素材、版本/过期管理）与历史报价多维分析辅助定价。

技术主线（详见 research.md）：**Python 3.11 + FastAPI + Celery/Redis** 后端；**React 18 + TypeScript + Ant Design 5 + Tiptap + ECharts** 前端；**PostgreSQL 16 + pgvector + MinIO** 存储；**Claude（`claude-opus-4-8` / `claude-sonnet-5`）经 LLMGateway 抽象**做抽取/生成/审核；文档处理走 **PyMuPDF/pdfplumber/PaddleOCR/python-docx/docxtpl/python-pptx/Pillow/LibreOffice/Graphviz** 组合管线。除 Claude API 外全部可离线运行。核心约束：AI 产出均为初稿需人工确认、不联网抓取、不伪造素材、暗标残留以致命级阻断导出。

---

## Technical Context

**Language/Version**: 后端 Python 3.11；前端 TypeScript 5 / Node 20

**Primary Dependencies**:
- 后端：FastAPI, Celery 5, SQLAlchemy 2 + Alembic, pydantic v2, anthropic SDK
- 文档：PyMuPDF(fitz), pdfplumber, python-docx, docxtpl, python-pptx, Pillow, PaddleOCR, LibreOffice(headless), Graphviz, matplotlib, pandas
- 前端：React 18, Ant Design 5, Vite, Tiptap(ProseMirror), ECharts, TanStack Query

**Storage**: PostgreSQL 16 + pgvector（结构化 + 向量同库）；MinIO（S3 兼容对象存储：原始文件/图片/导出物）；Redis（Celery broker + 缓存）

**Testing**: pytest（后端单元/集成/契约）、Vitest + React Testing Library（前端）、Playwright（E2E 关键流程）、验收样本集回归（10 份真实招标文件 + 人工标注）

**Target Platform**: Linux 服务器（Docker Compose 一体化内网部署）；浏览器端使用，移动端只读

**Project Type**: Web application（backend + frontend + worker）

**Performance Goals**（源自 SC/FR）：≤300 页招标文件解读 ≤10 分钟；知识库千级检索 ≤3 秒；100 页标书导出 ≤2 分钟；PPT 生成 ≤5 分钟；图片 DPI ≥150

**Constraints**: 同时在线 ≤20 人、并发长任务 ≤5；简体中文；除 Claude API 外可离线；长任务须断点续跑；暗标残留未确认时阻断导出

**Scale/Scope**: 单企业、5 类角色、千级知识条目、10 大用户故事 / 41 条 FR

---

## Constitution Check

*GATE: 必须在 Phase 0 前通过，Phase 1 后复检。*

项目 `.specify/memory/constitution.md` 仍为未填充模板（无已批准的具体原则），故**无可执行的硬性门禁条款**。采用以下与 spec 一致的自约束原则替代，作为设计守则：

| 守则 | 说明 | 状态 |
|------|------|------|
| 真实素材唯一事实源 | 数据/承诺/资质内容只能来自知识库真实条目，AI 不得凭空编造，缺素材报缺口 | ✅ 贯穿生成/审核链路 |
| 人工终审不可绕过 | 所有产出为初稿；无「免审直接递交」路径 | ✅ 无自动递交 |
| 可追溯 | 生成段落/图片可溯源到知识条目；敏感操作全审计 | ✅ 见 FR-016/039/040 |
| 离线优先 | 除 Claude API 外全部内网可运行，供应商经抽象层可替换 | ✅ LLMGateway/EmbeddingProvider/MapProvider |
| 边界克制 | 不对接招投标平台、不爬取、不签章、不做 ERP/SaaS/多租户 | ✅ 见 Out of Scope |

**结论**：Gate PASS（无违规项，Complexity Tracking 留空）。建议后续用 `/speckit-constitution` 正式固化上述原则。

---

## 模块划分（Module Breakdown）

系统按「投标项目」为工作台组织。后端划分为 9 个业务模块 + 4 个平台能力层；★ 为用户重点关注模块。

### 平台能力层（横切）

| 层 | 职责 | 关键组件 |
|----|------|----------|
| **LLM 网关** | 统一封装 Claude 调用：模型路由（opus-4-8/sonnet-5）、超时、重试、成本记账、提示词模板 | `LLMGateway`, `PromptRegistry` |
| **检索层** | 向量嵌入（BGE-M3 经 `EmbeddingProvider`）+ pgvector 语义/全文混合检索 | `RetrievalService` |
| **任务编排** | Celery 章节级任务、进度、暂停/续跑/单节重生成、断点检查点 | `JobOrchestrator`, `Checkpoint` |
| **存储/安全** | 对象存储（MinIO）、RBAC、审计日志、版本历史 | `ObjectStore`, `AuthZ`, `AuditLog` |

### 业务模块

| # | 模块 | 覆盖 FR / US | 说明 |
|---|------|-------------|------|
| M1 ★ | **招标文件解析** (`tender_parser`) | FR-001~005, US1 | 多格式解析 + OCR + 表格规整 + 资格/评分/废标/政策抽取，产出「文档结构树 + 页码锚点」 |
| M2 | **大纲生成** (`outline`) | FR-006~008, US1 | 资格比对（满足/不满足/需补充）、评分点驱动多级大纲、大纲模板 |
| M3 ★ | **知识库** (`knowledge`) | FR-009~016, US2/US10 | 批量入库、元数据、语义检索、一键复用、**版本替代与过期管理**、到期提醒、溯源 |
| M4 ★ | **标书渲染** (`proposal_render`) | FR-017~024, US3/US9 | 逐节生成正文、智能插图、**图片标注（红框+说明框+题注）**、证书题注表格、自动表格、流程图/思维导图、网点/路线示意图、导出 Word/PDF |
| M5 ★ | **审核与优化** (`review`) | FR-025~029, US4/US5 | 智能审核报告（致命/重要/提示 + 定位 + 依据 + 建议）、无损内容提升、关键信息标注、佐证截图归档匹配 |
| M6 ★ | **暗标脱敏** (`redaction`) | FR-030/031, US6 | 文本层脱敏词典替换 + 图片层 OCR 脱敏 + 残留扫描 + **致命级阻断导出** |
| M7 ★ | **报价分析** (`quotation`) | FR-032~034, US8 | 导入校验、多维筛选统计、参考区间 + 依据 + 免责声明 |
| M8 ★ | **PPT 导出** (`presentation`) | FR-035/036, US7 | 依定稿标书生成 15–25 页答辩 PPT、模板风格、标书↔PPT 引用映射与冲突提示同步 |
| M9 | **项目/权限/交付** (`project`, `iam`) | FR-037~041 | 项目工作台、版本回滚、RBAC、审计、试用期评估记录 |

### 重点模块设计要点

**M1 招标文件解析**：管线式 `ParserPipeline` —— 解包(zip)→格式分发→文本层(PyMuPDF)/表格(pdfplumber)/OCR(PaddleOCR)→结构树归一化→LLM 抽取器（`QualExtractor`/`ScoringExtractor`/`DisqualExtractor`/`PolicyExtractor`）。每条抽取结果携带 `page_anchor`（页码 + 坐标）供原文跳转。评分表跨页/合并单元格通过 pdfplumber 单元格网格重建，保证评分项-分值不错行；同一评分点多处表述在抽取后做去重合并。非招标文件由分类校验器识别并提示。

**M3 知识库版本/过期管理**：`KnowledgeItem` 具 `status(active/expired)`、`valid_until`、`superseded_by` 字段。上传新版指定替代 → 旧版置 `expired` 并写 `superseded_by`；每日 `expiry_scan` 定时任务按 `valid_until` 自动过期并触发 T-30 天提醒。检索层默认 `WHERE status='active'`，从机制上保证生成引用过期资料处数为 0（SC-008）。

**M4 标书渲染**：`SectionRenderer` 逐节流水——LLM 生成正文（含所响应评分项编号）→ `IllustrationMatcher`（语义检索匹配真实图片，无匹配则插占位提示，绝不虚构）→ `AnnotationComposer`（Pillow 在图上绘红框、合成右侧说明框、下方题注）→ `TableSynthesizer`（对比/进度/配置表）→ `DiagramGen`（Graphviz 流程图/思维导图）。`DocxRenderer`（python-docx + docxtpl 母版）落地样式、目录域、页眉页脚、图表编号；`PdfExporter` 用 LibreOffice headless 保真转换。证书题注表格用 docx 表格（N 列图 + 下方题注行，N 可配置）实现样例截图版式。

**M5 审核**：`AuditEngine` 组合规则检查（格式：字体/字号/行距/页码/目录/签章位/密封，按项目级配置）+ LLM 语义检查（废标规避、评分点覆盖、资质齐全）。每条 `AuditFinding` 含严重级别、定位（章节/页码）、原文依据、修改建议，支持跳转与复审消解。无损提升在 Tiptap/ProseMirror 文档模型上只改正文节点文本、冻结样式与标题结构，输出修订对照。

**M6 暗标**：`Redactor` 两级——文本遍历（正文/页眉页脚/图注/表格）按脱敏词典（词/正则/关联词）替换；图片经 PaddleOCR 检出内嵌文字，命中则遮盖/替换/剔除并逐张出报告。导出前 `ResidualScanner` 全量扫描（含图片内文字），存在未确认残留则以致命级 `block_export`（SC-005 处数为 0）。

**M8 PPT 同步**：`PptBuilder` 从定稿标书结构化摘要生成页面，每页记录 `source_section_id`；标书更新后 `sync` 比对，未被用户改动的页刷新、`user_edited=true` 的页给冲突提示而非静默覆盖（FR-036）。

---

## 数据模型（Data Model）概要

完整字段、关系、状态机见 [data-model.md](./data-model.md)。核心实体与关系：

```
User ──* Role                        BidProject 1──* TenderDocument
BidProject 1──1 Outline 1──* OutlineNode 1──1 ProposalSection
TenderDocument 1──* QualificationRequirement / ScoringItem / DisqualificationClause
ScoringItem *──* OutlineNode（评分点↔响应节点，覆盖率校验）
ProposalSection 1──* ImageAnnotation / TableBlock / Diagram / SourceRef
KnowledgeItem 1──* (embedding, tags)  KnowledgeItem 0..1──superseded_by──> KnowledgeItem
BidProject 1──* AuditFinding / RedactionRule / Presentation
QuotationRecord（独立分析域）        AuditLog（全局横切）
```

关键状态机：
- **TenderDocument.parse_status**: `uploaded → parsing → parsed | failed`（failed 带可读原因：加密/损坏/超限）
- **OutlineNode.gen_status / ProposalSection**: `pending → generating → generated → reviewed`（支持 pause/resume/regenerate 单节）
- **KnowledgeItem.status**: `active → expired`（版本替代或到期触发，不可逆除非人工恢复）
- **AuditFinding.status**: `open → fixed | ignored`
- **Presentation.sync_state**: `in_sync | stale | conflict`
- **Export gate**: 暗标 `residual_confirmed=false` → 阻断

---

## 关键流程（Key Flows）

### 流程 1：招标文件解读（US1，SC-001/002）
```
上传(可含zip/扫描件) → 解包&格式分发 → [文本层|表格|OCR]并行解析
  → 结构树+页码锚点 → Celery 抽取任务(资格/评分/废标/政策)
  → 与知识库资质比对(满足/不满足/需补充) → 前端展示可跳转原文
  → 生成多级大纲(末级节点关联评分项, 未覆盖项显式列出) → 用户增删拖排/存模板
```

### 流程 2：图文标书生成（US3，SC-003/007）
```
确认大纲 → JobOrchestrator 按章节派发 Celery 任务(逐节, 每节完成即 checkpoint)
  循环每节: LLM 生成正文 → 智能插图(语义检索真实图片/无则占位)
           → 图片标注(红框+说明框+题注) → 自动表格 → 流程图/思维导图
  → 全书组装(样式/目录/页眉页脚/编号) → 导出 .docx (LibreOffice→.pdf)
  中断: 从未完成章节续跑, 已生成章节不丢
```

### 流程 3：智能审核（US4，SC-004）
```
选定标书+招标文件 → 规则检查(格式,项目级配置)+LLM检查(废标/评分覆盖/资质)
  → AuditFinding 清单(级别+定位+原文依据+建议) → 点击跳转修复 → 复审消解
```

### 流程 4：暗标生成（US6，SC-005）
```
正常标书 → 文本脱敏(词典/正则, 全区域) → 图片OCR脱敏(遮盖/替换/剔除+逐张报告)
  → 残留扫描(含图片内文字) → 有残留? 致命级阻断导出 : 允许导出脱敏版
```

### 流程 5：报价分析与定价辅助（US8）
```
导入 Excel/CSV(字段映射+校验报告) → 存 PG → 多维筛选(时间/区域/平台/业态)
  → 统计(均价/极值/中标区间/样本数, <5 标注样本不足) → 参考区间+依据清单+免责声明
```

### 流程 6：答辩 PPT 生成与同步（US7，SC-006）
```
定稿标书 → LLM 抽取要点+复用标书图表 → python-pptx 按模板生成 15–25 页
  → 每页记 source_section_id → 标书更新后 sync: 未改页刷新 / user_edited 页冲突提示
```

---

## 外部依赖（External Dependencies）

| 类别 | 依赖 | 用途 | 联网 |
|------|------|------|------|
| LLM | Anthropic Claude API（`claude-opus-4-8` / `claude-sonnet-5`） | 抽取/生成/审核/脱敏推理 | 是（唯一必需外呼，经 `LLMGateway` 可切私有化端点） |
| 嵌入 | BGE-M3（本地服务，1024 维） | 语义检索向量 | 否 |
| OCR | PaddleOCR | 扫描件解析、图片内文字检测 | 否 |
| 文档解析 | PyMuPDF, pdfplumber | PDF 文本/表格 | 否 |
| 文档渲染 | python-docx, docxtpl, python-pptx, Pillow | Word/PPT/图片标注 | 否 |
| 转换 | LibreOffice headless | DOC→DOCX、DOCX→PDF | 否 |
| 图形 | Graphviz, matplotlib, ECharts(前端) | 流程图/思维导图/网点图/报价图表 | 否 |
| 数据/中间件 | PostgreSQL 16 + pgvector, MinIO, Redis | 数据+向量/对象存储/队列 | 否（内网） |
| 运行时 | Docker Compose | 一体化部署 | 否 |
| 扩展点（预留） | `MapProvider`（真实地图底图/路线）、`EmbeddingProvider`、`LLMGateway` | 后续增强可插拔 | 视实现 |

> 除 Claude API 外全部可离线运行；完全离线时 `LLMGateway` 切换到私有化模型端点。

---

## Project Structure

### Documentation (this feature)
```text
specs/001-food-delivery-bid-ai/
├── plan.md              # 本文件
├── research.md          # 选型 + NEEDS CLARIFICATION 决议
├── data-model.md        # 实体/字段/状态机
├── quickstart.md        # 部署与验收运行指南
├── contracts/           # API 契约
│   └── openapi.yaml
├── checklists/          # 质量检查单
└── tasks.md             # 由 /speckit-tasks 生成（本命令不产出）
```

### Source Code (repository root)
```text
backend/
├── app/
│   ├── main.py                    # FastAPI 入口
│   ├── core/                      # 配置、日志、鉴权(JWT/RBAC)、审计
│   ├── platform/                  # 横切能力层
│   │   ├── llm/                   # LLMGateway, PromptRegistry, 成本记账
│   │   ├── embedding/             # EmbeddingProvider(BGE-M3)
│   │   ├── retrieval/             # pgvector 混合检索
│   │   ├── storage/               # MinIO ObjectStore
│   │   └── jobs/                  # Celery, JobOrchestrator, Checkpoint
│   ├── modules/
│   │   ├── tender_parser/         # M1 解析管线 + 抽取器
│   │   ├── outline/               # M2 大纲/资格比对/模板
│   │   ├── knowledge/             # M3 入库/检索/版本过期/溯源
│   │   ├── proposal_render/       # M4 渲染
│   │   │   ├── section_renderer.py
│   │   │   ├── illustration.py    # 智能插图匹配
│   │   │   ├── annotation.py      # 红框+说明框+题注(Pillow)
│   │   │   ├── cert_table.py      # 证书题注表格
│   │   │   ├── table_synth.py     # 对比/进度/配置表
│   │   │   ├── diagram.py         # Graphviz 流程图/思维导图/网点图
│   │   │   ├── docx_renderer.py   # python-docx + docxtpl
│   │   │   └── pdf_exporter.py    # LibreOffice
│   │   ├── review/                # M5 审核/无损提升/关键信息/佐证
│   │   ├── redaction/             # M6 脱敏词典/图片脱敏/残留扫描/阻断
│   │   ├── quotation/             # M7 导入/多维分析/参考区间
│   │   ├── presentation/          # M8 python-pptx/引用映射/同步
│   │   ├── project/               # M9 项目工作台/版本
│   │   └── iam/                   # 用户/角色/审计
│   ├── models/                    # SQLAlchemy 实体
│   ├── schemas/                   # pydantic DTO
│   └── api/                       # 路由聚合(见 contracts/openapi.yaml)
├── alembic/                       # 迁移
├── tests/ {unit,integration,contract,acceptance}
└── pyproject.toml

frontend/
├── src/
│   ├── pages/                     # 项目工作台/解读/大纲/编辑器/审核/暗标/报价/PPT
│   ├── components/                # 大纲树(拖拽)、Tiptap 编辑器、图片标注器、图表(ECharts)
│   ├── features/                  # 按模块的状态与 API 封装
│   ├── api/                       # TanStack Query 客户端
│   └── main.tsx
├── tests/                         # Vitest + RTL
└── package.json

e2e/                               # Playwright 关键流程
deploy/
├── docker-compose.yml             # backend/worker/frontend/pg/minio/redis/libreoffice
└── env/                           # 环境变量样例
fixtures/acceptance/               # 10 份招标文件 + 人工标注(验收样本集)
```

**Structure Decision**: 采用 Web application 三段式（`backend` + `frontend` + `worker`，worker 与 backend 同镜像不同入口）。后端以「平台能力层 + 业务模块」分层：横切能力（LLM/检索/存储/任务）沉淀到 `platform/`，9 个业务模块在 `modules/` 内高内聚，模块间通过 service 接口与领域事件解耦，便于按用户故事独立交付与测试。

---

## Complexity Tracking

> 无 Constitution 违规项，无需填写。
