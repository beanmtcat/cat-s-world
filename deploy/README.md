# 生产发布配置

这个目录留给生产环境的私有配置。建议放置：

- `.env`：数据库、对象存储、邮件、会话等生产环境变量；可参考本目录的 `.env.example`。
- `docker-compose.yml`：生产服务器的 Compose 配置；可参考本目录的 `docker-compose.example.yml`（完整独立服务模板）。
- `secrets/`：确需以文件形式保存的密钥或证书。

以上实际配置默认不进入 Git。不要把本地数据库目录、数据库备份或 SSH 私钥放进版本库。

示例 Compose 的构建路径以仓库根目录为准；从根目录检查模板时使用 `docker compose --project-directory . --env-file deploy/.env.example -f deploy/docker-compose.example.yml config -q`。当前生产配置复用已有的 PostgreSQL、Redis 和 MinIO，不应直接用完整独立服务模板覆盖它。

现有生产 PostgreSQL 不由这个 Compose 启动。先在 `deploy/.env` 填写 `DB_HOST`（API 容器能访问的数据库地址）、`DB_PORT`（API 使用的端口）、现有 PostgreSQL 容器的准确名称 `DB_CONTAINER_NAME`、容器内端口 `DB_CONTAINER_PORT`（通常是 5432），以及应用专用的 `POSTGRES_USER`、`POSTGRES_DB` 和 `POSTGRES_PASSWORD`。建库和导入脚本直接在该容器内运行 `psql`、`createdb`、`pg_restore`，不依赖服务器安装 PostgreSQL 客户端，也不会启动新的数据库容器。原 `DB_CLI_HOST` 不再使用。还须配置 PostgreSQL 的监听地址、`pg_hba.conf` 和防火墙，只放行所需来源。

发布脚本通过 SSH 把 `.env`、`docker-compose.yml` 和建库脚本安装到服务器项目目录根部；可选的 `secrets/` 也会上传。初次发布顺序：

1. 填写 `deploy/.env` 中 `DB_CONTAINER_NAME`、数据库与 SSH 配置，再运行 `./deploy.sh config`（只上传，不启动容器；此阶段允许域名和邮件配置暂未填写）。这些 SSH 参数会被两个本机脚本自动读取。
2. 在 `deploy/.env` 填写已有 PostgreSQL 管理员的 `DB_ADMIN_USER` 和 `DB_ADMIN_PASSWORD`，上传配置后，在服务器项目目录运行 `./provision-production-db.sh`。管理员密码如含 `$` 等特殊字符，请用单引号包裹以符合 Compose `.env` 格式。
3. 回到本机确认 `deploy/.env` 中的 `CONFIRM_PRODUCTION_DB_IMPORT=YES`，然后运行 `./migrate-production-db.sh`，仅向空库导入。本地备份会保留在 `content/backups/`。本地 PostgreSQL 16 备份由临时的 `postgres:16-alpine` 客户端容器恢复到现有数据库，服务器首次运行可能需要下载这个镜像；它不会创建新数据库服务。若目标 PostgreSQL 为 15，跨大版本恢复并非官方保证兼容，脚本使用单事务恢复，SQL 不兼容时会回滚并报错。
4. 确认导入成功后运行 `./deploy.sh all` 启动应用。

建库后清空本地 `deploy/.env` 中的 `DB_ADMIN_PASSWORD`，再运行 `./deploy.sh config` 清除服务器上的副本；应用和后续数据导入只使用 `POSTGRES_USER`/`POSTGRES_PASSWORD`。迁移脚本不会清空现有库，也不会复制 MinIO 对象；附件需要另行迁移。Redis 和 MinIO 复用现有服务，不由当前生产 Compose 创建；`.env` 中对应的密码和密钥必须与现有服务一致。发布脚本每次都会重新上传本地 `deploy/.env`，不要只在服务器修改生产配置。
