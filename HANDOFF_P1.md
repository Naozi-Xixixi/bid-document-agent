# new_biaoshuai P1 阶段交接文档

> 文档版本：P1 完成交接版  
> 适用项目：`new_biaoshuai`  参考日期：2026-09-22  
> 当前状态：核心代码已完成，前端构建、后端编译和核验规则冒烟检查通过；真实数据环境下的完整浏览器/API 联调仍需按本文“已知限制”执行。

## 0. 接手速览

P1 阶段围绕“素材可识别、图片可精准匹配、插入素材可核验”完成了四项增量能力：

1. 知识库素材从大类扩展到细分类，并保存 OCR/上下文识别得到的结构化字段。
2. Word/PDF 图片提取支持单页多图，图文标书生成按章节主题、细分类别、有效期和重复使用情况选择图片。
3. 知识库页面支持细分类筛选、结构化字段展示、单条/批量重新提取。
4. 图文标书生成和导出前增加证件、资质、车辆和票据风险核验，并支持风险定位与导出确认。

P1 不改变正文生成、流式输出、文本 RAG 检索和原有后端 Word 导出接口；P0 阶段的 docxtemplater 导出层继续作为默认导出层，原生后端导出保留为降级能力。

## 1. 阶段概述

### 1.1 核心目标

食材配送投标场景中的证书、人员证件、车辆证明、票据和现场图片原先主要依赖通用图片分类及人工判断。P1 将这些素材沉淀为可检索、可筛选、可核验的数据：

- 一份素材可保存“资质/人员/车辆/票据/场景”大类及更细的 `asset_sub_type`。
- OCR 文本、文件名、所属文档上下文和图片特征共同参与分类。
- 编号、有效期、主体名称、姓名、车牌号、发票号码等字段写入统一结构化 JSON，同时保留历史字段。
- 章节生成时先按语义和类别过滤，再按有效期、结构化完整度、置信度和是否已使用排序。
- 对已经插入标书的图片进行统一风险核验，风险只提示，不修改原始素材。

### 1.2 整体价值

P1 将知识库图片从“可展示附件”升级为“可检索素材资产”：

```text
文档/PDF 多图提取
        ↓
OCR + 文档上下文 + 图片特征
        ↓
细分类 + 结构化字段 + 置信度
        ↓
章节主题/类别/有效期精准匹配
        ↓
正文插图与导出前风险核验
```

## 2. 改动清单

### 2.1 数据库变更

#### `knowledge_asset` 新增字段

所有字段都是增量添加，默认空字符串，原有字段未删除或改名：

| 字段 | 类型 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `structured_info` | `TEXT` | `''` | JSON 字符串，保存完整结构化结果、证据、置信度和兼容旧字段的信息 |
| `asset_sub_type` | `TEXT` | `''` | 细分类别，例如营业执照、健康证、车辆行驶证、增值税发票 |
| `expire_date` | `TEXT` | `''` | 统一有效期，目标格式为 `YYYY-MM-DD` |
| `cert_no` | `TEXT` | `''` | 证件、发票或其他凭证编号 |
| `holder_name` | `TEXT` | `''` | 持证主体、人员姓名或票据抬头等主体信息 |

`ensure_schema()` 会通过 `PRAGMA table_info(knowledge_asset)` 检查字段，不存在时执行 `ALTER TABLE ... ADD COLUMN`；已有 NULL 值会归一化为空字符串。该过程是幂等的，不会主动重写历史素材的识别结果。

#### 新增索引

在现有索引基础上增加：

- `idx_knowledge_asset_sub_type`：细分类筛选和类别匹配。
- `idx_knowledge_asset_expire_date`：有效期排序和核验。
- `idx_knowledge_asset_cert_no`：证件编号搜索。

当前没有为 `holder_name` 单独建立索引；主体核验通过读取素材字段并与当前标书企业资料比较完成。

#### 表结构范围

- 没有新增 P1 专用表。
- `knowledge_item`、`knowledge_chunk`、`knowledge_entity`、`knowledge_relation` 等原表保留。
- 现有 `knowledge_item.extracted_image_count`、`image_category_stats_json` 等处理统计字段继续使用，不改变历史数据语义。

### 2.2 后端改动

#### 知识库解析与结构化识别

主要文件：

- `backend/app/modules/knowledge/service.py`
- `backend/app/modules/knowledge/router.py`
- `backend/app/modules/knowledge/schemas.py`

实现内容：

- PDF 使用现有 PDF 解析链路，并遍历每页 `get_images(full=True)`，支持一页多图提取；保留页码和页面上下文。
- DOCX 遍历 `word/media/`，支持一份文档内多张图片独立入库。
- 过滤图片尺寸/特征判定的装饰图，并识别跨页重复的页眉、页脚图片，避免将装饰图作为业务素材。
- 继续复用现有 OCR 能力；P1 没有新增 OCR 引擎。
- 使用图片文件名、OCR 文本、文档上下文、图片尺寸/边缘密度等特征参与分类。
- `_save_image_asset()`、`_reextract_one_asset()` 会同时写入顶层字段和 `structured_info` JSON，并保留 `category`、`annotation`、`tags`、`resource_path` 等旧字段。
- 重新提取完成后刷新图片语义注释的向量块，保证后续 RAG 检索能看到新信息。

`structured_info` 的当前基本形态如下，字段可能因素材类型不同而不同：

```json
{
  "category": "资质证书类",
  "asset_sub_type": "食品经营许可证",
  "confidence": 0.86,
  "evidence": {
    "ocr_keywords": [],
    "context_keywords": [],
    "image_features": []
  },
  "fields": {
    "cert_no": "...",
    "issuing_authority": "...",
    "expire_date": "2027-01-01",
    "holder_name": "...",
    "business_scope": "..."
  },
  "legacy": {}
}
```

#### 知识库接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/knowledge/assets?asset_sub_type=...` | 图片资产列表按细分类过滤；原有参数仍兼容 |
| `GET` | `/api/knowledge/assets/{asset_id}` | 返回结构化字段和顶层兼容字段 |
| `POST` | `/api/knowledge/{item_id}/reextract` | 对单个知识文档下的图片重新提取；可传 `asset_ids`、`asset_sub_type`、`only_missing`、`limit` |
| `POST` | `/api/knowledge/reextract/batch` | 按 `item_ids`、`asset_ids` 或细分类批量重提取 |
| `POST` | `/api/knowledge/assets/reextract-batch` | 批量重提取兼容别名 |

重提取返回 `scanned`、`updated`、`skipped`、`failed` 和 `details`。文件不存在、OCR/解析失败等情况进入 `failed`，不会伪造字段。

#### 图文图片精准匹配

主要文件：

- `backend/app/services/bid_graphic_service.py`
- `backend/app/api/routes/bid_graphic.py`
- `backend/app/schemas/bid_graphic.py`

生成请求新增或正式使用以下参数：

```json
{
  "image_strategy": "rag_auto",
  "asset_sub_type": [],
  "priority_expire": false,
  "exclude_ids": [],
  "chapter_theme": "车辆设备与配送能力"
}
```

流式接口通过 Query 参数接收同名字段；不传时使用原有默认策略。

核心实现函数包括：

- `_resolve_image_scope()`：从章节标题、章节路径和正文相关上下文推导细分类及大类范围。
- `_select_knowledge_images()`：合并 RAG 结果和企业知识库候选图片。
- `_select_project_images()`：无 RAG 或需要项目图片时收集当前知识库候选。
- `_rank_image_candidates()`：按语义命中、类别范围、结构化质量、有效期和置信度排序。
- `_select_ranked_images()`：处理未使用素材优先、过期降级和复用素材标记。
- `_used_image_ids_for_bid()`：按 `bid_id` 收集其他章节已使用的图片，保持项目隔离并避免连续重复。

图文章节返回的图片可带有 `match_score`、`expire_status`、`reuse_material`、`match_scope` 等匹配元数据。

#### 素材风险核验

`backend/app/api/routes/bid_graphic.py` 新增：

```text
POST /api/bid/{bid_id}/audit-assets
```

实现位于 `bid_graphic_service.audit_graphic_assets()` 和 `_audit_asset()`：

- 只核验当前 `bid_id` 下已保存章节正文引用的知识库图片。
- 没有有效 `structured_info` 的素材直接跳过，不产生误报。
- `GET /api/bid/{bid_id}/graphic-section/{chapter_id}` 返回 `risks`。
- 生成接口和保存接口的章节结果也返回 `risks`。
- 全文核验返回 `risks`、`assets` 和 `summary`，其中 `summary` 包含 `high`、`medium`、`low`、`total`。

#### 原有接口保持

- `/api/bid/{bid_id}/graphic-section/generate` 保留。
- `/api/bid/{bid_id}/graphic-section/generate-stream` 保留，并仅扩展可选匹配参数。
- `/api/bid/{bid_id}/graphic-section/save` 保留。
- `/api/bid/{bid_id}/graphic-section/download` 原生后端导出保留，作为前端模板导出失败时的降级入口。

### 2.3 前端改动

#### 知识库页面

主要文件：

- `frontend/src/KnowledgeWorkspace.tsx`
- `frontend/src/wordagentApi.ts`
- `frontend/src/index.css`

已实现：

- 文档库、图片库都增加细分类下拉筛选。
- 主分类变化后自动收窄细分类选项；细分类通过 `asset_sub_type` 传给后端。
- 文档列表增加细分类标签；图片卡片增加细分类标签。
- 标签按大类区分：资质蓝色、人员绿色、车辆橙色、票据紫色、场景灰色、其他浅灰。
- 详情弹窗基础信息区域增加结构化信息卡片。
- 根据细分类显示证件编号、发证机关、有效期、主体、人员、车辆、发票和现场信息；空字段自动隐藏。
- “更多工具”中支持当前选择或当前筛选结果的批量重提取。
- 图片预览和文档详情支持单条重新提取。
- 重提取期间按钮禁用并显示处理状态，完成后刷新列表和详情。

#### 图文标书生成页面

主要文件：

- `frontend/src/GraphicBidWorkspace.tsx`
- `frontend/src/wordagentApi.ts`
- `frontend/src/index.css`

已实现：

- “图片使用策略”支持不使用、自动匹配、优先有效期内、仅指定分类等选项。
- “已匹配素材”区域显示缩略图、细分类、有效/临期/过期状态、复用标记，并支持查看、替换、移除。
- 章节生成请求携带 `asset_sub_type`、`priority_expire`、`exclude_ids`、`chapter_theme`。
- 风险面板位于右侧参数区底部，默认折叠；有风险时自动展开并显示总数。
- 风险按高/中/低分组，显示缩略图、章节、图片 ID、问题和定位按钮。
- 跨章节定位时先切换章节，正文加载后滚动到图片并添加高亮动画。
- 正文图片根据最高风险级别显示红/黄/低风险边框及角标，悬停可查看问题和结构化信息。
- 导出前调用全文档核验接口；有高风险时弹出确认弹窗，用户可以返回修改或仍要导出。
- 仅有中低风险时不弹出阻断确认，导出完成提示风险统计。
- 切换章节、重新生成或保存章节时清理全文档旧风险，避免旧章节风险残留；导出确认后的风险统计继续传入导出提示。

#### 前端 API 类型与调用

`frontend/src/wordagentApi.ts` 新增/扩展：

- `AssetRisk`、`AssetAuditResult` 类型。
- `KnowledgeAsset.structured_info`、`asset_sub_type`、`expire_date`、`cert_no`、`holder_name`、`reuse_material` 等字段。
- `GraphicSection.risks`、`image_match_meta`。
- `auditGraphicAssets()`、`reextractKnowledgeItemAssets()`、`reextractKnowledgeAssetsBatch()`。
- 图文生成流式 URL 的匹配参数序列化。

### 2.4 依赖与资源文件

#### P1 新增依赖

P1 没有新增第三方依赖。继续使用项目已有后端能力：

- `python-docx`：DOCX 文本和兼容文档处理。
- `pdfplumber`、`PyMuPDF`：PDF 文本和多图提取。
- `Pillow`：图片特征和尺寸处理。
- 可选的 `PaddleOCR`：OCR 识别；未安装时沿用现有降级逻辑。
- 现有向量/RAG 组件：图片语义注释和检索。

前端 P0 已安装的 `docxtemplater`、`pizzip`、`jszip-utils`、`docxtemplater-image-module-free` 继续使用，P1 未改变其版本和导出架构。

#### P1 新增资源文件

P1 没有新增模板或在线资源文件。以下 P0 资源继续被图文导出使用：

- `frontend/src/assets/bid-templates/standard-public.docx`
- `frontend/src/assets/bid-templates/compact-anonymous.docx`
- `frontend/src/assets/bid-templates/food-delivery.docx`
- `frontend/src/utils/wordTemplateExport.js`
- `frontend/src/docxExport.ts`

## 3. 核心能力说明

### 3.1 素材细分类与结构化提取

后端 `ASSET_SUBTYPE_GROUPS` 当前支持以下细分类：

| 大类 | 细分类 |
| --- | --- |
| 资质证书 | 营业执照、食品经营许可证、有机产品认证、ISO 体系认证/体系认证、检测报告 |
| 人员证件 | 身份证、人员身份证、健康证、从业资格证、安全员证、岗位证书 |
| 车辆资产 | 冷链配送车、普通货车、车辆行驶证、车辆保险单 |
| 票据凭证 | 增值税发票、银行转账凭证、缴费证明、保证金单据 |
| 场景现场 | 冷库仓储、分拣中心、门店实景、配送现场 |
| 兜底 | 其他附件 |

分类依据按以下顺序综合计算，不是只看文件名：

1. OCR 关键词命中。
2. 文档段落、来源章节和文件名上下文。
3. 图片尺寸、边缘密度等基础图像特征。
4. 多个规则候选中取置信度最高者。

置信度低于 `0.70` 时，细分类回退为“其他附件”，并保留证据与实际置信度。提取失败的字段为空，不根据图片内容臆造值。

不同类别当前提取的重点字段：

- 资质：证件/证书编号、发证机关、有效期、持证主体、经营范围。
- 人员：姓名、性别、身份证/证件编号、岗位、有效期。
- 车辆：车牌号、车型、核定载重、年检有效期、冷链设备配置。
- 票据：发票号码、开票日期、金额、抬头、税号。
- 场景：场景细分类和场景关键词/说明。

### 3.2 图片精准匹配机制

图片匹配流程为：

1. 读取当前章节路径、章节标题、项目概述和评分要求，形成章节查询主题。
2. 通过现有知识库/RAG 检索获得文本和图片候选。
3. 根据章节语义和用户选择的 `asset_sub_type` 推导精确细分类及大类范围。
4. 过滤不属于指定范围的素材；没有精确细分类时可在同大类范围内降级。
5. 按语义命中、细分类范围、结构化字段完整度、分类置信度、有效期和已使用状态排序。
6. 优先选择当前标书未使用且未过期的图片；未过期候选不足时才允许过期素材降级。
7. 仍不足时可复用已使用素材，并在返回数据中标记 `reuse_material=true`。
8. 没有合适候选时返回空列表，正文不强制插入无关图片。

前端的默认策略是 `rag_auto`。`valid_only` 会打开有效期优先；`category_only` 要求至少选择一个细分类。所有候选查询和已使用图片集合均以当前 `bid_id` 为范围，不跨项目混用。

### 3.3 证件核验与风险提示

#### 后端规则

核验只针对已被章节 Markdown 或 `ref_image_asset_ids` 引用的图片；没有结构化信息的图片跳过。

| 类型 | 高风险 | 中风险 | 低风险 |
| --- | --- | --- | --- |
| 资质证书 | 已过期、证件编号缺失、持证主体与投标企业资料不一致 | 30 天内到期、有效期/主体信息缺失 | 置信度低于 70% |
| 人员证件 | 已过期、证件编号缺失 | 30 天内到期、姓名缺失 | 置信度低于 70% |
| 车辆资产 | 已过期 | 30 天内到期或未提取到可核验有效期 | 车牌/车型等车辆信息不完整、置信度低 |
| 票据凭证 | 发票号码、开票日期、金额、抬头中缺失两项及以上 | 缺失一项 | 低置信度或信息模糊提示 |

规则集中在 `bid_graphic_service.py` 的 `_audit_asset()`，风险对象包含：

```json
{
  "risk_id": "章节-素材-问题长度-等级",
  "severity": "high|medium|low",
  "issue": "问题描述",
  "rule": "命中的字段规则",
  "chapter_id": 1,
  "chapter_title": "章节标题",
  "asset_id": 123,
  "asset": {}
}
```

#### 前端行为

- 风险面板默认折叠，有风险自动展开。
- 高风险红框/红色角标，中风险黄框/黄色角标，低风险显示低风险提示角标。
- 定位支持当前章节和跨章节；跨章节会等待章节正文加载后再定位。
- 点击“下载图文标书 Word”先做全文核验。
- 存在高风险时要求用户确认是否继续，但不会禁止继续导出。
- 只有中低风险时不弹确认，导出成功后显示风险数量。
- 风险只读提示，不更新或删除原始素材。

## 4. 验证结果

### 4.1 已验证通过

在当前项目环境执行过：

```powershell
cd new_biaoshuai/frontend
npx tsc --noEmit
npm run build

cd ../backend
py -m compileall -q app
```

结果：

- TypeScript 类型检查通过。
- Vite 生产构建通过。
- Python 后端编译通过。
- 构建仅有既有的 JS chunk 体积提示，没有阻断性错误。
- 前端产物包含三套本地 DOCX 模板资源。

后端规则冒烟验证覆盖：

- 过期资质证件 -> `high / expire_date`。
- 30 天内到期 -> `medium / expire_date`。
- 证件编号缺失 -> `high / cert_no`。
- 票据缺少两项关键信息 -> `high / invoice_fields`。
- `structured_info` 为空 -> 不生成风险。
- 置信度低于 70% -> `low / confidence`。

### 4.2 与验收标准的对应情况

| 验收项 | 状态 | 说明 |
| --- | --- | --- |
| 过期证件高风险、正文红框 | 已实现/规则已冒烟验证 | 真实页面需用实际素材再验一次 |
| 临期证件中风险、正文黄框 | 已实现/规则已冒烟验证 | 日期以标准化有效期为准 |
| 风险项跨章节定位和高亮 | 已实现 | 代码已处理异步章节加载时序，建议浏览器手工回归 |
| 高风险导出确认 | 已实现 | “仍要导出”继续调用模板导出并携带本次风险结果 |
| 中低风险不阻断并提示 | 已实现 | 风险统计使用全文档核验结果 |
| 无结构化信息不报风险 | 已实现/规则已验证 | 只核验章节引用素材 |
| 原有生成、编辑、导出能力 | 构建级验证通过 | 尚未覆盖所有历史项目的真实回归样本 |
| 前端构建、后端接口代码 | 已通过 | `tsc`、Vite build、Python compileall 通过 |

### 4.3 已知限制与遗留问题

1. 当前没有独立的后端自动化测试文件；规则验证以函数级冒烟检查和编译检查为主。
2. 本次未在真实运行中的前后端服务上完成完整浏览器联调，因此需要用真实项目执行一次：上传多图文档 → 生成章节 → 插图 → 触发风险 → 导出。
3. 风险全文核验只检查标书章节已引用的知识库图片，不检查尚未插入正文的知识库素材；这是为了避免对未使用素材产生误报。
4. DOCX 图片能够批量提取，但当前 DOCX 提取链路没有像 PDF 一样可靠的页面坐标/页码信息；来源文档和上下文仍会保留。
5. OCR 是可选运行能力。未安装 PaddleOCR 时，系统依靠已有 OCR/上下文/文件名/图片特征降级识别，分类和字段完整度会下降。
6. 日期核验优先使用顶层 `expire_date` 或结构化字段中的标准日期；未标准化的日期范围、模糊日期可能被标记为未知。
7. 当前车辆规则对“未提取到有效期”沿用通用证件信息缺失的中风险提示，同时对车牌/车型缺失给低风险提示；如验收要求将车辆有效期缺失严格归为低风险，需要在 P2 前统一规则。
8. 核验规则集中在服务代码中，已经按 `_audit_asset()`、`_asset_group()` 拆分，便于扩展，但还不是外部配置驱动。
9. 风险展示使用新增的 `graphic-risk-*` 样式类；颜色与现有系统风格保持一致，但高/中/低风险色仍有部分显式颜色值，后续可进一步收敛到统一 CSS 变量。

## 5. 数据兼容与隔离说明

### 5.1 历史数据处理

- 所有 P1 数据库变更都是 `ADD COLUMN`，旧字段和旧数据保留。
- 新字段默认空字符串；旧素材不会因为迁移自动被重新 OCR 或分类。
- 历史图片可通过单文档或批量重新提取接口补全新字段。
- `only_missing=true` 是后端接口默认值，适合补齐历史空数据；前端主动点击“重新提取”时传 `only_missing=false`，用于强制刷新。
- 提取失败只记录失败详情，不覆盖原始资源文件，不填充猜测字段。

### 5.2 接口和前端兼容

- 列表接口新增参数是可选参数，不传时沿用原有列表行为。
- 图文生成新增匹配参数均有默认值，不传时使用原有 `rag_auto` 行为。
- 章节返回新增 `risks`、图片匹配元数据字段，旧前端忽略这些字段也不影响正文生成。
- 原有搜索、上传、查看大图、编辑、删除、后端原生导出接口未移除。

### 5.3 `bid_id` 隔离

- 图文章节、全文风险审计、已使用图片集合均以 `bid_id` 查询。
- 生成匹配时排除的是当前标书其他章节已经使用的图片，不会把其他标书的使用记录混入当前项目。
- 重新提取接口按 `item_id/asset_id` 操作知识库素材，不改变标书章节数据。

## 6. 下一步建议：P2 切入路径

### 6.1 P2 目标

P2 重点是知识库多维度分类体系：标书类型（明标/暗标）、篇幅等级（精简/标准/详细）及未来可扩展字段，并让检索和前端筛选支持组合条件。

### 6.2 推荐切入顺序

1. 先冻结 P1 字段和枚举：`asset_sub_type`、`bid_type`、`length_level`、`structured_info` 的 JSON 结构及空值约定。
2. 把前后端重复维护的细分类选项抽成版本化字典接口或共享配置，避免中文枚举不一致。
3. 为 `bid_type`、`length_level` 增加组合筛选的后端查询测试，并确认文档库模式下按素材条件筛选文档的语义。
4. 将审计规则整理为规则表/配置对象，补充车辆有效期缺失、日期范围和置信度缺失的明确策略。
5. 建立固定测试数据集：每个大类至少包含有效、临期、过期、缺字段、低置信度和无结构化信息样本。
6. 在真实服务上完成一次 E2E 回归后，再增加 P2 前端下拉和筛选组合，避免同时引入字段和 UI 问题。
7. 继续保持增量迁移：新增字段/索引只通过 `ensure_schema()` 幂等添加，不修改既有列和历史素材文件。

### 6.3 P2 开始前检查清单

- [ ] 确认生产 SQLite 备份和当前 `knowledge_asset` 字段快照。
- [ ] 用真实历史素材运行批量重提取并统计分类置信度。
- [ ] 确认 P1 风险规则中车辆有效期缺失的最终等级。
- [ ] 补充后端规则单元测试和接口测试。
- [ ] 启动前后端服务，完成浏览器端四条核心回归：筛选、重提取、图片匹配、风险导出。
- [ ] 确认前端枚举与后端 `ASSET_SUBTYPE_GROUPS` 完全一致。

## 7. 关键文件路径索引

### 后端

| 路径 | 责任 |
| --- | --- |
| `backend/app/modules/knowledge/service.py` | 知识库 schema、图片提取、分类、结构化字段、列表筛选、重提取和向量刷新 |
| `backend/app/modules/knowledge/router.py` | 知识库列表、详情、重提取接口路由 |
| `backend/app/modules/knowledge/schemas.py` | 知识库检索和重提取请求模型 |
| `backend/app/services/bid_graphic_service.py` | 图文章节生成、图片候选收集/排序、项目内去重、素材风险核验 |
| `backend/app/api/routes/bid_graphic.py` | 图文生成、流式生成、章节读取/保存、全文素材审计路由 |
| `backend/app/schemas/bid_graphic.py` | 图文生成请求的匹配策略参数模型 |
| `backend/app/services/storage.py` | SQLite 连接和通用数据库初始化基础设施 |
| `backend/requirements.txt` | 既有 PDF/DOCX/图片/向量依赖；P1 未新增第三方包 |
| `backend/requirements-ocr.txt` | 可选 PaddleOCR 依赖 |

### 前端

| 路径 | 责任 |
| --- | --- |
| `frontend/src/KnowledgeWorkspace.tsx` | 知识库筛选、细分类标签、结构化详情、批量/单条重提取 |
| `frontend/src/GraphicBidWorkspace.tsx` | 图文生成参数、匹配素材列表、风险面板、正文风险标记、导出确认 |
| `frontend/src/wordagentApi.ts` | 前端接口、请求参数和风险/素材类型定义 |
| `frontend/src/index.css` | 知识库细分类/结构化信息和图文风险展示样式 |
| `frontend/src/P1Workspace.tsx` | P1 工作台入口及四个独立模块编排 |
| `frontend/src/App.tsx` | 前端应用入口 |
| `frontend/package.json` | 前端依赖清单；P1 未新增依赖 |

### P0 导出基础（P1 依赖但未重构）

| 路径 | 责任 |
| --- | --- |
| `frontend/src/docxExport.ts` | 模板导出数据映射、进度回调和后端降级 |
| `frontend/src/utils/wordTemplateExport.js` | docxtemplater 通用渲染/下载工具 |
| `frontend/src/assets/bid-templates/*.docx` | 本地标准明标、精简暗标、食材配送模板 |
| `frontend/src/templates/*.docx` | 既有格式模板资源 |
| `frontend/src/types/docxtemplater-image-module-free.d.ts` | 图片模块类型声明 |

## 8. 接手建议

新会话接手时建议先阅读本文件第 4、5 节，再按以下顺序定位：

1. 先确认后端数据库初始化已执行，检查 `knowledge_asset` 五个新增字段和三个新增索引。
2. 用 `GET /api/knowledge/assets?asset_sub_type=营业执照` 验证列表返回结构化字段。
3. 选择一个历史文档调用 `/api/knowledge/{item_id}/reextract`，检查 `updated/skipped/failed`。
4. 进入图文标书生成页生成一个章节，确认返回的图片 ID、`expire_status` 和 `reuse_material`。
5. 使用包含结构化字段的过期图片，调用 `/api/bid/{bid_id}/audit-assets`，确认全文风险摘要和导出确认链路。
6. 最后再开始 P2 字段或筛选 UI 开发，避免重复修改 P1 的识别和审计基础。
