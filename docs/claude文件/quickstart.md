# Quickstart & 验收运行指南

**Feature**: `001-food-delivery-bid-ai` | 源: [plan.md](./plan.md) · [contracts/openapi.yaml](./contracts/openapi.yaml)

本指南用于**部署环境 + 端到端验证核心用户故事**，不含实现代码（实现细节见 tasks.md 与代码）。

---

## 1. 前置条件

- Docker + Docker Compose（内网一体化部署）
- `ANTHROPIC_API_KEY`（唯一必需外呼；完全离线时改配私有化模型端点，见 `LLMGateway`）
- 验收样本集：`fixtures/acceptance/` 下 10 份真实招标文件 + 人工标注（提取类 FR-002/003/004 召回率基准）
- 首批知识库数据：企业资质证书、场景图片（冷库/车辆/检测/分拣）、历史标书/方案

## 2. 启动（本地/内网）

```bash
cp deploy/env/.env.example deploy/env/.env   # 填入 ANTHROPIC_API_KEY、DB/MinIO/Redis 凭据
docker compose -f deploy/docker-compose.yml up -d   # backend/worker/frontend/pg+pgvector/minio/redis/libreoffice
docker compose exec backend alembic upgrade head     # 建表
docker compose exec backend python -m app.scripts.seed_roles  # 初始化 5 类角色与管理员账号
```

- 前端：`http://<host>:8080`  ·  API 文档：`http://<host>:8000/docs`
- 健康检查：`curl http://<host>:8000/api/v1/health`

## 3. 端到端验收场景

> 每个场景对应一个可独立交付的用户故事；先登录取 JWT，后续请求带 `Authorization: Bearer <token>`。

| # | 场景 | 步骤（API/UI） | 期望结果（验收判据） |
|---|------|----------------|----------------------|
| S1 US1 | 招标解读 | 上传样本 PDF → 轮询 job → 取 `parse-result` → `qualification-match` → 生成大纲 | 10 分钟内出资格/评分表/废标清单，均可跳原文；评分点大纲覆盖 ≥98%，未覆盖项列出（SC-001/002, FR-007） |
| S2 US2 | 知识库入库检索 | 批量上传 30 文件打标签 → 检索「冷链配送 学校」→ 一键复用到节点 | 部分失败不影响其余；≤3s 返回；复用带来源标注（FR-009/011/012） |
| S3 US10 | 版本/过期 | 上传新版营业执照指定替代旧版 → 生成标书 | 旧版不出现在检索/引用；引用过期资料处数为 0（SC-008, FR-014）；T-30 天进 `expiring` 列表 |
| S4 US3 | 图文标书生成 | 确认大纲 → generate → 导出 docx | 逐节响应评分点；证书题注表格 + 图片红框/说明框版式与样例一致；Word 打开无错乱、图片 DPI≥150（SC-007, FR-017~024） |
| S5 US3 | 断点续跑 | 生成中途 `docker restart worker` → resume | 已生成章节完整保留、从未完成章节续跑（FR-038） |
| S6 US4 | 智能审核 | 注入 20 处缺陷的标书 → review | 缺失资质/未响应评分点检出率 100%，误报 ≤20%；每条含级别+定位+依据+建议可跳转（SC-004, FR-025/026） |
| S7 US6 | 暗标脱敏 | redaction/generate → residual-scan → 尝试导出 | 脱敏词命中 0；含图片内文字残留时导出被阻断，confirm 后放行（SC-005, FR-030/031） |
| S8 US8 | 报价分析 | 导入 3 年报价 CSV → analyze「学校+示例+2024」→ reference-range | 与人工核算一致；样本<5 标注不足；参考区间含依据清单+免责声明（FR-032~034） |
| S9 US7 | 答辩 PPT | presentation/generate → 改标书一项数据 → sync → export .pptx | 5 分钟内 15–25 页；PowerPoint 无错乱；未改页刷新、user_edited 页冲突提示（SC-006, FR-035/036） |
| S10 US5 | 无损提升 | 上传成稿 docx → proposals/enhance | 样式/标题层级/编号/页眉页脚差异为 0，仅正文变化；修订对照可逐条接受/拒绝（FR-027） |

## 4. 自动化验收

```bash
docker compose exec backend pytest tests/acceptance -q   # 基于 fixtures/acceptance 的召回率/准确率回归
cd frontend && npm run test                              # Vitest + RTL
npx playwright test e2e/                                  # 关键流程 E2E（S1/S4/S7/S9）
```

## 5. 通过标准（对应 Success Criteria）

- SC-001/002 解读时效与召回率  ·  SC-003 成稿提速 + 自动占比 ≥80%
- SC-004 审核检出 100%/误报 ≤20%  ·  SC-005 暗标处数 0  ·  SC-007 版式正确率 ≥90%
- SC-006 PPT ≤5 分钟/改动 ≤20% 页  ·  SC-008 过期引用 0
- SC-009/010 试用期：独立完成 ≥3 个真实项目、满意度 ≥80%（导出试用期评估报告，FR-041）
