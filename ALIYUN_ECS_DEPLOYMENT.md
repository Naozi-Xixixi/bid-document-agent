# 阿里云 ECS 部署手册

本文面向 `new_biaoshuai` 智能标书系统的单机 ECS 部署，默认使用 Ubuntu、Nginx、systemd、SQLite、本地 Mermaid 运行时，以及可选 Milvus 向量库。

## 1. 项目情况

`new_biaoshuai` 是 FastAPI + SQLite + React + TypeScript + Vite 架构的智能标书系统，包含四个独立模块：招标文件和大纲生成、企业知识库入库、Mermaid 图表生成、标书智能审核。系统文件存储位于 `backend/data/uploads/`，SQLite 数据库位于 `backend/data/wordagent.sqlite3`，Mermaid 使用交付包内本地 `mermaid-develop/packages/mermaid/dist`，不依赖外网 CDN。

推荐 ECS：测试演示用 2 核 4GB；正式小团队用 4 核 8GB；开启 Milvus 建议 4 核 16GB 起，并预留 100GB+ 数据盘。

## 2. 本地打包

在 Windows 开发机执行：

```powershell
cd "C:\path\to\bid-document-agent"
.\scripts\package-release.ps1
```

压缩包输出到：

```text
C:\path\to\bid-document-agent\.packaging\new_biaoshuai_deploy_YYYYMMDD-HHMMSS.zip
```

交付包不会包含真实 `.env`、数据库、上传文件、虚拟环境、`node_modules`、构建产物、日志和缓存。

## 3. ECS 准备

推荐系统：Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS。

安全组建议：正式部署只放行 `22`、`80`、`443`；测试时如果不用 Nginx，可临时放行 `3896` 后端端口和 `5181` 前端端口。

## 4. 上传并解压

本地 PowerShell 上传：

```powershell
scp "C:\path\to\bid-document-agent\.packaging\new_biaoshuai_deploy_YYYYMMDD-HHMMSS.zip" root@你的ECS公网IP:/root/
```

登录 ECS：

```bash
ssh root@你的ECS公网IP
```

安装基础工具并解压到 `/opt`：

```bash
apt update
apt install -y unzip rsync curl build-essential nginx
unzip /root/new_biaoshuai_deploy_YYYYMMDD-HHMMSS.zip -d /root/new_biaoshuai_release
rsync -a /root/new_biaoshuai_release/new_biaoshuai_deploy_YYYYMMDD-HHMMSS/new_biaoshuai/ /opt/new_biaoshuai/
rsync -a /root/new_biaoshuai_release/new_biaoshuai_deploy_YYYYMMDD-HHMMSS/mermaid-develop/ /opt/mermaid-develop/
mkdir -p /opt/new_biaoshuai/backend/data/uploads
chown -R www-data:www-data /opt/new_biaoshuai
chmod -R u+rwX,g+rwX /opt/new_biaoshuai/backend/data
```

## 5. 安装运行环境

```bash
apt install -y python3 python3-venv python3-pip
python3 --version
```

安装 Node.js 20：

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
node -v
npm -v
```

## 6. 后端部署

```bash
cd /opt/new_biaoshuai/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.cloud.example .env
nano .env
```

关键配置：

```env
WORDAGENT_BRIDGE_HOST=127.0.0.1
WORDAGENT_BRIDGE_PORT=3896
WORDAGENT_BRIDGE_DATA_DIR=./data
WORDAGENT_BRIDGE_DATABASE_PATH=./data/wordagent.sqlite3
WORDAGENT_BRIDGE_CORS_ORIGINS=["*"]

QWEN_API_KEY=你的DashScope或兼容OpenAI接口Key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_GENERATION_MODEL=qwen3.7-flash
QWEN_OCR_ENABLED=False

VECTOR_BACKEND=local
MERMAID_DIST_DIR=/opt/mermaid-develop/packages/mermaid/dist
```

手工测试：

```bash
cd /opt/new_biaoshuai/backend
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 3896
```

另开 SSH 验证：

```bash
curl http://127.0.0.1:3896/api/health
```

验证成功后停止手工进程，改用 systemd。

## 7. systemd 后台运行

```bash
cp /opt/new_biaoshuai/deploy/aliyun/new-biaoshuai-backend.service.example /etc/systemd/system/new-biaoshuai-backend.service
systemctl daemon-reload
systemctl enable --now new-biaoshuai-backend
systemctl status new-biaoshuai-backend --no-pager
```

查看日志：

```bash
journalctl -u new-biaoshuai-backend -f
```

## 8. 前端构建

```bash
cd /opt/new_biaoshuai/frontend
cp .env.example .env.local
nano .env.local
```

生产同域代理推荐：

```env
VITE_API_BASE=/api
```

构建：

```bash
npm install
npm run build
```

## 9. Nginx 配置

```bash
cp /opt/new_biaoshuai/deploy/aliyun/nginx-new-biaoshuai.conf.example /etc/nginx/sites-available/new-biaoshuai.conf
ln -sf /etc/nginx/sites-available/new-biaoshuai.conf /etc/nginx/sites-enabled/new-biaoshuai.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
```

访问：

```text
http://你的ECS公网IP/
```

## 10. 可选：启用 Milvus

安装 Docker 后启动 Milvus：

```bash
cd /opt/new_biaoshuai/deploy/milvus
docker compose up -d
docker compose ps
```

安装 Python 客户端：

```bash
cd /opt/new_biaoshuai/backend
source .venv/bin/activate
python -m pip install -r requirements-milvus.txt
```

修改 `/opt/new_biaoshuai/backend/.env`：

```env
VECTOR_BACKEND=milvus
VECTOR_EMBEDDING_PROVIDER=qwen
QWEN_EMBEDDING_MODEL=text-embedding-v4
QWEN_EMBEDDING_DIMENSIONS=1024
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION_KNOWLEDGE=new_biaoshuai_knowledge_chunks
MILVUS_COLLECTION_RAG_CASES=new_biaoshuai_rag_case_chunks
```

重启后端：

```bash
systemctl restart new-biaoshuai-backend
```

新上传资料会自动写入 Milvus；历史资料可通过项目内重建脚本或重新入库补索引。

## 11. 可选：安装 OCR

当前 Qwen OCR 已默认关闭；PaddleOCR 是扫描件增强项，需要时安装：

```bash
cd /opt/new_biaoshuai/backend
source .venv/bin/activate
python -m pip install -r requirements-ocr.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python -c "import paddleocr; print('paddleocr ok')"
systemctl restart new-biaoshuai-backend
```

如果日志提示 `No module named 'paddle'`，说明 `paddlepaddle` 没安装到当前 `.venv`。

## 12. 运维命令

```bash
systemctl status new-biaoshuai-backend --no-pager
systemctl restart new-biaoshuai-backend
systemctl reload nginx
journalctl -u new-biaoshuai-backend -n 200 --no-pager
journalctl -u new-biaoshuai-backend -f
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

## 13. 数据备份

核心数据目录：

```text
/opt/new_biaoshuai/backend/data/
```

备份：

```bash
mkdir -p /root/new_biaoshuai_backup
systemctl stop new-biaoshuai-backend
tar -czf /root/new_biaoshuai_backup/data_$(date +%Y%m%d_%H%M%S).tar.gz -C /opt/new_biaoshuai/backend data
systemctl start new-biaoshuai-backend
```

## 14. 常见问题

- 页面打开但接口失败：检查 `VITE_API_BASE=/api`、`systemctl status new-biaoshuai-backend`、`nginx -t`、ECS 安全组 80 端口。
- 上传大文件失败：修改 Nginx `client_max_body_size`，再执行 `nginx -t && systemctl reload nginx`。
- 图片 404：检查 `/uploads/` 代理和 `/opt/new_biaoshuai/backend/data/uploads/` 权限；旧坏路径可在知识库页面执行“一键清理冗余图片”。
- 大模型不调用：检查 `QWEN_API_KEY` 和后端日志。
- Mermaid 图表不显示：检查 `MERMAID_DIST_DIR` 和 `/opt/mermaid-develop/packages/mermaid/dist/mermaid.min.js`。

## 15. 正式上线建议

- 绑定域名并配置 HTTPS。
- 限制安全组来源 IP，避免公开测试系统消耗模型额度。
- 定期备份 `backend/data/`。
- 大并发入库或多人同时使用时，优先把 SQLite 升级为 PostgreSQL；Milvus 只解决向量检索，不替代业务库。
