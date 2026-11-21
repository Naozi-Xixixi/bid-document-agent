# Gitee upload and deployment

This repository includes a Docker Compose deployment for a standard single-machine setup. The web application uses SQLite for business and authentication data, Qdrant for vectors, Ollama with `bge-m3` for local embeddings, and Qwen/DashScope for document interpretation and generation. Database files, uploaded bid documents, model weights, and API keys must stay out of Gitee.

## Prepare a private Gitee repository

Create an empty private repository in the company's Gitee organization. In PowerShell, from this project directory:

```powershell
git init
git branch -M main
git status --short
```

Review the output before staging. It must not contain `.env` files, `backend/data`, customer documents under `backend/app/data`, SQLite files, logs, backups, `frontend/output`, `node_modules`, `.venv`, or model files. Ignore rules exclude these. The SmartArt manifest and application-owned Word templates under `frontend/src/assets/bid-templates` are application assets.

Then stage, inspect the staged list, commit, add the repository URL supplied by your Gitee administrator, and push:

```powershell
git add .
git status --short
git diff --cached --stat
git commit -m "Prepare deployable bid assistant"
git remote add origin <enterprise-gitee-repository-url>
git push -u origin main
```

Do not put access tokens in the remote URL or Git history. Use the organization's approved SSH key or Gitee credential manager. If a real API key was committed before, revoke and replace it; deleting the file in a later commit does not remove it from Git history.

## Deploy with Docker Compose

Install Docker Engine/Desktop with Compose support. Create runtime settings and set a Qwen API key and unique administrator password of at least 12 characters:

```powershell
Copy-Item backend/.env.example backend/.env
Copy-Item .env.example .env
notepad backend/.env
```

Set `QWEN_API_KEY` and `ADMIN_INITIAL_PASSWORD` in `backend/.env`. The password is used only when the first administrator is created. Map credentials are optional; set `VITE_AMAP_JS_KEY` and `VITE_AMAP_SECURITY_CODE` in the root `.env` before building if the vehicle route page is needed. Browser map keys are visible to users; restrict them to the deployed domains in the AMap console.

Start the application:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f backend
```

Open `http://localhost:8080`. The first build downloads dependencies and the first start downloads Ollama's `bge-m3` model. Later starts reuse named volumes. Nginx exposes the application through one origin; MySQL, Qdrant, and Ollama ports are not published to the network.

For an NVIDIA GPU host with Docker GPU support installed:

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
```

On Windows, GPU passthrough depends on Docker Desktop's WSL2/NVIDIA setup. The standard Compose setup runs Ollama on CPU when GPU support is unavailable. With an RTX 2070 Super, native Windows Ollama may perform better; to use it, set the backend service's `OLLAMA_BASE_URL` override to `http://host.docker.internal:11434` and configure Ollama to accept Docker Desktop connections.

Checks:

```powershell
Invoke-RestMethod http://localhost:8080/api/health
docker compose exec ollama ollama list
docker compose logs --tail 100 backend
```

Stop without deleting database, upload, vector, or model data:

```powershell
docker compose down
```

Do not add `-v` unless you intend to delete named data volumes. Back up `app_data` and `qdrant_data` together. Keep `ollama_data` to avoid downloading the model again.

## MySQL and production operation

The Compose quick start defaults to SQLite so a clone can run without database setup. SQLite stores tenant data in per-company files and is suitable for local evaluation or light single-host use. For production multi-company deployment with concurrent document processing, follow the [MySQL migration and setup guide](../MYSQL_MIGRATION.md), configure a MySQL account able to create and access the per-company databases, set `DB_BACKEND=mysql`, and point `MYSQL_HOST` at that server. Do not expose MySQL publicly. Test backup and restore before moving company data.

Do not only flip `DB_BACKEND`: migrate the authentication database and each tenant database, and preserve uploads. Embeddings are separate from MySQL; after Qdrant and Ollama are ready, use the authenticated knowledge reindex endpoint to rebuild vectors from stored chunks.

This Compose setup is single-node, not high availability. Before public production use, configure HTTPS, scheduled encrypted backups, monitoring, firewall rules, and a tested restore procedure. Store `.env` values in a secret manager or a file with restricted permissions.
