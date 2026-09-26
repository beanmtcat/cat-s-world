# Backend

后端采用 **Python 3.12+ / FastAPI / SQLAlchemy / Alembic / PostgreSQL**，异步导入任务使用 **Celery + Redis**。Python 3.9/3.10/3.11 不在支持范围内，避免在本地与生产使用不同语法或密码学实现。

系统设计、API、数据模型与 Halo 迁移方案见：[系统设计](../docs/系统设计.md)。

当前目录：

```text
backend/
├── app/
│   ├── api/          # FastAPI 路由
│   ├── models/       # SQLAlchemy 模型（所有逻辑关联均无数据库外键）
│   ├── services/     # Markdown、媒体、搜索业务
│   ├── importers/    # Halo v2 导入适配器
│   ├── workers/      # Celery 任务
│   └── main.py       # 应用入口
├── alembic/          # 数据库迁移
├── tests/
└── pyproject.toml
```

## 本地启动

1. 复制 `backend/.env.example` 为 `backend/.env`，替换所有示例密钥。
2. 安装 [uv](https://docs.astral.sh/uv/)，再运行：

   ```bash
   cd backend
   uv sync --extra dev
   uv run alembic upgrade head
   uv run uvicorn app.main:app --reload --port 8000
   ```

3. 前端单独运行 `npm run dev`。API 文档仅在非生产环境位于 `/api/docs`。

容器开发可从仓库根目录执行 `docker compose up --build`。不要把 `backend/.env`、S3 密钥、搜索引擎提交令牌或生产导入包提交到仓库。

## 导入 Halo v2

导出 Halo v2 的 ZIP 后，先创建一个拥有 `admin`、`editor` 或 `author` 角色的用户。首次安装可使用：

```bash
cd backend
uv run python -m app.cli.create_admin --email admin@example.com --display-name 管理员
```

随后执行：

```bash
cd backend
uv run python -m app.cli.import_halo_v2 /绝对路径/halo-v2-export.zip \
  --author-email admin@example.com
```

导入会保留 Halo 的发布/草稿状态、文章与独立页面正文、分类、标签、封面、站点设置、菜单、友情链接、历史评论、可识别的统计数据，以及附件和图库元数据；每条内容建立版本记录和旧详情页 301 重定向。附件不会下载或上传到 S3，`mmcat_media.source_url` 和 `origin_url` 保留 Halo 的完整原始 URL。重复运行默认跳过同一 Halo `metadata.name` 的文章、页面及附属资源；确认需要覆盖文章/页面时才添加 `--update-existing`。
