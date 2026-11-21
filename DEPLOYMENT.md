# 部署手册

> **推荐部署方式：**请优先使用 [Gitee 部署指南](docs/GITEE_DEPLOYMENT.md) 中的 Docker Compose 流程。下方手动部署内容为历史说明，其中 Mermaid 外部目录和旧向量库配置可能已过时。

本文说明如何把 `new_biaoshuai` 部署为别人可以直接使用的智能标书系统。

## 1. 交付包结构

解压后建议保持以下结构：

```text
部署目录/
├─ new_biaoshuai/
│  ├─ backend/
│  ├─ frontend/
│  ├─ scripts/
│  ├─ README.md
│  └─ DEPLOYMENT.md
└─ mermaid-develop/
   └─ packages/mermaid/dist/
```

`mermaid-develop/packages/mermaid/dist` 是图表模块的本地运行资源。后端会自动挂载为 `/mermaid`，前端通过该地址加载 Mermaid，不需要 CDN。

## 2. 本地演示部署（Windows）

### 2.1 安装基础环境

- 安装 Python 3.11。
- 安装 Node.js 20+。
- 准备 Qwen / DashScope API Key。

### 2.2 配置后端

```powershell
cd "部署目录\new_biaoshuai\backend"
Copy-Item .env.example .env
notepad .env
```

填写：

```env
QWEN_API_KEY=你的真实APIKey
```

如需限制前端域名，可把：

```env
WORDAGENT_BRIDGE_CORS_ORIGINS=["*"]
```

改为：

```env
WORDAGENT_BRIDGE_CORS_ORIGINS=["http://127.0.0.1:5181"]
```

### 2.3 启动后端

```powershell
cd "部署目录\new_biaoshuai"
.\scripts\start-backend.ps1
```

首次运行会创建 `.venv` 并安装基础依赖。启动成功后访问：

```text
http://127.0.0.1:3896/api/health
```

### 2.4 启动前端

打开另一个 PowerShell：

```powershell
cd "部署目录\new_biaoshuai\frontend"
Copy-Item .env.example .env.local
cd ..
.\scripts\start-frontend.ps1
```

访问：

```text
http://127.0.0.1:5181/
```

## 3. Linux / 云服务器部署

### 3.1 后端

```bash
cd /opt/new_biaoshuai/backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.cloud.example .env
vim .env
python -m uvicorn app.main:app --host 0.0.0.0 --port 3896
```

生产环境建议用 systemd/supervisor/pm2 等进程管理器托管后端。

### 3.2 前端

```bash
cd /opt/new_biaoshuai/frontend
cp .env.example .env.local
npm ci
npm run build
```

构建产物在：

```text
frontend/dist/
```

可由 Nginx、Apache 或对象存储静态托管。

### 3.3 Nginx 反向代理示例

以下示例假设前端静态文件位于 `/opt/new_biaoshuai/frontend/dist`，后端监听 `127.0.0.1:3896`。

```nginx
server {
    listen 80;
    server_name your-domain.com;

    root /opt/new_biaoshuai/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:3896/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }

    location /uploads/ {
        proxy_pass http://127.0.0.1:3896/uploads/;
    }

    location /mermaid/ {
        proxy_pass http://127.0.0.1:3896/mermaid/;
    }
}
```

前端生产环境 `.env.local` 可写：

```env
VITE_API_BASE=/api
```

如果前后端不在同域，写完整后端地址：

```env
VITE_API_BASE=https://api.your-domain.com/api
```

## 4. OCR 部署

基础系统不依赖 PaddleOCR。若客户需要扫描件全文识别，在后端虚拟环境中安装：

Windows：

```powershell
cd "部署目录\new_biaoshuai\backend"
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-ocr.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Linux：

```bash
cd /opt/new_biaoshuai/backend
source .venv/bin/activate
python -m pip install -r requirements-ocr.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证：

```bash
python -c "import paddleocr; print('paddleocr ok')"
```

若仍提示 `No module named 'paddle'`，表示 `paddlepaddle` 没有安装到当前后端运行环境。

## 5. Milvus 向量库部署

Milvus 是可选增强项。不开启时系统继续使用 SQLite 中的本地检索兜底；开启后，企业知识库分块和历史案例分块会同步写入 Milvus，检索时优先用 Milvus 找候选，再回 SQLite 查询完整业务数据。

### 5.1 启动 Milvus

如果服务器已安装 Docker：

```powershell
cd "部署目录\new_biaoshuai\deploy\milvus"
docker compose up -d
```

默认端口：

```text
19530
```

### 5.2 安装 Python 客户端

```powershell
cd "部署目录\new_biaoshuai\backend"
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-milvus.txt
```

### 5.3 打开 Milvus 开关

编辑 `backend/.env`：

```env
VECTOR_BACKEND=milvus
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION_KNOWLEDGE=new_biaoshuai_knowledge_chunks
MILVUS_COLLECTION_RAG_CASES=new_biaoshuai_rag_case_chunks
MILVUS_SEARCH_MULTIPLIER=30

# 推荐用于真实语义检索；如果留 local，则使用本地 hash 向量写入 Milvus。
VECTOR_EMBEDDING_PROVIDER=qwen
QWEN_EMBEDDING_MODEL=text-embedding-v4
QWEN_EMBEDDING_DIMENSIONS=1024
QWEN_EMBEDDING_BATCH_SIZE=10
```

重启后端。

### 5.4 同步历史数据

```powershell
cd "部署目录\new_biaoshuai"
.\scripts\reindex-milvus.ps1
```

同步完成后访问诊断接口：

```text
http://127.0.0.1:3896/api/knowledge/diagnostics
```

看到 `vector_backend=milvus` 且 `vector_store_available=true` 即表示 Milvus 接入成功。

## 6. 数据目录和备份

默认运行数据目录：

```text
backend/data/
├─ wordagent.sqlite3       SQLite 数据库
├─ uploads/                上传文件、图片资源、审核文件
└─ projects/               项目过程文件
```

备份时至少保存：

- `backend/data/wordagent.sqlite3`
- `backend/data/uploads/`

恢复时关闭后端服务，覆盖上述文件后重新启动。

## 7. 发布包生成

在开发机执行：

```powershell
cd "C:\path\to\bid-document-agent"
.\scripts\package-release.ps1
```

输出目录：

```text
C:\path\to\bid-document-agent\.packaging\
```

发布包不会包含：

- `backend/.env` 真实密钥
- `backend/data/` 客户数据和数据库
- `backend/.venv/` Python 虚拟环境
- `frontend/node_modules/` 前端依赖
- `frontend/dist/` 构建产物
- 日志、缓存、临时文件

## 8. 验证清单

部署完成后按顺序检查：

1. `GET /api/health` 返回成功。
2. 前端首页能打开四个功能模块。
3. 招标文件上传解析正常。
4. 大纲生成、编辑、下载正常。
5. 知识库上传、检索、图片查看正常。
6. Mermaid 图表能生成并预览；`/mermaid/mermaid.min.js` 可访问。
7. 图文正文可按章节生成，知识库图片能真实显示。
8. 标书智能审核可上传两份文件并输出报告。
9. `npm run build` 通过。
10. 后端 `python -m compileall app` 通过。

## 9. 常见运维问题

### 端口占用

Windows：

```powershell
netstat -ano | findstr :3896
taskkill /PID 进程号 /F
```

或运行：

```powershell
.\scripts\stop-dev-ports.ps1
```

Linux：

```bash
lsof -i :3896
kill -9 进程号
```

### SQLite database is locked

当前系统已做超时、重试和并发限制，但 SQLite 仍更适合单机轻量部署。如果客户长期高并发批量入库，建议后续把数据库迁移到 PostgreSQL。

### 大模型调用失败

检查：

- `QWEN_API_KEY` 是否填写。
- 服务器是否能访问 `QWEN_BASE_URL`。
- 模型名是否在账号权限内。
- 后端日志中的错误信息。

### Mermaid 图表无法加载

确认：

- `mermaid-develop/packages/mermaid/dist/mermaid.min.js` 存在。
- 后端已启动。
- 浏览器能访问 `/mermaid/mermaid.min.js`。
- 如果目录不标准，在 `backend/.env` 设置 `MERMAID_DIST_DIR`。
