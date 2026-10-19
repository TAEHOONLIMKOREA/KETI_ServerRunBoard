# KETI RunBoard — GPU 서버 사용 현황 모니터링 기획·아키텍처

| 항목 | 내용 |
|---|---|
| 문서 상태 | Draft v0.2 |
| 작성일 | 2026-10-16 |
| 대상 독자 | RunBoard 개발·운영 담당자, GPU 서버 관리자 |

### 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v0.2 | 2026-10-16 | gpt_Plan.md와 비교해 반영: 실행 계정·작업 소유자·요청자 3층 모델과 작업 유형, 공용 서비스 구분, 상태 4축, 연결 끊김 시 세션 '미확인' 처리, 작업(workload) 단위 묶음, 부팅 ID·측정 시각 추가, '수집 불가'와 0% 구분, 프로젝트·작업 라벨, GB·시간 지표, Docker 소켓 조회 전용 제한, M1 완료 기준 구체화, 단일 워커 전제 명시, 참고 자료 추가. 프론트는 React로 확정 |
| v0.1 | 2026-10-16 | 초안 |

## 목차

1. [개요](#1-개요)
2. [사용자와 시나리오](#2-사용자와-시나리오)
3. [기능 요구사항](#3-기능-요구사항)
4. [아키텍처](#4-아키텍처)
5. [데이터 모델](#5-데이터-모델)
6. [API 설계](#6-api-설계)
7. [화면 설계](#7-화면-설계)
8. [보안](#8-보안)
9. [배포와 운영](#9-배포와-운영)
10. [프로젝트 구조](#10-프로젝트-구조)
11. [로드맵](#11-로드맵)
12. [리스크와 열린 질문](#12-리스크와-열린-질문)
13. [대안 비교](#13-대안-비교)
14. [참고 자료](#14-참고-자료)

---

## 1. 개요

### 1.1 배경과 문제

- 여러 대의 GPU 서버를 여러 사람이 공유한다. 지금은 누가 어떤 GPU를 쓰는지 알려면 서버마다 SSH로 들어가 `nvidia-smi`를 실행해야 한다.
- `nvidia-smi`는 PID만 보여준다. Docker 컨테이너 안에서 실행된 프로세스는 호스트 PID로 보이기 때문에 "누가 띄운 것인지"를 바로 알 수 없다.
- Ollama 같은 공용 추론 서비스가 GPU를 상주 점유하면 OS 계정은 `ollama`일 뿐이라 누가 쓰는지 더더욱 알 수 없다.
- GPU 메모리만 잡아 두고 연산은 하지 않는 **유휴 점유**가 자주 발생한다. 자원이 낭비되고 사용자 간 마찰이 생긴다.
- "3번 서버 2번 GPU 쓰시는 분 계세요?" 같은 질문이 메신저에서 반복된다.

### 1.2 목표

- 한 화면에서 모든 GPU 서버의 GPU별 **작업 / 소유자 / 사용률 / 메모리**를 실시간(5~10초)으로 확인한다.
- 사용자 기준으로 "누가 어떤 GPU를 몇 장 쓰고 있는지"를 확인한다.
- 소유자를 모를 때는 추정하지 않는다. "미확인"으로 두고 확인된 사실(실행 계정, 컨테이너명)만 보여준다.
- 유휴 점유 GPU를 자동으로 탐지하고 표시한다.
- 노드 추가가 쉬워야 한다. 설치 스크립트 하나와 토큰 하나로 끝나야 한다.

### 1.3 비목표 (1차 범위에서 제외)

- 작업 스케줄링·큐잉 (Slurm의 역할). RunBoard는 **관측**만 한다.
- 원격 프로세스 종료. 책임·보안 문제가 있으므로 관리자 기능으로 추후 검토한다.
- 공용 서비스의 **요청자** 식별. 서비스 쪽 요청 로그 연동이 필요하므로 F-21로 미룬다.
- CPU·디스크·네트워크의 상세 모니터링. 노드 요약 수준만 보여준다.
- NVIDIA 외 GPU (AMD ROCm 등).

### 1.4 용어

| 용어 | 의미 |
|---|---|
| 노드 (Node) | GPU가 장착된 물리 서버 한 대 |
| 에이전트 (Agent) | 각 노드에서 실행되며 GPU 상태를 수집해 서버로 보내는 프로세스 |
| 서버 (Server) | 에이전트 보고를 받아 저장하고 API와 웹 UI를 제공하는 중앙 프로세스 |
| 세션 (Session) | 한 프로세스가 한 GPU를 점유한 시작~종료 구간. 키는 (노드, 부팅 ID, PID, 프로세스 시작 시각, GPU UUID) |
| 작업 (Workload) | 같은 목적으로 함께 도는 프로세스·세션의 묶음. 컨테이너 하나 또는 같은 프로세스 그룹. DDP 8프로세스는 작업 1개 |
| 실행 계정 (Exec account) | 프로세스를 실행한 OS 계정. 관측된 사실 |
| 작업 소유자 (Owner) | 작업의 책임자. 라벨·규칙·매핑으로 서버가 판정 |
| 요청자 (Requester) | 공용 서비스에 요청을 보낸 사람. 1차 범위 밖 |
| 공용 서비스 (Shared service) | Ollama, vLLM처럼 여러 사람이 호출하는 상주 프로세스 |
| 점유 (Occupied) | GPU 메모리를 할당한 프로세스가 하나 이상 존재하는 상태 |
| 유휴 점유 (Idle hog) | 메모리는 잡고 있으나 일정 시간 동안 사용률이 0%에 가까운 상태 |
| 미확인 (Unknown) | 소유자를 판정할 수 없거나, 노드 연결이 끊겨 세션 상태를 알 수 없는 경우 |

### 1.5 전제

- 노드 OS는 Linux (Ubuntu 20.04 이상 가정), NVIDIA 드라이버 470 이상.
- 사용자는 호스트 계정으로 직접 실행하거나 Docker 컨테이너 안에서 실행한다. 두 경우 모두 지원한다.
- Ollama, vLLM 같은 공용 추론 서비스가 일부 GPU에 상주할 수 있다.
- 노드와 서버는 같은 내부망에 있다. 노드 쪽 inbound 포트 개방은 기대하지 않는다.

---

## 2. 사용자와 시나리오

### 2.1 사용자 유형

| 역할 | 주요 니즈 |
|---|---|
| 일반 사용자 (연구원) | 빈 GPU 찾기, 내 작업 상태 확인, 특정 GPU를 누가 쓰는지 확인 |
| 관리자 | 노드 상태 확인, 유휴 점유 식별, 사용 통계, 에이전트·토큰·매핑 관리 |

### 2.2 핵심 시나리오

1. **빈 GPU 찾기** — 대시보드 접속 → "빈 GPU" 필터 → 메모리 여유가 큰 GPU 확인 → 해당 노드에서 작업 실행.
2. **점유자 확인** — GPU 클릭 → 작업 목록(소유자, 실행 계정, 컨테이너, 명령어, 시작 시각, 메모리) 확인 → 필요하면 소유자에게 연락. 공용 서비스면 서비스명과 담당자를 표시한다.
3. **공용 서비스 점유 파악** — Ollama가 GPU 2장을 상주 점유 중임을 "사람"이 아니라 "서비스"로 구분해서 본다.
4. **유휴 점유 알림** — 6시간 이상 사용률 0%인데 메모리를 점유 중 → 대시보드에 경고 뱃지 → (v0.3) Slack 알림.
5. **사용 이력 확인** — 지난 7일 동안 노드별·사용자별·프로젝트별 GPU 점유 시간 확인.
6. **노드 장애 인지** — 에이전트 보고가 끊긴 노드를 "연결 끊김 (마지막 보고 12분 전)"으로 표시하고, 그 노드의 작업은 "미확인"으로 둔다.

---

## 3. 기능 요구사항

### 3.1 MVP (v0.1)

| ID | 기능 | 비고 |
|---|---|---|
| F-01 | 노드 목록과 연결 상태 (정상 / 지연 / 끊김). 끊긴 노드의 작업은 '미확인'으로 유지 | 수신 시각 기반 판정 |
| F-02 | GPU별 상태: 모델명, 사용률, 메모리 사용/전체, 온도, 전력, 상태 4축 (연결·점유·활동·건강) | 4.6 참고 |
| F-03 | GPU별 작업 목록: 작업명, 유형 (개인 / 컨테이너 / 공용 서비스), 작업 소유자, 실행 계정, 컨테이너, 명령어 요약, 메모리, 경과 시간. 소유자를 모르면 '미확인'과 사실만 표시 | 핵심 가치 |
| F-04 | 사용자별 뷰: 사용자가 점유 중인 작업·GPU 목록과 장 수 | |
| F-05 | 자동 갱신 (5~10초) | MVP는 폴링 |
| F-06 | 에이전트 인증 (노드별 토큰) | |
| F-07 | 에이전트 설치 스크립트 (systemd 또는 docker) | |

### 3.2 v0.2

| ID | 기능 |
|---|---|
| F-08 | 시계열 이력: GPU 사용률·메모리 24h / 7d 차트 |
| F-09 | 유휴 점유 탐지와 표시 |
| F-10 | 사용자 매핑: 노드별 계정(uid) → 사용자, 컨테이너명 규칙, 공용 서비스 레지스트리와 담당자 |
| F-11 | 필터·검색 (노드, 사용자, 프로젝트, 상태, 최소 여유 메모리) |
| F-12 | SSE 실시간 푸시 (폴링 대체) |
| F-19 | 프로젝트·작업 라벨 (`runboard.project`, `runboard.job`) 기반 필터와 이력 |
| F-20 | 에이전트 로컬 버퍼와 재전송. 측정 시각으로 정렬·중복 제거하고 지연 데이터는 최신 상태에 반영하지 않음 |

### 3.3 v0.3 이후

| ID | 기능 |
|---|---|
| F-13 | 알림 (Slack / Discord / Email): 유휴 점유, 연결 끊김, 온도 경고, 특정 GPU 비워짐 |
| F-14 | 사용 통계 리포트: 사용자·프로젝트별 GPU 점유 시간(GPU-hours)과 메모리 점유량(GB·시간) |
| F-15 | 관리자 로그인과 관리 페이지 (노드 등록, 토큰 발급, 계정 매핑, 서비스 레지스트리) |
| F-16 | Prometheus `/metrics` 엔드포인트 (기존 Grafana 연동) |
| F-17 | GPU 예약 (간단 캘린더) — 선택 |
| F-18 | MIG 지원 |
| F-21 | 공용 서비스 요청자 연동: 서비스 요청 로그와 인증 사용자를 연결. 서비스 쪽 수정이 필요하므로 실제 필요가 확인된 뒤 진행 |

### 3.4 비기능 요구사항

| 항목 | 기준 |
|---|---|
| 규모 | 노드 30대, GPU 300장, 동시 접속 50명까지 단일 서버 프로세스로 처리 |
| 에이전트 오버헤드 | CPU 1% 미만, 메모리 100MB 미만, 수집 주기 5초 |
| 노드 장애 시 | 서버는 마지막 상태를 유지하고 연결 끊김으로 표시. 작업은 종료가 아닌 미확인 |
| 서버 장애 시 | 에이전트는 지수 백오프로 재시도하며 노드의 작업에는 영향을 주지 않음 |
| 데이터 보존 | 원본 샘플 7일, 5분 집계 90일, 세션·작업 1년 |
| 접근 | 내부망 전용, 리버스 프록시로 HTTPS |
| 화면 | 데스크톱과 모바일 폭 모두 지원, 다크/라이트 |

---

## 4. 아키텍처

### 4.1 전체 구조

```
 ┌──────────────── GPU Node #1 ──────────────┐   ┌───── GPU Node #N ─────┐
 │  NVIDIA driver / NVML                     │   │                       │
 │  ┌─────────────┐   /proc, docker.sock     │   │         ...           │
 │  │  runboard-  │ ─▶ pid → 계정/컨테이너/   │   │  ┌─────────────┐      │
 │  │   agent     │      프로세스 그룹        │   │  │   agent     │      │
 │  └──────┬──────┘                          │   │  └──────┬──────┘      │
 └─────────┼─────────────────────────────────┘   └─────────┼─────────────┘
           │   POST /api/v1/report  (5초마다, Bearer token)  │
           └──────────────────────┬─────────────────────────┘
                                  ▼
 ┌────────────────────── RunBoard Server (FastAPI) ──────────────────────┐
 │  Ingest API   : 수신 → 검증 → 유형·소유자 판정 → 작업·세션 갱신 → 저장   │
 │  Query API    : 노드 / GPU / 작업 / 사용자 / 프로젝트 / 이력 조회        │
 │  Stream (SSE) : 브라우저 실시간 푸시 (v0.2)                             │
 │  Scheduler    : 연결 판정, 유휴 점유 탐지, 5분 집계, 보존 기간 정리       │
 │  ─────────────────────────────────────────────────────────────────    │
 │  In-memory 최신 상태 (NodeState)      SQLite (WAL) — 샘플·세션·작업·집계 │
 └───────────────────────────────┬───────────────────────────────────────┘
                                 │
                 ┌───────────────▼───────────────┐    ┌───────────────┐
                 │  Web UI (React + Vite)        │    │ Slack / Email │
                 │  대시보드 · 노드 · 사용자 · 이력  │    │ 알림 (v0.3)   │
                 └───────────────────────────────┘    └───────────────┘
```

### 4.2 설계 결정과 근거

**Push 방식 채택 (에이전트 → 서버)**

- 노드에 inbound 포트가 필요 없다. 노드 추가 시 서버 설정을 바꿀 필요 없이 토큰만 발급하면 된다.
- Pull 방식(서버가 노드를 폴링)의 장점인 "노드 다운 감지"는 수신 시각 타임아웃으로 대신한다.
- 전송 프로토콜은 HTTP/JSON. gRPC는 스트리밍에 유리하지만 디버깅과 배포 단순성에서 HTTP/JSON이 낫다.

**에이전트는 호스트 PID 네임스페이스에서 실행**

- NVML이 반환하는 PID는 호스트 기준이다. 컨테이너 안에서 에이전트를 돌리면 다른 컨테이너의 프로세스가 보이지 않는다.
- Docker로 배포할 때는 `--pid=host`, `--gpus all`, Docker 소켓(또는 조회 전용 프록시) 마운트가 필요하다.

**에이전트는 사실만 보고하고, 유형과 소유자 판정은 서버가 한다**

- 에이전트가 보내는 것은 사실이다. 실행 계정(username, uid), 컨테이너 ID·이름·이미지·라벨, 프로세스 그룹(pgid, ppid), 명령어 요약.
- 서버는 이 사실로 **작업 유형**과 **작업 소유자**를 판정한다. 실행 계정과 작업 소유자는 별도 필드로 저장한다. 컨테이너 안의 root, 공용 서비스 계정 `ollama`처럼 실행 계정이 사람을 가리키지 않는 경우가 많기 때문이다.
- 판정할 수 없으면 **미확인**으로 두고 확인된 사실(계정, 컨테이너명)만 보여준다. 실명을 추정해서 표시하지 않는다.
- 공용 서비스를 호출한 **요청자**는 OS 정보로 알 수 없다. 1차에서는 서비스 단위 점유로 보여주고, 요청 로그 연동은 F-21로 미룬다.

유형 판정 순서:

1. 서비스 레지스트리에 매칭 (실행 계정, 컨테이너명 패턴, 이미지 패턴) → `shared_service`
2. 컨테이너 안에서 실행 → `container`
3. 그 외 → `personal`

소유자 판정 순서 (먼저 맞는 규칙이 이긴다):

1. 컨테이너 라벨 `runboard.owner`
2. 컨테이너명 규칙 `<user>_<용도>`. `<user>`가 등록된 사용자나 별칭과 일치할 때만
3. 공용 서비스면 레지스트리에 등록된 담당자
4. 노드별 계정 매핑 (노드, uid) → 사용자. 호스트의 uid 0은 매핑이 없으면 사람으로 보지 않는다
5. 없으면 미확인

판정 근거(`owner_source`)를 함께 저장해 화면에서 "왜 이 사람으로 표시됐는지"를 보여준다.

**작업(workload) 단위로 묶는다**

- 사용자 입장의 단위는 프로세스가 아니라 작업이다. DDP 학습은 프로세스 8개가 GPU 8장에 걸치지만 작업 하나다.
- 묶는 키: 컨테이너가 있으면 컨테이너 ID. 없으면 (노드, 부팅 ID, 프로세스 그룹 ID). torchrun과 multiprocessing 자식은 같은 프로세스 그룹을 공유한다.
- 작업명: `runboard.job` 라벨 → 컨테이너명 → 명령어의 스크립트명 (`train.py`) 순.
- 프로세스와 GPU는 다대다다. 세션은 (프로세스, GPU) 한 쌍이고, 작업은 세션의 묶음이다.

**상태는 4축으로 저장하고 색은 요약만 한다**

- 연결·점유·활동·건강은 독립된 사실이다. 사용률 0%는 "메모리만 잡음"일 수도, "데이터 로딩 중"일 수도, "수집 불가"일 수도 있다. 색 하나로 합치면 이 구분이 사라진다.
- 4축은 API와 DB에 그대로 남기고, 대시보드 색은 4축에서 유도한 요약이다. 4.6 참고.
- 미지원·오류로 값을 못 얻은 항목은 0이 아니라 **null(수집 불가)** 로 저장하고 표시한다.

**연결 끊김은 종료가 아니다**

- 에이전트 보고가 끊긴 것은 "모른다"이지 "끝났다"가 아니다. 그 노드의 열린 세션은 `unknown`으로 두고, 복귀 보고에서 같은 키가 보이면 이어 간다.
- 통계에서 미확인 구간은 점유 시간에 넣지 않고 따로 표시한다. 5.2 참고.

**시각은 측정 시각과 수신 시각을 둘 다 저장한다**

- 수신 시각(서버)은 연결 판정과 "N초 전" 표시에 쓴다.
- 측정 시각(에이전트)은 이력·차트·세션 시각에 쓴다. 두 시각의 차이가 60초를 넘으면 시계 오차로 보고 수신 시각으로 대체하며 노드에 경고 플래그를 단다.
- 로컬 버퍼(F-20)를 넣으면 지연 도착 데이터를 측정 시각으로 정렬하고, 최신 상태 갱신에는 쓰지 않는다.

**최신 상태는 메모리, 이력은 SQLite**

- 대시보드 조회는 메모리의 NodeState만 읽는다. DB를 거치지 않는다.
- 이 설계는 **서버 프로세스가 하나(uvicorn 워커 1개)** 라는 전제가 있다. 노드 30대·동시 접속 50명 규모에서는 충분하다. 워커를 늘려야 하면 최신 상태를 Redis로 옮긴다.
- 에이전트는 5초마다 보고하지만 DB에는 30초마다 한 번만 샘플을 쓴다 (`persist_interval`). GPU 300장 기준 하루 86만 행, 7일 600만 행으로 SQLite로 충분하다.
- 프로세스는 매 샘플 저장 대신 **세션(시작~종료)** 으로 저장하고, 메모리 점유량(GB·시간)과 평균 사용률은 보고마다 세션에 누적한다. "누가 언제 얼마나 썼는가"를 바로 계산할 수 있고 행 수도 훨씬 적다.
- 규모가 커지면 PostgreSQL + TimescaleDB로 교체할 수 있도록 저장소 계층을 분리한다 (SQLAlchemy, repository 패턴). 전환은 DB URL 변경이다.

**실시간 전달은 MVP 폴링, v0.2 SSE**

- 서버→브라우저 단방향만 필요하므로 WebSocket보다 SSE가 단순하다. 자동 재연결이 내장되어 있고 프록시 호환성이 좋다.

**Prometheus + Grafana를 주 스택으로 쓰지 않는 이유**

- DCGM exporter는 "누가"를 모른다. PID·사용자·컨테이너 매핑이 없다.
- 대신 수치 메트릭은 `/metrics`로 내보내 기존 Grafana와 병행할 수 있게 한다 (F-16).

### 4.3 기술 스택

| 구성 | 선택 | 대안 | 선택 이유 |
|---|---|---|---|
| 에이전트 | Python 3.11, `nvidia-ml-py`, `psutil`, `docker` SDK, `httpx` | Go 단일 바이너리 | 레포가 Python 템플릿, NVML 바인딩 성숙 |
| 서버 | FastAPI + uvicorn (워커 1개), SQLAlchemy 2.x, APScheduler, pydantic | Go / Gin | 스키마 검증과 OpenAPI 자동 생성 |
| DB | SQLite (WAL 모드) | PostgreSQL + TimescaleDB | 운영 부담 최소. 전환은 URL 변경 |
| 프론트 | React 18 + Vite + TypeScript, TanStack Query, Recharts (확정) | Vue 3 | 차트·실시간 갱신에 유리, 생태계 넓음 |
| 배포 | Docker Compose (server + Caddy), 에이전트는 systemd | | |
| CI | GitHub Actions (ruff, pytest, docker build) | | |

`pynvml` 패키지는 더 이상 권장되지 않으며 NVIDIA 공식 배포는 `nvidia-ml-py`다.

### 4.4 컴포넌트 상세

#### 4.4.1 Agent (`runboard-agent`)

역할: 주기적으로 NVML 조회 → 프로세스 메타 보강 → 서버 전송.

수집 항목:

| 범주 | 항목 |
|---|---|
| 노드 | hostname, IP, 부팅 ID (`/proc/sys/kernel/random/boot_id`), driver_version, cuda_version, 측정 시각, CPU 사용률, 메모리 사용/전체, 에이전트 버전 |
| GPU | index, uuid, name, memory_total/used, util.gpu, util.memory, temperature, power_draw/limit, MIG 여부 |
| 프로세스 | pid, ppid, pgid, type(C/G), used_memory, exec_username, exec_uid, cmdline(앞 200자, 마스킹), proc_start_ts, container {id, name, image, labels}, util_sm(미지원이면 null) |
| 오류 | 범위(gpu:N, node), 항목, 코드. 미지원 항목은 0이 아니라 오류 목록으로 보고 |

PID → 계정·컨테이너·그룹 매핑 절차:

1. `psutil.Process(pid)`로 username, uids().real, ppid, create_time. `os.getpgid(pid)`로 프로세스 그룹.
2. `/proc/<pid>/cgroup`을 읽어 컨테이너 ID 추출
   - cgroup v1: `.../docker/<64hex>`
   - cgroup v2 (systemd driver): `/system.slice/docker-<64hex>.scope`
   - containerd / k8s: `cri-containerd-<id>.scope`, `kubepods...`
3. Docker SDK `containers.get(id)` → name, image, labels. 환경변수는 수집하지 않는다. 호출은 조회 하나뿐이므로 조회 전용 소켓 프록시 뒤에 둘 수 있다 (8장).
4. 소유자 판정은 하지 않는다. 사실만 보낸다.

프로세스별 GPU 사용률:

- `nvmlDeviceGetProcessUtilization`로 얻는다. GPU 세대·드라이버에 따라 미지원이면 `util_sm: null`로 보내고 오류 목록에 `NOT_SUPPORTED`를 남긴다. 0으로 보내지 않는다.

동작 규칙:

- 주기 5초(설정 가능). 전송 실패 시 지수 백오프(최대 60초). MVP는 과거 데이터를 재전송하지 않고 항상 최신 상태만 보낸다. 로컬 버퍼는 F-20.
- NVML 초기화 실패 시에도 노드 정보와 `errors`를 보고한다. UI에서 건강 축 `collect_error`로 표시한다.
- 설정 파일: `/etc/runboard/agent.toml` (server_url, token, interval, node_name).
- 배포: `pip install runboard-agent` + systemd unit, 또는 `docker run`.

#### 4.4.2 Server (`runboard-server`)

| 모듈 | 책임 |
|---|---|
| `api/ingest.py` | `POST /api/v1/report`. 토큰 검증, pydantic 스키마 검증, 시계 오차 검사, 상태 갱신 호출 |
| `api/query.py` | 조회 API |
| `api/stream.py` | SSE (v0.2) |
| `core/state.py` | 최신 상태 in-memory (`dict[node_name, NodeState]`), GPU별 최근 2분 사용률 링버퍼, 락 |
| `core/owner.py` | 작업 유형·소유자 판정 (라벨, 컨테이너명 규칙, 서비스 레지스트리, 계정 매핑) |
| `core/workloads.py` | 세션을 작업으로 묶기 (컨테이너 ID 또는 부팅 ID + pgid) |
| `core/sessions.py` | 세션 열기·닫기·미확인 전이. 키는 (gpu_uuid, boot_id, pid, proc_start_ts). GB·시간과 평균 사용률 누적 |
| `core/detector.py` | 연결 판정 (정상/지연/끊김), 유휴 점유 판정, 온도 경고 |
| `core/aggregator.py` | 5분 집계, 보존 기간 삭제 |
| `db/` | SQLAlchemy 모델, repository |
| `notify/` | Slack, Email (v0.3) |

서버 재시작 시: `status IN (active, unknown)`인 세션과 작업을 DB에서 읽어 복원하고, 다음 에이전트 보고와 대조해 정리한다.

#### 4.4.3 Web UI

7장 화면 설계 참고.

### 4.5 데이터 흐름

1. 에이전트: NVML 조회(GPU당 수 ms) → `/proc`·Docker로 보강 → 측정 시각 기록 → JSON 직렬화(수 KB).
2. `POST /api/v1/report` (Bearer token).
3. 서버: 검증 → 수신 시각 기록, 측정 시각과 비교해 시계 오차 검사 → NodeState 갱신 → 유형·소유자 판정 → 세션 열기/닫기/복귀 → 작업 묶기 → GB·시간 누적 → 30초마다 샘플 insert → SSE 구독자에게 브로드캐스트.
4. 스케줄러 10초: 마지막 수신이 15초를 넘은 노드는 `delayed`, 30초를 넘으면 `offline`. offline 노드의 active 세션·작업을 `unknown`으로 전이. 유휴 점유 조건 평가.
5. 스케줄러 5분: 5분 집계 생성. 1시간: 보존 기간 지난 행 삭제, 24시간 넘게 offline인 노드의 unknown 세션 종료.
6. 브라우저: 최초 `GET /api/v1/overview` → 이후 폴링(MVP) 또는 SSE(v0.2).

### 4.6 상태 모델 (4축)

| 축 | 대상 | 값 | 판정 |
|---|---|---|---|
| 연결 | 노드 | `online` / `delayed` / `offline` | 마지막 수신 15초 이내 / 15~30초 / 30초 초과 |
| 점유 | GPU | `free` / `occupied` / `service` | 세션 없음 / 개인·컨테이너 작업 있음 / 공용 서비스 세션만 있음 |
| 활동 | GPU | `active` / `low` / `unknown` | 최근 1분 평균 사용률 10% 이상 / 10% 미만 / 수집 불가 |
| 건강 | GPU·노드 | `ok` / `temp_warn` / `collect_error` | 온도 85°C 미만 / 이상 / NVML 오류·미지원 항목 존재 |

대시보드 요약 색은 4축에서 유도한다.

| 색 | 조건 |
|---|---|
| 회색 | 연결 `offline`. 마지막 상태를 흐리게 표시 |
| 녹색 | 점유 `free` |
| 파랑 | 점유 `service` |
| 노랑 | 점유 `occupied`, 메모리 사용 70% 미만 |
| 빨강 | 점유 `occupied`, 메모리 사용 70% 이상 또는 사용률 90% 이상 |
| 주황 | 유휴 점유 규칙(4.7) 충족 |

색과 별개로 뱃지를 붙인다: `지연`, `낮은 활동`, `수집 불가`, `온도`, `수집 오류`, `미확인`.

### 4.7 유휴 점유 판정 규칙 (v0.2)

| 항목 | 기본값 (설정 가능) |
|---|---|
| 대상 | GPU 메모리 500MB 이상 점유 중인 세션 |
| 조건 | 최근 2시간 동안 해당 GPU 전체 사용률 평균 3% 미만 |
| 단계 | `유휴 의심`: 2시간 경과, `유휴 점유`: 6시간 경과 (알림 발송) |
| 예외 | 유형이 `shared_service`인 작업, 레지스트리에서 `idle_exempt`로 지정한 서비스, 사용자별 화이트리스트 |

- "낮은 활동"(4.6)은 1분 단위 사실이고, "유휴 점유"는 2시간 창으로 판정한 규칙이다. 데이터 로딩이나 체크포인트 저장으로 생기는 일시적 저활동은 2시간 평균에 흡수된다.
- GPU 전체 사용률을 기준으로 하므로 프로세스별 사용률이 미지원인 GPU에서도 판정할 수 있다. GPU가 활동 중이면 그 위의 개별 프로세스가 유휴인지는 판정하지 않는다.
- 활동 축이 `unknown`(수집 불가)이면 판정하지 않는다.

### 4.8 장애·엣지 케이스

| 상황 | 처리 |
|---|---|
| NVML 초기화 실패 | 노드 정보만 보고 + 오류 목록. 건강 축 `collect_error` |
| 프로세스별 사용률 미지원 | `util_sm: null`, 화면에 "수집 불가". 유휴 판정은 GPU 전체 사용률로 |
| Xorg 등 그래픽 프로세스 | `type=G`로 표시. 기본 필터에서 접음 |
| 수 초짜리 짧은 프로세스 | 5초 샘플링으로 놓칠 수 있음. 이력은 "관측된 이력"임을 통계 화면에 명시 |
| PID 재사용 | (부팅 ID, PID, 프로세스 시작 시각) 키로 구분 |
| 노드 재부팅 | 부팅 ID가 바뀌면 그 노드의 열린 세션을 모두 `ended`, 사유 `node_rebooted` |
| 에이전트 끊김 중 프로세스 종료 | 복귀 보고에 키가 없으면 `ended`, 사유 `lost_while_offline`, 종료 시각은 마지막 관측 시각 |
| 에이전트 24시간 이상 끊김 | unknown 세션을 `ended`, 사유 `node_offline_timeout` |
| 프로세스 그룹이 깨짐 (setsid 등) | 작업이 쪼개져 보임. 컨테이너 또는 `runboard.job` 라벨 사용을 권장 |
| 같은 사용자의 여러 작업 | 소유자 기준으로 집계 |
| 에이전트 중복 실행 | node_name 기준 마지막 보고 우선 + 경고 로그 |
| 노드 시계 오차 60초 초과 | 수신 시각으로 대체, 노드에 `clock_skew` 플래그 |
| 지연 도착 데이터 (F-20 도입 후) | 측정 시각 기준으로 이력에만 반영, 최신 상태 갱신에는 미사용 |
| MIG 활성 GPU | 1차는 "지원 안 함" 표시. F-18에서 처리 |
| 서버 재시작 | 열린 세션·작업 복원, 5초 내 다음 보고로 상태 복구 |

---

## 5. 데이터 모델

### 5.1 테이블

```
nodes            id PK, name UNIQUE, ip, boot_id, driver_version, cuda_version, agent_version,
                 first_seen, last_received_ts, last_measured_ts, clock_skew_s,
                 conn_status [online|delayed|offline], token_hash

gpus             id PK, node_id FK, index, uuid UNIQUE, name, memory_total_mb, mig_enabled

gpu_samples      id PK, gpu_id FK, ts, received_ts, util_gpu, util_mem, mem_used_mb, temp_c, power_w
                 INDEX (gpu_id, ts)                        -- 30초 간격, 7일 보존. ts = 보정된 측정 시각

gpu_samples_5m   gpu_id FK, bucket_ts, util_avg, util_max, mem_avg, mem_max, occupied_ratio
                 PK (gpu_id, bucket_ts)                    -- 90일 보존

workloads        id PK, node_id FK, type [personal|container|shared_service],
                 name, group_key UNIQUE,                   -- container_id 또는 "<boot_id>:<pgid>"
                 container_id, container_name, image, project, job,
                 owner_user_id FK NULL, owner_source [label|container_rule|service|account_map|none],
                 status [active|unknown|ended], first_seen, last_seen, end_ts NULL
                 INDEX (owner_user_id, first_seen), INDEX (project, first_seen), INDEX (status)
                                                           -- 1년 보존

gpu_sessions     id PK, workload_id FK, gpu_id FK, node_id FK,
                 boot_id, pid, proc_start_ts,              -- 프로세스 식별 키
                 exec_username, exec_uid, cmdline,
                 status [active|unknown|ended], start_ts, end_ts NULL, ended_reason,
                 unknown_seconds, mem_max_mb, mem_gb_hours, util_avg NULL, util_samples
                 UNIQUE (gpu_id, boot_id, pid, proc_start_ts)
                 INDEX (workload_id), INDEX (gpu_id, start_ts), INDEX (status)
                                                           -- 1년 보존

users            id PK, username UNIQUE, display_name, email, slack_id, aliases JSON

account_mappings node_id FK NULL, uid, username, user_id FK
                 PK (node_id, uid)                         -- node_id NULL이면 모든 노드에 적용

services         id PK, name, match_exec_account, match_container_pattern, match_image_pattern,
                 owner_user_id FK NULL, idle_exempt BOOL

alerts           id PK, type [idle_hog|node_offline|gpu_freed|temp_warn|collect_error],
                 gpu_id, workload_id, owner_user_id, started_at, resolved_at, notified_at
```

`ended_reason` 값: `exited` (보고에서 사라짐), `lost_while_offline`, `node_rebooted`, `node_offline_timeout`.

### 5.2 세션 상태 전이

| 사건 | 전이 |
|---|---|
| 보고에 새 키 등장 | 세션 생성 `active`, 작업에 연결 (없으면 작업 생성) |
| 같은 키가 2회 연속 보고에서 빠짐 | `ended`, 사유 `exited`, `end_ts` = 마지막 측정 시각 |
| 노드 `offline` | 그 노드의 `active` 세션 → `unknown` |
| 노드 복귀, 같은 키 있음 | `unknown` → `active`, 공백을 `unknown_seconds`에 누적 |
| 노드 복귀, 같은 키 없음 | `ended`, 사유 `lost_while_offline`, `end_ts` = 마지막 측정 시각 |
| 부팅 ID 변경 | 열린 세션 전부 `ended`, 사유 `node_rebooted` |
| `offline` 24시간 초과 | `unknown` 세션 `ended`, 사유 `node_offline_timeout` |

작업 상태는 세션 상태의 집계다. 하나라도 `active`면 `active`, 모두 `ended`면 `ended`, 그 외 `unknown`.

### 5.3 통계 정의

| 지표 | 정의 | 비고 |
|---|---|---|
| 점유 시간 (GPU-hours) | 세션별 `active` 시간의 합 | `unknown` 시간은 제외하고 따로 표시. 공유 GPU에서는 사용자별 합이 경과 시간을 초과할 수 있음 |
| 메모리 점유량 (GB·시간) | 보고마다 (메모리 GB × 보고 간격)을 세션에 누적 | 메모리를 많이, 오래 잡을수록 큼. 공정 배분 논의에 유용 |
| 평균 사용률 | `util_sm`이 있는 보고만 평균 | 미지원이면 null |
| 연산 시간 | 측정하지 않음 | 프로세스별 사용률 미지원 GPU가 있어 정의할 수 없음 |

통계를 보여주는 모든 화면에 이 정의를 함께 표시한다. 점유 시간은 연산 시간이 아니다.

---

## 6. API 설계

### 6.1 Ingest (에이전트용)

`POST /api/v1/report` — 헤더 `Authorization: Bearer <node token>`, 응답 `204`.

```json
{
  "node": {
    "name": "gpu01", "ip": "10.0.0.11", "boot_id": "6f1c9a2e-...",
    "driver_version": "550.54", "cuda_version": "12.4",
    "agent_version": "0.2.0", "measured_ts": 1760601000.2,
    "cpu_percent": 12.5, "mem_used_mb": 51200, "mem_total_mb": 262144
  },
  "gpus": [
    {
      "index": 0, "uuid": "GPU-7b2c...", "name": "NVIDIA A100 80GB PCIe",
      "mem_total_mb": 81920, "mem_used_mb": 40200,
      "util_gpu": 87, "util_mem": 60, "temp_c": 61,
      "power_w": 250.3, "power_limit_w": 300, "mig_enabled": false,
      "processes": [
        {
          "pid": 12345, "ppid": 12300, "pgid": 12300, "type": "C",
          "mem_used_mb": 40000, "util_sm": 85,
          "exec_username": "root", "exec_uid": 0,
          "cmdline": "python train.py --cfg configs/a.yaml",
          "proc_start_ts": 1760600000,
          "container": {
            "id": "ab12cd34...", "name": "taehoon_torch",
            "image": "pytorch/pytorch:2.4.0-cuda12.4",
            "labels": {
              "runboard.owner": "taehoon",
              "runboard.project": "pbf-defect",
              "runboard.job": "seg-train"
            }
          }
        }
      ]
    },
    {
      "index": 1, "uuid": "GPU-91e0...", "name": "NVIDIA A100 80GB PCIe",
      "mem_total_mb": 81920, "mem_used_mb": 36800,
      "util_gpu": 3, "util_mem": 1, "temp_c": 41,
      "power_w": 70.1, "power_limit_w": 300, "mig_enabled": false,
      "processes": [
        {
          "pid": 2210, "ppid": 1, "pgid": 2210, "type": "C",
          "mem_used_mb": 36500, "util_sm": null,
          "exec_username": "ollama", "exec_uid": 998,
          "cmdline": "/usr/local/bin/ollama serve",
          "proc_start_ts": 1760400000,
          "container": null
        }
      ]
    }
  ],
  "errors": [
    {"scope": "gpu:1", "item": "process_util", "code": "NOT_SUPPORTED"}
  ]
}
```

서버는 수신 시각을 기록하고 `measured_ts`와 비교한다. 두 번째 GPU의 프로세스는 서비스 레지스트리(`match_exec_account = ollama`)에 걸려 유형 `shared_service`, 소유자는 레지스트리 담당자가 된다.

### 6.2 Query (UI용)

| 메서드·경로 | 설명 |
|---|---|
| `GET /api/v1/overview` | 모든 노드·GPU·작업의 최신 상태와 4축. 대시보드 최초 로드용 |
| `GET /api/v1/nodes` | 노드 목록과 연결 상태 |
| `GET /api/v1/nodes/{name}` | 노드 상세 (GPU, 작업, 세션 포함) |
| `GET /api/v1/gpus?occupancy=free\|occupied\|service&activity=&min_free_mb=` | 조건으로 GPU 검색 |
| `GET /api/v1/workloads?status=&owner=&project=&type=` | 작업 목록 |
| `GET /api/v1/workloads/{id}` | 작업 상세: 세션, GPU, 소유자 판정 근거 |
| `GET /api/v1/users` | 사용자별 점유 작업·GPU 수, 메모리 합, 유휴 점유 수 |
| `GET /api/v1/users/{name}` | 사용자가 점유 중인 작업과 GPU |
| `GET /api/v1/users/{name}/stats?from=&to=` | 점유 시간, GB·시간, 미확인 시간 (v0.3) |
| `GET /api/v1/projects`, `/api/v1/projects/{name}` | 프로젝트별 현재 작업과 이력 (v0.2) |
| `GET /api/v1/services` | 공용 서비스별 점유 GPU와 담당자 (v0.2) |
| `GET /api/v1/gpus/{uuid}/history?from=&to=&step=5m` | 시계열 (v0.2) |
| `GET /api/v1/sessions?owner=&gpu=&from=&to=` | 세션 이력 (v0.2) |
| `GET /api/v1/stream` | SSE. 이벤트: `snapshot`, `node_update`, `alert` (v0.2) |
| `GET /metrics` | Prometheus 형식 (v0.3) |
| `GET /healthz` | 헬스체크 |

### 6.3 Admin (v0.3)

| 메서드·경로 | 설명 |
|---|---|
| `POST /api/v1/admin/nodes` | 노드 등록, 토큰 발급 |
| `PUT /api/v1/admin/users/{name}` | 사용자 정보·별칭 수정 |
| `PUT /api/v1/admin/account-mappings` | 노드별 uid → 사용자 매핑 |
| `PUT /api/v1/admin/services` | 공용 서비스 레지스트리 (매칭 규칙, 담당자, 유휴 예외) |
| `PUT /api/v1/admin/settings` | 임계값, 알림 채널 |

---

## 7. 화면 설계

### 7.1 페이지 구성

| 경로 | 페이지 | 내용 |
|---|---|---|
| `/` | 대시보드 | 상단 요약(노드 수, 끊긴 노드, GPU 수, 빈 GPU 수). 노드 카드 그리드. GPU 행마다 작업·소유자·상태 뱃지 |
| `/nodes/:name` | 노드 상세 | GPU 테이블, 작업·세션 테이블, 24h 차트(v0.2), 수집 오류 목록 |
| `/workloads/:id` | 작업 상세 | 세션 목록, 걸친 GPU, 소유자 판정 근거, 타임라인 |
| `/users` | 사용자 뷰 | 사용자별 점유 작업·GPU 수, 메모리 합, 유휴 점유 수. 클릭 시 작업 목록 |
| `/users/:name` | 사용자 상세 | 현재 작업, 이력, 점유 시간·GB·시간 통계와 정의 (v0.3) |
| `/projects` | 프로젝트 뷰 | 프로젝트별 현재 작업과 이력 (v0.2) |
| `/history` | 이력 | 노드·GPU 선택 → 시계열, 세션 타임라인 (v0.2) |
| `/admin` | 관리 | 노드·토큰, 계정 매핑, 서비스 레지스트리, 알림 설정 (v0.3) |

### 7.2 대시보드 와이어프레임

```
┌ RunBoard ───────────── 검색   [전체 | 빈 GPU | 유휴 의심 | 공용 서비스]   갱신 5초 전 ┐
│  노드 6 (끊김 1)   GPU 48   빈 GPU 11   유휴 의심 2                                 │
│                                                                                   │
│ gpu01   ● online   A100 80GB x8   driver 550.54                   마지막 보고 3초 전 │
│  [0] ████████░░  87%   65 / 80 GB   taehoon    seg-train (컨테이너)     5h 12m       │
│  [1] ░░░░░░░░░░   0%    0 / 80 GB   —  FREE                                         │
│  [2] ██░░░░░░░░   0%   30 / 80 GB   minsu      jupyter   [낮은 활동] [유휴 의심 7h]  │
│  [3] ██████████  99%   79 / 80 GB   jihye      ddp_main.py (작업 1개, GPU 3~6) 1d 3h │
│  [7] ████░░░░░░   3%   36 / 80 GB   공용 서비스  ollama    담당 taehoon   2d          │
│                                                                                   │
│ gpu02   ○ offline (12분 전)   작업 2개 미확인                                        │
│  [0..7] (마지막 상태, 흐리게)                                                         │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### 7.3 표시 규칙

- 색은 4.6의 요약 색을 따르고, 뱃지로 `지연`, `낮은 활동`, `수집 불가`, `온도`, `수집 오류`, `미확인`을 덧붙인다.
- 수집 불가 항목은 0이 아니라 `—`로 표시하고 툴팁에 "수집 불가 (NOT_SUPPORTED)"를 보여준다.
- 소유자가 미확인이면 `미확인 · root @ taehoon_torch`처럼 확인된 사실을 함께 표시한다. 이름을 추정해 보여주지 않는다.
- 소유자 옆에 판정 근거 아이콘(라벨 / 컨테이너명 / 서비스 / 계정 매핑)을 두고 hover로 설명한다.
- 여러 GPU에 걸친 작업은 각 GPU 행에 나오되 같은 작업임을 표시한다.
- 마지막 갱신 시각을 항상 표시한다. 연결 끊긴 노드는 마지막 상태를 흐리게 보여준다.
- 명령어는 한 줄로 자르고 hover 시 전체(마스킹 후)를 보여준다.
- 통계 화면에는 5.3의 지표 정의를 그대로 표시한다.
- 모바일에서는 노드 카드가 1열로 쌓인다.

---

## 8. 보안

- **에이전트 토큰**: 노드별 발급. 서버는 해시만 저장. `Authorization: Bearer` 헤더로 전송. 리버스 프록시에서 HTTPS 종단.
- **중앙 서버는 노드에 접근하지 않는다**: 어떤 노드의 Docker 소켓, SSH, 명령 실행 권한도 갖지 않는다. 데이터는 에이전트가 밀어 넣는 방향뿐이다.
- **Docker 소켓은 조회 전용으로**: Docker 소켓 접근은 호스트 root와 같다. 에이전트는 컨테이너 조회 하나만 쓰므로, 노드에서 `docker-socket-proxy`로 `CONTAINERS` 조회 API만 열어 에이전트에 넘기는 구성을 권장한다. 프록시 없이 쓸 때는 systemd 전용 계정 + `docker` 그룹으로 제한한다.
- **대시보드 접근**: 1차는 내부망 + 읽기 전용(인증 없음). 외부 노출이 필요하면 v0.3에서 로그인(단순 비밀번호, 이후 OAuth/LDAP)을 붙인다.
- **명령어 마스킹**: `--token=`, `password=`, `key=` 같은 패턴은 에이전트에서 값을 `***`로 치환한다. 200자로 자른다.
- **환경변수 미수집**: 컨테이너 env는 읽지 않는다.
- **쓰기 API**: ingest는 토큰, admin은 로그인 세션으로 보호한다. 조회 API에는 쓰기 동작이 없다.
- **라벨은 신고 정보다**: `runboard.owner`는 사용자가 적는 값이므로 책임 추적의 근거로는 약하다. 판정 근거를 함께 저장해 두고, 엄격한 추적이 필요해지면 인증된 작업 등록 절차를 추가한다.

---

## 9. 배포와 운영

### 9.1 서버

```yaml
# deploy/docker-compose.yml (개요)
services:
  server:
    image: ghcr.io/<org>/runboard-server:latest
    command: uvicorn runboard_server.main:app --host 0.0.0.0 --port 8000 --workers 1
    volumes: ["./data:/data"]          # /data/runboard.db
    environment:
      RUNBOARD_DB_URL: sqlite:////data/runboard.db
      RUNBOARD_PERSIST_INTERVAL: "30"
  caddy:
    image: caddy:2
    ports: ["443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile"]
```

### 9.2 에이전트

```bash
# 설치 스크립트 (systemd 유닛 생성)
curl -sSL https://runboard.internal/install.sh | sudo bash -s -- --token <NODE_TOKEN>

# 또는 docker
docker run -d --name runboard-agent --restart unless-stopped \
  --gpus all --pid=host \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e RUNBOARD_SERVER=https://runboard.internal -e RUNBOARD_TOKEN=<NODE_TOKEN> \
  ghcr.io/<org>/runboard-agent:latest
# 소켓 프록시를 쓰면 -v 대신 -e DOCKER_HOST=tcp://docker-socket-proxy:2375
```

### 9.3 운영

| 항목 | 방법 |
|---|---|
| 로그 | 서버는 JSON 구조화 로그, 에이전트는 journald |
| 백업 | SQLite 파일을 하루 1회 복사 (`sqlite3 .backup`) |
| 에이전트 버전 관리 | 보고에 버전 포함. 관리 페이지에서 구버전 노드 표시 |
| 헬스체크 | `/healthz`, 에이전트는 systemd `Restart=always` |
| 노드 추가 | 토큰 발급 → 설치 스크립트 실행 → 5초 후 대시보드에 자동 등장 |
| 소유자 식별률 | 관리 페이지에서 "미확인 작업 비율"을 보여주고, 높으면 계정 매핑·라벨 안내를 보낸다 |

---

## 10. 프로젝트 구조

```
KETI_RunBoard/
├── agent/                      # runboard-agent (Python package)
│   ├── runboard_agent/
│   │   ├── collector.py        # NVML 조회, 오류 목록
│   │   ├── enrich.py           # pid → 계정 / 컨테이너 / 프로세스 그룹
│   │   ├── reporter.py         # HTTP 전송, 백오프 (F-20: 로컬 버퍼)
│   │   ├── config.py
│   │   └── main.py
│   ├── systemd/runboard-agent.service
│   ├── tests/
│   └── pyproject.toml
├── server/                     # runboard-server
│   ├── runboard_server/
│   │   ├── api/                # ingest, query, stream, admin
│   │   ├── core/               # state, owner, workloads, sessions, detector, aggregator
│   │   ├── db/                 # models, repository, migrations
│   │   ├── notify/
│   │   ├── schemas.py          # pydantic (에이전트와 공유)
│   │   └── main.py
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
├── web/                        # React + Vite + TS
├── deploy/
│   ├── docker-compose.yml
│   ├── Caddyfile
│   └── install-agent.sh
├── docs/
│   └── PLAN.md                 # 이 문서
└── README.md
```

에이전트와 서버의 보고 스키마는 한 곳(`schemas.py`)에서 정의하고 양쪽이 같은 pydantic 모델을 쓴다.

---

## 11. 로드맵

| 단계 | 기간 | 산출물 | 완료 기준 |
|---|---|---|---|
| M0 설계 | 1주차 | 이 문서, 보고 스키마 확정 | 리뷰 완료 |
| M1 에이전트 | 1~2주차 | NVML 수집 + 계정·컨테이너·프로세스 그룹 보강 + JSON 출력 | 실제 노드 1대에서 **개인 계정 작업, Docker 컨테이너 작업, 공용 서비스** 세 경우가 각각 올바른 유형·소유자로 식별됨. 미지원 항목은 '수집 불가'로 보고됨 |
| M2 서버 + 기본 UI | 2~3주차 | ingest/overview API, 유형·소유자 판정, 작업 묶음, 대시보드 표 | 노드 3대 연동, 5초 갱신. DDP 8프로세스가 작업 1개로 보임 |
| M3 연결 상태 · 사용자 뷰 | 3주차 | F-01 4축 연결 판정, 세션 미확인 전이, F-04 | 에이전트를 멈추면 30초 내 끊김 표시, 작업은 미확인. 재시작하면 같은 작업으로 이어짐 |
| M4 이력 · 유휴 탐지 | 4~5주차 | 세션·GB·시간 누적, 시계열 차트, 유휴 점유, 프로젝트 뷰 | 실제 유휴 점유 1건 이상 탐지. 통계 정의가 화면에 표시됨 |
| M5 알림 · 관리 | 6주차 | Slack 알림, 관리 페이지(계정 매핑, 서비스 레지스트리) | 유휴 점유 알림 수신. 미확인 작업 비율이 관리 페이지에 보임 |
| M6 운영 전환 | 7주차 | 설치 스크립트, README, 전 노드 배포 | 전 노드 등록, 1주 무중단 |
| 추후 | 필요 확인 후 | F-20 로컬 버퍼, F-21 공용 서비스 요청자 연동, F-17 예약 | 실제 필요가 확인된 기능부터 |

MVP(v0.1)는 M1~M3까지다.

---

## 12. 리스크와 열린 질문

### 12.1 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| 컨테이너 내부 root 실행으로 소유자 식별 불가 | 핵심 기능 정확도 저하 | 컨테이너명 규칙(`<user>_<용도>`) 또는 `runboard.owner` 라벨을 운영 정책으로 안내. 미식별은 '미확인'과 사실만 표시. 관리 페이지에서 미확인 비율 추적 |
| 공용 서비스의 요청자를 알 수 없음 | 서비스 점유가 특정인에게 귀속되지 않음 | 1차는 서비스 단위 점유와 담당자만 표시. 요청자 연동(F-21)은 서비스 쪽 로그 수정이 필요하므로 필요가 확인된 뒤 진행 |
| 프로세스별 사용률 미지원 GPU/드라이버 | 작업별 활동 판정 불가 | null로 저장하고 '수집 불가'로 표시. 유휴 판정은 GPU 전체 사용률로 |
| 프로세스 그룹으로 묶기 실패 (setsid 등) | 작업이 쪼개져 보임 | 컨테이너 또는 `runboard.job` 라벨 사용 권장. 묶음 키를 관리자가 수동 지정하는 기능은 추후 |
| 노드마다 cgroup v1/v2, Docker 버전 혼재 | 매핑 실패 | enrich 로직이 둘 다 지원. 노드별 통합 테스트 |
| 라벨은 자진 신고라 조작 가능 | 책임 추적 약화 | 판정 근거를 저장·표시. 필요 시 인증된 작업 등록 절차 추가 |
| SQLite 쓰기 병목 | 이력 유실 | persist_interval 조정, 배치 insert. 한계 시 PostgreSQL 전환 (URL 변경) |
| 단일 워커 전제 | 수평 확장 불가 | 이 규모에서는 불필요. 필요 시 최신 상태를 Redis로 이동 |

### 12.2 결정이 필요한 질문

1. 노드 대수, GPU 수, 각 노드의 OS·드라이버·Docker 버전은?
2. 사용자들이 주로 Docker 컨테이너로 쓰는가, 호스트 계정으로 쓰는가? (소유자 규칙 우선순위 결정)
3. 컨테이너명 규칙이나 라벨을 운영 정책으로 안내·강제할 수 있는가?
4. 어떤 공용 서비스(Ollama, vLLM 등)가 어느 노드에 있고 담당자는 누구인가? (서비스 레지스트리 초기값)
5. 여러 사람이 함께 쓰는 공용 Linux 계정이 있는가? 있으면 그 계정의 작업은 미확인으로 둘지, 작업 등록 절차를 둘지
6. 대시보드를 외부망에 노출할 것인가? (인증 필요 여부)
7. 알림 채널은 Slack, Discord, 메일 중 무엇인가?
8. 유휴 점유 임계값(시간, 메모리)과 알림 수신자 정책은?
9. 기존 Grafana/Prometheus가 있는가? 있으면 F-16 우선순위를 올린다.

---

## 13. 대안 비교

| 도구 | 장점 | 한계 |
|---|---|---|
| gpustat-web | 설치 간단, SSH로 여러 노드 집계, 사용자 표시 | 컨테이너 소유자 식별 없음, 이력·알림 없음 |
| nvitop / nvtop | 단일 노드 TUI로 상세 | 웹 없음, 다중 노드 없음 |
| DCGM exporter + Grafana | 메트릭 표준, 확장성 | 프로세스·사용자 정보 없음 |
| nvidia_gpu_exporter | 프로세스 정보 일부 | 사용자·컨테이너 매핑 없음 |
| Slurm | 완전한 스케줄링과 사용자 추적 | 운영 부담 큼, 사용 방식 전환 필요 |

RunBoard의 차별점은 **컨테이너와 공용 서비스를 구분하는 소유자 모델**, **작업·세션 기반 사용 이력**, **유휴 점유 탐지**, **토큰 하나로 끝나는 설치**다.

---

## 14. 참고 자료

- NVML API Reference: https://docs.nvidia.com/deploy/nvml-api/
- nvidia-ml-py (PyPI): https://pypi.org/project/nvidia-ml-py/
- Docker Engine security (소켓 접근 권한): https://docs.docker.com/engine/security/
- docker-socket-proxy: https://github.com/Tecnativa/docker-socket-proxy
- NVIDIA dcgm-exporter: https://github.com/NVIDIA/dcgm-exporter
- gpustat-web: https://github.com/wookayin/gpustat-web
