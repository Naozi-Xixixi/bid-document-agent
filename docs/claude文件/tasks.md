---
description: "Task list for 食材配送 AI 投标系统 implementation"
---

# Tasks: 食材配送 AI 投标系统（Food Delivery Bidding AI System）

**Input**: Design documents from `specs/001-food-delivery-bid-ai/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/openapi.yaml](./contracts/openapi.yaml), [quickstart.md](./quickstart.md)

**Tests**: 本 spec 以「验收样本集 + Success Criteria」为验收基准（research R-16），故每个用户故事包含**验收/E2E 测试任务**（非逐接口 TDD），用于回归量化指标。

**Organization**: 任务按用户故事分组，每个故事可独立实现与验收。P1 = US1–US4，P2 = US5–US10。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事（US1…US10）
- 路径约定（源 plan.md）：后端 `backend/app/…`，前端 `frontend/src/…`，E2E `e2e/`，部署 `deploy/`，验收夹具 `fixtures/acceptance/`

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 工程初始化与骨架

- [ ] T001 创建 monorepo 目录结构（`backend/`、`frontend/`、`e2e/`、`deploy/`、`fixtures/acceptance/`）per plan.md
- [ ] T002 初始化后端工程 `backend/pyproject.toml`：Python 3.11 + FastAPI, Celery 5, SQLAlchemy 2, Alembic, pydantic v2, anthropic, PyMuPDF, pdfplumber, python-docx, docxtpl, python-pptx, Pillow, paddleocr, pandas, graphviz, matplotlib, psycopg[binary], pgvector, minio, redis
- [ ] T003 [P] 初始化前端工程 `frontend/package.json`：Vite + React 18 + TypeScript + Ant Design 5 + Tiptap + ECharts + TanStack Query
- [ ] T004 [P] 编写 `deploy/docker-compose.yml`：pg16+pgvector、minio、redis、libreoffice(headless)、backend、worker、frontend；`deploy/env/.env.example`
- [ ] T005 [P] 配置 lint/format：`backend/` ruff+black、`frontend/` eslint+prettier；`.editorconfig`
- [ ] T006 [P] 配置管理 `backend/app/core/config.py`（pydantic-settings 读取 `ANTHROPIC_API_KEY`、DB/MinIO/Redis 凭据）

---

## Phase 2: Foundational（阻塞性前置，所有故事依赖）

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事均不可开工

- [ ] T007 建立 Alembic 迁移框架 `backend/alembic/`；启用 `CREATE EXTENSION vector`
- [ ] T008 基础模型与 Mixin `backend/app/models/base.py`（uuid 主键、created_at/updated_at、created_by、project_id 行级隔离）
- [ ] T009 [P] 对象存储层 `backend/app/platform/storage/object_store.py`（MinIO 上传/下载/预签名）
- [ ] T010 [P] LLM 网关 `backend/app/platform/llm/gateway.py`（Claude 路由 `claude-opus-4-8`/`claude-sonnet-5`、超时/重试/成本记账）+ `prompt_registry.py`
- [ ] T011 [P] 嵌入 Provider `backend/app/platform/embedding/bge_m3.py`（1024 维，`EmbeddingProvider` 抽象）
- [ ] T012 检索服务 `backend/app/platform/retrieval/service.py`（pgvector 语义 + 全文混合，默认 `status='active'`）依赖 T008、T011
- [ ] T013 [P] 任务编排 `backend/app/platform/jobs/`（Celery app、`orchestrator.py`、`checkpoint.py`、`GET/pause/resume /jobs/{id}`）
- [ ] T014 [P] 鉴权与 RBAC `backend/app/core/auth.py` + `backend/app/models/user.py`、`role.py`（5 角色）+ `POST /auth/login`
- [ ] T015 [P] 审计日志 `backend/app/models/audit_log.py` + `backend/app/core/audit.py`（中间件，敏感操作留痕）
- [ ] T016 API 路由与全局错误处理 `backend/app/main.py`、`backend/app/api/__init__.py`
- [ ] T017 [P] 初始化脚本 `backend/app/scripts/seed_roles.py`（5 角色 + 管理员）
- [ ] T018 [P] 前端骨架 `frontend/src/`：路由、`components/Layout`、`api/client.ts`（TanStack Query）、登录态
- [ ] T019 项目工作台 `backend/app/modules/project/`（`BidProject` 模型、`POST/GET /projects`、`/projects/{id}`、版本历史 `/versions`）依赖 T008

**Checkpoint**: 基础就绪 —— 用户故事可并行开工

---

## Phase 3: User Story 1 - 招标文件解读并生成大纲 (Priority: P1) 🎯 MVP

**Goal**: 上传招标文件 → 10 分钟内输出资质门槛/评分表/废标清单（可跳原文）+ 资格比对 + 多级大纲

**Independent Test**: 上传一份真实招标文件，输出结论与人工阅读一致并导出大纲（SC-001/002, FR-001~008）

### Tests for US1

- [ ] T020 [P] [US1] 验收测试 `backend/tests/acceptance/test_us1_parsing.py`：基于 `fixtures/acceptance/` 校验评分项/废标召回率 ≥95%、page_anchor 可定位
- [ ] T021 [P] [US1] E2E `e2e/us1_tender.spec.ts`：上传→解读→大纲→编辑保存

### Implementation for US1

- [ ] T022 [P] [US1] 模型 `backend/app/models/tender.py`：TenderDocument、QualificationRequirement、ScoringItem、DisqualificationClause、PolicyHint（含 page_anchor）
- [ ] T023 [P] [US1] 模型 `backend/app/models/outline.py`：Outline、OutlineNode（scoring_item_codes、gen_status）
- [ ] T024 [US1] 解析管线 `backend/app/modules/tender_parser/pipeline.py`：zip 解包 + 格式分发 + 非招标文件识别（Edge Case）
- [ ] T025 [P] [US1] 文本/表格解析 `backend/app/modules/tender_parser/extract_text.py`（PyMuPDF）、`extract_tables.py`（pdfplumber，跨页/合并单元格网格重建）
- [ ] T026 [P] [US1] OCR `backend/app/modules/tender_parser/ocr.py`（PaddleOCR，无文字层触发，准确率≥95%）
- [ ] T027 [US1] 结构树归一化 `backend/app/modules/tender_parser/structure_tree.py`（章节 + 页码锚点）依赖 T024–T026
- [ ] T028 [US1] LLM 抽取器 `backend/app/modules/tender_parser/extractors.py`：资格/评分/废标/政策（政策必带来源，FR-005/R-1）依赖 T010、T027
- [ ] T029 [US1] 资格比对服务 `backend/app/modules/tender_parser/qualification_match.py`（满足/不满足/需补充，FR-006）依赖 T012
- [ ] T030 [US1] 大纲生成 `backend/app/modules/outline/generator.py`（末级关联评分项、未覆盖项显式列出，FR-007）
- [ ] T031 [US1] 大纲编辑与模板 `backend/app/modules/outline/service.py`（增删/重命名/拖排、另存模板，FR-008）
- [ ] T032 [US1] 路由 `backend/app/api/tender.py`、`backend/app/api/outline.py`（对齐 contracts：上传解读、parse-result、qualification-match、outline、outline-templates）依赖 T028–T031
- [ ] T033 [US1] 解读为 Celery 任务接入编排（进度/断点）依赖 T013、T028
- [ ] T034 [P] [US1] 前端页面 `frontend/src/pages/TenderReview.tsx`（解读结果 + 原文跳转）
- [ ] T035 [P] [US1] 前端大纲树 `frontend/src/components/OutlineTree.tsx`（拖拽增删、评分项标注、未覆盖告警）

**Checkpoint**: US1 可独立验收 —— 解读 + 大纲成 MVP

---

## Phase 4: User Story 2 - 企业知识库统一入库与检索复用 (Priority: P1)

**Goal**: 批量入库打标签 + 语义/全文检索 + 一键复用到大纲节点（带溯源）

**Independent Test**: 导入 50 份资料，按「学校+冷链」检索命中并复用章节文本与图片（FR-009~013/016）

### Tests for US2

- [ ] T036 [P] [US2] 验收测试 `backend/tests/acceptance/test_us2_knowledge.py`：千级库检索 ≤3s、Top-10 命中率 ≥90%

### Implementation for US2

- [ ] T037 [P] [US2] 模型 `backend/app/models/knowledge.py`：KnowledgeItem（type/元数据/tags/valid_until/status/superseded_by/version/embedding）、SourceRef
- [ ] T038 [US2] 批量入库 `backend/app/modules/knowledge/ingest.py`（≥30 文件、部分失败隔离、证书缺 valid_until 提示，FR-009/010）依赖 T009、T011
- [ ] T039 [US2] 检索服务 `backend/app/modules/knowledge/search.py`（业态/关键词/类型/年份 + 语义，FR-011）依赖 T012
- [ ] T040 [US2] 一键复用 `backend/app/modules/knowledge/reuse.py`（文字+图片插入大纲节点 + SourceRef 溯源，FR-012/016）
- [ ] T041 [US2] CRUD + 角色鉴权 `backend/app/modules/knowledge/service.py`（越权拒绝并记审计，FR-013）依赖 T014、T015
- [ ] T042 [US2] 路由 `backend/app/api/knowledge.py`（knowledge-items CRUD/检索/reuse，对齐 contracts）依赖 T038–T041
- [ ] T043 [P] [US2] 前端 `frontend/src/pages/KnowledgeBase.tsx`（批量上传打标签、检索、复用到节点）

**Checkpoint**: US1 + US2 独立可用；知识库成为生成/审核数据底座

---

## Phase 5: User Story 3 - 图文并茂的标书正文自动生成 (Priority: P1)

**Goal**: 逐节生成正文 + 智能插图 + 图片标注 + 证书题注表格 + 自动表格/流程图 + 导出 Word/PDF

**Independent Test**: 给定大纲与知识库生成可下载 Word（含插图/标注/证书表格/对比表），版式与样例一致（SC-003/007, FR-017~024）

### Tests for US3

- [ ] T044 [P] [US3] 验收测试 `backend/tests/acceptance/test_us3_render.py`：证书题注表格/红框说明框版式正确率 ≥90%、导出图片 DPI≥150、无未替换占位符
- [ ] T045 [P] [US3] E2E `e2e/us3_generate_export.spec.ts`：生成→导出 docx→打开校验

### Implementation for US3

- [ ] T046 [P] [US3] 模型 `backend/app/models/proposal.py`：ProposalSection、ImageAnnotation、TableBlock、Diagram
- [ ] T047 [US3] 章节生成器 `backend/app/modules/proposal_render/section_renderer.py`（逐条响应评分点、标注评分项编号，FR-017）依赖 T010、T023
- [ ] T048 [P] [US3] 智能插图 `backend/app/modules/proposal_render/illustration.py`（语义匹配真实图片、无匹配插占位不虚构，FR-018）依赖 T012
- [ ] T049 [P] [US3] 图片标注 `backend/app/modules/proposal_render/annotation.py`（Pillow 红框 + 右侧说明框 + 题注，FR-019）
- [ ] T050 [P] [US3] 证书题注表格 `backend/app/modules/proposal_render/cert_table.py`（N 列图 + 下方题注行，N 可配置，FR-020）
- [ ] T051 [P] [US3] 自动表格 `backend/app/modules/proposal_render/table_synth.py`（对比/进度/配置，FR-021）
- [ ] T052 [P] [US3] 流程图/思维导图 `backend/app/modules/proposal_render/diagram.py`（Graphviz，DOT 由 LLM 生成，节点与正文对应，FR-022）
- [ ] T053 [US3] Word 渲染 `backend/app/modules/proposal_render/docx_renderer.py`（python-docx + docxtpl 母版：目录/页眉页脚/编号）依赖 T047–T052
- [ ] T054 [US3] PDF 导出 `backend/app/modules/proposal_render/pdf_exporter.py`（LibreOffice headless，保真）依赖 T053
- [ ] T055 [US3] 生成为章节级 Celery 任务 + 章节检查点（暂停/续跑/单节重生成，FR-038）依赖 T013、T047
- [ ] T056 [US3] 章节并发锁 `backend/app/modules/proposal_render/locking.py`（乐观锁 + 章节级行锁，多人编辑 Edge Case）
- [ ] T057 [US3] 路由 `backend/app/api/proposal.py`（generate/sections/regenerate/annotations/export，对齐 contracts）依赖 T053–T056
- [ ] T058 [P] [US3] 前端编辑器 `frontend/src/pages/ProposalEditor.tsx`（Tiptap 正文 + 生成进度 + 单节重生成）
- [ ] T059 [P] [US3] 前端图片标注器 `frontend/src/components/ImageAnnotator.tsx`（红框拖拽 + 说明框/题注编辑）

**Checkpoint**: US1–US3 独立可用 —— 核心产出物（图文 Word 标书）成立

---

## Phase 6: User Story 4 - 标书智能审核与废标风险拦截 (Priority: P1)

**Goal**: 检查资质/废标/评分覆盖/格式，输出带级别的问题清单可跳转修复

**Independent Test**: 注入 20 处缺陷的标书 → 缺失资质/未响应评分点检出率 100%、误报 ≤20%（SC-004, FR-025/026）

### Tests for US4

- [ ] T060 [P] [US4] 验收测试 `backend/tests/acceptance/test_us4_review.py`：20 缺陷检出率与误报率断言

### Implementation for US4

- [ ] T061 [P] [US4] 模型 `backend/app/models/review.py`：AuditFinding（severity/location/evidence/suggestion/status）
- [ ] T062 [US4] 规则检查 `backend/app/modules/review/format_rules.py`（字体/字号/行距/页码/目录/签章位/密封，项目级配置，Edge Case）
- [ ] T063 [US4] 语义检查 `backend/app/modules/review/semantic_audit.py`（废标规避、评分覆盖、资质齐全，引用原文依据）依赖 T010
- [ ] T064 [US4] 审核引擎聚合 `backend/app/modules/review/engine.py`（合并 finding、级别排序、复审消解）依赖 T062、T063
- [ ] T065 [US4] 路由 `backend/app/api/review.py`（`POST /projects/{id}/review`、`PATCH /audit-findings/{id}`）依赖 T064
- [ ] T066 [P] [US4] 前端 `frontend/src/pages/AuditReport.tsx`（问题清单 + 跳转定位 + 状态更新）

**Checkpoint**: 全部 P1（US1–US4）完成 —— 首个完整可交付版本（解读→大纲→生成→审核→导出）

---

## Phase 7: User Story 10 - 知识库版本更新与过期清理 (Priority: P2)

**Goal**: 新版替代旧版自动过期 + 证书到期自动过期 + T-30 提醒 + 过期不参与检索/生成

**Independent Test**: 上传新版营业执照替代旧版，生成标书引用新版且旧版不出现在检索（SC-008, FR-014/015）

> 置于 P2 之首：其余 P2 内容生成类故事均受益于「过期不误用」机制。

- [ ] T067 [P] [US10] 验收测试 `backend/tests/acceptance/test_us10_versioning.py`：过期引用处数为 0、T-30 提醒命中
- [ ] T068 [US10] 版本替代 `backend/app/modules/knowledge/versioning.py`（`POST /supersede`：旧版置 expired + superseded_by，FR-014）依赖 T037
- [ ] T069 [US10] 到期扫描定时任务 `backend/app/modules/knowledge/expiry_scan.py`（Celery beat：valid_until 自动过期 + T-30 提醒，FR-015）依赖 T013
- [ ] T070 [US10] 到期提醒接口 `backend/app/api/knowledge.py` 扩展 `GET /knowledge-items/expiring`
- [ ] T071 [P] [US10] 前端 `frontend/src/pages/KnowledgeBase.tsx` 扩展：过期标记、到期提醒面板、替代关系操作

**Checkpoint**: 知识库正确性机制生效，后续生成引用零过期

---

## Phase 8: User Story 6 - 暗标（脱敏标书）生成 (Priority: P2)

**Goal**: 文本 + 图片脱敏 + 残留扫描 + 未确认残留致命级阻断导出

**Independent Test**: 对正常标书生成暗标，全文与图片检索企业名称命中数为 0（SC-005, FR-030/031）

### Tests for US6

- [ ] T072 [P] [US6] 验收测试 `backend/tests/acceptance/test_us6_redaction.py`：10 处隐藏标识（3 处图片内）检出率 100%、命中数 0、导出阻断
- [ ] T073 [P] [US6] E2E `e2e/us6_redaction_block.spec.ts`：生成→残留扫描→阻断→confirm→放行

### Implementation for US6

- [ ] T074 [P] [US6] 模型 `backend/app/models/redaction.py`：RedactionRule、RedactionReport、ResidualScan（residual_confirmed）
- [ ] T075 [US6] 文本脱敏 `backend/app/modules/redaction/text_redactor.py`（词典/正则/关联词遍历正文/页眉页脚/图注/表格，FR-030）
- [ ] T076 [US6] 图片脱敏 `backend/app/modules/redaction/image_redactor.py`（PaddleOCR 检测图片内文字 + 遮盖/替换/剔除 + 逐张报告）依赖 T026
- [ ] T077 [US6] 残留扫描与阻断 `backend/app/modules/redaction/residual_scan.py`（含图片内文字；未确认残留 block_export，FR-031）依赖 T075、T076
- [ ] T078 [US6] 路由 `backend/app/api/redaction.py`（rules、generate、residual-scan、confirm）依赖 T075–T077
- [ ] T079 [P] [US6] 前端 `frontend/src/pages/Redaction.tsx`（脱敏词典维护、逐张处理确认、残留报告、导出阻断提示）

**Checkpoint**: 暗标可独立验收，导出前强制零残留

---

## Phase 9: User Story 7 - 述标/答辩 PPT 自动生成 (Priority: P2)

**Goal**: 依定稿标书生成 15–25 页 PPT + 模板风格 + 标书↔PPT 同步（冲突提示）

**Independent Test**: 基于定稿标书生成 .pptx，PowerPoint 打开正常、内容与标书一致（SC-006, FR-035/036）

### Tests for US7

- [ ] T080 [P] [US7] 验收测试 `backend/tests/acceptance/test_us7_ppt.py`：≤5 分钟、15–25 页、数据一致、同步冲突提示

### Implementation for US7

- [ ] T081 [P] [US7] 模型 `backend/app/models/presentation.py`：Presentation（sync_state）、PresentationSlide（source_section_id/user_edited）
- [ ] T082 [US7] PPT 生成 `backend/app/modules/presentation/builder.py`（python-pptx 模板母版 + LLM 抽要点 + 复用标书图表，FR-035）依赖 T010、T046
- [ ] T083 [US7] 同步引擎 `backend/app/modules/presentation/sync.py`（未改页刷新、user_edited 冲突提示，FR-036）
- [ ] T084 [US7] 路由 `backend/app/api/presentation.py`（generate/sync/export）依赖 T082、T083
- [ ] T085 [P] [US7] 前端 `frontend/src/pages/Presentation.tsx`（模板选择、生成、同步、冲突提示）

**Checkpoint**: PPT 可独立验收

---

## Phase 10: User Story 8 - 历史报价多维分析与定价辅助 (Priority: P2)

**Goal**: 导入报价 + 多维筛选统计 + 参考区间（含依据 + 免责声明）

**Independent Test**: 导入 3 年数据，按「学校+示例+2024」得对比图表与参考区间（FR-032~034）

### Tests for US8

- [ ] T086 [P] [US8] 验收测试 `backend/tests/acceptance/test_us8_quotation.py`：筛选与人工核算一致、样本<5 标注、区间含依据

### Implementation for US8

- [ ] T087 [P] [US8] 模型 `backend/app/models/quotation.py`：QuotationRecord
- [ ] T088 [US8] 导入 `backend/app/modules/quotation/importer.py`（pandas Excel/CSV 字段映射 + 校验报告，FR-032）
- [ ] T089 [US8] 分析 `backend/app/modules/quotation/analytics.py`（多维筛选、均价/极值/中标区间/样本数、<5 标注，FR-033）
- [ ] T090 [US8] 参考区间 `backend/app/modules/quotation/pricing.py`（依据项目清单 + 免责声明，FR-034）
- [ ] T091 [US8] 路由 `backend/app/api/quotation.py`（import/analyze/reference-range）依赖 T088–T090
- [ ] T092 [P] [US8] 前端 `frontend/src/pages/QuotationAnalysis.tsx`（导入、ECharts 图表、参考区间 + 免责声明）

**Checkpoint**: 报价分析独立上线，与主链路解耦

---

## Phase 11: User Story 5 - 已完成标书的无损内容提升 (Priority: P2)

**Goal**: 冻结样式/标题层级前提下润色正文 + 关键信息标注 + 佐证截图归档 + 修订对照

**Independent Test**: 上传成稿 .docx，输出样式/层级/编号/页面设置差异为 0，仅正文变化（FR-027~029）

### Tests for US5

- [ ] T093 [P] [US5] 验收测试 `backend/tests/acceptance/test_us5_enhance.py`：文档比对样式差异为 0、修订对照可接受/拒绝

### Implementation for US5

- [ ] T094 [P] [US5] 模型 `backend/app/models/review.py` 扩展：EvidenceShot（linked_entity/query_date）
- [ ] T095 [US5] 无损提升 `backend/app/modules/review/enhance.py`（ProseMirror 文档模型仅改正文节点、冻结样式、输出修订对照，FR-027）依赖 T010
- [ ] T096 [P] [US5] 关键信息标注 `backend/app/modules/review/key_info.py`（合同/发票：金额/期限/主体/发票号，FR-028）
- [ ] T097 [P] [US5] 佐证截图归档 `backend/app/modules/review/evidence.py`（用户上传→匹配资质/发票条目→插入题注，FR-029/R-3）依赖 T009
- [ ] T098 [US5] 路由 `backend/app/api/review.py` 扩展（enhance/key-info/evidence-shots）依赖 T095–T097
- [ ] T099 [P] [US5] 前端 `frontend/src/pages/Enhance.tsx`（修订对照视图逐条接受/拒绝、关键信息标注、佐证上传）

**Checkpoint**: 存量标书增值路径独立可用

---

## Phase 12: User Story 9 - 网点分布图与配送路线规划图生成 (Priority: P2)

**Goal**: 按招标点位/时效生成示意性网点分布图与车辆路线图，插入标书（含图例/说明，与时效一致）

**Independent Test**: 给定配送点位与时效，生成网点图 + 路线图并插入标书（FR-023/R-2）

### Tests for US9

- [ ] T100 [P] [US9] 验收测试 `backend/tests/acceptance/test_us9_maps.py`：图含配送中心/网点/覆盖/图例、路线说明与正文时效一致

### Implementation for US9

- [ ] T101 [US9] 网点/路线图生成 `backend/app/modules/proposal_render/site_route.py`（matplotlib/Graphviz 示意布局、车辆分组、图例/说明，含 `MapProvider` 扩展点占位）依赖 T052
- [ ] T102 [US9] 接入章节渲染与自动图注编号（插入标书对应章节）依赖 T053、T101
- [ ] T103 [P] [US9] 前端 `frontend/src/components/SiteRouteEditor.tsx`（点位/时效输入、图预览、插入章节）

**Checkpoint**: 全部 10 个用户故事独立可用

---

## Phase 13: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事完善与交付项

- [ ] T104 [P] 完善 `POST /projects/{id}/proposal/export` PDF/DOCX 导出性能（100 页 ≤2 分钟，SC 校验）
- [ ] T105 [P] 交付材料 `docs/`：模型使用培训文档、操作手册（FR-041）
- [ ] T106 试用期评估记录 `backend/app/modules/project/trial_report.py`（使用量/生成量/反馈导出，FR-041）
- [ ] T107 [P] 安全加固：导出全留痕、越权拒绝回归、审计检索（FR-039/040）
- [ ] T108 [P] 前端一致性与空态/错误态、移动端只读适配（Out of Scope #14）
- [ ] T109 运行 `quickstart.md` 全部 S1–S10 验收场景并归档结果
- [ ] T110 [P] 代码清理与重构、`fixtures/acceptance/` 回归基线固化

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Setup —— 阻塞所有用户故事
- **User Stories (Phase 3–12)**: 均依赖 Foundational 完成后开工
  - P1 优先：US1 → US2 → US3 → US4（US3 复用 US1 大纲 + US2 知识库，但夹具下可独立验收）
  - P2：US10（机制先行）→ US6 → US7 → US8 → US5 → US9
- **Polish (Phase 13)**: 依赖目标故事完成

### User Story Dependencies

- **US1 (P1)**: 仅依赖 Foundational
- **US2 (P1)**: 仅依赖 Foundational（US1 复用其检索层 T012）
- **US3 (P1)**: 逻辑上用 US1 大纲 + US2 素材；技术上依赖 Foundational + T023/T037，夹具可独立测
- **US4 (P1)**: 依赖 Foundational；对标书对象操作，夹具可独立测
- **US10 (P2)**: 依赖 US2 的 KnowledgeItem 模型（T037）
- **US6/US7/US9 (P2)**: 依赖 US3 的 ProposalSection（T046）
- **US8/US5 (P2)**: 与主链路解耦，仅依赖 Foundational（US5 复用 review 模型）

### Within Each User Story

- 测试（如含）先写并失败 → 模型 → 服务 → 路由 → 前端
- 模型先于服务、服务先于路由、核心先于集成

### Parallel Opportunities

- Setup 中 T003/T004/T005/T006 可并行
- Foundational 中 T009–T015、T017、T018 可并行（T012 依赖 T011、T019 依赖 T008）
- 完成 Foundational 后，US1/US2/US4/US8/US5 可由不同成员并行推进
- 各故事内标 [P] 的模型/前端/独立算法文件可并行

---

## Parallel Example: User Story 1

```bash
# 模型并行：
Task: "模型 backend/app/models/tender.py"          # T022
Task: "模型 backend/app/models/outline.py"          # T023
# 解析子模块并行：
Task: "extract_text.py / extract_tables.py"         # T025
Task: "ocr.py PaddleOCR"                             # T026
# 前端并行：
Task: "TenderReview.tsx"                             # T034
Task: "OutlineTree.tsx"                              # T035
```

---

## Implementation Strategy

### MVP First（US1 → US4）

1. Phase 1 Setup → 2. Phase 2 Foundational（阻塞项）→ 3. US1（解读+大纲）**STOP 验收 SC-001/002**
2. 续接 US2（知识底座）→ US3（图文 Word）→ US4（审核）= 首个完整可交付版本（解读→大纲→生成→审核→导出）

### Incremental Delivery（P2 增量）

US10（过期机制）→ US6（暗标）→ US7（PPT）→ US8（报价）→ US5（无损提升）→ US9（网点/路线图）；每个故事独立验收后 demo，不破坏既有能力。

### Parallel Team Strategy

Foundational 完成后：A 组 US1/US3（主链路），B 组 US2/US10（知识库），C 组 US4/US6（审核/暗标），D 组 US8/US7/US5/US9（分析与演示）。

---

## Notes

- [P] = 不同文件、无未完成依赖
- [Story] 标签用于任务→用户故事的可追溯性
- 每个用户故事独立可完成、可测试
- 契约以 `contracts/openapi.yaml` 为准，路由任务须对齐其路径
- AI 产出均为初稿；数据/承诺/资质以知识库真实条目为唯一事实源，缺素材报缺口不虚构
- 在每个 Checkpoint 停下独立验收对应 Success Criteria
