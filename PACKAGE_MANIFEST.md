# 交付包清单

本文说明 `scripts/package-release.ps1` 生成的交付包包含什么、排除什么，以及交付给他人前需要检查什么。

## 1. 包含内容

```text
new_biaoshuai_deploy_YYYYMMDD-HHMMSS.zip
└─ new_biaoshuai_deploy_YYYYMMDD-HHMMSS/
   ├─ README.md              顶层快速说明
   ├─ DEPLOYMENT.md          顶层部署手册
   ├─ PACKAGE_MANIFEST.md    顶层交付清单
   ├─ ALIYUN_ECS_DEPLOYMENT.md 阿里云 ECS 部署手册
   ├─ new_biaoshuai/
   │  ├─ backend/
   │  │  ├─ app/
   │  │  ├─ clients/
   │  │  ├─ scripts/
   │  │  ├─ requirements.txt
   │  │  ├─ requirements-ocr.txt
   │  │  ├─ requirements-milvus.txt
   │  │  ├─ requirements-linux.txt
   │  │  ├─ pyproject.toml
   │  │  ├─ uv.lock
   │  │  ├─ .env.example
   │  │  ├─ .env.cloud.example
   │  │  └─ README.md
   │  ├─ frontend/
   │  │  ├─ src/
   │  │  ├─ index.html
   │  │  ├─ package.json
   │  │  ├─ package-lock.json
   │  │  ├─ pnpm-lock.yaml
   │  │  ├─ tsconfig.json
   │  │  ├─ vite.config.ts
   │  │  └─ .env.example
   │  ├─ docs/
   │  ├─ scripts/
   │  ├─ deploy/milvus/docker-compose.yml
   │  ├─ deploy/aliyun/nginx-new-biaoshuai.conf.example
   │  ├─ deploy/aliyun/new-biaoshuai-backend.service.example
   │  ├─ README.md
   │  ├─ DEPLOYMENT.md
   │  ├─ PACKAGE_MANIFEST.md
   │  ├─ ALIYUN_ECS_DEPLOYMENT.md
   │  ├─ .gitignore
   │  └─ .gitattributes
   └─ mermaid-develop/
      ├─ LICENSE
      ├─ README.md
      ├─ README.zh-CN.md
      └─ packages/mermaid/dist/
```

## 2. 排除内容

以下内容不会进入交付包：

- `backend/.env`：真实 API Key 和本机配置。
- `backend/data/`、`backend/data_verify/`：数据库、上传文件、客户资料、测试数据。
- `backend/.venv/`、`backend/.uv-cache/`：本机 Python 环境和缓存。
- `frontend/node_modules/`：前端依赖。
- `frontend/dist/`：本机构建产物，部署时重新构建。
- `*.log`、`server-*.log`：运行日志。
- `*.sqlite3`、`*.db`、`*.db-wal`、`*.db-shm`：数据库文件。
- `__pycache__/`、`.vite/`、`.cache/` 等缓存。
- Mermaid 源码仓库中的 `node_modules`、测试、构建缓存等内容；只带运行所需 `packages/mermaid/dist`。

## 3. 交付前检查

交付前建议在开发机执行：

```powershell
cd "C:\path\to\bid-document-agent"
.\scripts\check-env.ps1

cd .\backend
.\.venv\Scripts\python.exe -m compileall app

cd ..\frontend
npm run build
```

再生成压缩包：

```powershell
cd "C:\path\to\bid-document-agent"
.\scripts\package-release.ps1
```

## 4. 接收方部署步骤

接收方只需要看两个文件：

1. `new_biaoshuai/README.md`：项目介绍和快速启动。
2. `new_biaoshuai/DEPLOYMENT.md`：本地/通用服务器部署细节。
3. `new_biaoshuai/ALIYUN_ECS_DEPLOYMENT.md`：阿里云 ECS 生产部署步骤。

最短启动路径：

```powershell
cd "部署目录\new_biaoshuai\backend"
Copy-Item .env.example .env
notepad .env

cd ..
.\scripts\start-backend.ps1
```

另开终端：

```powershell
cd "部署目录\new_biaoshuai\frontend"
Copy-Item .env.example .env.local
cd ..
.\scripts\start-frontend.ps1
```

## 5. 注意事项

- 发布包里不带真实 API Key；接收方必须自行填写 `backend/.env`。
- 发布包里不带历史数据；新部署会自动初始化 SQLite 表结构。
- 如果需要迁移历史资料，请单独交付 `backend/data/wordagent.sqlite3` 和 `backend/data/uploads/`，并注意客户数据安全。
- 如果客户需要扫描件 OCR，必须额外安装 `backend/requirements-ocr.txt`。
- 如果部署路径改变导致 Mermaid 不可访问，可在 `backend/.env` 中填写 `MERMAID_DIST_DIR`。
