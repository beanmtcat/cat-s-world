# 猫子的世界

项目按前后端目录组织：

- `frontend/`：React + Vite 前端应用
- `backend/`：Python 3.12+ FastAPI、SQLAlchemy、Alembic、Celery/Redis 服务
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

Docker 化 PostgreSQL、Redis、MinIO 与 API 的基线在 `docker-compose.yml`；执行前同样需要创建 `backend/.env`。完整功能进度以 [系统设计](docs/系统设计.md) 的 P0/P1 清单为准，未完成项不会标记为已交付。
