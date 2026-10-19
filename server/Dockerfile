# 빌드 컨텍스트는 레포 루트:  docker build -f server/Dockerfile -t runboard-server .
# 1) 프론트 빌드
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2) 서버
FROM python:3.12-slim
WORKDIR /app
COPY common/ ./common/
COPY server/ ./server/
RUN pip install --no-cache-dir ./common ./server
COPY --from=web /web/dist ./web/dist

ENV RUNBOARD_WEB_DIST=/app/web/dist \
    RUNBOARD_DB_URL=sqlite:////data/runboard.db \
    RUNBOARD_CONFIG=/config/runboard.yaml \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
CMD ["runboard-server", "--host", "0.0.0.0", "--port", "8000"]
