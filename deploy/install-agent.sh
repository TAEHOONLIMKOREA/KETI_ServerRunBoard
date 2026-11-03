#!/usr/bin/env bash
# RunBoard 에이전트 설치 (Ubuntu/Debian 계열, root 로 실행).
#
#   sudo bash deploy/install-agent.sh --token <NODE_TOKEN> [--node gpu01]
#
# 하는 일: Python 3.10 이상·venv 모듈 확인(없으면 apt 로 설치) → 전용 계정 runboard 생성 → /opt/runboard/venv 에
#          에이전트 설치 → /etc/runboard/agent.toml 작성 → systemd 유닛 등록·시작. 몇 초 뒤 대시보드에 노드가 나타난다.
#          서버 주소는 기본 http://keties.iptime.org:55400 이다. 다른 서버로 보내려면 --server URL 을 준다.
#          이미 설치한 노드는 인자 없이 다시 돌리면 설정은 두고 패키지를 갱신한 뒤 에이전트를 다시 시작한다.
#          Docker 소켓 중계의 설치·갱신은 deploy/start-agent.sh 가 한다 (이 스크립트를 부른다). 여기서는 중계를
#          다시 시작하지 않는다.
# 옵션:   --server <URL>  (기본: http://keties.iptime.org:55400)
#         --source <레포 경로>  (기본: 이 스크립트가 있는 레포)   --prefix /opt/runboard
#         --python python3  venv 를 만들 Python. 3.10 이상이어야 한다. 없으면 apt 로 설치하고, python3 가 3.10 보다
#                           낮으면 저장소에 있는 python3.12·3.11·3.10 중 하나를 설치해 쓴다
#         --no-start  설치·갱신만 하고 시작(다시 시작)하지 않는다
set -euo pipefail

SERVER="http://keties.iptime.org:55400"   # RunBoard 서버의 ipTIME DDNS 주소. 처음 설치할 때 agent.toml 에 적힌다
TOKEN=""
NODE="$(hostname -s)"
PREFIX="/opt/runboard"
PYTHON="python3"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START=1

usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --node) NODE="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --no-start) START=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$TOKEN" && ! -f /etc/runboard/agent.toml ]]; then
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

# ---------------------------------------------------------------------- Python
# 에이전트와 Docker 소켓 중계는 <prefix>/venv 의 Python 으로 돈다. 3.10 이상(common·agent 의 requires-python)이고
# venv 모듈이 있어야 한다. Debian·Ubuntu 는 venv 가 쓰는 ensurepip 을 python3.X-venv 패키지로 따로 둔다
MIN_PY=3.10
APT_UPDATED=0

die() { echo "중단: $*" >&2; exit 1; }

# "3.10" 꼴. 없거나 실행이 안 되면 빈 문자열
py_version() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true; }
py_ok() { "$1" -c "import sys; sys.exit(sys.version_info < (${MIN_PY%.*}, ${MIN_PY#*.}))" 2>/dev/null; }
has_venv() { "$1" -c 'import venv, ensurepip' 2>/dev/null; }

apt_update_once() {
  if (( APT_UPDATED )); then return 0; fi
  APT_UPDATED=1
  command -v apt-get >/dev/null 2>&1 \
    || die "apt-get 이 없어 자동 설치를 못 한다 (Ubuntu·Debian 전용). Python $MIN_PY 이상과 venv 모듈을 직접 설치하고 --python 으로 지정한다"
  echo "      apt-get update"
  # 죽은 저장소(옛 PPA 등)가 있어도 받은 목록으로 계속한다
  apt-get update -qq || echo "      경고: apt-get update 가 일부 실패했다. 있는 패키지 목록으로 계속한다"
}
# 저장소에서 설치할 수 있는 패키지인가 (시험 실행)
apt_installable() { apt-get install -s -qq "$1" >/dev/null 2>&1; }
apt_install() {
  apt_update_once
  echo "      apt-get install $*"
  # 다른 apt 가 잠금을 잡고 있으면(unattended-upgrades 등) 최대 120초 기다린다
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o DPkg::Lock::Timeout=120 "$@"
}

ensure_python() {
  echo "[1/5] Python"
  local v
  v="$(py_version "$PYTHON")"
  if [[ -z "$v" ]]; then
    echo "      $PYTHON 이 없다"
    # python3·python3.11 같은 이름은 apt 패키지 이름이기도 하다. 경로로 지정했으면 설치할 수 없다
    if [[ "$PYTHON" == */* ]]; then die "--python $PYTHON 이 없다. 설치한 뒤 다시 실행한다"; fi
    apt_install "$PYTHON" || die "$PYTHON 을 apt 로 설치하지 못했다. 저장소에 없으면 직접 설치하고 --python 으로 지정한다"
    v="$(py_version "$PYTHON")"
    [[ -n "$v" ]] || die "$PYTHON 을 설치했지만 실행되지 않는다"
  fi
  if ! py_ok "$PYTHON"; then
    echo "      $PYTHON 은 $v 라 $MIN_PY 이상이 아니다"
    [[ "$PYTHON" == "python3" ]] || die "--python 에는 $MIN_PY 이상을 지정한다"
    local cand found=""
    apt_update_once
    for cand in python3.12 python3.11 python3.10; do
      if apt_installable "$cand"; then found="$cand"; break; fi
    done
    [[ -n "$found" ]] || die "apt 저장소에 Python $MIN_PY 이상이 없다 (Ubuntu 20.04 등). deadsnakes PPA 를 추가하고(add-apt-repository ppa:deadsnakes/ppa) 다시 실행하거나, 직접 설치한 Python 을 --python 으로 지정한다"
    apt_install "$found" "$found-venv" || die "$found 를 apt 로 설치하지 못했다"
    PYTHON="$found"
    v="$(py_version "$PYTHON")"
    if [[ -z "$v" ]] || ! py_ok "$PYTHON"; then die "$PYTHON 을 설치했지만 쓸 수 없다 (${v:-실행되지 않는다})"; fi
  fi
  if ! has_venv "$PYTHON"; then
    echo "      $PYTHON 에 venv 모듈(ensurepip)이 없다"
    apt_install "python$v-venv" || die "python$v-venv 를 apt 로 설치하지 못했다"
    has_venv "$PYTHON" || die "python$v-venv 를 설치했지만 $PYTHON 에서 venv 모듈을 쓸 수 없다"
  fi
  echo "      $PYTHON $v, venv 모듈 있음"
}

ensure_python

echo "[2/5] 계정"
if ! id -u runboard >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin runboard
fi
if getent group docker >/dev/null; then
  usermod -aG docker runboard
  echo "      runboard 를 docker 그룹에 추가 (컨테이너 조회용)"
fi

echo "[3/5] 설치: $PREFIX/venv"
mkdir -p "$PREFIX"
# 전에 낮은 Python 으로 만든 venv 면 지우고 다시 만든다 (pip 이 requires-python 때문에 설치를 거부한다)
if [[ -x "$PREFIX/venv/bin/python" ]] && ! py_ok "$PREFIX/venv/bin/python"; then
  echo "      기존 venv 의 Python 이 $(py_version "$PREFIX/venv/bin/python") 라 $MIN_PY 이상이 아니다. 지우고 다시 만든다"
  rm -rf "$PREFIX/venv"
fi
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  echo "      $PYTHON -m venv $PREFIX/venv"
  "$PYTHON" -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$SOURCE/common" "$SOURCE/agent"

echo "[4/5] 설정: /etc/runboard/agent.toml"
mkdir -p /etc/runboard
WROTE_CONFIG=0
if [[ -f /etc/runboard/agent.toml ]]; then
  echo "      기존 설정을 유지합니다 (바꾸려면 파일을 수정하고 systemctl restart runboard-agent)"
else
  WROTE_CONFIG=1
  cat > /etc/runboard/agent.toml <<EOF
# RunBoard 에이전트 설정. 우선순위: 이 파일 < 환경변수(RUNBOARD_*) < CLI 인자
server_url = "$SERVER"
token = "$TOKEN"
node_name = "$NODE"
node_id = "$(cat /proc/sys/kernel/random/uuid)"   # 재부팅·호스트명 변경 뒤에도 같은 노드로 인식하는 고정 식별자
active_interval = 1.0   # GPU 동작 중(프로세스가 있거나 사용률 > 0) 보고 주기. 수집 주기이기도 하다
idle_interval = 30.0    # GPU 유휴 시 보고 주기
idle_grace = 5.0        # 동작이 멈춘 뒤 빠른 주기를 유지하는 시간
timeout = 5.0
collector = "nvml"
EOF
  chown root:runboard /etc/runboard/agent.toml
  chmod 640 /etc/runboard/agent.toml
fi

echo "[5/5] systemd"
install -m 644 "$SOURCE/agent/systemd/runboard-agent.service" /etc/systemd/system/runboard-agent.service
sed -i "s#/opt/runboard/venv#$PREFIX/venv#g" /etc/systemd/system/runboard-agent.service
systemctl daemon-reload
systemctl enable runboard-agent
if (( ! START )); then
  echo "      설치만 했다 (--no-start)"
  exit 0
fi
# enable --now 는 이미 돌고 있는 에이전트를 다시 띄우지 않아 갱신한 코드가 안 쓰인다. restart 는 멈춰 있어도 시작한다
systemctl restart runboard-agent
sleep 2
systemctl --no-pager --lines=5 status runboard-agent || true
echo
echo "완료. 로그: journalctl -u runboard-agent -f"
# 다시 실행할 때는 agent.toml 의 server_url 로 보고하므로(--server 는 적용되지 않는다) 처음 설치에만 찍는다
if (( WROTE_CONFIG )); then
  echo "확인: curl -s $SERVER/api/v1/nodes | grep -o '\"hostname\":\"$NODE\"'"
fi
# 중계도 이 venv 의 코드로 돈다. 유닛을 새로 깔고 다시 시작하는 일은 start-agent.sh 가 한다 (옛 유닛은 멈출 때 오래 걸릴 수 있다)
if systemctl is-active --quiet runboard-docker-proxy.service; then
  again="sudo bash $SOURCE/deploy/start-agent.sh"
  [[ "$PREFIX" == "/opt/runboard" ]] || again+=" --prefix $PREFIX"
  echo "참고: Docker 소켓 중계는 다시 시작하지 않아 갱신 전 코드로 돈다. 중계도 갱신하려면: $again"
fi
