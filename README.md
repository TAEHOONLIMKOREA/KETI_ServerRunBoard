# KETI Server RunBoard

사내용(KETI 내부). 외부에 배포하지 않는다.

연구실 GPU 서버의 GPU 를 **누가 어떤 작업으로 쓰고 있는지** 한 화면에 보여 주는 대시보드다. GPU 노드마다 도는 에이전트가 GPU 지표·프로세스·실행 계정·컨테이너 정보를 서버로 보내고, 서버가 프로세스를 작업으로 묶고 소유자를 판정해 웹 화면과 API 로 보여 준다.

![대시보드](docs/img/dashboard.png)

이 문서는 개요·사용 방법·주의사항이다. 동작 원리, 설정, API, 배포 절차, 개발 환경의 자세한 내용은 [README_DEV.md](README_DEV.md) 에 있다.

## 개요

| 구성 요소 | 하는 일 |
|---|---|
| 에이전트 (`agent/`) | GPU 노드마다 하나. NVML·`/proc`·Docker 에서 GPU 지표, 프로세스, 실행 계정, 컨테이너 정보를 모아 서버로 보낸다 (GPU 동작 중 1초, 유휴 30초). 함께 설치되는 Docker 소켓 중계가 새 컨테이너에 만든 계정을 라벨로 남긴다 |
| 서버 (`server/`) | 중앙에 하나. 보고를 받아 프로세스를 작업으로 묶고 유형·소유자를 판정한다. 최신 상태는 메모리에, 이력·작업·등록 정보·공지는 PostgreSQL 에 둔다. 웹 화면과 API 를 같은 포트로 낸다 |
| 웹 (`web/`) | 대시보드, 전체 GPU 표, 노드·사용자·작업 상세, 관리 화면 |

| 주소 | 내용 |
|---|---|
| `http://keties.iptime.org:55400` | 대시보드 |
| `http://keties.iptime.org:55400/admin` | 관리 화면 (사용자, 계정 매핑, 공용 서비스, 노드, 공지) |
| `http://keties.iptime.org:55400/docs` | API 문서 (Swagger UI) |

## 사용 방법

### 화면 보기

- 대시보드는 노드마다 카드 하나, GPU 한 장이 한 줄이다. 행 색은 작업 소유자의 색이고 빈 GPU 는 초록 `FREE` 다. 1초마다 새로 읽는다 (유휴 노드는 30초마다 보고하므로 값이 30초마다 바뀐다).
- 헤더의 검색창에 노드 이름이나 사람 이름을 치면 맞는 GPU 행만 남는다. `표로 보기` 는 모든 GPU 를 한 표에 놓고 열 머리로 정렬한다.
- 노드 이름을 누르면 노드 상세(GPU 표, 작업 표, GPU 별 24시간·7일 이력 차트), 소유자를 누르면 사용자 상세, 작업 이름을 누르면 작업 상세(세션 표)로 간다.
- 주황 뱃지는 확인할 것이다: `지연`, `온도`, `수집 오류`, `미확인 소유자`(누구 작업인지 모른다), `미확인`(노드가 끊겨 아직 도는지 모른다). 회색 뱃지(`낮은 활동`, `수집 불가`)는 참고다.
- 카드는 압정으로 고정하고 꺾쇠로 접는다. 이 설정은 내 브라우저에만 남는다.

### 내 작업이 내 이름으로 보이게 하기

서버는 소유자를 짐작하지 않는다. 관리 화면 '사용자' 탭에 등록된 사람만 이름과 색을 받고, 다음 중 하나로 작업과 사람을 잇는다.

| 경우 | 방법 |
|---|---|
| 중계가 설치된 노드에서 만든 컨테이너 | 만든 계정이 자동으로 붙는다 (`runboard.owner` 라벨). 그 계정명을 사용자 이름이나 별칭으로 등록해 두면 된다. root 로 만든 컨테이너는 붙지 않는다 |
| 그 밖의 컨테이너 | `docker run --label runboard.owner=<사용자> ...` 로 만들거나, 이름을 `<사용자>_<용도>`(예: `taehoon_torch`)로 짓는다 |
| 호스트 계정으로 직접 실행 | 계정명이 등록 사용자 이름·별칭과 같으면 바로 인식한다. 다르면 관리 화면 '계정 매핑' 에 (노드, uid) → 사용자를 넣는다 |
| 공용 서비스 (Ollama, vLLM 등) | 관리 화면 '공용 서비스' 에 매칭 규칙(실행 계정, 컨테이너명·이미지 패턴)과 담당자를 넣는다 |

관리 화면에서 고치면 돌고 있는 작업도 다음 보고 때 다시 판정한다. 소유자 미확인 작업은 `/users/_unknown` 에 모인다. 판정 순서는 [README_DEV.md 3장 '작업 유형과 소유자 판정'](README_DEV.md#작업-유형과-소유자-판정) 에 있다.

### 서버 띄우기와 운영

서버는 Docker compose 로 띄운다. PostgreSQL(15 이상)이 같은 Docker 네트워크(`postgres_default`)에 먼저 떠 있어야 한다.

```bash
cd deploy
cp runboard.example.yaml runboard.yaml     # auth.shared_token(등록 토큰)을 바꾸고 예시의 fake·dev 노드 토큰은 지운다
# 레포 루트에 settings.ini 를 만든다: [postgres_db] 섹션에 host, port, dbname, user, password (chmod 600)
docker compose up -d --build               # http://<서버>:55400
docker compose logs -f server              # 로그
```

- 띄운 뒤 `/admin` 에서 사용자·계정 매핑·공용 서비스를 등록한다. 공지는 '공지' 탭에서 내용·수준(안내·주의)·표시 기한을 정하면 모든 화면의 헤더 아래에 보인다.
- 노드는 에이전트가 처음 보고할 때 `Server_1`, `Server_2` … 이름으로 자동 등록된다. 이름 변경과 비활성화된 노드 삭제는 '노드' 탭이나 `docker compose exec server runboard-server nodes list|rename|remove` 로 한다.
- 업그레이드는 `git pull` 뒤 `docker compose up -d --build` 다. `runboard.yaml`·`settings.ini` 만 고쳤으면 `docker compose restart server` 로 충분하다.
- 이력은 원본 샘플 7일, 5분 집계 90일, 끝난 작업·세션 365일 동안 두고 지운다 (`runboard.yaml` 의 `server.retention`).

자세한 절차는 [README_DEV.md 9장 '배포와 운영'](README_DEV.md#9-배포와-운영), 설정 키는 [4장 '설정'](README_DEV.md#4-설정) 에 있다.

### GPU 노드에 에이전트 설치

GPU 노드에서 root 로 실행한다. 레포는 에이전트에 필요한 폴더만 sparse checkout 으로 받는다. 비공개 레포라 노드에 읽기 권한(deploy key)이 필요하다. Python 3.10 이상과 venv 모듈이 없으면 스크립트가 apt 로 설치한다.

```bash
git clone --filter=blob:none --sparse git@github.com:TAEHOONLIMKOREA/KETI_ServerRunBoard.git && cd KETI_ServerRunBoard
git sparse-checkout init --cone && git sparse-checkout set agent common deploy
sudo bash deploy/start-agent.sh --dry-run --token <토큰>   # 할 일만 보기
sudo bash deploy/start-agent.sh --token <토큰>             # 설치. 서버 주소는 기본 http://keties.iptime.org:55400
journalctl -u runboard-agent -u runboard-docker-proxy -f   # 로그
```

- 토큰은 서버 `runboard.yaml` 의 등록 토큰(`auth.shared_token`)이다. 몇 초 뒤 대시보드에 새 카드가 나타난다.
- Docker 가 있으면 Docker 소켓 중계도 설치한다. 사용자는 `docker` 를 그대로 쓴다.
- 업그레이드는 `git pull` 뒤 인자 없이 `sudo bash deploy/start-agent.sh` 다. 중계를 걷어내려면 `--remove-docker-proxy`, 중계 없이 에이전트만 두려면 `--no-docker-proxy`.
- 설정(서버 주소, 토큰, 보고 주기)은 `/etc/runboard/agent.toml` 에 있다. 고친 뒤 `sudo systemctl restart runboard-agent` 한다.

설치 순서와 옵션, 컨테이너로 돌리는 법은 [README_DEV.md 9장 'GPU 노드 설치'](README_DEV.md#gpu-노드-설치), 중계의 동작과 한계는 [2장 'Docker 소켓 중계'](README_DEV.md#docker-소켓-중계) 에 있다.

## 주의사항

- **Ubuntu 22.04 이상에서만 사용할 수 있다.** 서버 호스트와 GPU 노드 모두 Ubuntu 22.04 이상을 기준으로 만들었다. 에이전트 설치 스크립트와 Docker 소켓 중계는 apt, systemd, Python 3.10 이상을 전제하므로 Ubuntu 20.04 이하와 다른 배포판은 지원하지 않는다.
- **로그인이 없다.** 대시보드, 조회 API, 관리 화면(`/admin`), 관리 API 모두 서버 주소에 닿는 누구나 보고 고칠 수 있다. 사내에서만 쓰고, 등록 정보·공지를 고치거나 노드를 지울 때는 신중히 한다.
- **HTTP 만 쓴다.** HTTPS 가 없어 에이전트 토큰도 평문으로 오간다. 예시 설정의 등록 토큰(`change-me-join-token`)과 dev 토큰은 운영 전에 꼭 바꾸거나 지운다.
- **중계를 처음 설치할 때 Docker 데몬을 한 번 재시작한다.** 먼저 live-restore 를 켜므로 돌던 컨테이너는 멈추지 않는다. 켤 수 없는 노드(swarm 모드 등)에서는 돌던 컨테이너가 멈추므로 스크립트가 멈추고 안내한다. 그때는 학습이 없는 시간에 `--yes` 를 붙여 다시 한다.
- **중계를 다시 시작하면 긴 docker 연결이 끊긴다.** `start-agent.sh` 로 갱신할 때 `docker attach`·`exec -it`·`logs -f` 같은 연결은 2초 뒤 끊긴다. 컨테이너는 계속 돈다.
- **중계를 설치하기 전에 만든 컨테이너에는 라벨이 없다.** 다시 만들어야 만든 계정이 붙는다. 진짜 소켓을 직접 쓰는 root 나 containerd·Podman 이 만든 컨테이너도 같다.
- **서버 프로세스는 하나만 띄운다.** 최신 상태를 메모리에 두므로 같은 DB 로 둘을 띄우지 않는다.
- **소유자는 추정하지 않는다.** 등록되지 않은 사람의 작업은 소유자 미확인이나 미등록 이름으로 보인다. 위 '내 작업이 내 이름으로 보이게 하기' 대로 알려 준다.
- **명령어의 비밀값은 키워드로만 가린다.** `--token=`, `password=` 같은 `이름=값` 꼴만 `***` 로 바꾸고 200자로 자른다. 비밀값은 명령어 인자 대신 파일이나 환경변수로 넘긴다.
- **끝난 작업은 화면에서 사라진다.** 지난 작업·세션은 DB 에만 남고, 사용량 통계·보고서 기능은 없다.

그 밖의 한계는 [README_DEV.md 11장 '알려진 한계'](README_DEV.md#11-알려진-한계) 에 있다.

## 더 보기

[README_DEV.md](README_DEV.md) 의 목차: 1 구성과 데이터 흐름, 2 에이전트, 3 서버 동작, 4 설정, 5 데이터베이스, 6 API, 7 웹 화면, 8 명령줄, 9 배포와 운영, 10 개발(개발 환경, 로컬에서 돌려 보기, 테스트, 린트, 웹 빌드), 11 알려진 한계.
