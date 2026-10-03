# 猫子的世界

项目按前后端目录组织：

- `frontend/`：React + Vite 前端应用
- `backend/`：Python 3.12+ FastAPI、SQLAlchemy、Alembic、Redis 限流服务
- `content/`：Halo v2/Markdown 的受控导入、导出和离线备份目录（线上正文仍以 PostgreSQL 为准）
- `docs/系统设计.md`：数据、接口、安全、SEO、Halo 迁移与验收的唯一设计契约

在项目根目录执行：

```bash
npm run dev
npm run build
```

## 启动开发环境

前端：

```bash
npm run dev
```

后端：

```bash
cp backend/.env.example backend/.env
# 替换 .env 中的示例密钥后
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

Docker 化 PostgreSQL、Redis、MinIO、API 与前端的部署模板在 `deploy/docker-compose.example.yml`；部署前需要准备 `deploy/.env`。完整功能进度以 [系统设计](docs/系统设计.md) 的 P0/P1 清单为准，未完成项不会标记为已交付。

## 首次导入生产数据库

先在本地 `deploy/` 目录准备生产 `.env` 和 `docker-compose.yml`，并在生产服务器创建项目目录。在 `deploy/` 目录运行 `./deploy.sh config` 只同步配置和源码、不启动服务。执行 `./migrate-production-db.sh` 时需提供 `DEPLOY_HOST` 并显式设置 `CONFIRM_PRODUCTION_DB_IMPORT=YES`；脚本会备份本地 PostgreSQL，再经 SSH 导入到生产端新建或空的数据库。只要目标库已有 public 表，脚本就会拒绝覆盖。导入成功后再运行 `./deploy.sh all` 启动应用。

本地 Halo 导入的媒体记录仍指向 `s3.hzcat.com`，数据库迁移不会复制旧站图片。两个脚本的参数和前置条件可通过源码中的 `--help` 查看；不要将生产 `.env` 或数据库备份提交到 Git。
