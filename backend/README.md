# 后端服务说明

本目录是智能标书系统的 FastAPI 后端服务，负责项目数据、招标文件解析、目录生成、知识库入库、图文正文生成、Mermaid 图表资产、标书审核、Word/Markdown 导出等能力。

## 技术栈

- Python 3.11+
- FastAPI + Uvicorn
- SQLite 单文件数据库
- pdfplumber / PyMuPDF / python-docx 文档解析
- Pillow 图片处理
- 可选 PaddleOCR 扫描件识别
- 可选 Milvus 向量数据库
- Qwen / DashScope OpenAI-compatible API 大模型调用

## 目录说明

```text
backend/
├─ app/
│  ├─ api/              API 路由
│  ├─ core/             配置读取
│  ├─ modules/knowledge 企业知识库模块
│  ├─ schemas/          请求/响应模型
│  └─ services/         核心业务服务
├─ clients/             辅助客户端
├─ scripts/             后端工具脚本
├─ data/                运行时数据目录，交付包默认不包含
├─ requirements.txt     基础依赖
├─ requirements-ocr.txt 可选 OCR 依赖
├─ .env.example         本地配置模板
└─ .env.cloud.example   服务器配置模板
```

## 本地启动

推荐用 Python 3.11 创建全新的虚拟环境，不建议复用 Anaconda base 环境。

```powershell
cd "部署目录\new_biaoshuai\backend"
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 3896
```

健康检查：

```text
http://127.0.0.1:3896/api/health
```

## 关键环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WORDAGENT_BRIDGE_HOST` | `127.0.0.1` | 后端监听地址 |
| `WORDAGENT_BRIDGE_PORT` | `3896` | 后端监听端口 |
| `WORDAGENT_BRIDGE_DATA_DIR` | `./data` | 上传文件、数据库、项目资料存储目录 |
| `WORDAGENT_BRIDGE_DATABASE_PATH` | `./data/wordagent.sqlite3` | SQLite 数据库路径 |
| `WORDAGENT_BRIDGE_CORS_ORIGINS` | `["*"]` | 前端跨域来源 |
| `QWEN_API_KEY` | 空 | 大模型 API Key，生产环境必须填写 |
| `QWEN_BASE_URL` | DashScope 兼容地址 | OpenAI-compatible 接口地址 |
| `QWEN_GENERATION_MODEL` | `qwen3.7-flash` | 文本生成模型 |
| `QWEN_OCR_ENABLED` | `False` | 是否启用 Qwen OCR；当前默认关闭 |
| `VECTOR_BACKEND` | `local` | 向量检索后端，支持 `local` / `milvus` |
| `VECTOR_EMBEDDING_PROVIDER` | `local` | 向量生成方式，支持 `local` / `qwen` |
| `MILVUS_URI` | `http://127.0.0.1:19530` | Milvus 服务地址 |
| `MILVUS_COLLECTION_KNOWLEDGE` | `new_biaoshuai_knowledge_chunks` | 企业知识库向量集合 |
| `MILVUS_COLLECTION_RAG_CASES` | `new_biaoshuai_rag_case_chunks` | 历史案例向量集合 |
| `MERMAID_DIST_DIR` | 自动探测 | 本地 Mermaid dist 目录，可选 |
| `ENABLE_OFFICECLI_DOCX_FORMATTING` | `True` | 是否尝试使用 OfficeCLI 做 DOCX 后处理 |

## OCR 安装

基础部署不强制安装 OCR。未安装时，知识库仍会使用 PDF 文本层和普通图片信息提取逻辑，只是纯扫描件识别能力会受限。

需要 OCR 时，在后端虚拟环境中执行：

```powershell
python -m pip install -r requirements-ocr.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证 OCR 是否安装到当前环境：

```powershell
.\.venv\Scripts\python.exe -c "import paddleocr; print('paddleocr ok')"
```

如果看到：

```text
[KnowledgeOCR] PaddleOCR unavailable: No module named 'paddle'
```

说明当前后端解释器中没有 `paddlepaddle`，请重新在同一个 `.venv` 中安装 `requirements-ocr.txt`。

## Milvus 向量库

启用 Milvus 时，SQLite 仍作为业务数据库；Milvus 只负责 RAG 向量索引。这样即使 Milvus 暂时不可用，系统仍可降级到原本的本地检索。

安装依赖：

```powershell
python -m pip install -r requirements-milvus.txt
```

启动本地 Milvus：

```powershell
cd ..\deploy\milvus
docker compose up -d
```

编辑 `.env`：

```env
VECTOR_BACKEND=milvus
MILVUS_URI=http://127.0.0.1:19530
VECTOR_EMBEDDING_PROVIDER=qwen
QWEN_EMBEDDING_MODEL=text-embedding-v4
QWEN_EMBEDDING_DIMENSIONS=1024
```

重启后端后，同步历史索引：

```powershell
cd ..
.\scripts\reindex-milvus.ps1
```

诊断接口：

```text
GET /api/knowledge/diagnostics
```

## 数据库与运行数据

- 默认数据库：`backend/data/wordagent.sqlite3`
- 上传文件：`backend/data/uploads/`
- 知识库图片资源、审核文件、项目导出也会进入 `backend/data/`

交付包默认不包含 `data/`，避免把开发机历史文件、客户资料、数据库和密钥一起发出。新部署首次启动会自动初始化表结构。

## 常用接口

- `GET /api/health`：健康检查
- `GET /api/projects`：项目列表
- `POST /api/bid/{bid_id}/upload-and-parse`：上传并解析招标文件
- `GET /api/bid/{bid_id}/catalog`：获取大纲
- `POST /api/knowledge/items/upload`：知识库上传入库
- `GET /api/knowledge/assets`：图片资产列表
- `POST /api/bid/{bid_id}/graphic-section/generate`：生成图文正文
- `POST /api/audit/start`：启动标书智能审核

## 验证

```powershell
cd "部署目录\new_biaoshuai\backend"
.\.venv\Scripts\python.exe -m compileall app
```

生产运行建议使用进程管理工具托管 Uvicorn，并定期备份 `data/wordagent.sqlite3` 和 `data/uploads/`。
