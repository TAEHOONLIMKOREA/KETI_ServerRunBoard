#!/usr/bin/env bash
# RunBoard 에이전트 시작 (GPU 노드, Ubuntu/Debian, root 로 실행). 몇 번을 다시 돌려도 된다.
#
#   sudo bash deploy/start-agent.sh --server http://<서버 주소>:55400 --token <등록 토큰> [--node gpu01]
#   sudo bash deploy/start-agent.sh                         # 이미 설치한 노드: 갱신하고 다시 시작
#   sudo bash deploy/start-agent.sh --dry-run               # 무엇을 할지 보여 주기만 한다
#   sudo bash deploy/start-agent.sh --remove-docker-proxy   # 중계를 걷어내고 원래 Docker 소켓으로 되돌린다
#
# 순서  1) 에이전트 설치·갱신 (install-agent.sh)
#       2) Docker 소켓 중계 설치. 새 컨테이너에 실행한 계정을 runboard.owner 라벨로 붙인다 (이름은 그대로)
#          진짜 소켓은 /run/runboard-docker/docker.sock (root 전용) 으로 옮기고, 중계가 /run/docker.sock 을 맡는다
#       3) Docker 데몬 재시작. 진짜 소켓을 옮길 때(처음 한 번)만 한다.
#          먼저 live-restore 를 켜서 돌던 컨테이너는 멈추지 않는다. 켤 수 없고 돌던 컨테이너가 있으면 멈춘다
#       4) 중계·에이전트 시작과 확인
# 옵션  --yes              live-restore 를 켜지 못해 돌던 컨테이너가 멈추더라도 진행
#       --no-docker-proxy  중계 없이 에이전트만   (--node --source --prefix --python 은 install-agent.sh 로 넘긴다)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$HERE/.." && pwd)"
PREFIX="/opt/runboard"
REAL_SOCK="/run/runboard-docker/docker.sock"
UNITS="/etc/systemd/system"
PROXY="runboard-docker-proxy"
DROPIN="$UNITS/docker.socket.d/runboard-proxy.conf"
DAEMON_JSON="/etc/docker/daemon.json"
YES=0
DRY=0
USE_PROXY=1
REMOVE=0
INSTALL_ARGS=()

usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) YES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --no-docker-proxy) USE_PROXY=0; shift ;;
    --remove-docker-proxy) REMOVE=1; shift ;;
    --source) SOURCE="$2"; INSTALL_ARGS+=("$1" "$2"); shift 2 ;;
    --prefix) PREFIX="$2"; INSTALL_ARGS+=("$1" "$2"); shift 2 ;;
    --server|--token|--node|--python) INSTALL_ARGS+=("$1" "$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 2 ;;
  esac
done

die() { echo "중단: $*" >&2; exit 1; }

# --dry-run 이면 바꾸는 명령은 실행하지 않고 보여 준다
run() {
  if (( DRY )); then echo "      (dry-run) $*"; else "$@"; fi
}

# 멈춰야 하는 상황. --dry-run 이면 알리고 계속 보여 준다
halt() {
  if (( DRY )); then echo "      (dry-run) 실제로는 여기서 멈춘다: $*"; else die "$*"; fi
}

if [[ $EUID -ne 0 ]]; then
  (( DRY )) || die "root 로 실행하세요 (sudo)."
  echo "참고: root 가 아니라 Docker 상태 일부를 읽지 못한다 (--dry-run)"
fi
trap 'echo "실패. 로그: journalctl -u docker -u $PROXY -u runboard-agent -n 50 --no-pager / 되돌리기: sudo bash $HERE/start-agent.sh --remove-docker-proxy" >&2' ERR

# ---------------------------------------------------------------------- Docker 상태

has_docker() { command -v docker >/dev/null 2>&1 && systemctl cat docker.service >/dev/null 2>&1; }
socket_listen() { systemctl show docker.socket -p Listen --value 2>/dev/null || true; }
proxy_in_place() { [[ -f "$DROPIN" && "$(socket_listen)" == *"$REAL_SOCK"* ]]; }
live_restore() { docker info --format '{{.LiveRestoreEnabled}}' 2>/dev/null || echo "알 수 없음"; }

running_count() {
  local ids
  ids="$(docker ps -q 2>/dev/null)" || { echo "?"; return 0; }
  if [[ -z "$ids" ]]; then echo 0; else wc -l <<<"$ids" | tr -d ' '; fi
}

# docker.socket 으로 소켓을 받는 표준 설치(docker-ce 패키지)만 자동으로 바꾼다
check_layout() {
  local hint="--no-docker-proxy 로 중계 없이 진행할 수 있다"
  systemctl cat docker.socket >/dev/null 2>&1 || die "docker.socket 유닛이 없어 자동 설치를 지원하지 않는다 ($hint)"
  local exec
  exec="$(systemctl show docker.service -p ExecStart --value 2>/dev/null)"
  [[ "$exec" == *"fd://"* ]] || die "dockerd 가 docker.socket(fd://) 으로 소켓을 받지 않는다 ($hint)"
  if [[ -f "$DAEMON_JSON" ]] && grep -q '"hosts"' "$DAEMON_JSON"; then
    die "$DAEMON_JSON 에 hosts 가 있어 자동 설치를 지원하지 않는다 ($hint)"
  fi
}

ensure_live_restore() {
  if [[ "$(live_restore)" == "true" ]]; then
    echo "      live-restore: 이미 켜져 있음"
    return 0
  fi
  echo "      live-restore 켜기: $DAEMON_JSON 에 \"live-restore\": true (처음 원본은 $DAEMON_JSON.runboard-bak)"
  if (( DRY )); then
    echo "      (dry-run) $DAEMON_JSON 수정 → systemctl reload docker (설정만 다시 읽는다. 재시작 아님)"
    return 0
  fi
  mkdir -p "$(dirname "$DAEMON_JSON")"
  if [[ -f "$DAEMON_JSON" && ! -f "$DAEMON_JSON.runboard-bak" ]]; then
    cp -p "$DAEMON_JSON" "$DAEMON_JSON.runboard-bak"
  fi
  python3 - "$DAEMON_JSON" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        text = f.read()
except FileNotFoundError:
    text = ""
try:
    data = json.loads(text) if text.strip() else {}
except ValueError as exc:
    sys.exit(f"{path} 이 올바른 JSON 이 아니다: {exc}")
data["live-restore"] = True
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  systemctl reload docker
  for _ in 1 2 3 4 5; do
    [[ "$(live_restore)" == "true" ]] && break
    sleep 1
  done
  if [[ "$(live_restore)" == "true" ]]; then
    echo "      live-restore: 켬 (데몬을 재시작해도 컨테이너가 계속 돈다)"
  else
    echo "      경고: live-restore 가 켜지지 않았다 (swarm 모드 등). journalctl -u docker 로 확인한다"
  fi
}

# 데몬을 재시작해도 되는지 본다. 여기서 멈추면 아직 아무것도 바꾸지 않았다
preflight_restart() {
  local live running
  live="$(live_restore)"
  running="$(running_count)"
  if [[ "$live" == "true" ]]; then
    echo "      Docker 데몬을 한 번 재시작한다. 돌고 있는 컨테이너 ${running}개는 live-restore 로 계속 돈다"
  elif [[ "$running" == "0" ]]; then
    echo "      Docker 데몬을 한 번 재시작한다 (돌고 있는 컨테이너 없음)"
  elif (( YES )); then
    echo "      경고: live-restore 없이 재시작한다. 돌고 있는 컨테이너 ${running}개가 멈춘다 (--yes)"
  else
    halt "live-restore 를 켜지 못해 Docker 를 재시작하면 돌고 있는 컨테이너(${running}개)가 멈춘다. 학습이 없는 시간에 --yes 를 붙여 다시 실행한다"
  fi
}

# docker.socket 이 바뀐 ListenStream 으로 다시 열리게 한다. live-restore 면 컨테이너는 계속 돈다
restart_docker() {
  run systemctl stop docker.service docker.socket
  run systemctl start docker.socket docker.service
}

# ---------------------------------------------------------------------- 중계

verify_proxy() {
  (( DRY )) && return 0
  local version image name labels
  version="$(docker version --format '{{.Server.Version}}' 2>&1)" \
    || die "중계를 거친 docker 호출이 실패했다: $version (되돌리기: sudo bash $HERE/start-agent.sh --remove-docker-proxy)"
  echo "      확인: docker → 중계 → dockerd $version"
  echo "      확인: 진짜 소켓 $(stat -c '%n %U:%G %a' "$REAL_SOCK"), 사용자 소켓 $(stat -c '%n %U:%G %a' /run/docker.sock)"
  image="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -v '<none>' | head -n 1 || true)"
  if [[ -z "$image" ]]; then
    echo "      라벨 확인은 건너뜀 (로컬 이미지가 없다)"
    return 0
  fi
  name="runboard-proxy-check-$$"
  if docker create --name "$name" --label runboard.owner=spoof-check "$image" >/dev/null 2>&1; then
    labels="$(docker inspect -f '{{ index .Config.Labels "runboard.launcher" }} / {{ index .Config.Labels "runboard.owner" }}' "$name")"
    docker rm "$name" >/dev/null
    echo "      확인: 시험 컨테이너(만들고 바로 지움)의 runboard.launcher / runboard.owner = $labels"
  else
    echo "      경고: 시험 컨테이너를 만들지 못해 라벨 확인을 건너뜀 ($image)"
  fi
}

install_proxy() {
  echo "[2/4] Docker 소켓 중계"
  check_layout
  ensure_live_restore
  local move=0
  if proxy_in_place; then
    echo "      진짜 소켓은 이미 $REAL_SOCK 에 있다"
  else
    move=1
    preflight_restart
  fi
  run install -m 644 "$SOURCE/agent/systemd/$PROXY.socket" "$UNITS/$PROXY.socket"
  run install -m 644 "$SOURCE/agent/systemd/$PROXY.service" "$UNITS/$PROXY.service"
  run sed -i "s#/opt/runboard/venv#$PREFIX/venv#g" "$UNITS/$PROXY.service"
  run install -D -m 644 "$SOURCE/agent/systemd/docker.socket.d/runboard-proxy.conf" "$DROPIN"
  run systemctl daemon-reload

  echo "[3/4] Docker 데몬"
  if (( move )); then
    echo "      재시작: 진짜 소켓을 $REAL_SOCK (root 전용) 으로 옮긴다"
    restart_docker
  else
    echo "      재시작하지 않는다 (소켓을 옮길 필요 없음)"
  fi
  run systemctl enable --now "$PROXY.socket"
  run systemctl enable "$PROXY.service"
  run systemctl restart "$PROXY.service"
  verify_proxy
}

remove_proxy() {
  echo "[1/2] Docker 소켓 중계 걷어내기"
  if [[ ! -f "$DROPIN" && ! -f "$UNITS/$PROXY.socket" ]]; then
    echo "      설치돼 있지 않다"
    return 0
  fi
  ensure_live_restore
  preflight_restart
  run systemctl disable --now "$PROXY.service" "$PROXY.socket"
  run rm -f "$UNITS/$PROXY.service" "$UNITS/$PROXY.socket" "$DROPIN"
  run rmdir --ignore-fail-on-non-empty "$(dirname "$DROPIN")"
  run systemctl daemon-reload
  echo "      Docker 데몬 재시작: 소켓을 원래 자리(/run/docker.sock)로 되돌린다"
  restart_docker
  if (( ! DRY )); then
    docker version --format '      확인: docker → dockerd {{.Server.Version}}' \
      || die "docker 호출이 실패했다. journalctl -u docker 로 확인한다"
  fi
}

# ---------------------------------------------------------------------- 에이전트

masked_args() {
  local out=() hide=0 arg
  for arg in ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}; do
    if (( hide )); then out+=("***"); hide=0; else out+=("$arg"); fi
    [[ "$arg" == "--token" ]] && hide=1
  done
  echo "${out[*]-}"
}

start_agent() {
  run systemctl enable runboard-agent
  run systemctl restart runboard-agent
  if (( ! DRY )); then
    sleep 2
    systemctl --no-pager --lines=5 status runboard-agent || true
  fi
}

if (( REMOVE )); then
  remove_proxy
  if [[ -f "$UNITS/runboard-agent.service" ]]; then
    echo "[2/2] 에이전트 다시 시작"
    start_agent
  fi
  echo "완료. 중계 없이 원래 Docker 소켓을 쓴다"
  exit 0
fi

echo "[1/4] 에이전트 설치·갱신"
if [[ ${#INSTALL_ARGS[@]} -eq 0 && ! -f /etc/runboard/agent.toml ]]; then
  usage
  die "처음 설치에는 --server 와 --token 이 필요하다"
fi
if (( DRY )); then
  echo "      (dry-run) bash $HERE/install-agent.sh $(masked_args) --no-start"
else
  bash "$HERE/install-agent.sh" ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"} --no-start
fi

if (( ! USE_PROXY )); then
  echo "[2/4] Docker 소켓 중계: 건너뜀 (--no-docker-proxy)"
elif ! has_docker; then
  echo "[2/4] Docker 소켓 중계: Docker 가 없어 건너뜀"
else
  install_proxy
fi

echo "[4/4] 에이전트 시작"
start_agent
echo
echo "완료. 로그: journalctl -u runboard-agent -u $PROXY -f"
