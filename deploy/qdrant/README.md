# WordAgent 单机 Qdrant

## 作用

MySQL 保存知识库原文、分块、分类、项目关系和结构化字段；Qdrant 保存由当前配置的 Embedding 模型生成的向量和检索用 Payload。Qdrant 不替代 MySQL，删除或重新解析知识时由后端同步更新两边。

## 启动

在安装 Docker Desktop 后，在项目根目录执行：

```powershell
docker compose -f deploy/qdrant/docker-compose.yml up -d
Invoke-RestMethod http://127.0.0.1:6333
```

停止：

```powershell
docker compose -f deploy/qdrant/docker-compose.yml down
```

数据保存在 Docker volume `qdrant_qdrant_storage` 中。不要使用 `down -v`，否则会删除向量数据。

## 后端配置

安装 Qdrant 客户端：

```powershell
cd backend
.\.venv\Scripts\pip.exe install -r requirements-qdrant.txt
```

在 `backend/.env` 中设置：

```env
VECTOR_BACKEND=qdrant
VECTOR_EMBEDDING_PROVIDER=qwen
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_KNOWLEDGE=wordagent_knowledge_chunks_v1
QDRANT_COLLECTION_RAG_CASES=wordagent_rag_case_chunks_v1
QWEN_EMBEDDING_MODEL=text-embedding-v4
QWEN_EMBEDDING_DIMENSIONS=1024
```

如果暂时没有安装 Docker，可以先使用 Qdrant 客户端的本地持久化模式，适合开发和单进程演示：

```env
VECTOR_BACKEND=qdrant
VECTOR_EMBEDDING_PROVIDER=ollama
QDRANT_PATH=./data/qdrant
QDRANT_URL=
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_EMBEDDING_MODEL=bge-m3
OLLAMA_EMBEDDING_DIMENSIONS=1024
OLLAMA_EMBEDDING_BATCH_SIZE=16
```

本地持久化模式只支持一个后端进程，不要启用多个 worker；正式长期运行建议使用 Docker Qdrant 服务。

## Ollama 本地向量模型

安装 Ollama 后执行：

```powershell
ollama pull bge-m3
ollama list
```

`qwen3:4b` 可继续用于本地文本生成；向量检索使用专门的 `bge-m3`。本机 RTX 2070 Super 已验证 16 条批量输入可正常返回 1024 维向量；如果显存不足，把 `OLLAMA_EMBEDDING_BATCH_SIZE` 调到 4 或 8。

验证 Ollama Embedding：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:11434/api/embed `
  -ContentType 'application/json' `
  -Body '{"model":"bge-m3","input":["冷链车辆配置方案"]}'
```

返回的 `embeddings` 数组非空后，再执行 Qdrant 索引重建。

首次启用时使用带 `_v1` 的新集合，避免把原来 128 维哈希向量与所选 Embedding 模型的向量混在一起。模型或维度变更时，使用新版本集合，例如 `_v2`，重建完成后再切换配置。

## 重建索引

启动后端后，先通过登录接口取得会话 Cookie。下面的命令会安全提示输入账号密码，不要把真实密码写进命令或 PowerShell 历史：

```powershell
$credential = Get-Credential
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$loginBody = @{
  username = $credential.UserName
  password = $credential.GetNetworkCredential().Password
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3896/api/auth/login `
  -ContentType 'application/json' -Body $loginBody -WebSession $session | Out-Null
```

先查看当前进程实际加载的状态：

```powershell
$diagnostics = Invoke-RestMethod -Uri http://127.0.0.1:3896/api/knowledge/diagnostics -WebSession $session
$diagnostics.data | Select-Object vector_backend, embedding_provider, embedding_model, embedding_dimension, vector_store_available
```

必须看到：

```text
vector_backend = qdrant
vector_store_available = True
embedding_provider = ollama
embedding_dimension = 1024
```

确认配置正确后再重建索引：

```powershell
$result = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3896/api/knowledge/vector/reindex -WebSession $session
$result.data | ConvertTo-Json -Depth 5
```

重建会读取 MySQL 中已完成的知识分块和历史标书分块，由本机 Ollama 逐批生成向量，不调用 Embedding 云端 API。2070 Super 重建数千分块可能需要较长时间；重建期间不要同时修改大批量知识素材。

## 单机边界

- 目前使用一个共享集合，通过 `company_id` Payload 过滤不同公司的数据。
- 后端每次检索都会先生成 Qwen 查询向量，再由 Qdrant 召回；结果回到 MySQL 读取完整正文和结构化字段。
- 关键词检索与向量检索采用混合排序，证书编号、专有名词和评分点仍依赖关键词命中。
- Qdrant 或当前配置的 Embedding 模型不可用时，应用保留现有本地检索兜底；不会把本地哈希向量写进 Qdrant。
- 生产使用前应配置 Qdrant API Key、备份快照和磁盘监控。
