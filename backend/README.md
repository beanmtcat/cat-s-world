# Backend

后端采用 **Python 3.12+ / FastAPI / SQLAlchemy / Alembic / PostgreSQL**，登录/评论限流使用 **Redis**。Python 3.9/3.10/3.11 不在支持范围内，避免在本地与生产使用不同语法或密码学实现。

系统设计、API、数据模型与 Halo 迁移方案见：[系统设计](../docs/系统设计.md)。

当前目录：

```text
backend/
├── app/
│   ├── api/          # FastAPI 路由
│   ├── models/       # SQLAlchemy 模型（所有逻辑关联均无数据库外键）
│   ├── services/     # Markdown、媒体、搜索业务
│   ├── importers/    # Halo v2 导入适配器
│   ├── cli/          # 管理命令（创建管理员、导入、清理）
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

## 动态推送

`POST /api/feed-posts` 接收 `Authorization: Bearer <token>` 和 JSON 正文：`unit_id`、`title`、`kicker`、`category`、`body_md`、`published_at`。发布时间必须带时区；正文按 Markdown 保存，可用 `![说明](https://...)` 引用图片。首次写入返回 `201`，相同 `unit_id` 再次推送会更新标题、副标题、分类、正文及发布时间，返回 `200` 和 `created:false`；实际内容变化时 `updated:true`，完全相同时 `updated:false`。补图时应在 `body_md` 中发送包含原文和图片链接的完整正文，不能只发图片链接。已在后台删除的动态不会被推送恢复。公开列表位于 `GET /api/v1/feed-posts`，前台页面位于 `/feed`。

服务端只配置令牌的 SHA-256 摘要 `FEED_POSTS_TOKEN_HASH`（本地 `backend/.env`，部署时 `deploy/.env`），不要把原始令牌提交到仓库。未配置时推送接口返回 `503`；令牌错误返回 `401`。部署时执行 Alembic 迁移 `0014_feed_posts`，现有 API 容器启动命令会运行 `alembic upgrade head`。

动态图片使用同一令牌调用 `POST /api/feed-images`：请求体是图片原始字节，`Content-Type` 为 `image/png`、`image/jpeg`、`image/gif` 或 `image/webp`，单张最多 10 MiB。可在 URL 后加可选的 `?group_id=<附件分组 UUID>` 指定后台附件分组；在附件管理的分组旁可复制 ID。不传则保持未分组，分组不存在或不是附件分组时返回 `404`。成功时返回 `data.url`（`https://s3.hzcat.com/halo/...`）、`data.object_key`、`data.group_id` 和可直接粘贴到 `body_md` 的 `data.markdown`。图片先经过类型校验与现有病毒扫描，再写入配置的 S3 bucket，并登记到后台附件管理；相同图片重复上传会复用已登记的附件，也能加入指定分组。`S3_PUBLIC_BASE_URL` 必须设置为 `https://s3.hzcat.com/halo`，不能使用容器内的 `minio:9000` 地址。旧版本已经上传但未登记的图片不会自动出现在附件管理，需要重新上传或单独补录。例如：

```bash
curl -X POST 'https://你的博客地址/api/feed-images?group_id=附件分组UUID' \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: image/png' \
  --data-binary '@/本地路径/图片.png'
```

## 图片压缩与卡片缩略图

动态图片接口 `/api/feed-images` 和附件上传接口 `/api/v1/studio/media/upload` 会在类型校验、病毒扫描之后，把 PNG、JPEG、GIF、WebP 图片编码为 WebP。正文图最长边不超过 1600px，卡片图最长边不超过 640px，保持比例、不放大小图。正文 WebP 质量为 80，缩略图质量为 76；透明度和 ICC 颜色配置保留，重新编码时移除 EXIF/GPS 和 XMP，16 位灰度图先映射到 8 位。大 JPEG 在解码前用 `draft` 缩小。已是 JPEG/WebP 且尺寸不超过 1600px、重新编码更大时，只有在 `getexif()` 为空且不带 XMP 时才保留原文件；带元数据时始终重新编码。返回实际的扩展名和 MIME 类型，缩略图始终为 WebP。

两个上传入口只有命中带缩略图映射的图片记录才直接去重。命中旧版无缩略图记录时会重新处理；输出 MIME 不同时可新增附件，同 `(checksum, mime_type)` 冲突时更新旧记录，保留其 ID、分组关联及旧对象，并为旧 URL 补上缩略图映射。处理后的重复上传会复用新结果。

动图正文保留动画，缩略图取首帧；GIF 缺失或不超过 10ms 的帧时长按浏览器行为设为 100ms，无循环扩展的 GIF 仅播放一次。文件大小依内容而定，不保证每张都小于 100KB。非图片附件原样保存。

上传结果的 `url` / `sourceUrl` 指向正文图；动态上传额外返回 `thumbnail_url`，附件上传额外返回 `thumbnailUrl`。返回的 Markdown 仍引用正文图。首页动态、文章卡片、图库列表和附件库预览优先使用缩略图；旧图尚未处理时回退原图。不要在正文中手动替换成缩略图 URL。

先发布前后端（`deploy/deploy.sh all`）。API 启动时会执行迁移 `0015_media_thumbnails`，创建 URL 映射表，无需修改正文、封面地址。

在服务器项目目录预览旧封面、动态首图的待处理项：

```bash
docker compose exec -T mmcat-api python -m app.cli.backfill_thumbnails --limit 100
```

确认后执行同一批处理：

```bash
docker compose exec -T mmcat-api python -m app.cli.backfill_thumbnails --apply --limit 100
```

命令新增 640px WebP 对象并记录原地址到缩略图的映射，**不会覆盖或删除原图，不会改写文章、动态或封面 URL**。重复执行会跳过已完成项；`--limit` 限制成功生成数量，失败项不占名额，会继续处理后面的图片，直到达到成功数量或遍历完候选项。结束时汇总失败 URL 与原因，有失败项时以非零状态退出。还要处理附件库中未被封面引用的所有图片时，增加 `--include-media`。

默认通过 S3 SDK 读取当前 `S3_PUBLIC_BASE_URL` 下的图片，不向任意外站发起请求。历史图片在其他 bucket 或域名时，显式加上允许的公开路径前缀，例如 `--source-base-url https://s3.hzcat.com/old-bucket`；可重复指定。额外前缀通过公开 HTTP 读取，不携带 S3 凭据、不跟随重定向。源文件上限 50 MiB，JPEG 图片头标称最多 1 亿像素；需要完整解码的 PNG/WebP/GIF 最多 4000 万像素；动画最多 200 帧且累计不超过 2000 万像素，超限时报告错误。

本地可在 `backend/` 执行 `uv run python -m app.cli.backfill_thumbnails`，参数相同。

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
