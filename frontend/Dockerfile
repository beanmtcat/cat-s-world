FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm ci
COPY frontend ./frontend
ENV VITE_API_BASE_URL=/api/v1
RUN npm --prefix frontend run build

FROM nginx:latest
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist/ /usr/share/nginx/html/
EXPOSE 80
