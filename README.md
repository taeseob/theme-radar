# Theme Radar

한국(KOSPI/KOSDAQ 보통주)과 미국(S&P 500) 주식시장의 **섹터/테마별 수익률과 순위 변화**를 관찰하는 대시보드.

## 이 시스템이 답하는 질문

1. 과거 어느 섹터/테마의 성과가 높았는가? — 기간별 수익률과 **순위 범프 차트**
2. 어떤 섹터가 시장 등락에 크게 기여했는가? — **기여도 분해**(시장 수익률 = Σ 섹터 기여도)
3. 그 성과가 일부 대형 종목에 집중되어 있는가? — **종목 기여도 분해 및 집중도 지표**

## 기본 사양 요약

| 항목 | 값 |
| --- | --- |
| 조회 대상 | KR: KOSPI/KOSDAQ 보통주 · US: S&P 500 구성종목 |
| 분류 체계 | KR: WI26 대분류·소분류 · US: GICS 섹터·산업(준거) — **자체 제공 매핑 테이블** |
| 기간 단위 | 월(Month), ISO Week — **화면 기본값: 주(Week)** |
| 섹터 수익률 | **직전 기간 말 시가총액 가중** 평균 |
| 통화 | KR: KRW · US: USD (시장 간 환산·통합 없음) |

## 문서 목록

| 문서 | 내용 |
| --- | --- |
| [01-requirements.md](docs/01-requirements.md) | 요구사항, 유니버스, 분류 체계, 기간 체계, 범위 경계, 용어 |
| [02-domain-and-data-model.md](docs/02-domain-and-data-model.md) | 도메인 모델, ERD, 테이블 목적·키·규약, 입력 데이터 규격 (DDL은 [migrations](theme_radar/db/migrations/)) |
| [03-metrics-spec.md](docs/03-metrics-spec.md) | 지표 계산 명세(수익률·가중치·기여도·순위·집중도), 예외 처리, 검증 규칙 |
| [04-pipeline.md](docs/04-pipeline.md) | 아키텍처, 배치 파이프라인, 재계산 정책, 데이터 품질 |
| [05-api-spec.md](docs/05-api-spec.md) | REST API 명세 |
| [06-ui-spec.md](docs/06-ui-spec.md) | 화면 구성, 범프 차트 및 드릴다운 명세 |
| [07-price-ingestion.md](docs/07-price-ingestion.md) | 주가·상장주식수 수집 모듈: 출처 검증, yfinance·FinanceDataReader 사용 범위, 분할·병합 소급 갱신, 수집 단계 검증 |
| [08-library-review.md](docs/08-library-review.md) | FinanceDataReader·yfinance 검토 기록과 채택 결정, Yahoo 분할 이벤트의 분사 혼입 |
| [09-tech-stack.md](docs/09-tech-stack.md) | 기술 스택(Python·SQLite·FastAPI·ECharts)과 선택 이유, 저장소 구조, 배치 실행, 구현 순서 |

## 개발 환경

Python 3.12(pyenv) 기준이다. 기술 스택과 구조는 [09-tech-stack.md](docs/09-tech-stack.md)를 본다.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock   # 버전 고정 설치
.venv\Scripts\python -m theme_radar init-db                # data/theme_radar.sqlite3 생성, 스키마 적용
.venv\Scripts\python -m pytest                             # 테스트
```

처음 데이터를 적재하는 순서 (명령 설명은 [07 §5.2](docs/07-price-ingestion.md#52-명령)):

```bash
.venv\Scripts\python -m theme_radar prices universe --market KR
.venv\Scripts\python -m theme_radar prices universe --market US
.venv\Scripts\python -m theme_radar load-mapping --scheme WI26
.venv\Scripts\python -m theme_radar load-mapping --scheme WI26_SUB
.venv\Scripts\python -m theme_radar load-mapping --scheme GICS
.venv\Scripts\python -m theme_radar load-mapping --scheme GICS_IND
.venv\Scripts\python -m theme_radar prices backfill --market KR   # 약 1시간
.venv\Scripts\python -m theme_radar prices backfill --market US
.venv\Scripts\python -m theme_radar aggregate --market KR --full   # 파생 지표 산출 (스킴 2개, 약 40초)
.venv\Scripts\python -m theme_radar aggregate --market US --full
```

## 볼 때마다 하는 일

작업 스케줄러에 걸지 않고 **필요할 때 직접 켜서 본다**([09 T-13](docs/09-tech-stack.md#8-결정-기록)).
`status`가 지금 데이터가 최신인지와 무엇을 먼저 돌려야 하는지 알려 준다.

```bash
.venv\Scripts\python -m theme_radar status
```

```
시장  종목   가격         거래일       주               월             재계산
KR    2532   2026-09-15   2026-09-15   2026-W38 잠정    2026-09 잠정   없음
US    503    2026-09-15   2026-09-15   2026-W38 잠정    2026-09 잠정   없음

할 일
  없음. .venv\Scripts\python -m theme_radar serve 로 본다
```

"할 일"이 비어 있지 않으면 거기 적힌 명령을 그대로 실행한다. 보통은 `daily`다.
수집부터 집계까지 이어서 돌고, 앞 단계가 실패하면 뒤 단계를 실행하지 않는다.

```bash
.venv\Scripts\python -m theme_radar daily --market KR
.venv\Scripts\python -m theme_radar daily --market US
```

걸리는 시간은 빠진 거래일이 며칠이냐에 달렸다. 실측으로 KR 17분 · US 4분이었고(2,533종목 / 503종목),
대부분은 종목별 시세 요청을 출처 부하 제한에 맞춰 흘려보내는 시간이다. 집계는 그 뒤 20초 안에 끝난다(시장마다 스킴 2개를 돈다).

그다음 화면을 띄운다. http://127.0.0.1:8000 에서 화면을, `/docs`에서 API 문서를 본다.
서버는 `127.0.0.1`에만 바인딩하므로 같은 PC에서만 열린다.

```bash
.venv\Scripts\python -m theme_radar serve
```

- **백업은 수동이다.** `data/theme_radar.sqlite3`를 복사해 둔다. 상장폐지 종목의 과거 주가처럼
  출처에서 사라지는 데이터가 있어 이 파일은 다시 받을 수 없다. 복사는 `serve`나 배치가 떠 있지 않을 때 한다.

- 의존성을 바꿀 때는 `requirements.txt` / `requirements-dev.txt`를 고치고 설치한 뒤 `pip freeze --exclude pip` 결과로 `requirements.lock`을 갱신한다.
- 이 PC에만 해당하는 설정은 `config.local.toml`(git 제외)에 적는다. 미국 수집에는 SEC 연락처가 필요하다: `[sec] user_agent = "theme-radar 연락처이메일"`.

## 표기 규약

- 기간 식별자: 주 `2026-W03`(ISO-8601 week-year), 월 `2026-01`
- 수익률은 소수(0.0123 = +1.23%)로 저장하고 표시 시점에 백분율로 변환
- 시점 표기에서 `t`는 대상 기간, `t-1`은 직전 기간을 의미
