# stocktrend-kiwoom Specification

## 1. 개요 (Overview)
`stocktrend-kiwoom`은 키움증권 REST API로 국내(KOSPI·KOSDAQ) **수정주가 일봉(OHLCVA)**을 받아,
`stocktrend-kis`가 적재한 `daily_prices_adj`와 **대조만 하는 검증용 수집기**입니다.

- **아직 DB에 쓰지 않습니다.** 대조 결과는 로그로만 남깁니다. `PriceStore`를 `source` 없이 만들어
  쓰기는 `PermissionError`로 막힙니다.
- **아직 운영에 연결되지 않았습니다.** `stocktrend-infra`의 compose·`unittest.sh`, `stocktrend-manager`의
  `trigger_kiwoom` 발행 어디에도 없어 로컬에서 `run.sh`로만 돌립니다.

## 2. 기술 스택 (Tech Stack)
- **Language**: Python 3.12+
- **Framework**: FastAPI (Uvicorn) — `/health`·`/ready` 전용, 시세 서빙 API는 없음
- **Storage**: PostgreSQL 읽기(`daily_prices_adj`) + 공통 메타데이터 Redis 읽기(마켓·종목 목록)
- **Dependencies**:
  - `httpx`: 키움 REST API 비동기 호출
  - `stocktrend-core`: `RateLimitedApiQueue`(호출 간격), `collector_metadata`(청크 격자·목록 조회),
    `MarketSync`·`listen_for_triggers`(트리거 수신), `PriceStore`(대조용 DB 읽기), `setup_logging`(KST 로그)

## 3. 디렉토리 구조 (Directory Structure)
```text
├── src/
│   ├── config.py           # Pydantic 설정 (포트, Redis URL, 앱 키, 호출 간격·러너 수, 타임아웃)
│   ├── kiwoom_client.py    # 토큰 발급(au10001)과 주식일봉차트(ka10081) 호출·파싱
│   ├── collector.py        # 국내 일봉 대조 사이클 (청크 단위, DB와 비교해 로그만 남김)
│   └── main.py             # FastAPI 진입점, 백그라운드 사이클 lifespan, /health·/ready
├── tests/                  # pytest 단위 테스트 (conftest가 운영 인스턴스를 가리키지 못하게 막음)
├── pyproject.toml          # Poetry 의존성 관리 정의
├── run.sh                  # 로컬 기동 스크립트 (`service` 또는 `test`)
└── Dockerfile              # 컨테이너화 빌드 설정 (포트 8028)
```

## 4. 주요 기능 명세 (Key Features)

### 4.1 키움 REST API 호출 (`kiwoom_client.py`)
- **토큰(au10001)**: `POST /oauth2/token`에 `appkey`·`secretkey`를 보내 받습니다. 만료(`expires_dt`)
  10분 전에 새로 받고, 러너 여럿이 동시에 만료를 봐도 락으로 하나만 발급합니다. 키가 없으면 요청 전에
  `KiwoomApiError`로 실패합니다.
- **주식일봉차트(ka10081)**: `POST /api/dostk/chart`, `api-id: ka10081`, `upd_stkpc_tp=1`(수정주가).
  `base_dt`부터 과거로 첫 페이지만 받습니다. 청크가 100일이라 충분해 연속조회(`cont-yn`/`next-key`)는
  쓰지 않습니다.
- **실패 판정**: HTTP 4xx·5xx뿐 아니라 HTTP 200이어도 `return_code`가 0이 아니면 실패입니다. 401이면
  토큰을 버려 다음 호출이 새로 발급받게 하고, 그 호출은 실패로 셉니다. JSON이 아닌 응답도 실패입니다.
- **파싱**: 숫자는 부호 붙은 문자열(`"+70100"`)로 오고 가격의 부호는 전일 대비 방향이라 떼어 냅니다.
  빈 값은 0이 아니라 `None`으로 둡니다(0으로 채우면 대조에서 불일치로 잘못 잡힘). core 축약 필드
  `o/h/l/c/v/a`로 바꿔 날짜 오름차순으로 돌려줍니다.
- **거래대금 단위**: `trde_prica`는 **백만원 단위**입니다. 공개 문서에 없어 실측했습니다(2026-10-01
  005930 600거래일, 거래대금 ÷ (종가 × 거래량) 중앙값 1.0006e-06). DB가 원 단위라 100만을 곱합니다.

### 4.2 대조 사이클 (`collector.py`)
- **대상 마켓**: list 메타데이터의 국내 마켓(`DOMESTIC_MARKETS`) 중 `is_price_support`이고 지수가 아니며,
  트리거로 요청된 마켓입니다. 종목은 메타데이터 Redis의 마켓별 목록 전부입니다.
- **청크 격자**: 다른 수집기와 같습니다(`BACKFILL_START_DATE`부터 `BACKFILL_CHUNK_DAYS`일 단위, 최신 청크
  먼저). 청크마다 모든 종목을 API 큐로 동시에 넣고, 청크가 끝나면 누적 통계를 로그로 남깁니다.
- **대조 범위**: 청크 끝(오늘을 넘으면 오늘)을 `base_dt`로 받아 청크 구간으로 자릅니다. 오늘 봉은 장중
  값이라 키움·DB 양쪽에서 빼고 마감된 날만 비교합니다.
- **일치 기준**: `o/h/l/c/v/a` 여섯 필드를 상대오차 1e-9로 비교합니다. 거래대금은 키움에 백만원 미만
  자릿수가 없으므로 ±100만원 안의 차이는 일치로 봅니다. 한쪽이라도 `None`인 필드는 비교하지 않습니다.
- **통계**: 마켓별 `rows`·`matched`·`mismatched`·`missing_in_db`(키움에만 있는 날)·`missing_in_kiwoom`
  (DB에만 있는 날)·`calls_ok`·`calls_failed`. 불일치는 마켓당 20건까지 키움·DB 값을 나란히 로그로 남깁니다.
- **완료 기록 없음**: DB에 쓰지 않으므로 완료 비트도 남기지 않고, 매 사이클 전 구간을 다시 받습니다.

### 4.3 트리거와 재시도
- `trigger_kiwoom` 이벤트(`payload.markets`)를 받아 그 마켓으로 사이클을 돕니다. 재시작하면 list의 ready
  키(`MarketSync.restore`)로 직전 목록 갱신을 복구합니다.
- 사이클이 예외로 끝나면 `KIWOOM_SYNC_RETRY_DELAY_MINS`(기본 5분) 뒤 다시 돌고, 정상 종료면 다음 트리거를
  기다립니다. 개별 종목·청크 호출 실패는 사이클을 멈추지 않고 `calls_failed`로만 셉니다.

### 4.4 API
- `GET /health`: `{"status": "ok", "service": "stocktrend-kiwoom"}`
- `GET /ready`: 메타데이터 Redis PING이 되면 `{"status": "ready"}`, 아니면 503

## 5. 데이터 규약 및 연동 (Data Schema & Integration)
- **읽기**: 메타데이터 Redis(마켓 설정·종목 목록·list ready 키), PostgreSQL `daily_prices_adj`
  (`PriceStore.load_daily_prices_adj`, KIS가 쓴 국내 OHLCVA)
- **쓰기**: 없음
- **이벤트**: `trigger_kiwoom`을 구독만 합니다. 발행하는 이벤트는 없습니다. 이벤트 sender 이름은
  `stocktrend-kiwoom`(`SERVICE_NAME`)입니다.

## 6. 설정 (Environment)
| 변수 | 기본값 | 설명 |
|---|---|---|
| `KIWOOM_PORT` | 8028 | FastAPI 포트 |
| `METADATA_REDIS_URL` | (필수) | 마켓·종목 메타데이터 Redis |
| `_POSTGRES_URL` | (필수) | 대조용 `daily_prices_adj` 읽기 (core `PriceStore`가 읽음) |
| `KIWOOM_API_KEY`, `KIWOOM_SECRET_KEY` | 없음 | 앱 키 (Infisical 이름 그대로). 없어도 기동은 하고 첫 호출에서 실패 |
| `KIWOOM_BASE_URL` | `https://api.kiwoom.com` | 키움 REST API 주소 |
| `KIWOOM_INTERVAL_SECS` | 0.25 | 러너 전체가 공유하는 호출 시작 간격. 공개 문서에 한도 수치가 없어 실측 전까지 초당 4회 |
| `KIWOOM_API_WORKERS` | 2 | API 큐 러너 수 |
| `KIWOOM_HTTP_TIMEOUT_SECS` | 30 | HTTP 타임아웃 |
| `KIWOOM_SYNC_RETRY_DELAY_MINS` | 5 | 사이클 실패 뒤 재시도 대기 |
| `_LOG_LEVEL` | `info` | 로그 레벨 |

## 7. 개발 및 실행 방법 (Run)
```bash
poetry install

# 로컬 기동 (stocktrend-infra/secrets/service.env, test 모드는 test.env를 덧씌움)
./run.sh service

# 테스트 (conftest가 운영 Postgres·Redis 대신 일회용 5433·6377을 가리키게 함)
poetry run pytest
```
