# 10. 처음 설치와 실행

> **작성일: 2026-09-21.** GitHub에서 **소스만 받은 상태**(데이터 없음)에서 화면이 뜰 때까지의 순서다.
> 명령 하나하나의 뜻은 [07 §5.2](07-price-ingestion.md#52-명령)와 [09 §4.6](09-tech-stack.md#46-배치-실행과-운영)에 있고,
> 이 문서는 **클론에 없는 것**, **순서가 의미 있는 이유**, **처음에 걸리는 곳**만 다룬다.
>
> 한 번 적재한 뒤 볼 때마다 하는 일은 [README](../README.md#볼-때마다-하는-일)를 본다.

## 1. 클론에 들어 있는 것과 없는 것

| | 항목 | 비고 |
| --- | --- | --- |
| 포함 | `data/*.csv` (분류·매핑 5개) | WI26 · WI26_SUB · GICS · GICS_IND 스킴과 그룹 색. **네트워크 없이 적재된다** ([data/README](../data/README.md)) |
| 포함 | `data/raw/` (HTML·JSON 132건) | 위 CSV의 수집 원본. 재파싱·감사용이고 실행에 필요하지는 않다 |
| 포함 | `web/vendor/echarts-6.1.0.min.js` | 화면은 CDN도 빌드 도구도 쓰지 않는다 ([09 §4.4](09-tech-stack.md#44-화면-빌드-없는-html--javascript-모듈)) |
| 포함 | `references/*.pdf` | `scripts/parse_gics.py`의 입력 |
| **없음** | `.venv` | [§2](#2-가상환경) |
| **없음** | `config.local.toml` | [§3](#3-configlocaltoml--직접-만든다). **이게 없으면 US 명령은 한 줄도 돌지 않는다** |
| **없음** | `data/theme_radar.sqlite3` | [§4](#4-db). 받을 수 없는 데이터가 들어가는 유일한 원본이다 |
| **없음** | `data/raw/prices/`, `data/cache/`, `logs/` | 실행하면서 저절로 생긴다. 미리 만들 것 없다 |

전제 환경은 Windows 10 + PowerShell, pyenv Python 3.12.7이다 ([09 §1](09-tech-stack.md#1-전제)).
설정을 `tomllib`으로 읽으므로 **3.11 미만에서는 실행되지 않는다.**

수집은 네트워크를 쓴다. 호출 대상은 네이버 · KRX(FinanceDataReader) · KIND · Yahoo(yfinance) · SEC이고,
분류 CSV를 다시 받을 때만 WiseIndex · 위키백과 · SSGA를 더 쓴다 ([07 §2.1](07-price-ingestion.md#21-채택-출처)).

## 2. 가상환경

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pytest
```

- 설치는 `requirements.txt`가 아니라 **`requirements.lock`**으로 한다. `pip freeze` 결과 50개라 간접 의존성 버전까지 같아진다.
  개발 의존성(pytest·httpx)도 이 파일에 들어 있다 ([09 §5](09-tech-stack.md#5-의존성)).
- 테스트는 **네트워크를 쓰지 않는다.** 여기서 실패하면 수집으로 넘어가지 않는다.
- 전역 Python에는 아무것도 설치하지 않는다. `scripts/*.py`는 표준 라이브러리만 쓰므로 전역 Python으로도 실행된다.

## 3. config.local.toml — 직접 만든다

git에서 제외되는 파일이라 클론에는 없다. 프로젝트 루트에 만든다.

```toml
[sec]
user_agent = "theme-radar 본인이메일@example.com"
```

- SEC는 자동화 요청에 연락처가 든 User-Agent를 요구한다 ([07 §8.3](07-price-ingestion.md#83-주식수)).
  값이 비어 있으면 **`--market US` 명령은 무엇이든 시작하자마자 멈춘다**(`universe`·`backfill`·`daily`·`shares` 전부).
  미국 주식수를 SEC에서 받기 때문이고, 확인하는 자리는 [context.py](../theme_radar/prices/context.py#L66)다.
- 한국 시장만 볼 생각이면 이 파일 없이도 되지만, 그때는 아래 US 단계를 전부 건너뛴다.
- `config.toml`의 같은 키를 덮어쓰는 파일이므로, 수집 시작일(`collect.start_date`, 기본 `2025-01-01`)처럼
  이 PC에서만 바꾸고 싶은 값도 여기에 적는다 ([09 §4.7](09-tech-stack.md#47-설정)).

## 4. DB

```bash
.venv\Scripts\python -m theme_radar init-db
```

`data/theme_radar.sqlite3`를 만들고 마이그레이션을 적용한다. 디렉터리는 없으면 만든다.
다른 명령들은 스키마가 최신이 아니면 실행을 거부하고 이 명령을 먼저 실행하라고 알린다.

## 5. 첫 적재

```bash
.venv\Scripts\python -m theme_radar prices universe --market KR
.venv\Scripts\python -m theme_radar prices universe --market US
.venv\Scripts\python -m theme_radar load-mapping --scheme WI26
.venv\Scripts\python -m theme_radar load-mapping --scheme WI26_SUB
.venv\Scripts\python -m theme_radar load-mapping --scheme GICS
.venv\Scripts\python -m theme_radar load-mapping --scheme GICS_IND
.venv\Scripts\python -m theme_radar prices backfill --market KR
.venv\Scripts\python -m theme_radar prices backfill --market US
.venv\Scripts\python -m theme_radar aggregate --market KR --full
.venv\Scripts\python -m theme_radar aggregate --market US --full
```

**순서를 지켜야 하는 이유는 두 가지다.**

| 순서 | 이유 | 어기면 |
| --- | --- | --- |
| `universe` → `load-mapping` | 매핑 CSV의 티커를 종목 마스터에서 찾아 `security_id`로 바꾼다 | 하나라도 못 찾으면 **아무것도 적재하지 않고** 종료 코드 1 ([§7](#7-처음에-걸리는-곳) 2번) |
| `load-mapping` → `backfill` | 백필이 남기는 특이사항에 섹터 비중이 들어간다 | 경고를 남기고 진행하되 특이사항의 섹터 값이 빈다 |

`aggregate`는 시장마다 스킴 두 개를 함께 돈다. 첫 적재에서는 `--full`(전 기간 재계산)을 준다.

**실측 시간** (2026-09-16 초기 적재, KR 2,533종목 · US 503종목, 수집 시작일 2025-01-01)

| 단계 | 시간 | 비고 |
| --- | --- | --- |
| `prices backfill --market KR` | 약 67분 | 새 행 1,042,740. 대부분은 종목별 요청을 출처 부하 제한에 맞춰 흘려보내는 시간이다 ([07 §10.3](07-price-ingestion.md#103-요청-규칙)) |
| `prices backfill --market US` | 약 5분 | yfinance가 여러 종목을 묶어 받는다 |
| `aggregate --full` | KR 13초 · US 8초 | |

`universe`와 `load-mapping`은 백필에 비하면 무시할 만하다. 디스크를 아끼려면 수집 명령에 `--no-raw`를 줘
`data/raw/prices/` 원문 저장을 생략할 수 있다(문제 추적이 어려워지므로 첫 적재에서는 권하지 않는다).

## 6. 확인

```bash
.venv\Scripts\python -m theme_radar status
```

`status`는 시장별로 종목 수, 가격이 어디까지 들어왔는지, 최근 거래일, 주·월 집계 상태, 재계산 대기를 보여 주고
**"할 일"**에 다음에 실행할 명령을 적는다. 비어 있으면 적재가 끝난 것이다.

```bash
.venv\Scripts\python -m theme_radar serve
```

http://127.0.0.1:8000 에서 화면을, `/docs`에서 API 문서를 본다. `127.0.0.1`에만 바인딩하므로 같은 PC에서만 열린다.

## 7. 처음에 걸리는 곳

**1. `config.local.toml에 [sec] user_agent(연락처 포함)를 적어야 한다`**
→ [§3](#3-configlocaltoml--직접-만든다). US 명령 전부에 해당한다.

**2. `적재하지 않았다. 종목 마스터에 없는 티커 N개: [...]`**

`load-mapping`이 아무것도 적재하지 않고 종료 코드 1로 끝난다. **저장소의 분류 CSV는 스냅샷이기 때문이다.**
`wi26_constituents.csv`는 기준일 2026-09-11, `gics_sp500_constituents.csv`는 위키백과 2026-09-04 리비전인데 `universe`는 오늘 목록을 받아 온다.
그 사이 신규 상장·티커 변경·구성종목 교체가 있으면 어긋난다. CSV를 다시 받고 매핑을 다시 적재한다(전역 Python으로 된다).

```bash
python scripts/fetch_wi26.py        # 최근 영업일로 wi26_classification/constituents.csv 갱신
python scripts/fetch_wi26_sub.py    # 위 결과와 대조해 wi26_wics_map/sub_constituents.csv 갱신
python scripts/fetch_gics_sp500.py  # gics_sp500_constituents.csv 갱신
```

각 스크립트는 자체 검증을 통과하지 못하면 CSV를 쓰지 않고 실패한다([data/README §4](../data/README.md#4-검증-결과-및-주의사항), [§6.3](../data/README.md#63-검증-결과), [§7.2](../data/README.md#72-짐작이-아니다--대분류-전수-대조)).
`gics_classification.csv`(MSCI 분류표)는 GICS 구조 개편이 없는 한 다시 만들 필요가 없다.

**3. 백필이 중간에 끊겼다**

이어받기는 없다. `backfill`을 다시 실행하면 전 종목을 처음부터 다시 받는다. 쓰기는 종목마다 트랜잭션이라
중복 적재나 손상은 생기지 않지만, KR은 다시 한 시간이다. 이미 받은 종목을 건너뛰고 싶으면
`prices daily --market KR`이 종목별 마지막 저장일부터 받으므로 한 행도 없는 종목만 수집 시작일부터 채운다.
다만 `daily`는 증분 모드라 전 구간 수정계수 재판정을 하지 않으니, 그렇게 메운 뒤에는
`prices reconcile --market KR`로 한 번 대사한다([07 §9.4](07-price-ingestion.md#94-정기-대사)).

**4. 경고가 잔뜩 찍히는데 종료 코드는 0이다**

정상이다. 초기 적재에서는 기업행위 기록 없는 급변(C-2·C-3), 빠진 거래일(C-4), 믿기 어려운 주식수 관측치(U-5) 같은
경고가 수십 건 나온다. 보정하지 않고 특이사항으로 남겨 따로 본다 ([07 §11.2](07-price-ingestion.md#112-특이사항)).

| 종료 코드 | 뜻 | 다음 |
| --- | --- | --- |
| 0 | 정상. 경고는 있을 수 있다 | 다음 단계로 |
| 1 | 예외로 끝났다 (`batch_run.status = 'FAILED'`) | 메시지를 보고 고친 뒤 같은 명령을 다시 |
| 2 | 차단 검증 실패 (`BLOCKED`) | `status`와 `validation_result` 테이블에서 어느 규칙이 걸렸는지 본다 |

KR 수집은 요청 실패 종목이 전체의 5%를 넘으면 차단한다. 네트워크가 불안정했을 뿐이면 다시 실행하면 된다.

**5. 화면은 떴는데 값이 비어 있다**

`aggregate --full`을 돌리지 않았거나, 그 시장의 매핑을 적재하지 않은 것이다. `status`가 어느 쪽인지 알려 준다.

## 8. 적재한 뒤

- 볼 때마다 `status` → (시키면) `daily --market KR|US` → `serve` 순서로 한다 ([README](../README.md#볼-때마다-하는-일)).
- **백업은 수동이다.** `serve`와 배치가 떠 있지 않을 때 `data/theme_radar.sqlite3`를 복사해 둔다.
  상장폐지 종목의 과거 주가처럼 출처에서 사라지는 데이터가 있어 이 파일은 다시 받을 수 없다 ([09 §4.6](09-tech-stack.md#46-배치-실행과-운영)).
