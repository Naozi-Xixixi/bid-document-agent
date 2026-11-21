# 智能标书助手 · Bid Document Agent

一套面向政企采购、食材配送、服务类投标场景的**智能标书编写与审核平台**。输入招标文件，平台即可完成「招标解读 → 目录大纲 → 企业知识库检索 → 图文正文生成 → 证据链核验 → 暗标检查 → Word / PPT 导出」的完整链路，并内置投标审核、发票核验与配送路线规划等辅助模块。

后端基于 **FastAPI + SQLite / MySQL**，前端基于 **React 19 + TypeScript + Vite + Tailwind CSS**，大模型与向量检索均可按需替换，支持本地 Ollama 与云端模型混合使用。

> 本仓库为通用工程模板，不包含任何真实客户资料、资质文件或模型密钥。运行时数据全部保存在本机 `data/` 目录中。

---

## 功能特性

### 招标文件解读

- 支持 PDF / DOCX / 纯文本招标文件上传与解析，支持流式进度反馈。
- 自动提取项目概况、评分点、资格门槛、废标风险与投标重点。
- 支持招标信息搜索与历史项目复用。

### 目录与大纲

- 基于解读结果生成投标文件目录，支持增删改、层级调整、排序与版本固化。
- 可引用历史认可案例作为章节结构与写法参考。

### 企业知识库

- 资质、制度、方案、案例、现场图片、人员证件、设备资产等资料统一入库。
- PDF / DOCX / 图片 / 文本多格式解析，重复文件识别、装饰图过滤、图片分类与语义注释。
- 扫描件 OCR（RapidOCR + ONNX Runtime，本地离线运行），支持断点续跑、批次拆分与失败重试。
- 混合检索：关键词检索 + 向量检索，支持 Qdrant / Milvus / 本地兜底三种模式，Embedding 可走 Ollama（如 `bge-m3`）或云端接口。

### 图文标书正文

- 按章节流式生成正文，可编辑、保存、重新生成单个章节。
- 知识库图片自动匹配与真实渲染，支持图注、篇幅控制与用户插图。
- 评分响应与证据链绑定：生成时记录引用的知识片段与证据材料，导出前自动排除未核验的证明材料。

### Word 模板与导出

- 内置多套 Word 模板，支持企业页眉页脚、动态页码、自动目录与分页控制。
- 支持从历史标书提取格式模板，并在导出时套用。
- 可选的 `officecli` DOCX 后处理；未安装时自动降级为 `python-docx` 导出。

### 暗标与合规检查

- 一键进行暗标残留扫描：正文、页眉页脚、图注、表格与图片内文字。
- 脱敏词典可维护，输出可识别的风险报告。

### 辅助模块

- **标书审核**：对照招标文件检查废标风险、评分响应、资质与格式一致性。
- **发票批量核验**：批量识别与校验发票字段。
- **配送路线规划**：基于高德地图 Web 服务，支持多网点、最近网点自动选择与静态路线图导出。
- **PPT 生成**：根据已保存的目录与正文生成可编辑 PPTX。
- **章节协作**：多用户章节认领、状态跟踪与协同编辑。
- **多公司隔离**：按公司（租户）隔离数据库、知识库与文件目录。

---

## 技术栈

| 层次 | 选型 |
| --- | --- |
| 后端框架 | FastAPI、Pydantic Settings、Uvicorn |
| 数据库 | SQLite（默认） / MySQL（可选） |
| 向量检索 | Qdrant（默认）、Milvus（可选）、SQLite 本地兜底 |
| 模型服务 | 通义千问 / DashScope 兼容接口、DeepSeek、Ollama 本地 Embedding |
| 文档处理 | python-docx、python-pptx、pdfplumber、PyMuPDF、docxtemplater |
| OCR | RapidOCR + ONNX Runtime（本地离线） |
| 前端 | React 19、TypeScript 5、Vite 8、Tailwind CSS 4 |
| 图表 | Mermaid、ELK.js、@xyflow/react |
| 地图 | 高德地图 Web 服务 |

---

## 目录结构

```text
bid-document-agent/
├─ backend/                 FastAPI 后端
│  ├─ app/
│  │  ├─ api/routes/        各业务模块 API
│  │  ├─ services/          业务逻辑（解析、生成、检索、导出等）
│  │  ├─ modules/knowledge/ 知识库与 OCR 处理
│  │  ├─ utils/             招标解析与提取工具
│  │  ├─ core/              配置、路径、租户上下文
│  │  └─ main.py            应用入口
│  ├─ scripts/              迁移、索引、修复等运维脚本
│  ├─ tests/                后端测试
│  ├─ requirements*.txt     依赖清单（基础 / OCR / Qdrant / Milvus / Linux）
│  └─ .env.example          环境变量模板
├─ frontend/                React + TypeScript 前端
│  ├─ src/                  页面、组件、API 客户端与样式
│  ├─ tests/                前端测试（node:test）
│  └─ .env.example          前端环境变量模板
├─ deploy/                  Nginx、Qdrant、Milvus 部署配置
├─ docs/                    架构、功能与运维文档
├─ scripts/                 一键启动、环境检查、发布打包脚本
├─ compose.yaml             Docker Compose 编排
└─ README.md
```

---

## 快速开始

### 环境要求

- Python 3.11（推荐；3.12 在部分 OCR 依赖上存在兼容风险）
- Node.js 20+、npm 10+
- 可选：Docker / Docker Compose（用于 Qdrant、Ollama 或整体容器化部署）

### 方式一：本地开发启动（Windows PowerShell）

```powershell
# 1. 克隆仓库
git clone https://github.com/<your-account>/bid-document-agent.git
cd bid-document-agent

# 2. 配置后端环境变量
Copy-Item backend\.env.example backend\.env
notepad backend\.env    # 至少填写 QWEN_API_KEY（如使用云端模型）

# 3. 启动后端（自动创建 .venv 并安装依赖）
.\scripts\start-backend.ps1

# 4. 另开一个终端，启动前端
.\scripts\start-frontend.ps1
```

- 后端地址：`http://127.0.0.1:3896/api`
- 健康检查：`http://127.0.0.1:3896/api/health`
- 前端地址：`http://127.0.0.1:5181/`

首次启动会创建管理员账号，账号密码来自 `backend/.env` 中的 `ADMIN_INITIAL_USERNAME` / `ADMIN_INITIAL_PASSWORD`（密码至少 12 位）。

### 方式二：Docker Compose

`compose.yaml` 会一并启动 Qdrant、Ollama、后端与前端：

```powershell
Copy-Item backend\.env.example backend\.env
# 按需填写模型密钥
docker compose up -d --build
```

默认访问 `http://127.0.0.1:8080/`，可通过 `APP_PORT` 环境变量调整端口。

### 手动启动（macOS / Linux）

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 3896

# 另一个终端
cd frontend
npm ci
cp .env.example .env.local
npm run dev -- --host 127.0.0.1 --port 5181
```

Linux 服务器可安装 `requirements-linux.txt` 以获取 Linux 下的完整依赖。

---

## 环境变量

所有配置均通过环境变量注入，模板见 [`backend/.env.example`](backend/.env.example)。

| 变量 | 说明 |
| --- | --- |
| `QWEN_API_KEY` / `QWEN_BASE_URL` | 云端对话与 Embedding 接口 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | DeepSeek 文本模型 |
| `VECTOR_BACKEND` | `qdrant` / `milvus` / `local` |
| `QDRANT_URL`、`QDRANT_PATH` | Qdrant 连接方式（二选一） |
| `OLLAMA_BASE_URL`、`OLLAMA_EMBEDDING_MODEL` | 本地 Embedding |
| `DB_BACKEND`、`MYSQL_*` | 切换 MySQL 存储 |
| `AMAP_WEB_SERVICE_KEY` | 高德地图 Web 服务密钥（仅后端使用） |
| `ADMIN_INITIAL_USERNAME` / `ADMIN_INITIAL_PASSWORD` | 首次启动创建的管理员 |

> **安全提示**：`backend/.env`、`frontend/.env.local` 已被 `.gitignore` 忽略。请勿提交任何真实密钥、数据库密码或客户资料。若密钥曾意外提交，请立即在服务商控制台轮换。

---

## 运行测试

```bash
# 后端
cd backend
pytest -q

# 前端（需要先 npm ci）
cd frontend
node --test tests/*.test.mjs
```

前端构建校验：

```bash
cd frontend
npm run build
```

---

## 生产部署

- 前端执行 `npm run build`，将 `frontend/dist` 交给 Nginx 或任意静态服务托管。
- 后端使用 `uvicorn app.main:app --host 0.0.0.0 --port 3896` 或进程管理器运行。
- Nginx 反向代理 `/api`、`/uploads`、`/mermaid` 到后端。
- SQLite 适合单机轻量部署；高并发或大数据量场景建议切换 MySQL。

更多内容参见：

- [部署说明](DEPLOYMENT.md)
- [阿里云 ECS 部署](ALIYUN_ECS_DEPLOYMENT.md)
- [MySQL 迁移](MYSQL_MIGRATION.md)
- [多公司账号隔离](MULTI_COMPANY_ACCOUNTS.md)
- [知识库 RAG 架构](docs/KNOWLEDGE_RAG_ARCHITECTURE.md)

---

## 说明

- 本项目不内置任何企业数据。文档中出现的名称、资质编号、金额、联系方式等，均为用户自行上传或录入的内容。
- 生成结果仍需人工复核，尤其是证号、金额、日期与资质有效期等关键字段。
- 使用第三方地图、模型与云服务时，请遵守对应服务商的使用条款与数据合规要求。

## 许可证

本项目暂未指定开源许可证。如需商用或二次分发，请先与作者联系。
