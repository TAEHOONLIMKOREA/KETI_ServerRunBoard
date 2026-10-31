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

# DB 접속 정보는 /config/settings.ini 로 마운트한다 (git·이미지에 넣지 않는다). 없으면 서버가 시작하지 않는다
ENV RUNBOARD_WEB_DIST=/app/web/dist \
    RUNBOARD_CONFIG=/config/runboard.yaml \
    RUNBOARD_SETTINGS=/config/settings.ini \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["runboard-server", "--host", "0.0.0.0", "--port", "8000"]
