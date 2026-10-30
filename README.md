# KETI RunBoard

GPU 서버 사용 현황 모니터링. 여러 노드의 GPU 를 한 화면에서 보고, **누가 어떤 작업으로 어느 GPU 를 쓰고 있는지** 를 보여준다.

- 기획·아키텍처: [docs/PLAN.md](docs/PLAN.md)
- 구현 플랜과 진행 상태: [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md)

## 구성

| 디렉터리 | 패키지 | 역할 |
|---|---|---|
| `common/` | `runboard-common` | 에이전트·서버가 공유하는 보고 스키마 (pydantic) |
| `agent/` | `runboard-agent` | 각 GPU 노드에서 실행. NVML 수집 + 계정·컨테이너 보강 → GPU 동작 중 1초, 유휴 30초마다 서버로 보고 |
| `server/` | `runboard-server` | FastAPI. 보고 수신, 유형·소유자 판정, 작업·세션 추적, SQLite 저장, API, 웹 UI 서빙 |
| `web/` | | React + Vite + TypeScript 대시보드 |
| `deploy/` | | 서버 설정 예시, compose, Caddyfile, 에이전트 설치 스크립트 |

## 개발 환경

Python 3.10 이상, Node 20 이상.

```bash
# Windows (Git Bash)                      # Linux / macOS
py -3.12 -m venv .venv                     python3 -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt      # Linux: .venv/bin/pip ...

.venv/Scripts/ruff check .                 # 린트
.venv/Scripts/pytest                       # 테스트 (common / agent / server)

cd web && npm install && npm run build     # 프론트 빌드 → web/dist
```

## 로컬에서 돌려 보기 (GPU 없이, fake 에이전트)

```bash
# 터미널 1: 서버 (설정 예시 그대로 사용, web/dist 가 있으면 UI 도 서빙)
.venv/Scripts/runboard-server --config deploy/runboard.example.yaml

# 터미널 2: 가짜 GPU 노드. 개인 계정·컨테이너·공용 서비스·미확인 케이스가 섞인 8-GPU 시나리오
.venv/Scripts/runboard-agent run --fake --node fake01 --server http://localhost:8000 --token dev-token-fake01

# 노드를 더 띄우려면 터미널을 더 열고 이름만 바꾼다 (토큰은 runboard.example.yaml 의 node_tokens 에 fake01~fake03 이 있다).
# 이름 끝 숫자에 따라 GPU UUID·IP·churn 주기가 달라지고, --fake-gpus 와 --no-churn 으로 구성을 바꿀 수 있다
.venv/Scripts/runboard-agent run --fake --node fake02 --fake-gpus 4 --server http://localhost:8000 --token dev-token-fake02
.venv/Scripts/runboard-agent run --fake --node fake03 --no-churn --server http://localhost:8000 --token dev-token-fake03

# 브라우저: http://localhost:8000      API: http://localhost:8000/api/v1/overview
```

프론트를 고치면서 볼 때는 `cd web && npm run dev` (5173 포트, `/api` 는 8000 으로 프록시).

이 PC 에 NVIDIA GPU 가 있으면 실제 수집기도 바로 볼 수 있다 (Windows 에서는 GPU 지표만, 컨테이너·pgid 보강은 Linux 전용):

```bash
.venv/Scripts/runboard-agent run --once --print
```

## 운영 배포

### 서버

```bash
cd deploy
cp runboard.example.yaml runboard.yaml     # 노드 토큰, 사용자, 계정 매핑, 공용 서비스 레지스트리 수정
docker compose up -d --build               # server + caddy (HTTPS)
```

Docker 없이 돌리려면 위 개발 환경대로 설치한 뒤 `runboard-server --config deploy/runboard.yaml` 를 systemd 로 띄운다. 워커는 1개여야 한다 (최신 상태를 메모리에 둔다).

### GPU 노드 (에이전트)

`runboard.yaml` 의 `auth.shared_token` 에 등록 토큰을 정해 두면 노드는 서버 재시작 없이 바로 참여한다. 처음 보고한 순서대로 Server_1, Server_2 … 이름을 받고 DB `nodes` 표에 한 행으로 남는다. 설치 스크립트가 `agent.toml` 에 고정 `node_id` 를 써 두므로 재부팅하거나 호스트명이 바뀌어도 같은 노드로 이어진다. 노드별로 다른 토큰을 쓰려면 `auth.node_tokens` 에 노드 이름(또는 node_id) → 토큰을 넣는다 (평문 또는 `sha256:<hex>`). 노드에서:

```bash
git clone <this repo> && cd KETI_RunBoard
sudo bash deploy/install-agent.sh --server https://runboard.internal --token <NODE_TOKEN>
journalctl -u runboard-agent -f
```

스크립트는 전용 계정 `runboard` 를 만들고 `/opt/runboard/venv` 에 설치한 뒤 systemd 서비스로 등록한다. Docker 가 있으면 `docker` 그룹에 넣어 컨테이너 이름·라벨 조회만 한다. 몇 초 뒤 대시보드에 노드가 나타난다.

보고 주기는 적응형이다. GPU 에 프로세스가 있거나 사용률이 0 보다 크면 1초마다, 모두 놀고 있으면 30초마다 보고한다. 수집은 늘 1초마다 하므로 유휴 노드에서 작업이 시작돼도 바로 나타난다. `/etc/runboard/agent.toml` 의 `active_interval`, `idle_interval`, `idle_grace` 로 바꾼다. 서버는 보고에 실려 온 주기에 `delayed_s`·`offline_s` 를 더해 지연·끊김을 판정하므로 유휴 노드가 끊김으로 오판되지 않는다.

Docker 로 돌리려면 `agent/Dockerfile` 머리말의 `docker run` 예시를 쓴다. `--pid=host` 가 없으면 다른 컨테이너의 프로세스가 보이지 않는다.

### 소유자가 "미확인" 으로 나올 때

서버는 추정하지 않는다. 다음 중 하나로 알려 주면 된다.

1. 컨테이너에 라벨: `docker run --label runboard.owner=<user> --label runboard.project=<proj> ...`
2. 컨테이너명 규칙: `<user>_<용도>` (예: `taehoon_torch`). `<user>` 가 `runboard.yaml` 의 등록 사용자여야 한다
3. 호스트 계정: 계정명이 등록 사용자·별칭과 같으면 자동 인식. 다르면 `account_mappings` 에 (노드, uid) → 사용자
4. 공용 서비스(Ollama, vLLM 등): `services` 에 매칭 규칙과 담당자

## API

| 경로 | 설명 |
|---|---|
| `POST /api/v1/report` | 에이전트 보고 (Bearer 노드 토큰) |
| `GET /api/v1/overview` | 노드·GPU·작업 최신 상태, 상태 4축, 요약 |
| `GET /api/v1/nodes`, `/nodes/{name}` | 노드 |
| `GET /api/v1/workloads?owner=&node=&type=&project=&status=`, `/workloads/{id}` | 작업과 세션 |
| `GET /api/v1/users`, `/users/{username}` (`_unknown` = 미확인) | 사용자별 점유 |
| `GET /api/v1/healthz` | 헬스체크 |
| `GET /docs` | OpenAPI |
