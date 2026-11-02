# KETI Server RunBoard

사내용(KETI 내부). 외부에 배포하지 않는다.

연구실 GPU 서버의 GPU 를 **누가 어떤 작업으로 쓰고 있는지** 한 화면에 보여 주는 대시보드. 노드마다 도는 에이전트가 GPU·프로세스·실행 계정·컨테이너 정보를 보내고, 서버가 작업을 묶고 소유자를 판정해 웹 화면과 API 로 보여 준다.

![대시보드](docs/img/dashboard.png)

이 README 는 빠른 시작이다. 동작·설정·API·운영의 자세한 내용은 [docs/RUNBOARD.md](docs/RUNBOARD.md) 에 있다.

## 구성

| 디렉터리 | 패키지 | 역할 |
|---|---|---|
| `common/` | `runboard-common` | 에이전트·서버가 공유하는 보고 스키마 (pydantic) |
| `agent/` | `runboard-agent` | 각 GPU 노드에서 실행. NVML 수집과 계정·컨테이너 보강, GPU 동작 중 1초·유휴 30초마다 서버로 보고. Docker 소켓 중계(`runboard-agent docker-proxy`)도 있다 |
| `server/` | `runboard-server` | FastAPI. 보고 수신, 유형·소유자 판정, 작업·세션 추적, 5분 이력 집계와 보존 정리, PostgreSQL 저장, API, 웹 화면·관리 화면 서빙, `runboard-server nodes` 명령 |
| `web/` | | React + Vite + TypeScript 대시보드 |
| `deploy/` | | 서버 compose, 서버 설정 예시, 에이전트 설치 스크립트 |
| `docs/` | | [RUNBOARD.md](docs/RUNBOARD.md) 와 그림 |

## 개발 환경

Python 3.10 이상, Node 20 이상, PostgreSQL 15 이상.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt    # Windows 는 .venv/Scripts/ 아래

.venv/bin/ruff check . && .venv/bin/ruff format --check server common agent   # 린트
.venv/bin/pytest                                  # 테스트 (common / agent / server)

cd web && npm ci && npm run build                 # 웹 빌드 → web/dist
```

DB 를 쓰는 서버 테스트는 `RUNBOARD_TEST_DB_URL` 에 PostgreSQL 접속 URL 을 줄 때만 돈다. 그 서버에 `runboard_test_<pid>` DB 를 만들고 끝나면 지우므로 CREATEDB 권한이 있어야 한다 (없으면 오류로 끝난다). URL 을 주지 않으면 그 테스트는 건너뛴다.

```bash
RUNBOARD_TEST_DB_URL='postgresql+psycopg://<user>:<password>@<host>:<port>/postgres' .venv/bin/pytest
```

웹을 고치면서 볼 때는 `cd web && npm run dev` (5173 포트, `/api` 는 8000 으로 프록시).

대시보드 그림(`docs/img/dashboard.png`)과 같은 크기의 그림은 아래 '로컬에서 돌려 보기' 처럼 서버와 가짜 에이전트를 띄우고 Chrome headless 로 찍는다. 그림의 작업 경과 시간과 노드 상세의 이력은 레포 밖 보조 스크립트로 맞춘 것이라 이 방법으로는 똑같이 나오지 않는다 ([RUNBOARD.md 10장 '스크린샷'](docs/RUNBOARD.md#스크린샷)). `--virtual-time-budget` 이 없으면 `불러오는 중…` 이 찍히고, Chrome 120 의 새 headless 모드는 그림 높이가 창 높이보다 77 px 작다 (1320×851).

```bash
google-chrome --headless=new --hide-scrollbars --force-device-scale-factor=1 --blink-settings=preferredColorScheme=1 --window-size=1320,928 --virtual-time-budget=5000 --screenshot=docs/img/dashboard.png http://localhost:8000/
```

## 로컬에서 돌려 보기 (GPU 없이, 가짜 에이전트)

```bash
# DB 접속 정보: 레포 루트에 settings.ini 를 만들고 [postgres_db] 섹션에 host, port, dbname, user, password 를 적는다.
# git 에 올라가지 않는다 (.gitignore). 권한은 chmod 600 settings.ini. 또는 RUNBOARD_DB_URL=postgresql+psycopg://... 를 준다

# 터미널 1: 서버 (레포 루트에서. 설정 예시 그대로 사용, web/dist 가 있으면 화면도 낸다)
.venv/bin/runboard-server --config deploy/runboard.example.yaml

# 터미널 2: 가짜 GPU 노드. 개인 계정·컨테이너·공용 서비스·소유자 미확인이 섞인 8-GPU 시나리오
.venv/bin/runboard-agent run --fake --node fake01 --server http://localhost:8000 --token dev-token-fake01

# 노드를 더 띄우려면 이름만 바꾼다 (runboard.example.yaml 의 node_tokens 에 fake01~fake03 이 있다).
# --fake-gpus 와 --no-churn 으로 구성을 바꾼다
.venv/bin/runboard-agent run --fake --node fake02 --fake-gpus 4 --server http://localhost:8000 --token dev-token-fake02
.venv/bin/runboard-agent run --fake --node fake03 --no-churn --server http://localhost:8000 --token dev-token-fake03
```

브라우저로 `http://localhost:8000` 을 연다. 소유자의 이름과 색은 등록 정보를 넣어야 붙는다. `http://localhost:8000/admin` 에서 넣거나 관리 API 로 넣는다.

```bash
B=http://localhost:8000/api/v1/admin; J='Content-Type: application/json'
curl -X POST $B/users -H "$J" -d '{"username":"taehoon","display_name":"임태훈","aliases":["th"]}'
curl -X POST $B/users -H "$J" -d '{"username":"minsu","display_name":"김민수"}'
curl -X POST $B/users -H "$J" -d '{"username":"jihye"}'
curl -X POST $B/account-mappings -H "$J" -d '{"uid":1001,"username":"taehoon"}'
curl -X POST $B/services -H "$J" -d '{"name":"ollama","match_exec_account":"ollama","owner":"taehoon"}'
```

NVIDIA GPU 가 있는 PC 에서는 `.venv/bin/runboard-agent run --once --print` 로 실제 수집 결과를 본다 (Windows 에서는 uid·프로세스 그룹·컨테이너가 비어 있다. 이 셋은 Linux 에서만 모은다).

## 운영 배포

### 서버

```bash
cd deploy
cp runboard.example.yaml runboard.yaml     # 등록 토큰(auth.shared_token)을 바꾸고 예시의 fake·dev 노드 토큰은 지운다
# 레포 루트에 settings.ini 를 만든다 ([postgres_db] 의 host, port, dbname, user, password)
docker compose up -d --build               # http://<서버>:55400 (HTTP)
```

- compose 는 레포 루트의 `settings.ini` 를 컨테이너의 `/config/settings.ini` 로 읽기 전용 마운트하고, 서버 컨테이너를 Postgres compose 의 `postgres_default` 네트워크에 붙인다. 그래서 `settings.ini` 의 `host` 는 PostgreSQL 컨테이너 이름, `port` 는 컨테이너 안의 포트다. Postgres 스택을 먼저 띄워 둔다.
- DB 접속 URL 의 우선순위는 `RUNBOARD_DB_URL` > `settings.ini` > YAML 의 `server.db_url` 이고, 셋 다 없으면 서버가 시작하지 않는다. DB 는 PostgreSQL 만 쓴다. 표는 시작할 때 만든다.
- 사용자·별칭, 계정 매핑, 공용 서비스, 공지는 PostgreSQL 에 있다. 띄운 뒤 `http://<서버>:55400/admin` 에서 등록한다. `runboard.yaml` 에 `users`·`account_mappings`·`services` 가 있으면 서버가 시작하지 않고 지우라고 알린다.
- 서버 프로세스는 하나만 띄운다 (최신 상태를 메모리에 둔다). Docker 없이 띄울 때는 개발 환경대로 설치하고 `runboard-server --config deploy/runboard.yaml --port 55400` 을 실행한다. `settings.ini` 는 실행 디렉터리에서 찾으므로 레포 루트에서 실행하거나 `RUNBOARD_SETTINGS` 로 경로를 준다. 컨테이너 이름은 호스트에서 풀리지 않으므로 이때는 호스트에서 닿는 주소·포트를 적는다.
- 외부 접속은 HTTP 로만 받는다. 공유기(NAT) 뒤라면 공유기에 외부 55400 → 서버 55400 (TCP) 포트포워딩을 추가한다.
- 대시보드, 조회 API, 관리 화면(`/admin`), 관리 API 모두 로그인이 없다. 주소에 닿는 누구나 보고, 등록 정보·공지를 고치고, 비활성화된 노드를 지울 수 있다.

### GPU 노드 (에이전트)

`runboard.yaml` 의 `auth.shared_token`(등록 토큰)으로 보고하는 노드는 서버 재시작 없이 바로 참여하고, 처음 보고한 순서대로 `Server_1`, `Server_2` … 화면 이름을 받는다. 노드별로 다른 토큰을 쓰려면 `auth.node_tokens` 에 호스트명이나 `node_id` → 토큰(평문 또는 `sha256:<hex>`)을 넣는다. 노드에서:

```bash
git clone <레포 주소> && cd <레포>
sudo bash deploy/start-agent.sh --dry-run --server http://<서버 주소>:55400 --token <토큰>   # 할 일만 보기
sudo bash deploy/start-agent.sh --server http://<서버 주소>:55400 --token <토큰>
journalctl -u runboard-agent -u runboard-docker-proxy -f
```

- `start-agent.sh` 는 몇 번을 다시 돌려도 된다. 설치한 노드에서 인자 없이 돌리면 갱신하고 다시 시작한다. 중계가 있는 노드는 이것으로 갱신한다 (`install-agent.sh` 는 에이전트만 다시 시작한다).
- 에이전트는 전용 계정 `runboard` 로 `/opt/runboard/venv` 에 설치되고 systemd 서비스로 돈다. `/etc/runboard/agent.toml` 에 고정 `node_id`(UUID)를 적어 두므로 재부팅하거나 호스트명이 바뀌어도 같은 노드로 이어진다.
- Docker 가 있으면 **Docker 소켓 중계**도 설치한다. Docker 는 누가 컨테이너를 만들었는지 남기지 않으므로, 중계가 원래 소켓 자리(`/run/docker.sock`)를 맡고 새 컨테이너에 만든 계정을 `runboard.owner`·`runboard.launcher` 라벨로 붙인다. 사용자는 Docker 를 그대로 쓴다. 진짜 소켓을 옮길 때 Docker 데몬을 한 번 재시작하는데, 먼저 live-restore 를 켜므로 돌던 컨테이너는 멈추지 않는다 (켤 수 없는데 돌던 컨테이너가 있으면 멈추고, 학습이 없는 시간에 `--yes` 로 다시 한다).
- 중계를 걷어내려면 `--remove-docker-proxy`, 중계 없이 에이전트만 설치하려면 `--no-docker-proxy` (또는 `install-agent.sh`).
- 보고 주기는 GPU 동작 중 1초, 유휴 30초다. `agent.toml` 의 `active_interval`, `idle_interval`, `idle_grace` 로 바꾼다.

설치 순서, 중계의 동작과 한계, 컨테이너로 돌리는 법은 RUNBOARD.md 의 [2장 'Docker 소켓 중계'](docs/RUNBOARD.md#docker-소켓-중계)와 [9장 '배포와 운영'](docs/RUNBOARD.md#9-배포와-운영) 에 있다.

### 노드 관리

노드는 에이전트가 처음 보고할 때 자동으로 등록된다. 화면 이름 변경과 비활성화된 노드 삭제는 관리 화면의 '노드' 탭이나 `runboard-server nodes` 로 한다. 이 명령은 돌고 있는 서버의 관리 API 를 부르므로 DB 접속 정보가 필요 없다. `<노드>` 는 식별자·화면 이름·호스트명 중 노드 하나에만 맞는 값이다.

```bash
cd deploy
docker compose exec server runboard-server nodes list
docker compose exec server runboard-server nodes rename <노드> <화면 이름>
docker compose exec server runboard-server nodes remove <노드>          # 터미널이 없으면 --yes

runboard-server nodes --server http://<서버 주소>:55400 list            # 컨테이너 밖에서
```

같은 일을 curl 로 하려면 노드 경로에 식별자(`nodes list` 의 식별자 열)를 쓴다.

```bash
RB=http://<서버 주소>:55400
curl -s $RB/api/v1/admin/nodes
curl -s -X PATCH -H 'Content-Type: application/json' -d '{"display_name": "A100-1"}' $RB/api/v1/admin/nodes/<식별자>
curl -s -X DELETE $RB/api/v1/admin/nodes/<식별자>          # 비활성화된 노드만. 성공하면 204
```

지울 수 있는 것은 비활성화된 노드뿐이다. 먼저 그 노드에서 에이전트를 멈춘다 (`sudo systemctl disable --now runboard-agent`). 지우면 열린 작업은 '노드 삭제' 로 끝나고 지난 작업·세션·샘플 이력은 남는다. 같은 에이전트가 다시 보고하면 `Server_n` 화면 이름으로 다시 등록된다 (바꿔 둔 화면 이름은 돌아오지 않는다). 노드의 식별자가 바뀌면 새 노드로 등록되고, 돌던 작업은 옛 노드가 비활성화된 뒤 새 노드가 이어받는다. 옛 노드는 그 뒤에 지운다. 이때 에이전트는 새 식별자로 계속 돌게 둔다 ([RUNBOARD.md 3장 '식별자가 바뀔 때'](docs/RUNBOARD.md#식별자가-바뀔-때)).

### 공지

모든 화면의 헤더 아래에 한 줄씩 보이는 안내·주의다. 관리 화면의 '공지' 탭에서 내용, 수준(안내·주의), 표시 기한을 정한다. 기한이 지나면 재시작 없이 배너에서 내려가고, 지울 때까지 목록에 '만료' 로 남는다. API 는 `GET`·`POST /api/v1/admin/notices`, `PUT`·`DELETE /api/v1/admin/notices/{id}` 다.

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"text\": \"11/7(토) 18시까지 Server_3 점검\", \"level\": \"warning\", \"until\": $(date -d '2026-11-07 18:00' +%s)}" \
  http://<서버 주소>:55400/api/v1/admin/notices
```

### 데이터 보존

서버는 시작할 때와 그 뒤 1시간마다 보존 기간이 지난 행을 지운다. 기간은 `runboard.yaml` 의 `server.retention` 에 일 단위로 적는다 (0 보다 크고 36500 이하).

| 키 | 기본 | 대상 |
|---|---|---|
| `raw_days` | 7 | 원본 샘플 `gpu_samples` (GPU 마다 30초에 한 행) |
| `agg_days` | 90 | 5분 집계 `gpu_samples_5m`. 노드 상세의 이력 차트(24시간·7일)가 읽는다 |
| `sessions_days` | 365 | 끝난 세션 `gpu_sessions`, 그리고 남은 세션이 없는 끝난 작업 `workloads` |

환경변수 `RUNBOARD_RETENTION_RAW_DAYS`, `RUNBOARD_RETENTION_AGG_DAYS`, `RUNBOARD_RETENTION_SESSIONS_DAYS` 가 YAML 값을 덮어쓴다. 노드·GPU·등록 정보·공지 행은 지우지 않는다. 지운 자리는 PostgreSQL 이 다시 쓰지만 파일 크기는 바로 줄지 않는다. 줄이려면 사용이 적은 때 `VACUUM FULL gpu_samples;` 처럼 한다 (그 표를 잠그므로 그동안 샘플을 쓰려던 보고가 서버의 상태 락을 잡은 채 기다려, 보고 처리와 대시보드·조회 API 가 함께 멈출 수 있다).

### 소유자가 "미확인" 으로 나올 때

서버는 추정하지 않는다. 다음 중 하나로 알려 준다.

1. 컨테이너 라벨: `docker run --label runboard.owner=<user> --label runboard.project=<proj> ...` (중계가 있는 노드에서는 만든 계정이 자동으로 붙는다)
2. 컨테이너명 규칙: `<user>_<용도>` (예: `taehoon_torch`). `<user>` 는 영문으로 시작하는 영문·숫자이고, 관리 화면에 등록된 사용자·별칭이어야 한다
3. 호스트 계정: 계정명이 등록 사용자·별칭과 같으면 자동 인식. 다르면 관리 화면의 계정 매핑에 (노드, uid) → 사용자
4. 공용 서비스(Ollama, vLLM 등): 관리 화면의 공용 서비스에 매칭 규칙과 담당자

관리 화면에서 고치면 이미 돌고 있는 작업도 다음 보고 때 유형·소유자를 다시 판정한다. 판정 순서는 [RUNBOARD.md 3장 '작업 유형과 소유자 판정'](docs/RUNBOARD.md#작업-유형과-소유자-판정) 에 있다.

## API

전체 목록과 응답 모양은 [RUNBOARD.md 6장](docs/RUNBOARD.md#6-api) 에 있다. 인증은 보고에만 있다.

| 경로 | 설명 |
|---|---|
| `POST /api/v1/report` | 에이전트 보고 (`Authorization: Bearer <토큰>`) |
| `GET /api/v1/overview` | 요약, 노드·GPU·작업의 최신 상태, 지금 보이는 공지 |
| `GET /api/v1/nodes`, `/nodes/{key}` | 노드 (`key` 는 식별자) |
| `GET /api/v1/workloads?owner=&node=&type=&project=&status=`, `/workloads/{id}` | 열린 작업과 세션 |
| `GET /api/v1/users`, `/users/{username}` (`_unknown` = 소유자 미확인) | 사용자별 점유 |
| `GET /api/v1/nodes/{key}/history?range=24h\|7d` | 노드의 GPU 별 이력 (24시간 5분 288점, 7일 1시간 168점) |
| `GET /api/v1/gpus/{uuid}/history?from=&to=&step=raw\|5m\|1h` | GPU 하나의 이력 (`raw` 는 24시간 이내) |
| `GET`·`POST /api/v1/admin/users`, `PUT`·`DELETE /api/v1/admin/users/{username}`, `PUT /api/v1/admin/user-order` | 등록 사용자. 순서가 대시보드 색 순서 |
| `GET`·`POST /api/v1/admin/account-mappings`, `PUT`·`DELETE /api/v1/admin/account-mappings/{id}` | 계정 매핑 (노드, uid) → 사용자 |
| `GET`·`POST /api/v1/admin/services`, `PUT`·`DELETE /api/v1/admin/services/{id}` | 공용 서비스 |
| `GET`·`POST /api/v1/admin/notices`, `PUT`·`DELETE /api/v1/admin/notices/{id}` | 공지 |
| `GET /api/v1/admin/nodes`, `PATCH`·`DELETE /api/v1/admin/nodes/{key}` | 노드 목록, 화면 이름 변경, 비활성화된 노드 삭제 |
| `GET /api/v1/healthz` | 헬스체크 |
| `GET /docs` | OpenAPI (Swagger UI) |
