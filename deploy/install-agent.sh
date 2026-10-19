#!/usr/bin/env bash
# RunBoard 에이전트 설치 (Ubuntu/Debian 계열, root 로 실행).
#
#   sudo bash deploy/install-agent.sh --server https://runboard.internal --token <NODE_TOKEN> [--node gpu01]
#
# 하는 일: 전용 계정 runboard 생성 → /opt/runboard/venv 에 에이전트 설치 → /etc/runboard/agent.toml 작성
#          → systemd 유닛 등록·시작. 5초 뒤 대시보드에 노드가 나타난다.
# 옵션:   --source <레포 경로>  (기본: 이 스크립트가 있는 레포)   --prefix /opt/runboard   --python python3
set -euo pipefail

SERVER=""
TOKEN=""
NODE="$(hostname -s)"
PREFIX="/opt/runboard"
PYTHON="python3"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --node) NODE="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$SERVER" || -z "$TOKEN" ]]; then
  usage
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  echo "root 로 실행하세요 (sudo)." >&2
  exit 1
fi
if [[ ! -d "$SOURCE/agent" || ! -d "$SOURCE/common" ]]; then
  echo "레포 경로가 아닙니다: $SOURCE (--source 로 지정)" >&2
  exit 1
fi
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "경고: nvidia-smi 가 없습니다. NVML 초기화에 실패하면 노드 정보만 보고됩니다." >&2
fi

echo "[1/4] 계정"
if ! id -u runboard >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin runboard
fi
if getent group docker >/dev/null; then
  usermod -aG docker runboard
  echo "      runboard 를 docker 그룹에 추가 (컨테이너 조회용)"
fi

echo "[2/4] 설치: $PREFIX/venv"
mkdir -p "$PREFIX"
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  "$PYTHON" -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$SOURCE/common" "$SOURCE/agent"

echo "[3/4] 설정: /etc/runboard/agent.toml"
mkdir -p /etc/runboard
if [[ -f /etc/runboard/agent.toml ]]; then
  echo "      기존 설정을 유지합니다 (바꾸려면 파일을 수정하고 systemctl restart runboard-agent)"
else
  cat > /etc/runboard/agent.toml <<EOF
# RunBoard 에이전트 설정. 우선순위: 이 파일 < 환경변수(RUNBOARD_*) < CLI 인자
server_url = "$SERVER"
token = "$TOKEN"
node_name = "$NODE"
interval = 5.0
timeout = 5.0
collector = "nvml"
EOF
  chown root:runboard /etc/runboard/agent.toml
  chmod 640 /etc/runboard/agent.toml
fi

echo "[4/4] systemd"
install -m 644 "$SOURCE/agent/systemd/runboard-agent.service" /etc/systemd/system/runboard-agent.service
sed -i "s#/opt/runboard/venv#$PREFIX/venv#g" /etc/systemd/system/runboard-agent.service
systemctl daemon-reload
systemctl enable --now runboard-agent
sleep 2
systemctl --no-pager --lines=5 status runboard-agent || true
echo
echo "완료. 로그: journalctl -u runboard-agent -f"
echo "확인: curl -s $SERVER/api/v1/nodes | grep -o '\"name\":\"$NODE\"'"
