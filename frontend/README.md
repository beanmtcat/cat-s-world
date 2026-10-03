# Frontend

猫子的世界前端应用，基于 React + Vite。

## 开发

```bash
npm run dev
```

## Docker 部署

`deploy/docker-compose.example.yml` 是模板，生产配置位于 `deploy/docker-compose.yml`。前端由 `nginx:latest` 提供静态文件，并把 `/api/` 原样反代到 Compose 内的 `api:8000`；构建时 API 地址固定为同源 `/api/v1`。默认只在宿主机 `127.0.0.1:8080` 监听，外部访问需由 TLS 反向代理转发到该端口。

从项目根目录更新前端镜像并启动：

```bash
cd deploy
DEPLOY_HOST=user@server ./deploy.sh frontend
```
