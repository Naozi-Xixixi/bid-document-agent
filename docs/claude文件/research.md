# Phase 0 Research: 食材配送 AI 投标系统

**Feature**: `001-food-delivery-bid-ai` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

本文件记录技术选型决策，并解决 spec 中的 3 处 `NEEDS CLARIFICATION`。原则：单企业内部部署、简体中文、不联网抓取、不伪造素材、AI 产出均为初稿需人工确认。

---

## 一、NEEDS CLARIFICATION 决议

### R-1（FR-005）投标政策解读范围

- **Decision**: 仅基于「上传的招标文件」＋「用户在知识库中维护的政策文件条目」进行政策与合规提示，**不联网抓取**最新法规。每条政策提示必须标注来源（文件名 + 条款），无来源不输出。
- **Rationale**: 与 spec Assumptions（"系统不主动获取"）、Out of Scope #4（不爬取网站）一致；联网法规抓取涉及数据准确性/时效责任，超出本期边界，且属"法律意见"（Out of Scope #7）。
- **Alternatives considered**: 接入法规数据库/联网检索——因合规责任、维护成本、离线部署诉求被否决，列为后续可选。

### R-2（FR-023）网点分布图 / 路线规划图

- **Decision**: 默认生成**示意性/结构化图示**（Graphviz/matplotlib 布局，标注配送中心、网点、覆盖范围、车辆分组路线与图例），坐标由招标文件点位 + 用户输入的相对位置驱动，不接入在线地图底图。真实地图底图 + 地理编码 + 道路级路线规划列为可插拔的后续增强（预留 `MapProvider` 接口）。
- **Rationale**: 评标关注的是配送能力的合理性表达而非导航精度；离线部署、无外部地图 Key 依赖；示意图已满足 FR-023 验收（图例 + 路线说明 + 与正文时效一致）。
- **Alternatives considered**: 高德/百度地图 API 底图——需联网与商用 Key，违背离线诉求，作为 `MapProvider` 扩展点预留。

### R-3（FR-029）佐证截图（信用/发票/证书查询截图）

- **Decision**: 由**用户人工在外部平台查询后上传截图**，系统仅负责**归档、与资质/发票条目匹配、按题注插入标书对应位置、记录截图日期与来源**。系统不自动登录信用中国/税务/认证机构抓取。
- **Rationale**: 与 Out of Scope #1（不对接外部平台、不自动登录抓取）一致；外部查询页面多带验证码/登录态，自动抓取脆弱且有合规风险。
- **Alternatives considered**: RPA/爬虫自动查询截图——合规与稳定性风险高，明确排除。

---

## 二、技术栈选型

### R-4 后端语言与框架

- **Decision**: **Python 3.11 + FastAPI**（API 层）＋ **Celery 5 + Redis**（长任务/断点续跑队列）。
- **Rationale**: 文档处理（python-docx / python-pptx / PyMuPDF / pdfplumber）、OCR（PaddleOCR）、LLM SDK、数据分析（pandas）生态在 Python 最完整；FR-038 的"暂停/续跑/单节重生成/断点恢复"天然映射到 Celery 任务 + 章节级检查点。
- **Alternatives**: Node.js（文档/OCR 生态弱）、Java（迭代慢）——否决。

### R-5 前端

- **Decision**: **React 18 + TypeScript + Ant Design 5 + Vite**；富文本/大纲编辑用 **Tiptap（ProseMirror）**；图表用 **ECharts**。
- **Rationale**: Ant Design 中文业务组件成熟、表格/表单/树（大纲拖拽）完善；Tiptap 支持结构化文档模型与修订对照视图（FR-027）；ECharts 中文图表能力强（FR-033）。浏览器端交付，移动端只读（spec Out of Scope #14）。
- **Alternatives**: Vue3 + Element Plus（同级，团队无偏好时以 React 生态与招聘面更广取胜）。

### R-6 存储

- **Decision**: **PostgreSQL 16 + pgvector**（结构化数据 + 向量检索同库）＋ **MinIO**（S3 兼容对象存储，存原始文件/图片/导出物）。
- **Rationale**: ≤20 并发用户、千级知识条目，pgvector 足够且免运维额外向量库；单库简化事务、备份、权限。MinIO 便于大文件与图片流式访问、可离线部署。
- **Alternatives**: Milvus/Qdrant（规模不需要、增加运维）；文件直存磁盘（不利于权限与审计）——否决。

### R-7 大模型与向量嵌入

- **Decision**: 生成/抽取/审核用 **Claude（默认 `claude-opus-4-8` 复杂推理、`claude-sonnet-5` 高频/低延迟场景）**，经统一 `LLMGateway` 抽象封装（支持超时、重试、成本记账、模型路由）。嵌入用 **BGE-M3（本地化中文/多语向量，1024 维）** 经 `EmbeddingProvider` 抽象。
- **Rationale**: 环境规范要求默认使用最新且最强 Claude 模型；抽取评分表/废标条款、逐条响应、审核推理对长上下文与中文理解要求高，Opus 4.8 最稳。嵌入需中文语义检索且倾向离线，BGE-M3 开源、中文强。抽象层隔离供应商，满足 Out of Scope #15（不自训模型）。
- **Alternatives**: 纯本地开源 LLM（中文长文抽取质量不稳定）；OpenAI 嵌入（联网依赖）——否决为默认，保留为可替换 Provider。

### R-8 文档解析（FR-001~004）

- **Decision**: 组合管线——PDF 文本层用 **PyMuPDF(fitz)**，表格用 **pdfplumber**（跨页/合并单元格规整）；DOCX 用 **python-docx**；DOC 用 **LibreOffice headless** 转 DOCX；扫描件/无文字层 PDF 用 **PaddleOCR**；压缩包解包后按类型分发并合并（Edge Case：正文 + 多附件）。解析产出统一的「文档结构树 + 页码锚点」，供原文跳转（FR-002/003 定位）。
- **Rationale**: 无单一库能兼顾文本/表格/OCR，管线分工可分别达标各自召回率要求。
- **Alternatives**: 商用 OCR/解析 API——联网依赖、成本，否决为默认。

### R-9 Word 渲染（FR-017~020, 024）

- **Decision**: **python-docx** 主渲染 + **docxtpl（Jinja2 模板）** 承载页眉页脚/样式母版；证书题注表格与"图 + 右侧说明框"版式用 docx 表格布局实现；图片重点标注（红框 + 说明框）用 **Pillow** 先在图片上绘制高亮框并合成，再入表；PDF 导出用 **LibreOffice headless** 保真转换。
- **Rationale**: python-docx 精确控制样式/目录域/编号；LibreOffice 转 PDF 版式一致性优于 reportlab 重排。满足 FR-019/020 样例截图版式与 FR-024 导出保真。
- **Alternatives**: HTML→Word（样式易错乱）、Aspose（商用授权）——否决。

### R-10 暗标脱敏（FR-030/031, US6）

- **Decision**: 两级处理——(1) **文本层**：脱敏词典（原词/正则/关联词）遍历正文、页眉页脚、图注、表格文本做移除/替换；(2) **图片层**：PaddleOCR 检测图片内文字，命中脱敏词的图片做遮盖/替换/剔除并逐张出报告。导出前执行**残留扫描**（含图片内文字），存在未确认残留以"致命"级阻断导出（FR-031）。
- **Rationale**: 图片内嵌名称/Logo/水印是废标高风险点（Edge Case），必须 OCR 兜底；阻断式导出保证 SC-005 处数为 0。
- **Alternatives**: 仅文本替换（漏图片内文字）——不满足验收，否决。

### R-11 图表 / 流程图 / 思维导图 / 网点图（FR-021~023）

- **Decision**: 数据表用 python-docx 生成；流程图/思维导图用 **Graphviz**（DOT 由 LLM 从正文语义生成，节点与正文步骤一一对应）渲染为高分辨率 PNG/SVG；网点分布与路线用 matplotlib/Graphviz 布局的示意图（见 R-2）。可复用 `ai-draw` 能力生成结构图。
- **Rationale**: Graphviz 布局稳定、导出清晰（FR-022 验收）；LLM 只产结构（DOT/JSON）不产像素，避免虚构。
- **Alternatives**: 前端 Mermaid 截图（分辨率/字体不可控）——否决为默认。

### R-12 报价数据分析（FR-032~034, US8）

- **Decision**: 导入用 **pandas**（Excel/CSV，可配置字段映射 + 校验报告）；存 PostgreSQL；多维筛选与统计（均价/极值/中标价区间/样本数）用 SQL + pandas；图表前端 ECharts。参考区间给出依据项目清单 + 免责声明；样本 <5 标注"样本不足"。
- **Rationale**: 结构化数值分析用关系库 + pandas 最直接、可核算（FR-033 与人工核算一致）。
- **Alternatives**: BI 工具嵌入——过重，否决。

### R-13 PPT 导出（FR-035/036, US7）

- **Decision**: **python-pptx** + 模板母版（多风格），内容来自定稿标书的结构化摘要（LLM 抽取要点 + 复用标书图表）；建立**标书↔PPT 引用映射**，支持一键同步刷新；用户改过的页标记 `user_edited`，同步时冲突提示而非覆盖（FR-036）。
- **Rationale**: python-pptx 可编程控版式、图片、图表；引用映射保证一致性（SC-006 改动≤20% 页面）。
- **Alternatives**: HTML→PPT 转换（版式错乱）——否决。

### R-14 长任务、并发与断点续跑（FR-037/038）

- **Decision**: 生成以「项目 → 章节」为最小工作单元；Celery 任务按章节推进，每章节完成即持久化（`Proposal Section` + 检查点状态），中断后从未完成章节续跑；同一标书编辑用**乐观锁 + 章节级行锁**避免并发覆盖（Edge Case 多人编辑）。
- **Rationale**: 满足 FR-038"强制中断后已生成章节完整保留并可续跑"验收；章节级锁匹配 ≤5 并发生成任务规模。
- **Alternatives**: 单大任务无检查点（中断即全丢）——否决。

### R-15 权限、审计与安全（FR-039/040）

- **Decision**: **JWT 认证 + RBAC**（角色见 spec：投标专员/投标经理/知识库管理员/商务定价/系统管理员）；所有敏感操作（登录、上传、导出、删除、脱敏确认）写**审计日志**（可按用户/时间/操作类型检索）；导出操作 100% 留痕。
- **Rationale**: 直接映射 FR-039/040 验收与"企业敏感资料受控"诉求。
- **Alternatives**: 无——为硬性需求。

### R-16 测试策略

- **Decision**: pytest（后端单元/集成 + 契约测试）、Vitest + React Testing Library（前端）、Playwright（端到端关键流程）。抽取类需求（FR-002/003/004）用**验收样本集（10 份真实招标文件 + 人工标注）**做召回/准确率回归。
- **Rationale**: spec 的验收标准多为可量化指标，需要基准样本集与自动化回归夹具。

---

## 三、外部依赖清单（汇总）

| 类别 | 依赖 | 用途 | 联网 |
|------|------|------|------|
| LLM | Anthropic Claude API（opus-4-8 / sonnet-5） | 抽取/生成/审核/脱敏推理 | 是（唯一必需外呼；可换私有化端点） |
| 嵌入 | BGE-M3（本地服务） | 语义检索向量 | 否 |
| OCR | PaddleOCR | 扫描件解析、图片内文字检测 | 否 |
| 文档 | PyMuPDF, pdfplumber, python-docx, docxtpl, python-pptx, Pillow | 解析与渲染 | 否 |
| 转换 | LibreOffice headless | DOC→DOCX、DOCX→PDF | 否 |
| 图形 | Graphviz, matplotlib, ECharts | 流程图/思维导图/网点图/报价图表 | 否 |
| 数据 | PostgreSQL 16 + pgvector, MinIO, Redis | 数据/向量/对象存储/队列 | 否（内网） |
| 运行 | Docker Compose | 一体化部署 | 否 |

> 说明：除 Claude API 外，全部可离线运行。若需完全离线，`LLMGateway` 可切换到私有化模型端点。
