# SQLite 到 MySQL 迁移

当前项目仍保留 SQLite 回退模式。MySQL 模式按现有多公司设计使用独立数据库：认证库为 `wordagent_auth`，默认公司为 `wordagent_company_default`，其他公司为 `wordagent_company_<company_id>`。上传文件、模板和生成文件继续保存在本机 `backend/data` 目录，不会被复制进数据库。

## 1. 确认 MySQL 服务

在 PowerShell 执行：

```powershell
Get-Service MySQL
& 'D:\HJW\develop\mysql-8.0.34-winx64\bin\mysql.exe' --version
```

服务已经安装时只需保持 `Running`。如果不是运行状态，先启动 MySQL 服务。

## 2. 创建应用账号

用已有 MySQL 管理员账号执行 [backend/scripts/mysql_create_user.sql](backend/scripts/mysql_create_user.sql)。请先把脚本中的 `CHANGE_ME_STRONG_PASSWORD` 改为强密码。也可以直接执行：

```powershell
Get-Content backend/scripts/mysql_create_user.sql -Raw | & 'D:\HJW\develop\mysql-8.0.34-winx64\bin\mysql.exe' -h 127.0.0.1 -P 3306 -u root -p
```

如果当前没有管理员密码，先找回或重置 MySQL 管理员密码；不要把密码写进仓库脚本或提交到版本库。

## 3. 配置迁移连接

把 [backend/.env.mysql.example](backend/.env.mysql.example) 中的变量复制到 `backend/.env`，填写实际 `MYSQL_PASSWORD`，但暂时不要启动后端。保持现有 `WORDAGENT_BRIDGE_DATA_DIR=./data`，这样迁移脚本能找到原 SQLite 文件。

先验证连接：

```powershell
cd backend
& .\.venv\Scripts\python.exe -c "import pymysql; from app.core.config import settings; c=pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user, password=settings.mysql_password); print(c.get_server_info()); c.close()"
```

## 4. 迁移数据

确认后端已经停止，再执行：

```powershell
cd backend
& .\.venv\Scripts\python.exe scripts/migrate_sqlite_to_mysql.py
```

脚本会迁移 `auth.sqlite3`、默认公司数据库以及认证库中登记的其他公司数据库。脚本使用 `INSERT IGNORE`，可以在中断后重复执行；目标库已有记录不会被覆盖。迁移前建议复制整个 `backend/data` 目录做备份。

只迁移一个公司时：

```powershell
& .\.venv\Scripts\python.exe scripts/migrate_sqlite_to_mysql.py --company-id company_xxxxxxxx
```

## 5. 自检目标库

用应用账号检查库和行数：

```powershell
& 'D:\HJW\develop\mysql-8.0.34-winx64\bin\mysql.exe' -h 127.0.0.1 -P 3306 -u wordagent -p -e "SHOW DATABASES LIKE 'wordagent%';"
```

重点确认 `auth_user`、`auth_company`、`projects`、`bids`、`knowledge_item`、`knowledge_asset`、`bid_catalog_chapters`、`bid_chapter_contents` 存在且行数与 SQLite 基本一致。

## 6. 启动 MySQL 模式

在 `backend/.env` 保持 `DB_BACKEND=mysql`，然后按原方式启动：

```powershell
cd backend
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 3896
```

先登录管理员，再验证公司列表、普通账号登录、知识库列表、历史项目、上传、正文生成和 Word 导出。上传文件路径仍走 `/uploads`，数据库切换不会移动历史文件。

## 7. 回退 SQLite

如果联调发现问题，停止服务，在 `backend/.env` 把 `DB_BACKEND=mysql` 改回 `DB_BACKEND=sqlite`，保留原 `WORDAGENT_BRIDGE_DATABASE_PATH=./data/wordagent.sqlite3`，重新启动即可。MySQL 目标库不会影响 SQLite 原文件。

## 当前边界

- 需要有效的 MySQL 管理员账号执行一次用户创建；当前机器虽然 MySQL 服务运行，但未提供管理员密码，因此建库和真实数据导入还不能替你执行。
- 现有代码大量使用 SQLite 兼容 SQL，适配层已保留 `?` 参数、行下标访问、`lastrowid` 和常用 `ON CONFLICT` 写法；切换后仍需完成真实库联调。
- Milvus 向量集合不属于 MySQL 迁移范围。若启用 Milvus，需要按公司集合名称单独保留或重建向量索引。
- SQLite 和 MySQL 的数据库内容迁移不包含本地上传文件、模板和导出文件，但这些路径仍由 `backend/data` 保留并继续使用。
