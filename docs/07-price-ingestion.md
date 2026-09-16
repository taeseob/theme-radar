# 07. 주가 수집 모듈 개발 방법

> **검증 기준일: 2026-09-15.** 이 문서의 출처 동작과 수치는 이날 각 엔드포인트와 라이브러리를 직접 호출해 얻은 결과다. "미검증"이라고 적은 항목은 호출해 보지 않은 내용이다. 비공식 엔드포인트는 예고 없이 바뀔 수 있으므로 구현 착수 시 3장의 확인을 다시 수행한다.
>
> **라이브러리 채택 결정 (2026-09-15):** 미국은 yfinance를 쓰고, 한국은 직접 호출로 얻을 수 없거나 직접 호출 결과에 결함이 있는 데이터에만 FinanceDataReader를 쓴다. 검토 근거는 [08-library-review.md](08-library-review.md)에 있다.
>
> **운영 원칙 (2026-09-15):** 섹터의 흐름을 보는 데 집중한다. 단순한 규칙으로 일관되게 계산하고, 규칙 밖의 사건은 자동으로 보정하지 않고 특이사항으로 기록해 따로 본다 ([§11.2](#112-특이사항), [§13](#13-결정-기록)).

## 1. 목적과 범위

[01 §3](01-requirements.md#3-조회-대상-universe)의 두 유니버스에 대해 [02 §4.2](02-domain-and-data-model.md#42-price_daily)의 `price_daily`를 채우는 수집 모듈을 정의한다. 섹터·테마별 시가총액과 수익률 계산([03](03-metrics-spec.md))의 원천 데이터다.

| # | 요구사항 |
| --- | --- |
| R-1 | 대상은 `KR_COMMON`(KOSPI·KOSDAQ 보통주)과 `US_SP500`(S&P 500 구성종목)이다 |
| R-2 | 2025-01-01 이후 거래일의 일간 종가를 수집한다 |
| R-3 | 별도 인증(로그인·API 키)이 필요 없는 출처를 우선한다 |
| R-4 | 액면분할·병합이 발생하면 저장된 과거 주가를 갱신할 수 있어야 한다 |
| R-5 | 이 모듈은 프로젝트 가상환경에서 버전을 고정한 yfinance와 FinanceDataReader를 쓴다. 그 밖의 수집 스크립트는 표준 라이브러리만 쓴다 ([§5.3](#53-실행-환경)) |
| R-6 | 가격 외 이유로 섹터 시총을 바꾸거나 계산에 오차를 남기는 사건은 특이사항으로 기록해 따로 볼 수 있어야 한다 |

### 1.1 종가만으로는 부족하다

시가총액은 **무수정 종가 × 상장주식수**이고([02 §4.2](02-domain-and-data-model.md#42-price_daily)), 수익률은 **수정 종가**로 계산한다([03 §2](03-metrics-spec.md#2-종목-수익률)). 그래서 이 모듈의 산출물은 종가 한 컬럼이 아니라 아래 여섯 컬럼이다.

| 컬럼 | 의미 | 기업행위 발생 시 과거 행 |
| --- | --- | --- |
| `close_raw` | 그날 실제로 형성된 종가 | 바꾸지 않는다 |
| `adj_factor` | 누적 수정계수 | **갱신한다** |
| `close_adj` | `close_raw × adj_factor` | **갱신한다** |
| `shares_listed` | 그날의 상장주식수 | 바꾸지 않는다 |
| `market_cap` | `close_raw × shares_listed` | 바꾸지 않는다 |
| `trade_status` | `NORMAL` / `SUSPENDED` / `HALTED` / `NO_TRADE` | 바꾸지 않는다 |

R-4의 "과거 주가 갱신"은 이 설계에서 **`adj_factor`와 `close_adj`의 소급 갱신**을 뜻한다. `close_raw`를 덮어쓰면 과거 시가총액이 틀어지고 감사 추적이 끊긴다.

## 2. 결론 요약

### 2.1 채택 출처

| 데이터 | KR | US |
| --- | --- | --- |
| 종목 목록·시장구분 | KIND 상장법인목록 (직접 호출) + FDR KRX 스냅샷의 소속부 | 위키백과 S&P 500 구성종목 (기존 [`fetch_gics_sp500.py`](../scripts/fetch_gics_sp500.py)) |
| 편입·폐지 이력 | **FDR** `StockListing("KRX-DELISTING")` | 위키백과 *Historical components of the S&P 500* 변경표 |
| 무수정 종가 | 네이버 증권 모바일 일별시세 API (직접 호출) | **yfinance** 종가에 이후 분할비율을 곱해 역산 |
| 수정 종가 | 네이버 증권 `siseJson` (직접 호출) | **yfinance** `auto_adjust=False`의 `Close` |
| 분할·병합 감지 | 수정계수(수정 종가 ÷ 무수정 종가)의 변화 | **yfinance** `Stock Splits` 컬럼 |
| 상장주식수, 가동일 이후 | Daum 금융 시세 API (직접 호출). FDR KRX 스냅샷으로 교차검증·대체 | SEC XBRL API. 복수 클래스 7개 라인은 **yfinance** `Ticker.info` |
| 상장주식수, 가동일 이전 | **FDR** KRX 스냅샷 캐시 (2026-03-09 이후). 그 이전은 2026-03-09 주식수로 근사 ([§7.4](#74-상장주식수)) | SEC XBRL API. 복수 클래스는 가동일 값으로 근사 ([§8.3](#83-주식수)) |
| 거래일 캘린더 | 네이버 `siseJson`의 `KOSPI` 지수 | **yfinance** `^GSPC` |

FDR은 FinanceDataReader의 줄임이다. 표의 출처는 모두 계정·API 키 없이 호출된다.

**한국에서 FDR을 쓰는 곳과 쓰지 않는 곳**

| 데이터 | FDR 사용 | 이유 |
| --- | --- | --- |
| 무수정 종가 | 쓰지 않음 | FDR은 수정 종가만 반환한다 |
| 수정 종가 | 쓰지 않음 | FDR도 네이버 차트 API를 호출하므로 `siseJson` 직접 호출과 값이 같다 |
| 과거 상장주식수 (2026-03-09 ~ 가동 전일) | **씀** | 날짜별 KRX 스냅샷을 주는 무인증 출처가 FDR 캐시뿐이다 |
| 상장폐지 목록 | **씀** | KIND 상장폐지 페이지에는 6자리 종목코드와 증권 종류가 없다. FDR은 둘 다 준다 |
| SPAC 판별 | **씀** | KIND 목록에 없는 KOSDAQ 소속부 값을 준다 |
| 가동일 이후 일별 상장주식수 | 교차검증·대체에만 | Daum 직접 호출로 당일 값을 얻을 수 있다. FDR 당일 파일은 확정 시점이 일정하지 않다 ([§7.4](#74-상장주식수)) |

### 2.2 알려진 한계와 처리

한계는 추가 데이터 확보나 복잡한 보정으로 메우지 않는다. 단순한 근사를 쓰고, 영향 받는 구간과 종목은 특이사항으로 남긴다.

| # | 한계 | 처리 | 특이사항 |
| --- | --- | --- | --- |
| G-1 | KR 상장주식수 이력이 2025-01-02 ~ 2026-03-06 구간에 없다 | 2026-03-09 주식수를 수정계수로 환산해 근사한다. 이 구간의 증자·소각은 반영되지 않는다 | `DATA_GAP` 1건 |
| G-2 | Yahoo는 상장폐지된 미국 종목의 이력을 지운다. 2025년 이후 S&P 500 편출 32종목 중 10종목이 해당한다 | 해당 종목을 계산에서 제외한다 | 종목별 `DATA_GAP` |
| G-3 | SEC 공시 주식수를 쓸 수 없는 라인(2026-09-16 현재 구성 기준 83라인, §8.3)의 가동 전 주식수 이력이 없다 | 가동일 yfinance 현재 주식수를 수정계수로 환산해 근사한다 | `DATA_GAP` 1건 |
| G-4 | Yahoo 분할 이벤트에 분사 조정이 섞여 있다 | 정수비 이벤트만 주식수에 반영한다 | 정수비가 아닌 이벤트마다 `CORP_ACTION` |

## 3. 출처 검증 결과

### 3.1 검토한 출처

| 출처 | 엔드포인트 | 인증 | 확인 결과 | 판정 |
| --- | --- | --- | --- | --- |
| KRX 정보데이터시스템 | `data.krx.co.kr/comm/bldAttendant/getJsonData.cmd` (전종목 시세 `MDCSTAT01501`) | 로그인 | 로그인 세션 없이 호출하면 HTTP 400, 본문 `LOGOUT` | 무인증 불가 |
| 공공데이터포털 금융위원회_주식시세정보 | `apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo` | API 키 | 키 없이 호출하면 HTTP 401 `SERVICE_KEY_IS_NULL` | 사용 안 함 |
| 네이버 모바일 일별시세 | `m.stock.naver.com/api/stock/{code}/price` | 없음 | 무수정 종가. 상장폐지 종목은 빈 배열 | **채택** (KR 원종가) |
| 네이버 siseJson | `api.finance.naver.com/siseJson.naver` | 없음 | 수정 종가. 상장폐지 종목도 폐지 직전까지 보존 | **채택** (KR 수정주가) |
| Daum 금융 시세 | `finance.daum.net/api/quotes/A{code}` | 없음 | 보통주 상장주식수 `listedShareCount` 현재값 | **채택** (KR 일별 주식수) |
| KIND 상장법인목록 | `kind.krx.co.kr/corpgeneral/corpList.do?method=download` | 없음 | 회사명·시장구분·종목코드·업종·상장일 | **채택** |
| KIND 상장폐지 현황 | `kind.krx.co.kr/investwarn/delcompany.do` (POST) | 없음 | 회사명·폐지일·사유만 있고 6자리 종목코드가 없다 | FDR로 대체 |
| FinanceDataReader 0.9.202 | 한국 종가는 `fchart.stock.naver.com`, 목록은 GitHub `FinanceData/fdr_krx_data_cache` | 없음 | [08 §3](08-library-review.md#3-financedatareader-검토) | **채택** (한국 일부) |
| yfinance 1.7.0 | Yahoo 차트 API, quoteSummary API | 없음 (쿠키·crumb 토큰은 라이브러리가 처리) | [08 §4](08-library-review.md#4-yfinance-검토) | **채택** (미국) |
| Yahoo 재무 시계열 | `query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}` | 없음 | 분기 주식수. 복수 클래스는 회사 합계를 클래스마다 중복 반환 | 사용 안 함 |
| SEC XBRL API | `data.sec.gov/api/xbrl/companyconcept/CIK{10자리}/dei/EntityCommonStockSharesOutstanding.json` | 없음 (연락처 User-Agent 필수) | 분할을 소급하지 않은 공시 원값. Alphabet은 404 | **채택** (US 주식수) |
| Nasdaq API | `api.nasdaq.com/api/quote/{symbol}/historical` | 없음 | 최근 구간은 응답. NFLX 2025-11 구간과 폐지 종목은 0건 | 교차검증용 |
| Stooq | `stooq.com/q/d/l/` | 없음 | 브라우저 JavaScript 검증 페이지를 반환 | 사용 불가 |
| 위키백과 | `Historical_components_of_the_S%26P_500`의 `table#changes` | 없음 | 2025-01-01 이후 36행 (편입 32 · 편출 32) | **채택** (US 이력) |

KRX 정보데이터시스템은 pykrx 등 오픈소스가 인증 없이 쓰던 경로였으나 현재는 로그인이 필요하다. FinanceDataReader도 기본 경로를 KRX 직접 호출에서 GitHub 캐시로 바꿨고, KRX 지수 직접 호출 코드는 "authenication required" 주석과 함께 비활성화돼 있다. 비공식 경로가 실제로 닫힌 사례이므로 [§4](#4-설계-원칙)의 어댑터 분리 원칙을 둔다.

### 3.2 한국: 네이버를 채택하고 Yahoo를 쓰지 않는 이유

WI26 구성종목 2,373개의 2025-01-02 ~ 2026-09-14 일봉을 두 출처에서 받아 비교했다. 6스레드로 211초가 걸렸고 요청 오류는 없었다.

| 항목 | 결과 |
| --- | --- |
| KOSPI 769종목의 Yahoo 종가 보유율 (네이버 거래일 대비) | 99.40% |
| 같은 종목의 네이버 대비 종가 일치율 | 93.89% |
| 불일치가 몰린 날 (기업행위 이벤트가 없는 1,944종목 중 불일치 비율) | 2025-09-18 91.7% · 2025-10-02 63.8% · 2026-04-01 37.0% · 2026-03-27 11.4% |
| 2025-09-18, 2025-10-02, 2026-04-01을 뺀 불일치 | 비교 514,149건 중 223건 |
| WiseIndex 역산가(유동시총 ÷ 적용주식수)와 대조, 2025-09-18 반도체 10종목 | 네이버 10/10 일치 · Yahoo 1/10 일치 |
| 같은 대조, 2025-09-18 화학 22종목 | 네이버 21/22 일치 (1건은 수정주가 반영분) · Yahoo 3/22 일치 |
| KOSDAQ 종목을 `.KS` 접미사로 조회 (표본 40종목) | HTTP 200이지만 거래일 보유율 20.9%. `.KQ`로는 보유율 99.5%, 일치율 91.8% |
| Yahoo 분할 이벤트가 없는 2,135종목 중 네이버 수정주가에 조정 흔적(호가단위 위반)이 있는 종목 | 146종목 |

- 삼성전자 2025-10-02 종가는 네이버와 WiseIndex 모두 89,000원이고 Yahoo는 89,750원이다. 89,750원은 해당 가격대 호가단위(100원)에 맞지 않는 값이다.
- Yahoo는 한국 종목의 기업행위 이벤트도 일부 주지 않는다. 146종목의 조정 원인은 종목별로 확인하지 않았다.
- 같은 이유로 한국 종목에는 yfinance도 쓰지 않는다. yfinance는 같은 Yahoo 차트 API를 호출한다.

### 3.3 기업행위 전후 각 출처의 값

| 종목 | 이벤트 (권리락일) | 기준일 | 네이버 원종가 | 네이버 siseJson | Yahoo close | siseJson ÷ 원종가 |
| --- | --- | --- | --- | --- | --- | --- |
| 001080 | 10:1 분할 (2026-03-09) | 2026-03-06 | 54,400 | 5,440 | 5,440 | 0.1 |
| 001080 | 〃 | 2026-03-09 | 5,010 | 5,010 | 5,010 | 1.0 |
| 011300 | 1:10 병합 (2026-04-30) | 2026-04-27 | 354 | 3,540 | 3,540 | 10.0 |
| 000670 | 10:1 분할 (2025-04-25), 이후 무상증자 (2025-12-29) | 2025-04-24 | 367,500 | 35,752 | 35,679.61 | 0.097284 |
| 068270 | 주식배당 (2026-06-04) | 2026-06-02 | 187,900 | 179,100 | 178,952.38 | 0.953167 |
| 207940 | 인적분할 (2025-10-30) | 2025-10-27 | 1,208,000 | 1,777,867 | 1,857,343.38 | 1.471744 |

- 네이버 모바일 일별시세는 **무수정** 종가, `siseJson`은 이후 모든 기업행위가 소급 반영된 **수정** 종가다. FDR의 한국 종가도 `siseJson`과 같은 수정 종가였다.
- 분할·병합은 네이버와 Yahoo의 수정 종가가 같다.
- 무상증자·주식배당·인적분할은 두 출처의 수정계수가 다르다. 삼성바이오로직스(207940)는 네이버 1.4717, Yahoo 1.5375로 4.5% 차이가 난다. Yahoo 값은 주식수 비율(0.650391)의 역수다. 따라서 한 시계열에 두 출처의 계수를 섞지 않는다.

미국은 넷플릭스(NFLX) 10:1 분할(2025-11-17)로 확인했다. yfinance의 2025-11-14 `Close`는 111.217, `Volume`은 47,607,000으로 분할이 소급 반영돼 있다. 원종가는 1,112.17이다. O'Reilly(ORLY) 15:1 분할(2025-06-10)과 ServiceNow(NOW) 5:1 분할(2025-12-18)도 같은 방식으로 반영돼 있었다.

### 3.4 미국 검증 결과

| 항목 | 결과 |
| --- | --- |
| Yahoo 종가 vs Nasdaq 종가 (S&P 500 무작위 39종목, 2026-08-24 ~ 09-14) | 546건 전부 1센트 이내 일치 |
| yfinance 일괄 다운로드 (503종목, 2025-01-01 이후) | 36.4초. 빈 종목 0. 상장 전 구간을 뺀 결측 1건 (FISV) |
| Yahoo 심볼 표기 | `BRK.B`는 404, `BRK-B`는 200 |
| 2025-01-01 이후 편출된 32종목 중 Yahoo 이력 없음 | 10종목. yfinance는 예외 없이 0행을 반환 |
| SEC 공시 주식수의 분할 반영 방식 | 원값. NFLX 423,732,334주 (2025-09-30) → 4,222,162,150주 (2025-12-31) |
| SEC `companyconcept`, 복수 클래스 회사 | Alphabet (CIK 0001652044) 404 |
| yfinance 분할 이벤트 (2025-01-01 이후, 23종목) | 25건. 이 중 11건은 정수비가 아니다 ([§8.3](#83-주식수)) |

- yfinance `download`와 `Ticker.history`의 `auto_adjust` 기본값은 True다. 이때 `Close`에 배당 조정이 들어간다. 코카콜라 2025-01-02 기본값은 59.2749, 실제 종가는 61.84였다. 이 시스템의 수익률은 배당을 반영하지 않으므로([03 §2](03-metrics-spec.md#2-종목-수익률)) 반드시 `auto_adjust=False`로 받고 `Close`를 쓴다.

### 3.5 구현 착수 시 재확인 (2026-09-16)

구현을 시작하며 각 출처를 다시 호출해 확인했다. 07 초안과 달라졌거나 새로 확인한 내용만 적는다.

| 출처 | 확인 결과 | 반영 |
| --- | --- | --- |
| 네이버 모바일 일별시세 | `pageSize`는 60이 최대다. 100 이상은 HTTP 400 | 종목당 약 7페이지 그대로 |
| Daum 금융 시세 | `Referer` 헤더가 없으면 HTTP 403 | 헤더 필수 |
| 네이버 siseJson | 거래정지 중인 날도 행이 있다(시가·고가·저가 0) | 원종가가 비어 있으면 수정 종가가 같은 전날 원종가를 잇는다 (§7.2) |
| KIND 상장법인목록 | 이름에 '리츠'가 든 27종목 중 메리츠금융지주, 블리츠웨이엔터테인먼트, 메리츠 스팩 2개는 리츠가 아니다 | 리츠 판정에 업종 조건 추가 (§7.1) |
| FDR 상장폐지 목록 | 372행, 보통주 주권 154행. 현재 상장 목록과 겹치는 코드 0 | 코넥스를 뺀 137종목을 폐지 종목으로 적재 |
| 위키백과 변경표 | `Refs` 컬럼이 추가됐다. EchoStar는 `SATS`로 편입(2026-03-23)된 뒤 `ECHO`로 티커를 바꿨다 | 티커 변경 추정 (§8.1) |
| SEC 티커 파일 | 2025년 이후 편출 32종목 중 12종목(인수된 회사)이 없다. 위키백과 목록 2024-12-26판에 SOLS를 뺀 31종목의 CIK가 있다 | CIK 출처 3곳 (§8.1) |
| SEC 공시 주식수 | Coca-Cola는 빈 목록, Berkshire는 마지막 값이 2011년 A주 수(941,481) | yfinance 대체 조건 추가 (§8.3) |
| yfinance | 여러 종목을 병렬로 받을 때 캐시 잠금(`database is locked`)으로 일부 종목이 빈다 | 빈 종목만 순차로 한 번 더 받는다 |

## 4. 설계 원칙

1. **원값은 불변, 조정은 계수로.** `close_raw`와 `shares_listed`는 수집한 값 그대로 두고, 기업행위는 `adj_factor`로만 반영한다.
2. **같은 기준끼리 곱한다.** 시가총액은 같은 날의 무수정 종가와 무수정 주식수를 곱한다. 수정 종가에 현재 주식수를 곱하는 근사는 분할에는 맞지만 유상증자·전환사채 전환·자사주 소각을 놓친다.
3. **시장별 수정주가 출처를 하나로 고정한다.** [§3.3](#33-기업행위-전후-각-출처의-값)처럼 출처마다 계수가 다르다. 출처를 바꾸면 전 구간을 다시 수정한다.
4. **수집 원문을 보존한다.** 직접 호출은 응답 원문을, 라이브러리 호출은 받은 DataFrame을 가공 전에 저장한다. 파서나 규칙을 고쳐도 재요청 없이 다시 적재할 수 있어야 한다.
5. **멱등 적재.** 같은 날을 여러 번 적재해도 결과가 같아야 한다. 증분 수집은 "오늘"이 아니라 "마지막 저장일 − 10거래일"부터 다시 받아 덮어쓴다. 실행을 며칠 건너뛰어도 다음 실행에서 자동으로 메워진다.
6. **출처 어댑터 분리.** 출처마다 모듈 하나를 두어, 엔드포인트나 라이브러리가 막히면 그 모듈만 교체한다. 라이브러리 객체(DataFrame 등)는 어댑터 밖으로 내보내지 않고 공통 레코드로 바꿔 넘긴다.
7. **라이브러리는 필요한 곳에만.** 한국은 직접 호출로 얻을 수 없거나 직접 호출 결과에 결함이 있는 데이터에만 FDR을 쓴다. 라이브러리 버전은 고정하고, 올릴 때는 [§9.5](#95-회귀-테스트-케이스) 회귀 테스트를 통과해야 한다.
8. **보정보다 기록.** 규칙 하나로 처리되지 않는 사건은 예외 로직을 늘려 보정하지 않는다. 특이사항으로 기록하고 따로 본다.

## 5. 모듈 구성

### 5.1 파일 구성

이 모듈은 애플리케이션 패키지 `theme_radar/` 아래에 둔다. DB 스키마(마이그레이션)·연결 설정·설정 파일·CLI 진입점은 패키지 공통 모듈을 쓴다 ([09 §6](09-tech-stack.md#6-저장소-구조)).

```
theme_radar/prices/
├── cli.py               # prices 하위 명령 (python -m theme_radar prices ...)
├── context.py           # 실행 설정·요청기·현지 시각(장 마감 판정)
├── net.py               # 직접 호출용 GET, 재시도, 호스트별 속도 제한, 원문 저장
├── sources/
│   ├── html_table.py    # HTML 표 파서 (KIND, 위키백과)
│   ├── kind.py          # KR 종목 목록 (직접 호출)
│   ├── naver.py         # KR 원종가(모바일 일별시세), 수정 종가·거래일(siseJson) (직접 호출)
│   ├── daum.py          # KR 일별 상장주식수 (직접 호출)
│   ├── fdr_krx.py       # KR KRX 스냅샷 캐시, 상장폐지 목록 (FinanceDataReader)
│   ├── wiki.py          # US 현재 구성, 과거 리비전 구성, 변경표 (직접 호출)
│   ├── yf.py            # US 종가, 분할 이벤트, 거래일, 복수 클래스 주식수 (yfinance)
│   └── sec.py           # US 공시 주식수, 티커 목록, 유동 시가총액 (직접 호출)
├── adjust.py            # 수정계수·기업행위·상장주식수 계산 (입출력 없는 순수 함수)
├── universe.py          # security, universe_membership 규칙과 반영
├── store.py             # 가격·관측치·기업행위·소급 갱신 기록의 DB 반영, 주식수 재계산
├── collect_kr.py        # KR 흐름: 유니버스 → 거래일 → 가격 → 주식수 (§7.5)
├── collect_us.py        # US 흐름: 유니버스 → 거래일 → 가격 → 주식수 (§8)
├── validate.py          # 수집 단계 가격 검증 C-2 ~ C-4, C-6 (§11.1)
└── events.py            # 특이사항 생성 (§11.2)
```

- 소급 갱신(9장)은 별도 모듈이 아니다. 저장된 날짜의 수정계수가 바뀐 종목을 찾으면 같은 수집 경로로 전 구간을 다시 받는다. `backfill`·`reconcile`·`restate`는 모두 이 경로를 전 구간 모드로 실행한다.
- 거래일 캘린더는 각 시장 흐름(`collect_kr.py`, `collect_us.py`)에서 만든다.
- 분류 그룹·매핑 적재는 `theme_radar/master/groups.py`에 있다 ([02 §3.5](02-domain-and-data-model.md#35-security_group_map)).

`http.py`, `calendar.py`, `yfinance.py`처럼 표준 라이브러리나 설치 패키지와 같은 이름은 import 충돌을 일으키므로 쓰지 않는다.

### 5.2 명령

처음 적재는 아래 순서로 한다. 매핑은 종목 마스터가 있어야 적재할 수 있고, 특이사항의 섹터 비중은 매핑이 있어야 계산된다.

```bash
.venv\Scripts\python -m theme_radar init-db                                    # DB 마이그레이션 적용
.venv\Scripts\python -m theme_radar prices universe  --market KR               # KIND 목록 + FDR 소속부·폐지 목록
.venv\Scripts\python -m theme_radar prices universe  --market US               # 현재 구성 + 변경표로 편입 이력 생성
.venv\Scripts\python -m theme_radar load-mapping --scheme WI26                 # 섹터 그룹 + 현재 분류 매핑
.venv\Scripts\python -m theme_radar load-mapping --scheme GICS
.venv\Scripts\python -m theme_radar prices backfill  --market KR               # 수집 시작일부터 전 구간
.venv\Scripts\python -m theme_radar prices backfill  --market US
```

운영 명령:

```bash
.venv\Scripts\python -m theme_radar prices daily     --market KR               # 증분 + 기업행위 감지 + 검증 + 특이사항
.venv\Scripts\python -m theme_radar prices daily     --market US
.venv\Scripts\python -m theme_radar prices reconcile --market KR               # 주간 전 구간 대사 (§9.4)
.venv\Scripts\python -m theme_radar prices restate   --market KR --only 001080 # 지정 종목 전 구간 재수정
.venv\Scripts\python -m theme_radar prices backfill  --market US --only NFLX   # 지정 종목만 받아 확인
.venv\Scripts\python -m theme_radar prices shares    --market KR               # 가격은 두고 주식수 관측·재계산·검증·특이사항만 다시
.venv\Scripts\python -m theme_radar prices events    --market KR               # 아무것도 받지 않고 특이사항만 다시 뽑는다 (§11.2)
```

- 수집 시작일은 `config.toml`의 `collect.start_date`다.
- 차단 검증이 실패하면 `batch_run.status = 'BLOCKED'`로 남기고 종료 코드 2로 끝난다. 예외로 끝나면 `FAILED`, 종료 코드 1이다.
- 수집 뒤 집계까지 이어서 실행하는 `daily --market KR|US`는 [09 §4.6](09-tech-stack.md#46-배치-실행과-운영)에 정의한다.

### 5.3 실행 환경

- 프로젝트 루트에 가상환경 `.venv`를 만들고 이 모듈은 그 안에서만 실행한다. pyenv 전역 Python에는 패키지를 설치하지 않는다. 가상환경은 애플리케이션 전체(API 서버, 테스트 포함)가 함께 쓴다 ([09 §5](09-tech-stack.md#5-의존성)).
- 이 모듈의 직접 의존성은 두 개이고 버전을 고정한다. 설치 후 `pip freeze` 결과를 잠금 파일로 저장한다. 2026-09-15 설치 시 함께 설치된 패키지는 32개였다 ([08 §5](08-library-review.md#5-공통-영향)).

```
yfinance==1.7.0
finance-datareader==0.9.202
```

- 가상환경에는 `tzdata`가 함께 설치되므로 `zoneinfo.ZoneInfo("Asia/Seoul")`, `ZoneInfo("America/New_York")`를 쓸 수 있다. pyenv 전역 Python에서는 `tzdata`가 없어 `ZoneInfoNotFoundError`가 난다.
- yfinance는 기본적으로 사용자 캐시 폴더 아래 `py-yfinance`에 시간대·쿠키 캐시를 만든다. 시작 시 `yf.set_tz_cache_location()`으로 `data/cache/yfinance`를 지정해 프로젝트 안에 둔다.

## 6. 저장소 스키마

수집 결과는 애플리케이션 DB인 SQLite 파일 하나(`data/theme_radar.sqlite3`)에 적재한다. 원천·마스터·파생 테이블을 같은 파일에 둔다 ([09 §4.1](09-tech-stack.md#41-db-sqlite)). 테이블 정의의 단일 출처는 [`theme_radar/db/migrations/`](../theme_radar/db/migrations/)의 SQL 파일이고, 테이블 규약은 [02](02-domain-and-data-model.md)에 있다. 이 모듈이 쓰는 테이블은 다음과 같다.

| 테이블 | 이 모듈에서의 용도 | 02 |
| --- | --- | --- |
| `security`, `universe_membership`, `trading_calendar` | 종목 마스터, 유니버스 편입 이력, 거래일 | [§3](02-domain-and-data-model.md#3-마스터-테이블), [§4.1](02-domain-and-data-model.md#41-trading_calendar) |
| `price_daily` | 무수정·수정 종가, 수정계수, 주식수, 시총, 거래 상태, 출처 추적 컬럼(`price_source`, `adj_source`, `fetched_at`, `adj_updated_at`) | [§4.2](02-domain-and-data-model.md#42-price_daily) |
| `shares_observation` | 출처별 상장주식수 관측치. `basis`는 공시 원값(`AS_REPORTED`)과 분할 소급값(`SPLIT_ADJUSTED`)을 구분한다 | [§4.3](02-domain-and-data-model.md#43-수집-보조-테이블) |
| `corporate_action` | 기업행위. `price_factor`는 권리락일 이전 가격에 곱하는 값(10:1 분할 = 0.1), `share_ratio`는 이후 ÷ 이전 주식수(10:1 분할 = 10, 주식수에 반영하지 않는 이벤트는 `NULL`) | [§4.3](02-domain-and-data-model.md#43-수집-보조-테이블) |
| `special_event` | 특이사항 ([§11.2](#112-특이사항)) | [§4.3](02-domain-and-data-model.md#43-수집-보조-테이블) |
| `restatement_log`, `batch_run`, `validation_result`, `recalc_request` | 소급 갱신 기록, 실행 이력, 검증 결과(C-1 ~ C-13), 재계산 요청 | [§8](02-domain-and-data-model.md#8-운영-테이블) |

- `price_daily.shares_listed`는 `shares_observation`에서 as-of 규칙([§7.4](#74-상장주식수), [§8.3](#83-주식수))으로 산출해 채운다. 같은 날 관측치가 여러 출처에 있으면 표의 우선순위를 따른다. 관측치를 따로 두는 이유는 출처와 기준(원값·분할 소급값)을 추적하기 위해서다.

| 시장 | 우선순위 |
| --- | --- |
| KR | `DAUM_QUOTE` → `FDR_KRX_CACHE` |
| US 단일 클래스 | `SEC_DEI` |
| US 복수 클래스 | `YF_INFO` |

- 가동 전 근사 구간(G-1, G-3)은 관측치를 만들지 않고 `price_daily.shares_listed`에 계산값을 바로 넣는다.
- 가격은 `REAL`로 저장한다. KR은 정수 원 단위, US는 역산 후 소수 2자리(센트)로 반올림한다.
- 이미 폐지된 KR 종목에서 원종가를 확정할 수 없는 날([§7.2](#72-무수정-종가))은 `close_raw`, `adj_factor`, `market_cap`을 `NULL`로 두고 `close_adj`만 저장한다.
- 출처 코드(`price_source` 등)는 `NAVER_MPRICE`, `NAVER_SISEJSON`, `YFINANCE`, `DAUM_QUOTE`, `FDR_KRX_CACHE`, `SEC_DEI`, `YF_INFO`, `NAVER_FACTOR_JUMP`, `YFINANCE_SPLIT`, `MANUAL`을 쓴다.

## 7. 한국 수집 절차

### 7.1 종목 마스터와 유니버스

**상장법인목록 (직접 호출)**

```
GET https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13
```

- `Content-Type`은 `application/vnd.ms-excel`이지만 본문은 **EUC-KR HTML 표**다. `html.parser`로 읽는다.
- 컬럼: 회사명, 시장구분(`유가`/`코스닥`/`코넥스`), 종목코드, 업종, 주요제품, 상장일, 결산월, 대표자명, 홈페이지, 지역.
- 2026-09-15 응답은 2,800행, 고유 코드 2,757개였다. **중복 행 43개가 있으므로 코드로 중복을 제거한다.** 고유 코드 기준 유가 832, 코스닥 1,817, 코넥스 108이다.
- 회사 단위 목록이라 우선주가 없다. 숫자 코드는 전부 끝자리가 0이다.
- 영문자가 섞인 코드가 59개 있다(예: `0197V0`). 종목코드는 항상 문자열로 다룬다.
- WI26 구성종목 2,373개는 전부 이 목록에 있고, 목록의 KOSPI·KOSDAQ 코드 2,649개 중 276개는 WI26에 없다.

**소속부 (FDR KRX 스냅샷)**

KIND 목록의 KOSPI·KOSDAQ 코드 2,649개는 모두 FDR KRX 스냅샷([§7.4](#74-상장주식수))에 있다. 스냅샷의 `Dept`(소속부) 값은 KOSDAQ 종목에만 있고, KOSPI 종목은 비어 있다.

| `Dept` 값 (KIND 코드 2,649개 중) | 종목 수 |
| --- | --- |
| 중견기업부 | 508 |
| 우량기업부 | 466 |
| 벤처기업부 | 339 |
| 기술성장기업부 | 255 |
| 관리종목(소속부없음) | 128 |
| SPAC(소속부없음) | 66 |
| 투자주의환기종목(소속부없음) | 40 |
| 외국기업(소속부없음) | 15 |
| 비어 있음 (KOSPI) | 832 |

`KR_COMMON` 판정([01 §3.1](01-requirements.md#31-kr_common))은 다음 순서로 한다.

| 단계 | 기준 | 해당 수 (2026-09-15) |
| --- | --- | --- |
| 시장 | KIND 시장구분이 `유가` 또는 `코스닥` | 2,649 |
| 스팩 제외 | 소속부가 `SPAC(소속부없음)`이거나 회사명에 `스팩` 포함 | 소속부 66 · 회사명 69 |
| 리츠 제외 | 회사명에 `리츠` 포함 + 업종이 `부동산 임대 및 공급업` 또는 `신탁업 및 집합투자업`. 메리츠금융지주·블리츠웨이엔터테인먼트가 걸러진다 | 23 (2026-09-16) |
| 외국기업 제외 | 종목코드가 `9`로 시작. `900`은 외국주권, `950`은 주식예탁증권(DR)이다 | 22 (외국주권 12 · DR 10, 시총 4.34조, WI26 매핑 없음) |
| 인프라·부동산 펀드 제외 | 수기 목록. 2026-09-15 기준 맥쿼리인프라(088980), KB발해인프라(415640), 맵스리얼티(094800) | 3 (시총 6.66조, WI26 매핑 없음) |

코드 `9` 규칙은 2026-09-15 기준 KOSPI·KOSDAQ에서 ISIN이 `KR7`로 시작하지 않는 22종목과 정확히 일치했다. 관리종목·투자주의환기종목은 제외하지 않고 [01 §3.1](01-requirements.md#31-kr_common)대로 상태 플래그로만 구분한다.

**상장폐지 목록 (FDR)**

```python
import FinanceDataReader as fdr

delisted = fdr.StockListing("KRX-DELISTING", start="2025-01-01")
common = delisted[(delisted["SecuGroup"] == "주권") & (delisted["Kind"] == "보통주")]
```

- 2025-01-01 이후 371행이 나왔다. 증권구분(`SecuGroup`)이 `주권`인 행은 156개이고, 그중 종류(`Kind`)가 `보통주`인 행은 153개였다.
- 컬럼: `Symbol`(6자리 종목코드), `Name`, `Market`, `SecuGroup`, `Kind`, `ListingDate`, `DelistingDate`, `Reason`, `Industry`, `ParValue`, `ListingShares`, `ToSymbol` 등.
- 예시: 현대홈쇼핑 `057050`, 폐지일 2026-07-20, 상장주식수 12,000,000주.
- 이 함수는 GitHub 캐시의 최신 파일을 읽는다. 호출에 실패하면 KIND 상장폐지 현황(직접 호출)으로 폐지일만 받고, 종목코드는 KIND 상장법인목록의 전일 스냅샷과 회사명으로 맞춘다.
- 사유가 이전상장인 행은 폐지가 아니라 시장 이동이다.

매일 KIND 목록과 FDR 폐지 목록의 스냅샷을 저장하고 전일과 비교해 신규상장·폐지를 반영한다. `security.listing_date`는 KIND 상장일, `security.delisting_date`는 FDR 폐지일로 채운다.

### 7.2 무수정 종가

```
GET https://m.stock.naver.com/api/stock/{code}/price?pageSize=60&page={n}
```

- 최신일부터 역순인 JSON 배열이다. 필드는 `localTradedAt`(`YYYY-MM-DD`), `closePrice`(`"248,500"`처럼 쉼표가 든 문자열), `openPrice`, `highPrice`, `lowPrice`, `accumulatedTradingVolume`이다.
- 백필은 2025-01-01 이전 날짜가 나올 때까지 `page`를 늘린다. 종목당 약 7페이지다.
- 증분은 "마지막 저장일 − 10거래일"이 나올 때까지 페이지를 넘긴다. 매일 실행하면 1페이지로 끝난다.
- `closePrice`는 **KRX 정규장 종가**다. 알테오젠(196170) 2026-09-14 값은 258,000원이고, 같은 날 넥스트레이드(NXT) 애프터마켓 가격 257,000원과 다르다.
- 거래량은 KRX와 NXT를 합친 값이다(알테오젠 631,147주). `siseJson`의 거래량은 KRX만이다(403,038주).
- **상장폐지 종목은 빈 배열을 반환한다.** 폐지 전에 수집해 두어야 한다. 이미 폐지된 종목은 `siseJson` 값을 쓰되, 호가단위 검사(C-1)를 통과하는 구간만 원종가로 인정한다. 통과하지 못하는 구간은 수정계수를 알 수 없으므로 원종가를 비워 두고 [03 §10](03-metrics-spec.md#10-예외-처리)의 `NO_BASE_PRICE`로 처리한다.
- 2026-03-09 이후 폐지 종목은 FDR KRX 스냅샷의 `Close`로 원종가를 채울 수 있다. 과거 파일의 `Close`는 무수정 종가다 ([§7.4](#74-상장주식수)).
- 거래정지일에 행이 포함되는지는 미검증이다.

### 7.3 수정 종가와 수정계수

```
GET https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1&startTime=20250101&endTime={YYYYMMDD}&timeframe=day
```

- 한 번의 요청으로 전 구간(약 414거래일)을 받는다.
- 각 행은 `[날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율]`이다.
- **응답은 JSON이 아니다.** 헤더 행이 작은따옴표 문자열이고 공백·탭이 섞여 있다. 아래처럼 읽는다.

```python
import ast
import re

def parse_sise_json(body: bytes) -> list[list]:
    """siseJson 응답을 [날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율] 행 목록으로 바꾼다."""
    rows = ast.literal_eval(re.sub(r"\s+", "", body.decode("utf-8")))
    return rows[1:]  # 헤더 제외
```

- 거래가 없던 날은 시가·고가·저가가 0이고 종가에 직전 값이 들어 있다. 현대홈쇼핑 2026-07-16 행이 `[0, 0, 0, 87300]`이다. 이런 날은 `trade_status = NO_TRADE`로 적재한다. 이날 모바일 일별시세에 원종가가 없으면, 수정 종가가 전날과 같을 때 전날 원종가를 잇는다. 과거 구간에서는 거래정지와 무거래를 구분할 수 없지만, [03 §10](03-metrics-spec.md#10-예외-처리)은 둘 다 직전 종가를 캐리포워드하므로 계산 결과는 같다.
- `symbol=KOSPI`, `symbol=KOSDAQ`은 지수를 반환한다. KR 거래일 캘린더를 여기서 만든다.
- FDR `DataReader`로도 같은 수정 종가를 받을 수 있지만 쓰지 않는다. 직접 호출과 값이 같아 라이브러리를 거칠 이유가 없다.

수정계수는 두 시계열의 비율이다.

```
adj_factor(d) = siseJson 종가(d) / close_raw(d)
close_adj(d)  = siseJson 종가(d)
```

`siseJson` 종가는 정수로 반올림돼 있어 계수에 `±0.5 / close_adj` 정도의 오차가 있다. 계수 비교 허용오차에 이 값을 반영한다([§9.2](#92-감지)).

### 7.4 상장주식수

**가동일 이후: Daum 일별 스냅샷 (직접 호출)**

```
GET https://finance.daum.net/api/quotes/A{code}?summary=false&changeStatistics=true
Referer: https://finance.daum.net/quotes/A{code}
```

- `listedShareCount`가 보통주 상장주식수다. 삼성전자는 5,846,278,608주였다.
- 같은 응답의 `marketCap` 1,452,800,234,088,000원은 종가 248,500원 × `listedShareCount`와 정확히 같고, 네이버 시가총액(1,452조 8,002억 원)과도 일치한다. 우선주는 포함되지 않는다.
- `Referer` 헤더를 붙여 호출했다. 헤더 없이 동작하는지는 미검증이다.
- 매 거래일 장 마감 후 스냅샷을 `shares_observation`(`source = DAUM_QUOTE`)에 쌓는다. `price_daily.shares_listed(d)`는 `as_of_date ≤ d`인 최신 관측치다.

**FDR KRX 스냅샷 캐시**

FinanceDataReader 운영자가 KRX 전종목 시세를 하루에 여러 번 받아 GitHub에 올리는 파일이다. KRX 원천 값이고, 한 파일에 KOSPI·KOSDAQ·KONEX 전 종목(우선주 포함)이 들어 있다.

```python
import urllib.error
import pandas as pd

KRX_CACHE = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
             "refs/heads/master/data/listing/krx/{date}.csv")

def read_krx_snapshot(date: str) -> pd.DataFrame | None:
    """date('YYYY-MM-DD') 파일을 읽는다. 파일이 없거나 휴장일 파일이면 None."""
    try:
        df = pd.read_csv(KRX_CACHE.format(date=date), index_col=0,
                         dtype={"Code": str, "ISU_CD": str, "MarketId": str})
    except urllib.error.HTTPError:          # 404: 그날 파일이 없다 (예: 2026-06-08)
        return None
    if (df["Close"].astype(str) == "-").all():  # 주말·휴장일 파일은 가격이 전부 '-'
        return None
    return df
```

- FDR의 `StockListing("KRX")`는 최신 파일 하나만 읽는다. 특정 날짜 파일은 위처럼 같은 캐시의 주소를 직접 읽는다.
- 컬럼: `Code`, `ISU_CD`, `Name`, `Market`, `Dept`, `Close`, `Open`, `High`, `Low`, `Volume`, `Amount`, `Marcap`, `Stocks`, `MarketId`.
- 파일 이름은 수집한 날(KST)이다. 주말에도 파일이 생기며 가격은 전부 `-`이고 `Stocks`만 채워져 있다.

| 확인 항목 (2026-09-15) | 결과 |
| --- | --- |
| 첫 파일 | 2026-03-08 |
| 2026-03-09 ~ 2026-09-14 거래일 중 파일이 없는 날 | 1일 (2026-06-08) |
| 과거 파일의 `Close` vs 네이버 무수정 종가 (2026-03-09, 06-02 표본 30건) | 30건 모두 일치 |
| 2026-09-11 파일의 `Stocks` vs Daum `listedShareCount` (표본 60종목) | 60건 모두 일치 |
| 2026-09-14 파일의 `Stocks` vs Daum (주식수가 바뀐 10종목 포함 30종목) | 30건 모두 일치 |
| 2026-09-14 파일의 `Close` vs 네이버 종가 | 2,373종목 중 430건 일치 (파일 마지막 갱신 18:05) |
| 2026-09-11 파일의 `Close` vs 네이버 종가 | 기업행위 이벤트가 없는 2,135종목 모두 일치 (파일 마지막 갱신 21:22) |

- **당일 파일의 가격은 확정값이 아닐 수 있다.** 파일은 장중부터 여러 번 덮어써지고 마지막 갱신 시각이 날마다 다르다. `Stocks`는 가격이 미확정인 파일에서도 Daum과 일치했다.
- 그래서 이 파일은 **가격이 아니라 `Stocks`에만** 쓴다. 폐지 종목 원종가를 채울 때([§7.2](#72-무수정-종가))는 네이버 `siseJson`과 호가단위 검사를 함께 통과한 값만 쓴다.
- 캐시는 개인 운영 저장소다. 갱신이 멈추면 Daum 단독으로 운영한다 ([§13](#13-결정-기록) D-6).

**가동일 이후 쓰임새**

1. 매일 KR 수집 때 마지막으로 받은 날부터 오늘까지의 파일의 `Stocks`를 `shares_observation`(`source = FDR_KRX_CACHE`)에 적재한다. 당일 파일은 하루 중 덮어써지므로 다음 실행에서 다시 받는다. 전 구간 모드(`backfill`·`reconcile`·`shares`)는 2026-03-09 이후 모든 거래일 파일을 다시 받는다.
2. 같은 날짜의 Daum 관측치와 다르면 C-7 경고를 낸다.
3. Daum 호출이 실패한 종목은 FDR 값으로 `shares_listed`를 채운다.

**가동 전 구간**

| 구간 | 방식 |
| --- | --- |
| 2026-03-09 ~ 가동 전일 | FDR KRX 스냅샷 캐시의 거래일 파일. 파일이 없는 거래일은 직전 관측치를 쓴다 |
| 2025-01-02 ~ 2026-03-06 | 2026-03-09 주식수를 수정계수로 환산한 근사치 (G-1) |

```
shares_listed(d) = shares(2026-03-09) × adj_factor(d) / adj_factor(2026-03-09)
```

- 2026-03-09 전에 폐지돼 스냅샷이 없는 종목은 FDR 상장폐지 목록의 폐지 시점 상장주식수(`FDR_KRX_DELISTING`, 폐지일 전날 기준)를 첫 관측치로 쓴다. 이 날짜는 마지막 거래일보다 늦으므로 기준 계수는 직전 거래일의 계수를 쓴다.
- 이 식은 `market_cap(d) = close_adj(d) / adj_factor(2026-03-09) × shares(2026-03-09)`와 같다. 분할·병합·무상증자는 수정계수에 들어 있으므로 맞게 환산된다.
- 유상증자와 자사주 소각은 반영되지 않는다. 2026-03-09 ~ 09-11 반년 동안 이 효과는 한국 전체 시총의 −0.07%였고, 섹터별로는 필수소비재 −3.87%, 보험 −3.23%, 은행 −2.15%가 가장 컸다.
- 인적분할처럼 가격 계수와 주식수 비율이 다른 기업행위는 오차가 남는다.
- 이후 기업행위로 수정계수가 바뀌어도 두 계수가 같은 비율로 바뀌므로 근사치는 변하지 않는다.
- 근사 구간 전체를 `DATA_GAP` 특이사항 1건으로 기록한다.

### 7.5 한국 일일 흐름

1. KIND 상장법인목록과 FDR 상장폐지 목록을 받아 전일과 비교하고 `security`, `universe_membership`을 갱신한다.
2. `siseJson`의 `KOSPI` 최근 구간으로 `trading_calendar`를 갱신한다.
3. 종목마다 모바일 일별시세와 `siseJson`을 "마지막 저장일 − 15거래일"부터 받는다.
4. `close_raw`, `close_adj`, `adj_factor`, `volume`, `trade_status`를 upsert한다. 저장된 과거 행의 계수가 바뀌었으면 [§9.3](#93-갱신-절차)을 실행한다.
5. Daum에서 당일 상장주식수를 받아 `shares_observation`에 쌓는다.
6. FDR KRX 스냅샷에서 전 거래일 파일의 `Stocks`를 적재하고 Daum 값과 대조한다. Daum이 비어 있는 종목은 FDR 값을 쓴다.
7. `shares_listed`, `market_cap`을 채운다.
8. [11장](#11-검증과-특이사항)의 검증을 실행하고 특이사항을 기록한다.

## 8. 미국 수집 절차

### 8.1 구성종목 이력

- 현재 구성은 기존 [`fetch_gics_sp500.py`](../scripts/fetch_gics_sp500.py)가 만든 [`gics_sp500_constituents.csv`](../data/gics_sp500_constituents.csv)(503행)를 쓴다.
- 변경 이력은 `https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500`의 `table#changes`에서 읽는다. 컬럼은 Effective Date, Added(Ticker, Security), Removed(Ticker, Security), Reason이다. 기존 목록 페이지(*List of S&P 500 companies*)에는 이 표가 더 이상 없다.
- 과거 시점 `D`의 구성은 현재 구성에서 거꾸로 되돌려 만든다.

```
members(D) = 현재 구성
           − { 편입 티커 : 효력일 > D }
           + { 편출 티커 : 효력일 > D }
```

- 편입 종목은 `valid_from = 효력일`, 편출 종목은 `valid_to = 효력일 전날`로 둔다. as-of 조회는 거래일에만 하므로 직전 거래일과 결과가 같다. 효력일 당일 개장 전에 반영된다는 해석이다.
- 수집 시작일 전부터 구성종목이던 라인은 `valid_from = 수집 시작일`로 둔다.
- 편입 기록은 있는데 현재 구성에 없고 이후 편출 기록도 없는 티커는 편입 뒤 티커를 바꾼 것으로 본다. 현재 구성에서 편입일(`Date added`)이 효력일과 같은 종목이 하나뿐이면 그 티커로 잇는다(2026-09-16: `SATS` → `ECHO`). 후보가 없거나 여럿이면 경고(U-2)만 남긴다.
- CIK는 현재 구성 표, 수집 시작일 직전 리비전의 구성 표, SEC 티커 파일(`company_tickers_exchange.json`) 순서로 찾는다. 거래소 구분(`security.board`)도 SEC 티커 파일에서 얻고, 인수로 사라진 종목은 `UNKNOWN`이다.
- 편입이나 편출 한쪽만 있는 행이 있다. 2026-06-30에는 CAG 편출만, 2026-06-29에는 HONA(Honeywell Aerospace 분사) 편입만 있다. 구성 라인 수는 503으로 고정되지 않는다.
- 위키백과는 누구나 편집할 수 있다. 기존 스크립트처럼 수집한 리비전 ID를 기록하고, 현재 구성은 SPY 보유종목과 대조한다.

### 8.2 종가 (yfinance)

```python
import yfinance as yf

yf.set_tz_cache_location(r"data\cache\yfinance")

symbols = [t.replace(".", "-") for t in tickers]          # BRK.B -> BRK-B
df = yf.download(
    symbols,
    start=start,              # 백필 "2025-01-01", 증분 "마지막 저장일 − 10거래일"
    auto_adjust=False,        # 필수. 기본값 True는 Close에 배당 조정을 넣는다
    actions=True,             # Stock Splits 컬럼을 받는다
    threads=True,
    progress=False,
)
close  = df["Close"]          # 분할이 소급 반영된 종가 (배당 조정 없음)
volume = df["Volume"]
splits = df["Stock Splits"]   # 권리락일에 비율, 그 밖의 날은 0
```

- 결과는 (컬럼 종류, 티커) 2단 컬럼 DataFrame이다. 어댑터 안에서 `(ticker, trade_date, close, volume, split_ratio)` 레코드로 풀어 넘긴다.
- `Adj Close`는 배당 조정 값이므로 쓰지 않는다.
- **이력이 없는 티커도 예외를 내지 않는다.** 표준 오류에 메시지를 찍고 그 티커 컬럼을 전부 NaN으로 채운다. 호출 후 컬럼이 전부 NaN인 티커를 따로 검사한다(C-11).
- 요청 한도에 걸리면 `yfinance.exceptions.YFRateLimitError`가 난다. 지수 백오프 후 실패한 티커만 다시 받는다.
- 원종가는 이후 분할비율을 곱해 역산한다. 분사 조정이 섞인 비율([§8.3](#83-주식수))도 가격에는 같은 비율로 조정돼 있으므로 역산식은 같다.

```python
def split_restore(bars, splits):
    """bars: [(trade_date, yf_close)], splits: [(ex_date, ratio)]
    반환: [(trade_date, close_raw, adj_factor, close_adj)]"""
    out = []
    for d, c in bars:
        k = 1.0
        for ex_date, ratio in splits:
            if ex_date > d:          # 이 날 이후에 일어난 이벤트만 되돌린다
                k *= ratio
        close_raw = round(c * k, 2)
        out.append((d, close_raw, 1.0 / k, close_raw / k))
    return out
```

- yfinance `Close`는 부동소수점 오차를 담고 있다(예: `111.21700286865234`). 역산 후 센트 단위로 반올림한다.
- 장중에 실행하면 마지막 행이 미확정 값이다. `ZoneInfo("America/New_York")` 기준 현재 시각이 16:30 이전이면 그날 행을 버린다.

### 8.3 주식수

**단일 클래스 종목: SEC 공시값 (직접 호출)**

```
GET https://data.sec.gov/api/xbrl/companyconcept/CIK{10자리}/dei/EntityCommonStockSharesOutstanding.json
User-Agent: theme-radar <연락처 이메일>
```

- CIK는 [`gics_sp500_constituents.csv`](../data/gics_sp500_constituents.csv)의 `cik` 컬럼에 있다.
- SEC는 연락처가 든 User-Agent와 초당 10회 이하 호출을 요구한다.
- `units.shares[]`의 `end`, `val`, `form`, `filed`를 `shares_observation`(`basis = AS_REPORTED`, `source = SEC_DEI`)에 저장한다. 같은 `end`에 정정 공시(`10-Q/A` 등)로 같은 값이 두 번 올 수 있으므로 `end` 기준으로 중복을 제거한다.
- 공시값은 분할을 소급하지 않은 원값이므로 `close_raw`와 기준이 같다. 다만 공시 기준일과 조회일 사이에 분할이 있으면 곱해 줘야 한다.

```
shares_listed(d) = val(end ≤ d 인 최신 공시)
                 × Π { 이벤트 s : end < s.ex_date ≤ d, s.share_ratio 확정 } s.share_ratio
```

**Yahoo 분할 이벤트의 주식수 비율 판정 (G-4)**

yfinance `Stock Splits`에는 실제 분할·병합과 분사 조정이 함께 들어 있다. 2025-01-01 이후 S&P 500 종목의 이벤트 25건을 이벤트 전후 SEC 공시 주식수와 대조했다.

| 구분 | 건수 | SEC 전후 비율로 확인된 결과 |
| --- | --- | --- |
| 비율 r 또는 1/r이 정수 | 14 | 공시가 있는 10건 모두 주식수가 r배로 바뀜 (BKNG 25:1은 24.465배, NFLX 10:1은 9.964배 등 자사주 매입분 차이) |
| 정수비가 아님 | 11 | 공시가 있는 7건 중 6건은 주식수가 그대로 (DD 2.39 → 1.001배, WDC 1.323 → 1.003배, HON 1.061 → 1.001배, BDX 1.272 → 0.968배, FDX 1.241 → 0.992배, J 1.01 → 0.995배). HON 2026-06-29의 0.9535는 주식수가 0.500배로 줄었다 |

판정 규칙은 하나다.

- **r 또는 1/r이 정수(허용오차 1e-6)이면** `SPLIT`/`REVERSE_SPLIT`으로 기록하고 주식수에 r을 곱한다.
- **그 밖의 비율이면** 주식수에 곱하지 않는다. 가격 역산에는 r을 그대로 쓴다. `UNKNOWN`으로 기록하고 `CORP_ACTION` 특이사항을 남긴다.

정수비가 아닌 이벤트의 실제 주식수 변화는 다음 SEC 공시가 as-of 규칙으로 반영한다. 그 사이에는 HON 2026-06-29처럼 주식수가 틀릴 수 있으므로 특이사항으로 따로 본다. 모든 이벤트에 비율을 곱하면 DuPont의 2025-11-03 주식수가 2.39배로 부푼다.

**SEC 공시 주식수의 커버리지 (2026-09-16 실측)**

현재 구성 503라인의 SEC `dei:EntityCommonStockSharesOutstanding`을 yfinance 현재 주식수와 대조했다. 07 초안은 복수 클래스 7라인만 대체가 필요하다고 봤지만 실제로는 83라인이었다.

| 분류 | 라인 수 | 예 |
| --- | --- | --- |
| 정상 (최신 공시, yfinance와 10% 이내) | 414 | — |
| 주식수 목록이 비어 있음 | 34 | KO, ABT, CB, HCA, MDLZ |
| 404 (클래스별로만 보고) | 30 | META, GOOGL, ABNB, DELL, PLTR |
| 최신 공시가 2024년 이전 | 19 | BRK.B(2011), V, MA, CMCSA, CHTR |
| 최신 공시지만 차이가 큼 | 6 | APH·MNST(공시 뒤 2:1 분할, 규칙으로 반영됨), VMRK(합병) |

- `us-gaap:CommonStockSharesOutstanding`으로 대신 이력을 얻을 수 있는 라인은 83개 중 1개(EXPE)였다.
- yfinance `get_shares_full`은 SEC 정상 라인 40개 표본 120건 대조에서 차이 중앙값 0.24%였지만 2% 넘게 다른 건이 10건이고, PKG −99.9%·BDX +31% 같은 오류 값이 있었다.
- 83라인의 현재 주식수 근사가 GICS 섹터 비중에 주는 영향을 `get_shares_full` 보정값과 비교했다. 2025-01-02·2025-07-01 모두 섹터별 차이는 0.09%p 이하(상대 1% 미만)였다. 개별 라인으로는 36라인이 2025년 초 대비 주식수가 5% 넘게 달라졌다(OMC +45%, HBAN +39%, BG +38%, CHTR −16%, HCA −15%).
- 섹터 흐름에 주는 영향이 작으므로 근사를 유지하고 특이사항(G-3)으로 기록한다 (§13 D-7).

**SEC 값이 틀어지는 사례와 처리**

| 사례 | 처리 |
| --- | --- |
| 수집 시작일 직전 공시가 분할 전 값인데 분할이 시작일 전(2024-12)에 있었다 (TSCO, SMCI, ANET, ETR, PANW) | 분할 이벤트를 수집 시작일 1년 전부터 받아 곱한다 |
| 합병 전 지주회사가 1,000주로 공시했다 (PSKY) · 자리수가 틀린 공시 (AEP 481조 주, PKG) | 관측치 × 그 무렵 원종가로 만든 시가총액이 범위(US 1억~20조 달러, KR 100만~1경 원)를 벗어나면 쓰지 않는다 (U-5 경고). KR 하한이 낮은 이유는 정리매매 가격(2원 × 3천만 주)을 걸러내지 않기 위해서다. 처음에는 "최대 관측치의 1% 미만"으로 걸렀으나, 너무 큰 오류 값이 기준이 되어 정상 값을 모두 버리는 문제가 있어 바꿨다 |
| 한 라인의 출처가 SEC에서 yfinance로 바뀌었다 (BRK.B) | 라인마다 이번에 쓰기로 한 출처의 관측치만 남긴다 |

**복수 클래스 종목 (G-3)**

Alphabet(GOOGL, GOOG), Fox(FOXA, FOX), News Corp(NWSA, NWS), Berkshire Hathaway(BRK.B)의 7개 라인이다. SEC `companyconcept`는 Alphabet에 404를 반환했다.

SEC 값을 쓰지 않고 yfinance로 대체하는 조건은 다섯 가지다. 공시 데이터가 없음(404), 주식수 목록이 빔(Coca-Cola), 한 공시 안에 클래스별 값이 여럿(EchoStar), 같은 CIK의 편입 라인이 여럿(Alphabet·Fox·News Corp), 최신 공시가 수집 시작일 1년 전보다 오래됨(Berkshire, 2011년 A주 수가 마지막)이다.

가동일 이후에는 yfinance `Ticker.info`의 클래스별 현재 주식수를 매일 받아 `shares_observation`(`source = YF_INFO`)에 쌓는다. 이 값을 주는 quoteSummary API는 쿠키·crumb 토큰이 필요해 직접 호출하면 401 `Invalid Crumb`가 난다. yfinance가 이 절차를 처리한다.

| 라인 | `sharesOutstanding` | `impliedSharesOutstanding` (회사 합계) |
| --- | --- | --- |
| GOOGL | 5,867,155,790 | 12,229,934,831 |
| GOOG | 5,527,000,000 | 12,229,934,831 |
| FOXA | 200,188,712 | 420,437,476 |
| FOX | 220,248,764 | 420,437,476 |
| NWSA | 358,881,847 | 536,370,159 |
| NWS | 177,488,312 | 536,370,159 |
| BRK-B | 1,408,035,161 | 2,140,709,794 |

- FOXA+FOX, NWSA+NWS는 회사 합계와 정확히 같다.
- GOOGL+GOOG는 회사 합계보다 835,779,041주 적다. 상장되지 않은 Class B 주식으로 보인다. BRK-B도 A주를 뺀 B주 수다.
- 편입되지 않은 클래스는 회사 합계를 편입 라인에 주식수 비율로 나눠 반영한다. 단일 클래스 종목과 FOXA·FOX, NWSA·NWS는 비율이 1이라 값이 바뀌지 않는다.

  ```
  line_shares = sharesOutstanding(line) × impliedSharesOutstanding / Σ sharesOutstanding(같은 CIK의 편입 라인)
  ```

  Berkshire는 B주 수에 1.520을, Alphabet 두 라인은 1.073을 곱하게 된다. 클래스 간 전환은 회사 합계를 바꾸지 않으므로 흐름으로 보이지 않는다.
- GOOG 값은 반올림된 수치라 추정치일 수 있다.
- `info`의 값이 분할이나 공시 후 언제 갱신되는지는 미검증이다. 갱신이 늦으면 `SHARE_CHANGE` 특이사항으로 드러난다.
- `yfinance`의 `get_shares_full`은 쓰지 않는다. 복수 클래스에 회사 합계 수준 값을 주고, 단일 클래스는 SEC와 같은 분기 공시값이다.

가동 전 구간(2025-01-01 ~ 가동 전일)은 가동일 값을 수정계수로 환산한 근사치를 쓴다. 식은 한국과 같다([§7.4](#74-상장주식수)). 복수 클래스 라인뿐 아니라 위 커버리지 표에서 SEC 값을 쓸 수 없는 라인 모두에 같은 방식을 쓰고, 근사 구간은 `DATA_GAP` 특이사항 1건으로 기록한다 (G-3).

### 8.4 상장폐지 종목

2025-01-01 이후 S&P 500에서 편출된 32종목 중 아래 10종목은 Yahoo 이력이 없다. yfinance는 0행을 반환했고, Nasdaq API도 0건이며 Stooq은 접근이 막혀, 무인증으로 과거 종가를 얻을 방법을 찾지 못했다.

| 편출 효력일 | 티커 | 사유 (위키백과 변경표) |
| --- | --- | --- |
| 2026-05-07 | CTRA | Devon Energy가 Coterra Energy 인수 |
| 2026-04-09 | HOLX | Blackstone·TPG가 Hologic 인수 |
| 2026-02-09 | DAY | Thoma Bravo가 Dayforce 인수 |
| 2025-12-11 | K | Mars가 Kellanova 인수 |
| 2025-11-28 | IPG | Omnicom이 Interpublic Group 인수 |
| 2025-08-28 | WBA | Sycamore Partners가 Walgreens Boots Alliance 인수 |
| 2025-07-23 | HES | Chevron이 Hess 인수 |
| 2025-07-18 | ANSS | Synopsys가 Ansys 인수 |
| 2025-07-09 | JNPR | Hewlett Packard Enterprise가 Juniper Networks 인수 |
| 2025-05-19 | DFS | Capital One이 Discover Financial 인수 |

- 이 10종목은 계산에서 제외한다. [03 §10](03-metrics-spec.md#10-예외-처리)의 `NO_BASE_PRICE`로 처리되어 가중치 분모에서도 빠진다.
- 종목마다 편출 효력일에 `DATA_GAP` 특이사항을 남긴다. 규모는 10-K 표지의 유동 시가총액(`dei:EntityPublicFloat`)으로 기록한다. 2025-01-02 섹터 시총 대비 비중은 에너지(HES, CTRA) 3.68%, 필수소비재(K, WBA) 1.01%, 금융(DFS) 0.55%이고 나머지는 0.3% 미만이다.
- 주식 교환 인수에서는 인수사 주식수가 합병 신주만큼 뛴다(SEC 공시 기준 CVX +16.5%, DVN +77%, COF +66.9%, SNPS +19.7%). 이 변화는 별도 규칙 없이 `SHARE_CHANGE` 특이사항으로 드러난다.
- 편출됐지만 아직 Yahoo에 이력이 남은 종목이 있다. AVB(2026-08-18 편출)와 EA(2026-08-05 편출)가 그렇다. **모듈 가동 전이라도 편출 종목의 이력은 즉시 받아 둔다.**
- 가동 후에는 매일 수집하므로 폐지 전에 데이터가 쌓인다. 편출된 종목도 거래가 끝날 때까지 계속 수집한다.

## 9. 액면분할·병합 소급 갱신

### 9.1 무엇을 갱신하는가

| 컬럼 | 권리락일 이전 행 | 권리락일 이후 행 |
| --- | --- | --- |
| `close_raw` | 그대로 | 그대로 |
| `adj_factor` | 새 계수로 교체 | 그대로 |
| `close_adj` | `close_raw × 새 adj_factor` | 그대로 |
| `shares_listed` | 그대로 (공시 원값). §7.4 A 방식으로 채운 값이면 `÷ share_ratio` | 그대로 |
| `market_cap` | 그대로 | 그대로 |

분할·병합은 시가총액을 바꾸지 않는다. 바뀌는 것은 과거 수익률 계산에 쓰는 수정 종가뿐이다. 10:1 분할의 `price_factor`는 0.1이고 `share_ratio`는 10이다. 1:10 병합은 각각 10과 0.1이다.

### 9.2 감지

| 시장 | 신호 | 방법 |
| --- | --- | --- |
| KR | 수정계수 변화 | 증분 수집 때 받은 `siseJson`으로 계수를 다시 계산해 저장값과 비교한다. `\|new / old − 1\| > 0.5 / close_adj + 1e-6`인 날이 하나라도 있으면 전 구간을 다시 받는다. 기업행위 경계일은 연속한 두 거래일의 계수가 0.5% 넘게 다른 날로 본다. 네이버 수정 종가에는 0.1% 이하의 반올림 흔들림이 있어서다(011300, 068270 실측) |
| US | 새 분할 이벤트 | yfinance `Stock Splits`에 `corporate_action`에 없는 (종목, 권리락일)이 있으면 발생으로 본다. 주식수 비율은 [§8.3](#83-주식수) 규칙으로 판정한다 |
| 공통 | 가격 점프 | 원종가가 전일 대비 KR ±30%, US ±50%를 넘었는데 기업행위 기록이 없으면 점검 큐에 넣는다 (C-2, C-3) |
| 공통 | 주간 대사 | 전 종목 전 구간을 다시 받아 비교한다 ([§9.4](#94-정기-대사)) |

한국에서 Yahoo 이벤트 대신 계수 변화를 쓰는 이유는 Yahoo가 기업행위를 빠뜨리기 때문이다([§3.2](#32-한국-네이버를-채택하고-yahoo를-쓰지-않는-이유)). 권리락일에는 원종가만 급변하고 수정 종가는 연속적이므로, 권리락일 이전에 저장된 모든 행의 계수가 바뀐다. 증분 창("마지막 저장일 − 15거래일"부터)에 저장된 행이 들어 있으므로 변화는 반드시 감지된다.

한국 액면병합·분할은 FDR KRX 스냅샷의 `Stocks` 변화로도 드러난다. 누보(332290)는 1:5 병합 권리락일인 2026-09-14 파일에서 39,696,247주가 7,939,249주로 바뀌었다.

### 9.3 갱신 절차

종목 하나를 한 트랜잭션으로 처리한다. 계수 계산식은 두 시장이 같다. 새로 받은 수정 종가를 저장된 원종가로 나누면 된다.

1. 전 구간(2025-01-01 ~ 오늘) 수정 종가를 다시 받는다. KR은 `siseJson`, US는 yfinance `Close`다.
2. 날짜마다 `new_factor = 수정 종가 / close_raw`를 계산하고 허용오차를 넘게 바뀐 행만 갱신한다. 허용오차는 KR `0.5 / 가격`, US `0.005 / 가격`이다.
3. 계수가 바뀐 경계일을 `corporate_action`에 기록한다. US는 yfinance 이벤트와 [§8.3](#83-주식수) 판정을 쓴다. KR은 경계 전후 계수의 비를 `price_factor`로 두고, 정수배면 `SPLIT`·`REVERSE_SPLIT`, 아니면 `UNKNOWN`으로 기록하고 `CORP_ACTION` 특이사항을 남긴다.
4. `restatement_log`를 남긴다.
5. [04 §4](04-pipeline.md#4-재계산-정책)의 재계산 큐에 넣는다. "`adj_factor` 소급 변경" 규칙에 따라 해당 종목이 속한 그룹의 `affected_from` 기간부터 최신 기간까지가 대상이다.
6. 검증 C-5, C-6을 통과하면 커밋하고, 실패하면 롤백한다.

```python
def restate(conn, sec, source, run_id, now):
    adjusted = source.adjusted_closes(sec.ticker, "2025-01-01", now.date().isoformat())  # {date: close_adj}
    rows = conn.execute(
        "SELECT trade_date, close_raw, adj_factor FROM price_daily "
        "WHERE security_id = ? ORDER BY trade_date",
        (sec.security_id,),
    ).fetchall()

    changed, max_rel = [], 0.0
    with transaction(conn):  # theme_radar.db.transaction: BEGIN IMMEDIATE, 예외가 나면 롤백
        for d, close_raw, old in rows:
            if d not in adjusted:
                continue
            new = adjusted[d] / close_raw
            rel = abs(new / old - 1)
            if rel > source.rel_tolerance(adjusted[d]):   # KR 0.5/가격, US 0.005/가격 (+1e-6)
                changed.append(d)
                max_rel = max(max_rel, rel)
                conn.execute(
                    "UPDATE price_daily SET adj_factor = ?, close_adj = ?, adj_source = ?, adj_updated_at = ? "
                    "WHERE security_id = ? AND trade_date = ?",
                    (new, adjusted[d], source.name, now.isoformat(), sec.security_id, d),
                )
        if changed:
            record_corporate_actions(conn, sec, source, now)          # 계수 경계일 → corporate_action
            conn.execute(
                "INSERT INTO restatement_log VALUES (?, ?, 'FACTOR_CHANGED', ?, ?, ?, ?, ?)",
                (run_id, sec.security_id, changed[0], changed[-1], len(changed), max_rel, now.isoformat()),
            )
            enqueue_recalc(conn, sec.security_id, changed[0])         # 04 §4
            validate_restatement(conn, sec)                           # C-5, C-6. 실패 시 예외 → 롤백
```

### 9.4 정기 대사

매주 토요일 전 종목의 전 구간 수정 종가를 다시 받아 [§9.3](#93-갱신-절차)과 같은 로직을 돌린다. 증분 창을 벗어나 늦게 반영된 기업행위와 출처 쪽의 소급 정정을 잡기 위해서다. 요청 수는 KR 약 2,700회, US 약 530회다.

### 9.5 회귀 테스트 케이스

실측한 사례를 고정 테스트로 쓴다(`tests/prices/test_regression.py`, 2026-09-16 실측 응답에서 잘라 둔 픽스처). 라이브러리 버전을 올릴 때도 이 테스트를 통과해야 한다. 모든 케이스에서 `restate` 전후 `market_cap`이 바뀌지 않아야 한다.

| 시장 | 종목 | 권리락일 | 이벤트 | 기대 결과 |
| --- | --- | --- | --- | --- |
| US | NFLX | 2025-11-17 | 10:1 분할 | 2025-11-14 `close_raw` 1,112.17, `adj_factor` 0.1, `share_ratio` 10 |
| US | ORLY | 2025-06-10 | 15:1 분할 | 2025-06-09 이전 `adj_factor` 1/15 |
| US | NOW | 2025-12-18 | 5:1 분할 | 2025-12-17 이전 `adj_factor` 0.2 |
| US | DD | 2025-11-03 | 분사 (Yahoo 비율 2.39) | 주식수에 곱하지 않음. `CORP_ACTION` 특이사항 1건 |
| US | DD | 2026-06-24 | 1:3 병합 | `share_ratio` 1/3 |
| US | HON | 2026-06-29 | 분사와 주식수 감소 (Yahoo 비율 0.9535) | 주식수에 곱하지 않음. `CORP_ACTION` 특이사항 1건. 2026-06-30 공시 주식수 316,940,010을 as-of로 반영 |
| US | 전 종목 | — | 배당 | yfinance 호출이 `auto_adjust=False`인지 확인. KO 2025-01-02 `close_raw` 61.84 |
| KR | 001080 | 2026-03-09 | 10:1 분할 | 2026-03-06 `close_raw` 54,400, `adj_factor` 0.1 |
| KR | 011300 | 2026-04-30 | 1:10 병합 | 2026-04-27 `close_raw` 354, `adj_factor` 10 |
| KR | 000670 | 2025-04-25, 2025-12-29 | 분할 후 무상증자 | 계수 경계 2개, 2025-04-24 `adj_factor` 0.097284 |
| KR | 068270 | 2026-06-04 | 주식배당 | 2026-06-02 `adj_factor` 0.953167 |
| KR | 207940 | 2025-10-30 | 인적분할 | 2025-10-27 `adj_factor` 1.471744, `action_type` `UNKNOWN`, `CORP_ACTION` 특이사항 1건. 거래정지 중 계수가 바뀌어 경계일은 거래 재개일 2025-11-24로 기록된다 |
| KR | 332290 | 2026-09-14 | 1:5 병합 | `REVERSE_SPLIT` 기록, FDR 스냅샷 `Stocks` 39,696,247 → 7,939,249, `SHARE_CHANGE` 특이사항 없음 |

## 10. 배치 운영

### 10.1 실행 시각

[04 §2](04-pipeline.md#2-배치-스케줄)의 스케줄을 따른다.

| 작업 | 시각 (KST) | 근거 |
| --- | --- | --- |
| `prices daily --market KR` | 거래일 18:00 | 네이버 시총 목록 API의 `closePriceSendTime` 값 16:30 이후 |
| `prices daily --market US` | 다음 날 08:00 | 미국 정규장 마감 후 |
| `prices reconcile` | 토요일 | 전 구간 대사 |

- 이 PC는 Windows이므로 작업 스케줄러(`schtasks`)로 등록한다. 작업 스케줄러는 작업 디렉터리를 지정하지 않으므로 `cd /d C:\WORK\PROJECT\theme-radar` 후 `.venv\Scripts\python -m theme_radar ...`를 실행하는 `.cmd` 파일을 만들어 등록한다.
- 거래일 작업으로는 `prices daily` 대신 수집 뒤 집계까지 이어서 실행하는 `daily --market KR|US`를 등록한다. 토요일 작업은 `prices reconcile` → `recalc` → `backup` 순서다 ([09 §4.6](09-tech-stack.md#46-배치-실행과-운영)).
- PC가 꺼져 실행을 건너뛰어도 [§4](#4-설계-원칙)의 증분 규칙이 다음 실행에서 메운다.
- 장 마감 전에 실행되면 당일 행을 저장하지 않는다. KR은 16:30 KST, US는 뉴욕 시각 16:30이 기준이다.

### 10.2 요청 수

| 작업 | 출처 | 요청 수 (추정) |
| --- | --- | --- |
| KR 백필 | 네이버 모바일 일별시세 (종목당 약 7페이지) | 약 18,000 |
| KR 백필 | 네이버 `siseJson` (폐지 종목 포함) | 약 2,700 |
| KR 백필 | FDR KRX 스냅샷 캐시 (2026-03-09 이후 거래일) | 약 130 |
| US 백필 | yfinance (현재 구성 + 편출 종목, 종목당 1회) | 약 535 |
| US 백필 | SEC | 약 500 |
| KR 일일 | 네이버 2종 + Daum | 약 7,650 |
| KR 일일 | FDR 스냅샷 1파일 + 상장폐지 목록 | 2 |
| US 일일 | yfinance 종가 | 약 535 |
| US 일일 | yfinance 복수 클래스 주식수 | 7 |

직접 호출 검증 때 6스레드로 초당 약 23회를 호출해도 차단은 없었다. 그래도 직접 호출의 운영 기본값은 **동시 연결 4, 초당 5회 이하**로 둔다. 이 속도로 KR 백필은 약 1시간 20분, KR 일일 수집은 약 25분 걸린다. yfinance 일괄 다운로드는 라이브러리 기본 스레드로 503종목에 36초가 걸렸다.

### 10.3 요청 규칙

- 직접 호출하는 네이버·Daum·KIND에는 브라우저형 User-Agent를 쓴다. SEC에는 연락처가 든 User-Agent를 쓴다.
- HTTP 429, 5xx, 타임아웃은 지수 백오프로 재시도한다. 기존 스크립트의 `http_get` 재시도 규약을 확장한다.
- 직접 호출 원문은 `data/raw/prices/{source}/{YYYYMMDD}/{ticker}.json.gz`로 저장한다. 라이브러리 결과는 호출 단위로 `data/raw/prices/{yfinance|fdr}/{YYYYMMDD}/{작업}.csv.gz`로 저장한다. 일일 수집분은 보존 기간을 정해 지우고, 백필과 대사 원본은 남긴다.

## 11. 검증과 특이사항

### 11.1 수집 단계 검증

[04 §5.1](04-pipeline.md#51-적재-단계-차단)의 적재 단계 검증에 아래 항목을 더한다. 검증은 데이터 오류를 막는 용도다. 시장에서 일어난 사건은 [§11.2](#112-특이사항)로 기록한다.

| # | 검사 | 대상 | 실패 시 |
| --- | --- | --- | --- |
| C-1 | 원종가가 호가단위의 배수다 | KR `close_raw` | 해당 행 적재 거부 |
| C-2 | 원종가가 전일 대비 ±30.1%를 넘었는데 같은 날 기업행위 기록이 없다. 가격제한폭이 정확히 30%라 상한가가 부동소수점으로 걸리지 않게 0.1%p 여유를 둔다 | KR | 경고, 점검 큐 (상장 직후 5거래일·폐지 직전 10거래일은 예외) |
| C-3 | 원종가가 전일 대비 ±50%를 넘었는데 분할 이벤트가 없다 | US | 경고, 점검 큐 |
| C-4 | 캘린더상 거래일인데 행이 없다 | 전체 | 경고. 최근 3거래일 연속 행이 없는 상장 종목이 전체의 1%(최소 5종목)를 넘으면 차단 (출처 장애 판단) |
| C-5 | 기업행위 기록 없이 `adj_factor`가 바뀌었다 | 전체 | [§9.3](#93-갱신-절차) 재수정 |
| C-6 | 전일 대비 시총 변화율과 `close_adj` 변화율의 차이가 20%를 넘는다 | 전체 | 경고. 가격과 주식수의 기준 불일치 의심 |
| C-7 | KR에서 같은 날 Daum과 FDR 스냅샷의 상장주식수가 다르다 | KR | 경고. Daum 값을 쓴다 |
| C-8 | 표본 교차검증. KR은 WiseIndex 역산가, US는 Nasdaq 최근 종가와 대조한다 | 일 1회 50종목 | 불일치 2% 초과 시 경고. **아직 구현하지 않았다.** 앞으로 할 일로 [§11.3](#113-c-8-표본-교차검증-앞으로-할-일)에 남긴다 |
| C-9 | as-of S&P 500 라인 수가 495 ~ 510을 벗어난다 | US | 차단 |
| C-10 | 장 마감 전에 받은 당일 행이다 | 전체 | 적재 거부 |
| C-11 | 유니버스에 속한 US 티커의 yfinance 결과가 전 구간 NaN이다 | US | 편출 후 폐지 종목이면 경고, 현재 구성종목이면 차단 |
| C-12 | yfinance 결과에 `Adj Close` 컬럼이 없다. `auto_adjust=True`로 받으면 이 컬럼이 빠지고 `Close`가 배당 조정 값이 된다 | US | 차단 |
| C-13 | FDR KRX 스냅샷 파일이 없거나 휴장일 파일이다 | KR | 해당 날짜 FDR 관측치 생략. 7거래일 연속이면 경고 (캐시 중단 의심) |

구현하며 더한 검사는 다음과 같다. 결과는 C-*와 같이 `validation_result`에 남는다.

| # | 검사 | 실패 시 |
| --- | --- | --- |
| U-1 | KR: 상장 중으로 저장된 종목이 KIND 목록에서 사라졌는데 상장폐지 목록에 없다 | 경고 |
| U-2 | US: 변경표를 되돌리다 모순을 만났다(편출 후에도 구성종목, 티커 변경을 특정하지 못함) | 경고 |
| U-3 | US: CIK를 찾지 못한 티커가 있다 | 경고 |
| U-4 | US: 주식수를 얻지 못한 라인이 있다 | 경고 |
| FETCH | KR: 재시도 후에도 요청이 실패한 종목 | 전체의 5%를 넘으면 차단, 그 밖에는 경고 |

**C-1의 KRX 호가단위** (KOSPI·KOSDAQ 공통)

| 가격 구간 | 호가단위 |
| --- | --- |
| 2,000원 미만 | 1원 |
| 2,000원 이상 5,000원 미만 | 5원 |
| 5,000원 이상 20,000원 미만 | 10원 |
| 20,000원 이상 50,000원 미만 | 50원 |
| 50,000원 이상 200,000원 미만 | 100원 |
| 200,000원 이상 500,000원 미만 | 500원 |
| 500,000원 이상 | 1,000원 |

Yahoo 분할 이벤트가 없는 2,135종목 중 1,989종목은 2025-01-02 ~ 2026-09-14의 모든 종가가 이 표에 맞았다. 나머지 146종목의 위반은 수정주가 조정분으로 보인다. C-2도 오탐이 적다. 네이버 수정 종가에서 전일 대비 ±30%를 넘은 변동은 전 종목·전 기간을 통틀어 10건이었다.

### 11.2 특이사항

가격이 아닌 이유로 섹터 시총을 바꾸거나, 규칙으로 처리하지 못해 계산에 오차를 남기는 사건이다. 섹터 흐름 계산은 이 사건들을 보정하지 않고 규칙대로 진행하며, 사건은 `special_event`에 한 줄씩 남겨 따로 본다.

| 유형 | 발생 조건 | `market_cap`에 기록하는 값 |
| --- | --- | --- |
| `LISTING` | KR 신규상장, US S&P 500 편입 | 첫 거래일 시총 |
| `DELISTING` | KR 상장폐지, US S&P 500 편출 | 마지막 거래일 시총 |
| `RECLASS` | 분류 매핑의 섹터가 바뀜 | 변경일 시총 |
| `SHARE_CHANGE` | 상장주식수가 전일 대비 ±5% 넘게 바뀜. 같은 날 분할·병합 기록이 있으면 제외 | 주식수 변화분 × 종가 |
| `CORP_ACTION` | 주식수에 반영하지 않은 기업행위. KR 비정수 수정계수, US 비정수 분할 비율 | 권리락 전일 시총 |
| `DATA_GAP` | 알려진 데이터 공백과 근사 구간 (G-1, G-2, G-3) | 추정 규모. 모르면 NULL |

**출처.** 사건마다 그 사건을 만든 값이 어디서 왔는지를 `source`에 남긴다. 값은 원천 테이블의 출처 코드와 같다([§6](#6-저장소-스키마)).

| 유형 | `source` | 어디서 오는가 |
| --- | --- | --- |
| `LISTING` | KR `KIND_LISTING` · US `WIKI_SP500` | 유니버스 편입 이력([§7.1](#71-종목-마스터와-유니버스), [§8.1](#81-구성종목-이력)). 편입 이력에는 행마다 출처가 없어 **시장별 고정값**이다 |
| `DELISTING` | KR `FDR_KRX_DELISTING` · US `WIKI_SP500` | 〃 |
| `SHARE_CHANGE` | `DAUM_QUOTE`, `FDR_KRX_CACHE`, `SEC_DEI`, `YF_INFO` | 그날 상장주식수로 쓴 `shares_observation`의 출처. as-of 규칙이라 그날 관측치가 없으면 그 앞의 최신 관측치이고, 같은 날 여러 출처가 있으면 [§6](#6-저장소-스키마)의 우선순위로 고른다 |
| `CORP_ACTION` | `NAVER_FACTOR_JUMP`, `YFINANCE_SPLIT`, `MANUAL` | 같은 종목·권리락일의 `corporate_action.source`. 둘 이상이면 쉼표로 잇는다 |
| `DATA_GAP` | `INTERNAL` | 바깥 출처가 아니라 우리 판정이다. 근거는 `detail`의 G-번호와 [§2.2](#22-알려진-한계와-처리) |

- 출처는 사건과 함께 매 실행마다 다시 뽑는다. 스키마를 올린 뒤 첫 수집 전까지는 `NULL`이고, 화면에는 `—`로 나온다.
- 수집 원문은 `data/raw/prices/<출처>/<날짜>/`에 남아 있어, 출처 코드에서 그날 받은 파일까지 짚을 수 있다([§10.3](#103-요청-규칙)).
- `sector_share`는 사건 시점 섹터 시총 대비 비중이다. 이 값으로 정렬하면 섹터 흐름에 영향이 큰 사건부터 보인다.
- 사건은 `daily`·`backfill`·`reconcile` 실행 끝에 수집 데이터에서 시장 단위로 모두 다시 뽑는다. 같은 사건은 (시장, 유형, 종목, 날짜)로 한 번만 기록하고, 계속 나오는 사건은 처음 기록한 시각을 유지한다. 계산을 고쳐 더 이상 나오지 않는 사건은 사라진다.
- `LISTING`·`DELISTING`은 유니버스 편입 기간의 시작·끝에서 만든다. US `DELISTING`의 날짜는 편출 효력일이다.
- 현재 분류 매핑만 쓰므로([02 §3.5](02-domain-and-data-model.md#35-security_group_map)) 이미 편출·폐지된 종목의 사건은 `group_code`와 `sector_share`가 비어 있다.
- `SHARE_CHANGE`의 임계값 ±5%는 설정값으로 둔다.
- 화면과 API에서 특이사항을 조회하는 방법은 후속 작업이다 ([§13](#13-결정-기록)).

### 11.3 C-8 표본 교차검증 (앞으로 할 일)

**왜 필요한가.** C-1 ~ C-13 중 C-8만 바깥 출처와 대조한다. 나머지는 우리가 받은 값들 사이의
일관성(전일 대비 변동폭, 빠진 행, 출처 간 불일치)을 본다. 그래서 **파이프라인 전체가 일관되게
틀린 경우**는 어느 것도 잡지 못한다. 수정주가 규약을 잘못 적용했거나, 출처가 조용히 다른 계열의
값을 주기 시작했거나, 주식수 기준(발행/유통)이 어긋난 경우가 그렇다. 이런 오류는 화면의 수익률을
통째로 틀리게 하면서도 내부 검증은 전부 통과한다.

**KR은 방법이 확인됐다.** [§14](#14-초기-적재-결과-2026-09-16)에 측정 결과를 적었다.
`data/wi26_constituents.csv`의 `market_cap_mn_krw`(유동시총, 백만원)를 `applied_shares`(유동주식수)로
나누면 그날 종가가 역산된다. 유동비율은 분자·분모에 같이 들어 있어 약분된다. 2026-09-11 스냅샷으로
2,373종목을 대조했더니 편차 중앙값 0.0003%, 최대 0.01%였고 2%를 넘는 종목은 없었다.

**남은 일**

| 항목 | 내용 |
| --- | --- |
| 임계값 | 명세의 2%는 실측(최대 0.01%)에 비해 너무 느슨하다. 0.1% 수준으로 조이는 것을 검토한다. 느슨한 임계값은 잡으라는 오류를 그냥 통과시킨다 |
| KR 대조 일자 | 스냅샷은 하루치다([data/README.md §4.2](../data/README.md)). `scripts/fetch_wi26.py --date YYYYMMDD`로 검사할 날짜의 파일을 받아야 한다. 매일 1회로 할지, 주 1회 표본으로 할지 정한다 |
| US 출처 | Nasdaq 종가를 어디서 받을지 정하지 않았다. yfinance와 같은 계열의 값이면 교차검증이 되지 않으므로, **yfinance와 독립된 출처**여야 한다. 이 조건을 만족하는 출처를 먼저 찾는다 |
| 실행 위치 | `daily` 안에 넣으면 매 실행이 외부 요청에 묶인다. 별도 명령(`prices crosscheck`)으로 두고 `status`가 "마지막 교차검증 이후 N일"을 보여 주는 쪽이 수동 운영에 맞는다 |
| 기록 | 결과는 다른 검사와 같이 `validation_result`에 `C-8`로 남긴다 |

**언제 하나.** 지금은 수동으로 쓰므로([09 T-13](09-tech-stack.md#8-결정-기록)) 급하지 않다. 다만
수집 코드를 고쳤을 때, 출처를 바꿨을 때, 화면의 수익률이 직관과 어긋날 때는 이 검사가 있어야 원인을
가릴 수 있다. 그 셋 중 하나가 생기면 먼저 만든다.

## 12. 그 밖의 고려사항

1. **섹터 시총은 가격 외 이유로도 움직인다.** 신규상장·폐지, 분류 변경, 증자·소각, 분사가 그렇다. 섹터 흐름 계산은 이런 사건을 분해하거나 보정하지 않고, [§11.2](#112-특이사항) 특이사항으로 따로 본다. 테마는 비배타 분류라 테마 간 시총을 더하면 중복 계산된다.

2. **생존편향.** 폐지·편출 종목을 빼고 계산하면 과거 섹터 시총이 체계적으로 틀어진다. KR은 `siseJson`이 폐지 종목을 보존하지만 원종가 API는 비어 있고, US는 Yahoo가 이력을 지운다. 가장 확실한 대책은 가동 후 매일 수집해 두는 것이다.

3. **비공식 API 의존과 이용약관.** 네이버·Daum·Yahoo의 엔드포인트는 공개 API가 아니다. yfinance와 FinanceDataReader도 내부에서 같은 비공식 경로를 호출한다. 예고 없이 바뀌거나 막힐 수 있고, 이용약관상 자동 수집이나 재배포가 제한될 수 있다. 내부 분석 용도로 한정하고, 대시보드를 외부에 공개하거나 원시 데이터를 배포하기 전에 각 서비스의 약관을 검토한다.

4. **라이브러리 버전 변경.** 라이브러리 업데이트는 엔드포인트 변경을 따라가는 수단이지만 동작도 바꾼다. yfinance의 `auto_adjust` 기본값이 True인 것이 그런 예다. 버전은 고정하고, 올릴 때는 [§9.5](#95-회귀-테스트-케이스)와 C-12를 통과시킨다.

5. **FDR 캐시 의존.** 한국 과거 상장주식수(2026-03-09 이후)와 상장폐지 목록은 FinanceDataReader 운영자의 GitHub 캐시에 의존한다. 백필한 값은 DB에 남으므로 캐시가 사라져도 과거 데이터는 잃지 않는다. 가동 후 영향은 폐지 목록 대체(KIND 직접 호출)와 교차검증 상실이다.

6. **2026년 액면병합 급증.** Yahoo 이벤트 기준 KR 액면병합은 2025년에 월 0 ~ 5건이었는데, 2026-05에 44건, 2026-08에 34건, 2026-09(14일까지)에 21건이다. 원인은 확인하지 않았다. 스캔한 2,373종목 중 238종목에서 이벤트가 269건 나왔다. 소급 갱신을 예외 처리가 아니라 매일 도는 정상 경로로 만들어야 한다.

7. **수정계수 방법론 차이.** 무상증자·주식배당·인적분할의 계수는 출처마다 다르다([§3.3](#33-기업행위-전후-각-출처의-값)). 출처를 바꾸면 전 구간을 다시 수정하고 `restatement_log`에 `SOURCE_SWITCH`로 남긴다.

8. **분사·합병·이전상장.** 한국 인적분할은 가격 계수와 주식수 비율이 다르다. 미국 분사는 Yahoo가 정수비가 아닌 분할로 기록한다([§8.3](#83-주식수)). 분할 신설회사는 새 `security`로 신규상장 처리한다. 코넥스에서 코스닥으로 이전상장한 종목은 폐지 목록에 나오지만 폐지로 처리하지 않는다. 합병으로 소멸한 회사는 폐지로 처리한다.

9. **종목 식별자.** KR은 영문자가 섞인 코드와 선행 0이 있으므로 문자열로 저장한다. pandas로 읽을 때도 `dtype={"Code": str}`를 준다. US는 표기 차이(`BRK.B`와 `BRK-B`)가 있고 티커가 바뀌거나 재사용될 수 있다. 과거 티커 이력은 두지 않고, 티커가 바뀐 종목은 기존 `security_id`의 `ticker`를 갱신해 가격 이력을 잇는다 ([02 §3.2](02-domain-and-data-model.md#32-security)).

10. **날짜와 시간대.** 거래소 현지 날짜를 키로 쓴다. US 데이터는 한국 시간으로 다음 날 들어오므로 수집일과 거래일이 다르다. 시간 계산은 가상환경의 `zoneinfo`로 한다.

11. **휴장일 캘린더.** 지수 시계열에서 거래일을 뽑는다. 2025-01-02 ~ 2026-09-14 기준 `^KS11`은 414거래일, `^GSPC`는 425거래일이었다. 임시휴장도 자동으로 반영된다. 미래 거래일은 이 방법으로 알 수 없으므로 진행 중 기간은 [01 §5](01-requirements.md#5-기간-체계-period)대로 최신 거래일 기준으로 계산한다.

12. **넥스트레이드(NXT).** 종가는 KRX 정규장 종가를 쓴다. 거래량은 출처마다 KRX 단독인지 통합인지가 다르므로, 거래량 지표를 추가하게 되면 출처를 하나로 고정한다.

13. **유동시총과 전체 시총.** WiseIndex의 `MKT_VAL`은 유동시총이다([data/README.md](../data/README.md) §4.1). [03 §3](03-metrics-spec.md#3-가중치)은 전체 시총 기준이므로 WiseIndex 값은 가격 교차검증(C-8)에만 쓴다.

14. **미국 주식수의 지연.** SEC 공시는 분기 단위이고 공시일까지 몇 주가 걸린다. 분기 중 대규모 자사주 매입을 한 기업은 다음 공시 전까지 시총이 약간 크게 계산된다.

## 13. 결정 기록

2026-09-15 확정. 판단 기준은 "섹터의 흐름을 보는 데 집중하고, 특이사항은 따로 본다"이다. 추가 데이터 확보나 예외 규칙보다 단순한 근사와 특이사항 기록을 택했다.

| # | 항목 | 결정 | 이유 |
| --- | --- | --- | --- |
| D-1 | KR 상장주식수 2025-01-02 ~ 2026-03-06 | 2026-03-09 주식수를 수정계수로 환산해 근사한다. API 키 백필은 하지 않는다 | 분할·병합은 맞게 환산된다. 빠지는 증자·소각은 반년 기준 한국 전체 −0.07%, 섹터별 최대 −3.87%였다 |
| D-2 | US 상장폐지 10종목 | 계산에서 제외하고 `DATA_GAP`으로 기록한다. 키 기반 공급자와 합병 연결 규칙은 두지 않는다 | S&P 500 대비 0.37%다. 에너지 3.68%와 인수사 주식수 급증은 특이사항으로 드러난다 |
| D-3 | KR 외국기업·인프라 펀드 | 모두 제외한다. 외국기업은 코드 `9` 규칙, 펀드는 수기 목록이다 | WI26 매핑이 없어 섹터 흐름에 넣을 수 없다. 합계 11.0조로 한국 전체의 0.18%다 |
| D-4 | 신규 테이블의 02 문서 반영 | 반영한다. `special_event`를 포함한다 | 특이사항도 분석 대상 데이터다 |
| D-5 | 편입되지 않은 주식 클래스 | 회사 합계 주식수를 편입 라인에 주식수 비율로 나눈다 | 식 하나로 Berkshire A주(회사 시총의 34.2%)와 Alphabet Class B(6.9%) 누락을 없애고, 클래스 전환이 흐름으로 보이지 않는다 |
| D-6 | FDR KRX 캐시 중단 | Daum 단독으로 운영한다 | 교차검증만 사라지고 계산에는 영향이 없다 |
| D-7 | SEC 주식수를 쓸 수 없는 US 83라인 (2026-09-16 추가) | yfinance 현재 주식수를 수정계수로 환산한 근사를 유지한다. `get_shares_full` 이력은 쓰지 않는다 | 섹터 비중 차이가 0.09%p 이하였다. `get_shares_full`은 오류 값이 있어 걸러내는 규칙이 더 필요하다 |

**후속 작업**

| 작업 | 대상 문서 |
| --- | --- |
| 특이사항 조회 API | [05](05-api-spec.md) |
| 특이사항 목록 화면 | [06](06-ui-spec.md) |

## 14. 초기 적재 결과 (2026-09-16)

`backfill`로 2025-01-01 이후 전 구간을 적재한 결과다. 두 시장 모두 차단 검증(C-1·C-4·C-9 ~ C-13)을 통과했다.

| 항목 | KR | US |
| --- | --- | --- |
| 종목·라인 | 2,604 (상장 2,532 + 폐지 72) | 535 (현재 503 + 편출 32) |
| 가격 행 | 1,046,019 (2025-01-02 ~ 2026-09-15) | 221,164 (2025-01-02 ~ 2026-09-14) |
| 백필 소요 | 67분 (요청 실패 0) | 5분 |
| 증분 수집(`daily`) 소요 | 17분 | 4분 |
| 기업행위 | 분할 56 · 병합 239 · 주식수 미반영 292 | 분할 12 · 병합 2 · 주식수 미반영 12 |
| 특이사항 | 836건 (주식수 급변 346, 기업행위 292, 신규상장 125, 폐지 72, 데이터 공백 1) | 138건 (주식수 급변 51, 편입 32, 편출 32, 기업행위 12, 데이터 공백 11) |
| 유니버스 편입 행 중 시총 없음 | 522 / 1,045,601 (0.05%) | 0 |

- KR 시총 없음 522행은 이미 폐지된 4종목의 원종가를 확정할 수 없는 구간이다([§7.2](#72-무수정-종가)). [03 §10](03-metrics-spec.md#10-예외-처리)의 `NO_BASE_PRICE`로 계산에서 빠진다.
- 분류 미매핑 시총 비중은 2025-01-02 1.37%, 2026-03-09 0.36%, 2026-09-15 0.13%다. 현재 분류 매핑만 쓰기 때문에([02 §9](02-domain-and-data-model.md#9-결정-기록) S-14) 그 사이 폐지·편출된 종목이 미매핑으로 잡힌다. 초기 구간은 V-8 경고(0.5%)에 걸린다.
- KR 액면병합이 239건으로 분할(56건)보다 많다. 07 §12 6항에서 본 2026년 병합 급증이 그대로 나타난다.
- C-2 경고 176건 중 상당수는 액면병합·감자 뒤 원종가가 크게 바뀌었는데 네이버 수정 종가에 아직 반영되지 않은 종목이다. 다음 실행에서 수정계수 변화로 잡히거나 경고로 남는다. 대부분 소형주다.
- 적재 중 발견해 고친 문제는 [§3.5](#35-구현-착수-시-재확인-2026-09-16), [§8.3](#83-주식수), [§11.1](#111-수집-단계-검증)에 규칙으로 반영했다.
- **KR 가격을 바깥 출처와 한 번 대조했다**(C-8의 일회성 예행, [§11.3](#113-c-8-표본-교차검증-앞으로-할-일)).
  WiseIndex 2026-09-11 스냅샷에서 `market_cap_mn_krw ÷ applied_shares`로 종가를 역산해 같은 날
  `price_daily.close_raw`와 맞췄다. 2,373종목 전부 대조됐고(우리 DB에 없는 종목 0), 편차는 중앙값
  0.0003% · p95 0.0026% · 최대 0.01%였다. 2%를 넘는 종목은 없다. 즉 이날의 KR 원종가는 독립 출처와
  일치한다. US는 아직 대조하지 않았다.
