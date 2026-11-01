# RunBoard 개선 제안: 참고 저장소 비교에서 가져올 것

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-11-01 |
| 기준 문서 | [PLAN.md](PLAN.md) v0.2, [IMPLEMENTATION.md](IMPLEMENTATION.md) (MVP 완료, 2026-10-30 변경 반영) |
| 비교 대상 | [leftthomas/GPUView](https://github.com/leftthomas/GPUView), [doem97/gpu_dashboard](https://github.com/doem97/gpu_dashboard) (2026-11-01 master 기준) |
| 범위 | 두 저장소에 있고 우리에게 없는 것 중 가져올 가치가 있는 기능. 각 기능의 적용안과 구현 순서 |

이 문서는 두 저장소를 설명하지 않는다. "무엇을, 왜, 우리 구조에 어떻게 넣는가" 만 적는다. 설계 근거가 PLAN.md 에 이미 있으면 그 번호(F-xx, 절 번호)를 가리킨다.

## 1. 비교 대상 요약

| | GPUView | gpu_dashboard | RunBoard (현재) |
|---|---|---|---|
| 구조 | Python Bottle 한 패키지. 각 호스트가 `/gpustat` JSON 을 열고, 대시보드 호스트가 당겨와(pull) 템플릿으로 그린다 | React(CRA) + Express. 서버가 SSH 로 `nvidia-smi` 를 실행해 읽는다. 방화벽 뒤 노드는 ngrok 프록시 | 노드 에이전트(NVML + /proc + Docker)가 서버로 밀어 넣는다(push). FastAPI + SQLite + React/Vite |
| 수집 항목 | gpustat 결과: 온도·사용률·메모리·전력·프로세스(계정, 명령, 메모리) | 온도·사용률·메모리만 | GPU 지표 전부 + 프로세스·실행 계정·컨테이너·pgid, 노드 CPU/RAM |
| 갱신 | 페이지를 열 때 한 번 (자동 갱신 없음) | 페이지를 열 때 한 번 (자동 갱신 없음) | 1초 폴링. 에이전트는 동작 중 1초, 유휴 30초 |
| 이력 | 없음 | 10분마다 서버별 사용률을 JSON 파일에 기록, 7일 보존, 24h/7d 차트 | 30초 샘플을 DB 에 저장하지만 조회·차트가 없고 삭제도 없다 |
| 노드 관리 | CLI `add / remove / hosts` | config.json 수동 편집 | 등록 토큰으로 자동 등록, Server_n 자동 이름. 제거·이름 변경 수단 없음 |
| 그 외 | 온도로 카드 색, 전체 GPU 한 표 | 공지 배너, 카드 고정·접기, PWA 아이콘, 요청 제한 | 소유자별 행 색, 사용자·작업 페이지, 세션 추적, 재시작 복원 |

소유자·세션·인증·자동 등록·복원·실시간성은 우리가 앞선다. 가져올 것은 "이력과 보존", "보드에 바로 보이는 정보", "운영 편의" 세 묶음이다.

## 2. 가져올 기능과 우선순위

| # | 기능 | 출처 | 우리 현황 | PLAN | 크기(대략) | 우선순위 |
|---|---|---|---|---|---|---|
| A | 사용률·메모리 이력 차트 (24h / 7d) 와 데이터 보존 정리 | gpu_dashboard | 샘플만 쌓인다. 조회 API·차트·삭제 없음 | F-08, 5.1 `gpu_samples_5m`, 6.2 history, 3.4 보존 | 대 (2~3일) | 1 |
| B | GPU 행에 온도 표시 | GPUView, gpu_dashboard | 수집은 하고 노드 상세에만 표시 | F-02 | 소 (1시간) | 2 |
| C | 공지 배너 | gpu_dashboard | 없음 | (신규) | 소 (반나절) | 2 |
| G | 파비콘·PWA manifest | gpu_dashboard | `web/public` 이 비어 있다 | (신규) | 소 (1시간) | 2 |
| D | 노드 이름 변경·제거 API 와 CLI | GPUView | DB 행을 직접 고치고 재시작해야 한다 | 6.3 Admin 의 일부 선행 | 중 (1일) | 3 |
| E | 전체 GPU 한 표 페이지 (`/gpus`) | GPUView | 노드별 카드만 | (신규) | 소 (반나절) | 3 |
| F | 노드 카드 고정·접기 (보는 사람별) | gpu_dashboard | 없음 | (신규) | 소 (반나절) | 선택 |
| H | README 스크린샷, LICENSE | 둘 다 | 둘 다 없음 | (신규) | 소 | 선택 (LICENSE 는 결정 필요) |

크기는 테스트와 문서를 포함한 대략치다. 1 → 2 → 3 순서로 하고, 선택 항목은 필요가 보일 때 한다.

## 3. 항목별 적용안

### A. 이력 차트와 보존 정리 (우선순위 1)

**참고 구현.** gpu_dashboard 는 node-cron 으로 10분마다 각 서버의 GPU 사용률 배열을 `server/data/<ip>_history.json` 에 덧붙이고, 쓸 때마다 7일 지난 항목을 버린다. `/api/server-history?ip=` 가 (시각, 평균, 최대) 목록을 돌려주고, 프론트가 24h 는 시간별, 7d 는 일별로 묶어 chart.js 선 차트 두 줄(평균·최대)을 그린다. 서버마다 차트 하나다.

**우리 현황.** `gpu_samples` 에 GPU 별 30초 샘플(util_gpu, util_mem, mem_used_mb, temp_c, power_w)이 쌓인다 (`Repository.insert_samples`). 읽는 코드는 재시작 복원에서 마지막 샘플 하나를 읽는 것뿐이고, 지우는 코드는 없다. 지금 주기로는 GPU 1장당 하루 2,880행이다.

**적용안.** 집계 → 보존 → 조회·화면 세 조각이다. 집계가 보존과 조회 둘 다의 전제라서 같이 간다.

1. 집계 테이블 (PLAN 5.1 그대로, 열 세 개 추가)
   ```
   gpu_samples_5m   gpu_uuid, bucket_ts, util_avg, util_max, mem_avg_mb, mem_max_mb,
                    occupied_ratio, temp_max, power_avg, samples
                    PK (gpu_uuid, bucket_ts)
   ```
   - `gpu_samples` 에 `occupied` (0/1) 열을 추가한다. persist 시점에 그 GPU 에 작업이 있었는지는 엔진이 알고 있으므로 거기서 적는다. `occupied_ratio` 는 이 열의 평균이다. 기존 DB 는 `_add_missing_columns` 가 열을 더한다.
   - 엔진 tick(10초) 안에서 "마지막으로 닫은 버킷" 이후 5분 경계를 넘었으면 그 버킷의 샘플을 GPU 별로 묶어 upsert 한다. 시작 때는 집계 테이블의 마지막 bucket_ts 부터 지금까지를 한 번에 채운다 (서버가 꺼져 있던 동안도 원본 샘플이 남아 있으면 복구된다). 집계 함수는 (샘플 목록) → (행) 순수 함수로 두고 단위 테스트한다.
2. 보존 정리 (PLAN 3.4: 원본 7일, 집계 90일, 세션·작업 1년)
   - 설정 `server.retention` 에 `raw_days: 7`, `agg_days: 90`, `sessions_days: 365`. 환경변수 `RUNBOARD_RETENTION_RAW_DAYS` 등으로 덮어쓸 수 있게 한다.
   - 1시간마다 (tick 횟수로 계산) 세 테이블에서 기한 지난 행을 지운다. 세션은 `status = ended` 이고 `end_ts` 가 기한 전인 것만, 작업은 남은 세션이 없는 것만. 노드·GPU 행은 지우지 않는다 (한 번 보고한 노드는 남긴다는 결정 유지).
   - SQLite 파일 크기는 바로 줄지 않는다. 운영 문서에 `VACUUM` 한 줄을 적는다.
3. 조회 API (PLAN 6.2)
   - `GET /api/v1/gpus/{uuid}/history?from=&to=&step=raw|5m|1h` → `{gpu_uuid, step, points: [{ts, util_avg, util_max, mem_avg_mb, mem_max_mb, temp_max, power_avg}]}`. `raw` 는 24시간 이내만 허용한다.
   - `GET /api/v1/nodes/{key}/history?range=24h|7d` → GPU 별 series 묶음. 24h 는 5분 288점, 7d 는 5분 집계를 1시간으로 다시 묶어 168점. 화면이 쓰는 것은 이것 하나다.
   - 집계·보존·조회는 모두 DB 만 보므로 `Repository` 에 메서드를 두고 엔진은 호출만 한다. 1초 보고 경로(`ingest`)는 건드리지 않는다.
4. 화면
   - 노드 상세(`/nodes/:key`)에 "이력" 절을 추가하고 24h / 7d 전환을 둔다 (PLAN 7.1 의 "노드 상세 24h 차트"). 대시보드에는 아무것도 추가하지 않는다. 대시보드는 한눈에 보는 화면으로 둔다는 결정을 지킨다.
   - GPU 한 장에 작은 차트 하나씩. 세로축은 0~100% 하나로 두고 사용률 평균(면)과 최대(얇은 선), 메모리 사용 비율(선) 세 series 를 같은 축에 그린다. 범례는 차트 묶음 위에 한 번. 마우스를 올리면 그 시각의 값이 뜨는 툴팁을 단다.
   - 라이브러리 없이 인라인 SVG 로 그린다 (선 두 개와 면 하나라 200줄 안쪽). 색은 소유자 색과 겹치지 않게 `--accent` 와 `--series-neutral` 계열로 고정하고, 다크 모드는 기존 토큰이 처리한다.
   - 선택: 사용자 상세에 같은 차트를 "그 사용자가 점유한 GPU" 기준으로. F-14 (GPU-hours 통계) 와 함께 v0.3 에서 한다.

**확인.** fake 에이전트를 10분 이상 돌려 `gpu_samples_5m` 에 행이 생기는지, history API 가 288점을 돌려주는지, 노드 상세 차트가 라이트·다크에서 그려지는지. 보존은 테스트에서 8일 전 샘플을 넣고 정리 뒤 사라지는지로 본다.

### B. GPU 행에 온도 (우선순위 2)

GPUView 는 카드 바탕색을 온도로 정하고(25 / 50 / 75°C), gpu_dashboard 는 온도 숫자를 색으로 칠한다. 우리는 행 색이 소유자이므로 색은 바꾸지 않고 숫자만 더한다.

- `GpuRow` 의 메모리 칸 뒤에 온도 칸 하나 (`44px`, 흐린 글씨, `--°C`). 건강 축이 `temp_warn` 이면 주황색. 기존 "온도" 뱃지는 그대로 둔다.
- 700px 아래에서는 온도 칸을 숨긴다 (지금 grid 가 좁아진다).
- 전력은 노드 상세 표에 이미 있으므로 행에는 넣지 않는다.

### C. 공지 배너 (우선순위 2)

gpu_dashboard 는 "○○ 가 금요일까지 GPU 전부 사용" 같은 안내를 코드에 하드코딩해 상단에 띄운다. 연구실 보드에 실제로 쓰이는 기능이다.

- `runboard.yaml` 에 `notices:` 목록. 항목은 `id`, `text`, `level` (`info` | `warning`), `until` (선택. 이 시각이 지나면 숨김).
- 서버는 `OverviewOut.notices` 로 유효한 것만 내려준다. 프론트는 헤더 아래 한 줄 배너로 그린다. 헤더 자체(제목·검색·노드 수)는 바꾸지 않는다.
- 설정 파일이라 바꾸면 서버 재시작이 필요하다. D 의 관리 API 가 들어오면 `PUT /api/v1/admin/notices` 로 옮긴다.

### D. 노드 이름 변경·제거 (우선순위 3)

GPUView 에는 `gpuview add / remove / hosts` 가 있다. 우리는 등록은 자동이지만, 화면 이름을 바꾸거나 노드를 지우려면 DB 의 `nodes` 행을 직접 고치고 서버를 재시작해야 메모리 상태에 반영된다.

- 설정 `auth.admin_token` (노드 토큰과 같이 평문 또는 `sha256:`).
- API (PLAN 6.3 Admin 의 선행 구현):
  - `GET /api/v1/admin/nodes` : key, display_name, hostname, seq, 마지막 보고 시각
  - `PATCH /api/v1/admin/nodes/{key}` body `{display_name}` : 메모리 상태와 DB 둘 다 바꾼다
  - `DELETE /api/v1/admin/nodes/{key}` : 메모리와 `nodes`·`gpus` 행을 지운다. 세션·작업 이력은 남긴다. 그 노드의 에이전트가 계속 보고하면 새 Server_n 으로 다시 등록된다는 점을 응답과 문서에 적는다
- CLI 는 `runboard-server nodes list | rename <key> <name> | remove <key>` 로 위 API 를 호출한다. README 운영 절에 curl 예시도 적는다.
- 화면에서 이름을 바로 고치는 것(노드 상세 제목 클릭)은 관리자 로그인(F-15) 이 생긴 뒤에 한다.

### E. 전체 GPU 한 표 (우선순위 3)

GPUView 의 "All Hosts and GPUs" 표다. 노드가 열 대를 넘으면 카드보다 표가 빠를 때가 있다 (빈 GPU 찾기, 온도 높은 GPU 찾기).

- 경로 `/gpus`. 열: 노드, #, 모델, 사용률, 메모리(사용/전체), 온도, 전력, 소유자·작업, 뱃지. 열 머리를 누르면 정렬하고 정렬 상태는 `?sort=` 로 남긴다.
- 새 API 는 없다. `overview` 를 그대로 펼친다. 행 색은 카드와 같은 소유자 색 규칙(`gpuPaint`)을 쓴다.
- 들어가는 길은 대시보드 범례 줄 오른쪽 끝의 작은 링크("표로 보기")와 노드 상세. 헤더에는 넣지 않는다.

### F. 노드 카드 고정·접기 (선택)

gpu_dashboard 는 카드마다 핀(위로 고정)과 접기 버튼이 있고 상태를 localStorage 에 저장한다. 보는 사람마다 다른 설정이고 한 번 정하면 가만히 있으므로, 필터 칩을 뺀 결정과 충돌하지 않는다.

- 카드 머리 오른쪽에 핀 아이콘. 고정된 카드는 seq 순서보다 앞. 접힌 카드는 머리와 요약 한 줄만 보인다.
- 저장 키는 노드 key 기준 (`runboard.pinned`, `runboard.folded`). 토스트 같은 알림은 넣지 않는다.
- 노드가 열 대 미만이면 체감이 작다. 필요가 보일 때 한다.

### G. 파비콘·PWA manifest (우선순위 2)

- `web/public/favicon.svg`, `apple-touch-icon.png` (180px), `manifest.webmanifest` (`name`, `short_name`, `icons`, `theme_color`, `display: standalone`). `index.html` 에 link 세 줄.
- Vite 가 `public/` 을 그대로 복사하므로 빌드·서빙 코드 변경은 없다.

### H. README 스크린샷과 LICENSE (선택)

- 스크린샷은 대시보드·노드 상세 두 장을 `docs/img/` 에 둔다. 만드는 명령(헤드리스 Edge)을 README 개발 절에 한 줄 적어 두면 화면이 바뀔 때 다시 찍기 쉽다.
- LICENSE 는 두 저장소 모두 MIT 다. 우리는 공개 여부와 라이선스를 정한 적이 없다. 공개하지 않으면 README 에 "사내용" 한 줄로 충분하다. 결정이 필요한 항목이다.

## 4. 가져오지 않는 것

| 기능 | 어디에 | 이유 |
|---|---|---|
| SSH 나 gpustat 포트로 당겨오는 수집 | 둘 다 | 노드에 설치가 없는 대신 프로세스·계정·컨테이너를 못 얻는다. 우리 핵심 가치(F-03) 와 맞지 않는다 |
| 온도로 카드·행 색 결정 | GPUView | 행 색은 소유자로 정했다. 온도는 숫자와 뱃지로 (B) |
| 메모리 막대 (사용률 막대와 별도) | gpu_dashboard | 행이 더 두꺼워진다. 숫자(사용/전체)로 충분하고, 이력 차트(A)에서 선으로 본다 |
| 조회 API 요청 제한 | gpu_dashboard | 내부망, 리버스 프록시 뒤. 필요해지면 Caddy 단에서 한다 |
| 조회수 카운터, 진입 애니메이션, 토스트 | gpu_dashboard | 보드의 목적과 무관 |
| Telegram 알림 스크립트 다운로드(Toolbox) | gpu_dashboard | 사용자가 자기 학습 코드에 넣는 스크립트다. 보드 알림은 F-13 에서 서버가 보낸다 |
| ngrok 터널 | gpu_dashboard | 내부망 전용(PLAN 8). 외부 공개는 Caddy + HTTPS 로 |

## 5. 이 문서가 다루지 않는 것

두 저장소에 없어서 비교에서 빠졌지만 PLAN 에 있는 다음 기능은 별도 플랜에서 다룬다: 유휴 점유 탐지(F-09), SSE(F-12), 알림(F-13), 사용 통계(F-14), 관리 페이지 전체(F-15), Prometheus(F-16). A 의 집계 테이블과 D 의 관리 토큰은 각각 F-14 와 F-15 의 바탕이 된다.

## 6. 진행 순서

| 차례 | 항목 | 완료 기준 | 상태 |
|---|---|---|---|
| 1 | A 집계·보존 | 테스트 통과. fake 10분 뒤 `gpu_samples_5m` 생성, 8일 전 샘플 삭제 확인 | 대기 |
| 2 | A 조회 API·노드 상세 차트 | history API 288점, 차트 라이트·다크 확인 | 대기 |
| 3 | B, C, G | GPU 행 온도, 공지 한 줄, 파비콘 | 대기 |
| 4 | D, E | 이름 변경·삭제가 재시작 없이 화면에 반영, `/gpus` 정렬 | 대기 |
| 선택 | F, H | 필요가 보일 때 | 대기 |

각 차례는 IMPLEMENTATION.md 의 단계처럼 "만드는 것"과 "확인 명령" 으로 끝낸다. 끝난 항목은 이 표의 상태를 갱신하고 IMPLEMENTATION.md 4장 진행 상태에도 한 줄 적는다.
