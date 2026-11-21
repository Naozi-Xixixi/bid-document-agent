# 多公司账号

系统现已支持一个系统管理员管理多个公司和公司账号。普通账号登录后只访问所属公司的业务数据；同一公司的账号共享该公司的项目、知识库、上传文件和模型配置。

## 首次启动

从 `backend/.env.example` 复制配置到 `backend/.env`，并在第一次启动后端之前设置 `ADMIN_INITIAL_USERNAME` 和强密码 `ADMIN_INITIAL_PASSWORD`。如果尚未设置，后端默认创建 `admin` 账号，默认密码为 `admin123456`。管理员首次创建后，改动环境变量不会重置现有账号密码；后续通过「账号管理」里的密码重置操作修改。

让局域网内其他公司访问时，将 `WORDAGENT_BRIDGE_HOST` 设为 `0.0.0.0`，并把 `WORDAGENT_BRIDGE_CORS_ORIGINS` 改成实际前端来源，例如 `http://192.168.1.20:5181`。同时开放后端端口 `3896` 和前端端口 `5181`。跨公网部署应通过 HTTPS 反向代理提供前后端服务，并为前端域名配置 CORS；不要直接暴露未加密的 HTTP 登录页面。

运行前端并登录管理员账号，进入首页的「账号管理」：

1. 新建公司。
2. 在「新增账号」中填写账号、初始密码、显示名称和所属公司。
3. 向普通用户分配「公司用户」角色；只有受信任的管理人员应分配「管理员」角色。
4. 可停用公司或账号。停用后现有登录会话失效；重新启用后，用户需要重新登录。

## 数据位置

数据库位置取决于 `DB_BACKEND`：

| 内容 | `DB_BACKEND=sqlite` | `DB_BACKEND=mysql` |
| --- | --- | --- |
| 管理员、公司、账号和登录会话 | `backend/data/auth.sqlite3` | MySQL 数据库 `MYSQL_AUTH_DATABASE`，默认 `wordagent_auth` |
| 公司业务数据 | `backend/data/wordagent.sqlite3` 和 `backend/data/companies/<company_id>/wordagent.sqlite3` | 每家公司独立 MySQL 数据库：`<MYSQL_DATABASE_PREFIX>_<company_id>`；默认公司使用 `<MYSQL_DATABASE_PREFIX>_default` |
| 上传文件、生成文件和模板等本地资源 | `backend/data/uploads/`，公司上传文件位于 `backend/data/uploads/companies/<company_id>/` | 仍保存在服务器本地对应目录，按公司隔离 |

SQLite 模式下，现有 `wordagent.sqlite3` 和历史上传文件继续归属默认公司，不搬迁、不重建。MySQL 模式下，业务数据写入按公司划分的 MySQL 数据库；首次访问时初始化所需表。公司账号和上传文件仍由本地部署管理，切换数据库后上传目录不会自动迁移。启用 Qdrant 或 Milvus 时，向量索引独立于 MySQL 业务库，需另行备份；确认向量集合/collection 的租户隔离配置后再用于多公司生产环境。

备份和恢复时，SQLite 模式应将 `auth.sqlite3`、各公司的 `wordagent.sqlite3` 和上传目录一并处理；MySQL 模式应备份 `MYSQL_AUTH_DATABASE`、所有 `MYSQL_DATABASE_PREFIX_*` 公司库及上传目录。另行备份 Qdrant/Milvus 数据。环境变量中的模型 API 密钥由部署环境提供；公司配置的模型参数保存在公司业务数据中。

## 登录与接口

前端首次访问会显示登录页。浏览器通过 HttpOnly Cookie 携带会话；后端同时接受 Bearer token 和旧版查询参数认证，便于兼容已有调用方式。退出登录、停用账号、停用公司都会清除或拒绝对应会话。

管理员接口位于 `/api/auth/admin/companies` 与 `/api/auth/admin/users`。公司业务 API 必须登录。上传文件通过 `/uploads/...` 读取，并按登录账号的公司验证文件路径；默认公司不能访问新公司的上传目录。

## MySQL 模式切换

1. 备份当前数据库和 `backend/data/uploads/`。
2. 按 `MYSQL_MIGRATION.md` 创建 MySQL 用户和数据库，并将 `backend/.env.mysql.example` 中配置合并至 `backend/.env`，填写真实密码并设置 `DB_BACKEND=mysql`。
3. 在切换前运行 `backend/scripts/migrate_sqlite_to_mysql.py`，将 SQLite 管理数据和默认公司业务数据迁入对应 MySQL 数据库；完成后校验迁移报告与数据条数。
4. 重启后端，验证管理员登录、两家测试公司的数据隔离、文件访问、知识库读写和备份恢复。仅认证表迁移成功不代表全部业务链路已验收。

MySQL 模式仍需要本地磁盘保存上传文件、模板、导出文档及部分本地运行状态；MySQL 不是这些文件的对象存储替代品。
