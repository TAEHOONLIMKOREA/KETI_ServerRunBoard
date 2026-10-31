# 빌드 컨텍스트는 레포 루트:  docker build -f agent/Dockerfile -t runboard-agent .
#
# 실행 (호스트 PID 네임스페이스와 Docker 소켓 조회가 필요하다, PLAN 4.2):
#   docker run -d --name runboard-agent --restart unless-stopped \
#     --gpus all --pid=host \
#     -v /var/run/docker.sock:/var/run/docker.sock:ro \
#     -e RUNBOARD_SERVER=http://<서버 주소>:55400 -e RUNBOARD_TOKEN=<NODE_TOKEN> \
#     -e RUNBOARD_NODE_NAME=$(hostname) -e RUNBOARD_NODE_ID=$(cat /etc/machine-id) \
#     runboard-agent
# 컨테이너 안의 hostname 은 컨테이너 ID 라서 RUNBOARD_NODE_NAME 을 꼭 넘긴다.
# RUNBOARD_NODE_ID 는 컨테이너를 다시 만들어도 같은 노드로 이어지게 하는 고정 식별자다 (호스트의 machine-id 가 적당하다).
FROM python:3.12-slim
WORKDIR /app
COPY common/ ./common/
COPY agent/ ./agent/
RUN pip install --no-cache-dir ./common ./agent
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=utility,compute \
    PYTHONUNBUFFERED=1
CMD ["runboard-agent", "run"]
