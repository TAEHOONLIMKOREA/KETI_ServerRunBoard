# RunBoard 구현 플랜 (MVP: M1~M3)

| 항목 | 내용 |
|---|---|
| 기준 문서 | [PLAN.md](PLAN.md) v0.2 |
| 범위 | MVP (F-01 ~ F-07). 이력·유휴 탐지·알림은 다음 플랜 |
| 작성일 | 2026-10-16 |

이 문서는 PLAN.md를 반복하지 않는다. "어떤 순서로 만들고, 각 단계를 무엇으로 확인하는가"만 적는다.

## 1. PLAN.md에 없던 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| Python 버전 | `>=3.10`. 로컬 개발은 3.12 venv (`.venv`) | 이 PC 기본은 3.10, Ubuntu 22.04도 3.10 |
| 패키지 구성 | 모노레포 3개 패키지: `common/` (runboard-common), `agent/` (runboard-agent), `server/` (runboard-server) + `web/` | 에이전트가 서버 패키지를 import하면 안 됨. 보고 스키마는 `common`에 둔다 |
| 패키지 도구 | venv + pip editable install. uv는 선택 | 전역 도구 설치 없이 시작 |
| 스키마 공유 | `runboard_common.schemas`의 pydantic 모델을 양쪽이 사용 | 에이전트는 보내기 전 검증, 서버는 받아서 검증 |
| 에이전트 collector | `nvml`과 `fake` 두 구현. `--fake`는 개인·컨테이너·공용 서비스 세 케이스를 합성 | Windows에서 서버·UI 개발, 테스트 픽스처 |
| 보강(enrich) | Linux 전용. 다른 OS에서는 컨테이너·pgid를 null로 보냄 | `/proc`, Docker 소켓은 Linux에만 있음 |
| MVP 설정 | 서버는 `runboard.yaml` 하나: 노드 토큰, 사용자·별칭, 계정 매핑, 서비스 레지스트리, 컨테이너명 규칙, 임계값 | 관리 페이지는 v0.3. 그 전까지 파일로 관리 |
| 토큰 | `runboard.yaml`에 노드별 토큰. 평문 또는 `sha256:` 접두 해시 | 내부망 MVP. 해시는 바로 지원 |
| DB 스키마 관리 | 시작 시 `create_all`. Alembic은 스키마가 안정된 뒤(v0.2) | MVP 동안 스키마가 자주 바뀜 |
| 스케줄러 | FastAPI lifespan 안의 asyncio 태스크 (연결 판정 10초, 샘플 persist 30초) | APScheduler 의존성 불필요 |
| 로깅 | 표준 logging, 한 줄 포맷 | MVP |
| 테스트 | pytest. 서버 core는 순수 함수로 두고 단위 테스트. API는 httpx TestClient | |
| 린트 | ruff (check + format) | |
| 프론트 개발 | Vite dev 서버가 `/api`를 `localhost:8000`으로 프록시 | |
| 프론트 배포 | `npm run build` → `web/dist`를 FastAPI가 `/`에서 정적 서빙 | 컨테이너 하나 |

## 2. 검증 환경

| 환경 | 가능한 검증 |
|---|---|
| 이 PC (Windows, RTX 3090) | fake 모드 전체, NVML collector의 GPU 지표·프로세스 목록, 서버·UI 전체 |
| Linux GPU 노드 (사용자 실행) | 컨테이너·pgid 보강, M1 완료 기준(개인 계정·컨테이너·공용 서비스 식별) |

## 3. 단계

각 단계는 "만드는 것"과 "확인 명령"으로 끝난다. 확인이 통과해야 다음 단계로 간다.

### 1단계. 골격·툴링·공용 스키마

- `common/pyproject.toml`, `common/runboard_common/schemas.py` (PLAN 6.1의 보고 JSON을 pydantic으로)
- `agent/pyproject.toml`, `server/pyproject.toml`, `pyproject.toml`(ruff 설정), `README.md`
- `.venv` 생성, 세 패키지 editable 설치

확인:
```
.venv/Scripts/python -c "from runboard_common.schemas import NodeReport; print('ok')"
.venv/Scripts/ruff check .
```

### 2단계. 에이전트 fake 모드와 reporter

- `agent/runboard_agent/config.py` (TOML + 환경변수 + CLI 인자)
- `agent/runboard_agent/collector/base.py`, `collector/fake.py`
- `agent/runboard_agent/reporter.py` (httpx, 지수 백오프, 최신 상태만 전송)
- `agent/runboard_agent/main.py` (`runboard-agent run [--fake] [--once] [--print]`)
- `agent/tests/test_fake_collector.py`

확인:
```
.venv/Scripts/runboard-agent run --fake --once --print   # NodeReport JSON 출력, 스키마 검증 통과
.venv/Scripts/pytest agent
```

### 3단계. 서버 ingest·메모리 상태·overview

- `server/runboard_server/config.py` (`runboard.yaml` 로드, 예시 파일 `deploy/runboard.example.yaml`)
- `server/runboard_server/core/state.py` (NodeState, GpuState, 사용률 링버퍼)
- `server/runboard_server/core/engine.py` (보고 1건 처리 파이프라인)
- `server/runboard_server/api/ingest.py`, `api/query.py` (overview, nodes), `main.py`

확인:
```
.venv/Scripts/uvicorn runboard_server.main:app --port 8000     # 터미널 1
.venv/Scripts/runboard-agent run --fake --server http://localhost:8000 --token dev   # 터미널 2
curl http://localhost:8000/api/v1/overview                      # 노드·GPU·프로세스가 보임
```

### 4단계. 유형·소유자·작업·세션 로직과 DB

- `core/owner.py` (PLAN 4.2 유형·소유자 판정 순서), `core/workloads.py` (묶음 키·작업명), `core/sessions.py` (PLAN 5.2 상태 전이, GB·시간 누적), `core/detector.py` (연결·점유·활동·건강 4축)
- `db/models.py` (PLAN 5.1), `db/repo.py` (세션·작업 upsert, 샘플 persist, 재시작 복원)
- `server/tests/test_owner.py`, `test_workloads.py`, `test_sessions.py`, `test_detector.py`, `test_api.py`

확인:
```
.venv/Scripts/pytest server
curl http://localhost:8000/api/v1/overview   # fake 3케이스가 personal/container/shared_service + 올바른 소유자로 표시
sqlite3 data/runboard.db "select type, name, owner_username, status from workloads"
```

### 5단계. React 대시보드·노드 상세

- `web/` (Vite + React + TS), `src/api.ts`, `src/pages/Dashboard.tsx`, `src/pages/NodeDetail.tsx`, 상태 색·뱃지 컴포넌트
- FastAPI에 `web/dist` 정적 서빙 추가

확인:
```
cd web && npm run build
http://localhost:8000  →  노드 카드, GPU 행, 소유자·뱃지, 1초 갱신
```

### 6단계. 연결 상태·미확인 전이·사용자 뷰

- lifespan 스케줄러: 10초 연결 판정 → offline 시 세션 `unknown`, 복귀 시 `active` 복귀
- `api/query.py`에 users, workloads 추가. `src/pages/Users.tsx`

확인:
```
fake 에이전트를 끈다 → 30초 내 노드 offline, 작업 '미확인' 뱃지
다시 켠다 → 같은 작업으로 이어짐 (workload id 동일, unknown_seconds 증가)
```

### 7단계. 실제 NVML collector와 enrich

- `collector/nvml.py` (nvidia-ml-py, 미지원 항목은 null + errors)
- `enrich.py` (psutil, `/proc/<pid>/cgroup` v1/v2/containerd 파싱, Docker SDK 조회 1종)
- `agent/tests/test_enrich.py` (cgroup 문자열 픽스처)

확인:
```
이 PC: .venv/Scripts/runboard-agent run --once --print   # RTX 3090 지표가 나옴
Linux 노드: 개인 계정 작업, 컨테이너 작업, ollama 각각 올바른 유형·소유자로 표시 (M1 완료 기준)
```

### 8단계. 설치 스크립트·compose·README

- `deploy/install-agent.sh`, `agent/systemd/runboard-agent.service`, `deploy/docker-compose.yml`, `server/Dockerfile`, `deploy/Caddyfile`
- README에 서버 띄우기·노드 추가 절차

확인: 문서대로 새 노드를 등록하면 몇 초 뒤 대시보드에 나타난다.

## 4. 진행 상태

| 단계 | 상태 | 비고 |
|---|---|---|
| 1 | 완료 | 2026-10-16. 공용 스키마 + 3패키지 editable 설치 |
| 2 | 완료 | fake 수집기 8-GPU 시나리오, reporter 백오프, CLI |
| 3 | 완료 | 서버 실행 → fake 보고 → overview 확인 |
| 4 | 완료 | owner/workloads/sessions/detector + SQLite 저장·복원. 테스트 78개 |
| 5 | 완료 | React + Vite + TS. 대시보드·노드 상세·작업 상세. FastAPI 가 web/dist 서빙 (SPA fallback 확인) |
| 6 | 완료 | 실제 프로세스로 확인: 중지 15초 → 지연, 30초 → 끊김·작업 미확인·회색, 재시작 → 같은 세션으로 복귀 (unknown_seconds 누적). 사용자 목록·상세 페이지 |
| 7 | Windows 검증 완료, Linux 대기 | RTX 3090 에서 드라이버·CUDA·메모리·사용률·온도·전력·프로세스 24개 수집. WDDM 이라 프로세스별 메모리는 null. **Linux 노드에서 M1 완료 기준(개인 계정·컨테이너·공용 서비스 식별) 검증은 사용자 실행** |
| 8 | 작성 완료, 실행 검증 대기 | install-agent.sh(bash -n), compose·Caddyfile·Dockerfile 2개. 이 PC 에 docker 가 없어 빌드·기동은 미검증 |

### 사용자가 Linux GPU 노드에서 확인할 것 (M1 완료 기준)

```bash
# 노드에서 (레포 clone 후)
python3 -m venv /tmp/rb && /tmp/rb/bin/pip install ./common ./agent
/tmp/rb/bin/runboard-agent run --once --print | head -80
```

- 호스트 계정 작업: `exec_username`, `exec_uid`, `pgid` 가 채워지고 `container` 는 null
- 컨테이너 작업: `container.id/name/image/labels` 가 채워짐. 안 되면 `errors` 의 `container` 항목(NO_PERMISSION 이면 docker 그룹 확인)
- Ollama 등 공용 서비스: `exec_username` 이 서비스 계정으로 나옴 → 서버 `services` 레지스트리에 등록
- 프로세스별 사용률 미지원 GPU 면 `errors` 에 `process_util NOT_SUPPORTED`, `util_sm` 은 null

### 1~4단계에서 추가로 정한 것

- 작업·세션 ID 는 uuid4 hex 문자열 (DB 왕복 없이 메모리에서 생성)
- 세션 테이블에 `unknown_since`, `miss_count` 도 저장한다 (복원 시 그대로 이어가기 위해)
- 소유자 판정 4번 규칙에 "실행 계정명이 등록 사용자·별칭과 같으면 인식" 을 추가했다. uid 매핑 없이도 호스트 계정 작업이 식별된다
- 빈 GPU 와 끊긴 노드에는 활동 뱃지(낮은 활동·수집 불가)를 붙이지 않는다
- 샘플 persist 는 노드별로 `persist_interval_s` 마다, 노드·GPU 행도 그때 갱신한다

### MVP 이후 바뀐 것 (2026-10-30)

- 보고 주기를 고정 5초에서 적응형으로 바꿨다 (`agent/runboard_agent/cadence.py`). GPU 에 프로세스가 있거나 사용률이 0 보다 크면 `active_interval`(1초), 모두 놀면 `idle_interval`(30초). 수집은 항상 1초마다 해서 유휴 노드에서 작업이 시작되면 바로 보고한다. 동작이 멈춘 뒤 `idle_grace`(5초) 동안은 1초 주기를 유지해, 서버가 2회 연속 누락으로 세션을 닫는 데 1~2초면 되게 했다
- 보고의 `node.report_interval_s` 에 다음 보고까지의 최대 간격을 적는다. 서버의 지연·끊김 판정은 `보고 주기 + delayed_s / offline_s` 다 (동작 중 노드 16초·31초, 유휴 노드 45초·60초). 값이 없는 옛 에이전트는 0 으로 본다
- 1초 보고로 DB 쓰기가 늘지 않도록, 세션·작업은 상태 전이(생성·종료·미확인·복귀) 때만 바로 저장하고 누적값(GB·시간, 사용률)은 tick(10초)과 샘플 저장(30초) 때 모아서 쓴다
- 웹 폴링은 1초 (`web/src/api.ts` 의 REFRESH_MS)
- 대시보드의 필터 칩과 "갱신 n초 전" 표시를 없애고 GPU 행을 소유자별 색으로 구분한다. 색은 runboard.yaml `users` 순서로 앞 8명에게 고정 배정하고(서버가 `owner_slot` 으로 내려줌), 공용 서비스와 9번째 이후 사용자는 중립색, 소유자 미확인은 빗금, 빈 GPU 는 초록이다. 팔레트는 dataviz 스킬의 기본 범주색 8개(라이트·다크 각각)를 이 앱의 카드 배경에 대해 검증해서 썼다. 이름은 항상 색 옆에 같이 적는다
- 상단은 헤더 한 줄(KETI Server RunBoard, 검색창, 연결된 노드 수)만 남기고 요약 타일·내비게이션·테마 버튼을 뺐다. 검색어는 URL `?q=` 로 전달한다. 노드 카드는 이름을 크게, 그 아래 서버 요약(GPU 모델·수, IP, 드라이버/CUDA, CPU, RAM, 에이전트)과 GPU 행 순서다
- 한 번이라도 보고한 노드는 서버가 재시작돼도 사라지지 않는다. 시작 때 `nodes`·`gpus` 와 GPU 별 마지막 샘플을 읽어 메모리에 "비활성화(끊김)" 로 올린다 (`Repository.load_nodes`). 노드를 완전히 지우려면 DB 의 `nodes` 행을 지운다
- 노드 등록을 자동화했다. `auth.shared_token`(등록 토큰)으로 보고하면 이름을 미리 등록하지 않아도 참여하므로 노드를 추가할 때 서버를 재시작하지 않는다. 처음 본 노드는 첫 보고에서 바로 `nodes` 표에 한 행으로 들어가고 순번(`seq`)과 화면 이름(`display_name`, 기본 Server_n)을 받는다
- 노드 식별자는 에이전트가 보내는 `node_id`(설치 스크립트가 agent.toml 에 써 주는 UUID, Docker 는 machine-id 권장), 없으면 노드 이름이다. 같은 node_id 면 호스트명이 바뀌어도 같은 노드, 다른 node_id 면 호스트명이 같아도 다른 노드다. `nodes.name` 열이 이 식별자고 `hostname` 열이 보고된 이름이다. 기존 DB 에는 시작 때 열을 추가하고 화면 이름을 채운다
