# WI26 수집 데이터

한국 시장 섹터 분류로 사용할 **WI26(Wise Sector Classification)** 원천 데이터.

| 파일 | 내용 | 행 수 |
| --- | --- | --- |
| `wi26_classification.csv` | WI26 대분류 26개 / 소분류 48개 분류표 | 48 |
| `wi26_constituents.csv` | 대분류별 구성종목 (기준일 2026-09-11) | 2,373 |
| `wi26_wics_map.csv` | WI26 소분류 ↔ WICS 소분류 대응표 ([§7](#7-wi26_wics_mapcsv--wi26_sub_constituentscsv)) | 86 |
| `wi26_sub_constituents.csv` | 소분류별 구성종목 (`WI26_SUB` 스킴용, [§7](#7-wi26_wics_mapcsv--wi26_sub_constituentscsv)) | 2,373 |
| `raw/` | 수집 원본 (HTML 2건, PDF 1건, JSON 114건). 재파싱·감사용 | — |
| `gics_classification.csv` | GICS 4단계 분류표 + 소분류 정의문 (미국 시장용, [§5](#5-gics_classificationcsv)) | 163 |
| `gics_sp500_constituents.csv` | S&P 500 구성종목 → GICS 4단계 코드 매핑 ([§6](#6-gics_sp500_constituentscsv)) | 503 |
| `group_colors.csv` | 그룹별 차트 고정 색상 (산업 계열 8색, [02 §3.4](../docs/02-domain-and-data-model.md#34-classification_scheme--classification_group)). 세분류 스킴은 상위 섹터의 계열을 그대로 받는다 | 111 |
| `theme_radar.sqlite3` | 애플리케이션 DB (git 제외, `python -m theme_radar init-db`로 생성) | — |

수집 스크립트: [`scripts/fetch_wi26.py`](../scripts/fetch_wi26.py) — Python 3.9+ 표준 라이브러리만 사용한다.

```bash
python scripts/fetch_wi26.py                  # 데이터가 있는 최근 영업일 자동 탐색
python scripts/fetch_wi26.py --date 20260911  # 기준일자 지정
python scripts/fetch_wi26.py --no-raw         # 원본 캐시 저장 생략
```

> 인자 없이 실행하면 **최신 영업일 데이터로 CSV를 덮어쓴다.** 이 디렉터리의 검증 결과는 기준일 `2026-09-11` 기준이므로, 동일 스냅샷을 재현하려면 `--date 20260911`을 준다.

CSV는 모두 **UTF-8 with BOM**(Excel 호환), 모든 텍스트 필드는 큰따옴표로 감쌌다.
섹터명에 쉼표가 포함되므로(`상사,자본재`, `소매(유통)`, `포장재,종이와목재`) 반드시 CSV 파서로 읽어야 한다.

---

## 1. 출처

| 데이터 | 출처 |
| --- | --- |
| 분류표 | `https://www.wiseindex.com/About/WI26` (정적 HTML 표) |
| 구성종목 | `https://www.wiseindex.com/Index/GetIndexComponets?ceil_yn=0&dt={YYYYMMDD}&sec_cd={대분류코드}` |

구성종목 API는 `https://www.wiseindex.com/Index/Index#/WI100` 의 **Component** 탭이 내부적으로 호출하는 엔드포인트다. `/bundles/angular/index` 번들에서 확인했다.

| 파라미터 | 값 | 설명 |
| --- | --- | --- |
| `sec_cd` | `WI100` … `WI800` | WI26 대분류 코드 |
| `dt` | `YYYYMMDD` | 기준일자. **하이픈 형식은 오류 페이지로 리다이렉트된다** |
| `ceil_yn` | `0` | 비중 상한(cap) 미적용. `1`이면 캡 적용치 |

- API는 **해당 일자에 데이터가 있을 때만** 반환한다. 가장 가까운 과거 영업일로 자동 대체하지 않으며, 데이터가 없으면 `CNT = 0`, `TRD_DT = /Date(...)/`가 `3939-01-01`로 온다. 스크립트는 오늘부터 최대 15일을 역순 탐색해 데이터가 있는 날을 찾는다.
- 응답의 `TRD_DT`는 `/Date(epoch_ms)/` 형식이며 **KST 자정 기준** 타임스탬프다.

## 2. `wi26_classification.csv`

| 컬럼 | 예시 | 설명 |
| --- | --- | --- |
| `sector_code` | `WI200` | WI26 **대분류** 코드 (26종) |
| `sector_name` | `비철금속` | 대분류명 |
| `sub_sector_code` | `WI20010` | WI26 **소분류** 코드 (48종) |
| `sub_sector_name` | `포장재,종이와목재` | 소분류명 |

대분류가 소분류를 여러 개 갖는 경우 대분류 값이 행마다 반복된다(원본 HTML의 `rowspan`을 평탄화).

### 대분류 26개

| 코드 | 명칭 | 소분류 수 | 구성종목 |
| --- | --- | --- | --- |
| WI100 | 에너지 | 1 | 40 |
| WI110 | 화학 | 1 | 109 |
| WI200 | 비철금속 | 2 | 69 |
| WI210 | 철강 | 1 | 51 |
| WI220 | 건설 | 2 | 118 |
| WI230 | 기계 | 2 | 128 |
| WI240 | 조선 | 1 | 26 |
| WI250 | 상사,자본재 | 3 | 78 |
| WI260 | 운송 | 1 | 42 |
| WI300 | 자동차 | 2 | 148 |
| WI310 | 화장품,의류 | 4 | 146 |
| WI320 | 호텔,레저 | 1 | 19 |
| WI330 | 미디어,교육 | 2 | 95 |
| WI340 | 소매(유통) | 2 | 33 |
| WI400 | 필수소비재 | 3 | 98 |
| WI410 | 건강관리 | 3 | 349 |
| WI500 | 은행 | 2 | 41 |
| WI510 | 증권 | 1 | 26 |
| WI520 | 보험 | 1 | 13 |
| WI600 | 소프트웨어 | 2 | 216 |
| WI610 | IT하드웨어 | 4 | 207 |
| WI620 | 반도체 | 1 | 163 |
| WI630 | IT가전 | 2 | 71 |
| WI640 | 디스플레이 | 2 | 64 |
| WI700 | 통신서비스 | 1 | 5 |
| WI800 | 유틸리티 | 1 | 18 |

## 3. `wi26_constituents.csv`

| 컬럼 | 원본 필드 | 예시 | 설명 |
| --- | --- | --- | --- |
| `base_date` | `info.TRD_DT` | `2026-09-11` | 기준일자 (KST) |
| `sector_code` | `IDX_CD` | `WI620` | WI26 대분류 코드 |
| `sector_name` | `IDX_NM_KOR` | `반도체` | 대분류명 (`WI26 ` 접두어 제거) |
| `seq` | `SEQ` | `1` | 섹터 내 시총 순위 |
| `ticker` | `CMP_CD` | `005930` | 종목코드 6자리 |
| `company_name` | `CMP_KOR` | `삼성전자` | 종목명 |
| `market_cap_mn_krw` | `MKT_VAL` | `1137831974` | **유동시가총액, 백만원** (§4 참고) |
| `weight_pct` | `WGT` | `49.37` | 섹터 내 비중(%) |
| `cum_weight_pct` | `S_WGT` | `49.37` | 섹터 내 누적 비중(%) |
| `cap_factor` | `CAL_WGT` | `1` | 비중 상한 조정계수. `ceil_yn=0`이므로 전 종목 `1` |
| `applied_shares` | `APT_SHR_CNT` | `4384708956` | **적용주식수(유동주식수), 주** |
| `wics_sector_code` | `SEC_CD` | `G45` | WICS 대분류 코드 |
| `wics_sector_name` | `SEC_NM_KOR` | `IT` | WICS 대분류명 |
| `sector_market_cap_mn_krw` | `ALL_MKT_VAL` | `2304563589` | 섹터 전체 유동시총, 백만원 |

## 4. 검증 결과 및 주의사항

수집 직후 다음을 확인했다.

| 검사 | 결과 |
| --- | --- |
| 총 행 수 | 2,373 |
| 고유 티커 수 | 2,373 |
| **티커 중복(배타성 위반)** | **0건** — 한 종목은 정확히 하나의 대분류에 속한다 |
| 섹터별 `weight_pct` 합계 = 100 (±0.5%p) | 26개 섹터 전부 통과 |
| `cap_factor` | 전 종목 `1` (캡 미적용) |

### 4.1 `market_cap_mn_krw`는 유동시가총액이다 — 중요

`market_cap_mn_krw ÷ applied_shares`로 역산한 주가가 실제 종가와 일치한다.

| 종목 | 유동시총(백만원) | 적용주식수 | 역산 주가 |
| --- | --- | --- | --- |
| 삼성전자 | 1,137,831,974 | 4,384,708,956 | 259,500 |
| SK하이닉스 | 979,502,602 | 540,564,350 | 1,812,000 |
| 현대차 | 50,907,899 | 133,092,547 | 382,500 |
| NAVER | 25,754,139 | 124,717,382 | 206,500 |

즉 단위는 **백만원 / 주**로 확정된다. 다만 삼성전자의 `applied_shares` 4,384,708,956주는 총 상장주식수(약 59.7억 주)보다 작다. **`MKT_VAL`은 유동비율이 반영된 유동시가총액(float-adjusted)** 이며 전체 시가총액이 아니다.

> **설계 문서와의 차이**: [03-metrics-spec.md §3.2](../docs/03-metrics-spec.md#3-가중치)는 가중치를 **전체 시가총액(full market cap)** 기준으로 정의한다. 이 파일의 `market_cap_mn_krw`를 그대로 가중치에 쓰면 유동시총 가중이 되어 명세와 불일치한다. 섹터 **매핑**(`ticker` → `sector_code`)만 이 파일에서 취하고, 가중치용 시가총액은 자체 `price_daily.market_cap`(무수정 종가 × 상장주식수)에서 산출하는 것을 전제로 한다.

### 4.2 스냅샷이지 이력이 아니다

이 파일은 **기준일 하루의 구성**이다. [02-domain-and-data-model.md](../docs/02-domain-and-data-model.md)의 `security_group_map`은 `valid_from`/`valid_to` 이력 테이블이므로, 과거 구간을 정확히 계산하려면 과거 일자별로 반복 수집해 유효기간을 구성해야 한다. 단일 스냅샷만 적재하면 **섹터 변경 이력이 소실되어 과거 기간이 현재 분류로 소급 계산**된다.

### 4.3 유니버스 범위가 설계와 다르다

WI26 구성종목 2,373개는 WiseIndex 지수 편입 기준을 따른 것으로, [01-requirements.md §3.1](../docs/01-requirements.md#31-kr_common)이 정의한 `KR_COMMON`(KOSPI/KOSDAQ 전체 보통주)과 정확히 같은 집합이라고 보장할 수 없다. 적재 시 `KR_COMMON`과 대조해 **미매핑 종목 비율**(`unmapped_cap_ratio`)을 반드시 측정한다.

### 4.4 WICS와의 관계

WI26은 WICS 10개 대분류를 26개로 세분한 체계다. 수집 데이터에서 확인된 분해 관계:

| WICS | → WI26 대분류 |
| --- | --- |
| G15 소재 | WI110 화학, WI200 비철금속, WI210 철강 |
| G20 산업재 | WI220 건설, WI230 기계, WI240 조선, WI250 상사·자본재, WI260 운송 |
| G25 경기관련소비재 | WI300 자동차, WI310 화장품·의류, WI320 호텔·레저, WI330 미디어·교육, WI340 소매(유통) |
| G40 금융 | WI500 은행, WI510 증권, WI520 보험 |
| G45 IT | WI600 소프트웨어, WI610 IT하드웨어, WI620 반도체, WI630 IT가전, WI640 디스플레이 |
| G50 커뮤니케이션서비스 | WI330 미디어·교육, WI600 소프트웨어(게임), WI700 통신서비스 |

G50이 세 대분류로 흩어지는 점에 유의한다. WICS 섹터에서 WI26을 기계적으로 유도할 수 없으므로, **매핑은 종목 단위로만 유효**하다. 상세 기준은 출처 페이지의 첨부 PDF(`WI26-WICS섹터매핑.pdf`)를 따른다.

### 4.5 기타

- 통신서비스(WI700)는 5종목뿐이다. 섹터 수익률이 사실상 1~2종목에 의해 결정되므로 집중도 지표가 상시 극단값을 보인다.
- 건강관리(WI410) 349종목, IT(G45 계열) 721종목으로 종목 수 편차가 크다. 동일가중 수익률 비교 시 감안한다.
- 우선주는 포함되지 않는다. 2,373개 티커 중 우선주 번호 체계(끝자리 5·7·9)에 해당하는 종목이 0건이다. `KR_COMMON`의 보통주 한정 조건과 부합한다.

---

## 5. `gics_classification.csv`

미국 시장 섹터 분류로 사용할 **GICS** 분류표. 형식(UTF-8 with BOM, 텍스트 필드 따옴표)은 WI26 CSV와 같다.

| 항목 | 내용 |
| --- | --- |
| 출처 | [`references/1_MSCI_Global_Industry_Classification_Standard_GICS_Methodology_20240801.pdf`](../references/1_MSCI_Global_Industry_Classification_Standard_GICS_Methodology_20240801.pdf) (MSCI, 2024-08판) |
| 생성 스크립트 | [`scripts/parse_gics.py`](../scripts/parse_gics.py) — 표준 라이브러리만으로 PDF를 파싱한다 |
| 구조 | 1.2절 *The GICS Structure* (p.6–12) |
| 정의문 | 7절 *GICS Sub-Industry Definitions* (p.20–54) |

```bash
python scripts/parse_gics.py
```

| 컬럼 | 예시 | 설명 |
| --- | --- | --- |
| `sector_code` | `45` | 섹터 코드 (2자리, 11종) |
| `sector_name` | `Information Technology` | 섹터명 |
| `industry_group_code` | `4530` | 산업그룹 코드 (4자리, 25종) |
| `industry_group_name` | `Semiconductors & Semiconductor Equipment` | 산업그룹명 |
| `industry_code` | `453010` | 산업 코드 (6자리, 74종) |
| `industry_name` | `Semiconductors & Semiconductor Equipment` | 산업명 |
| `sub_industry_code` | `45301020` | 소분류 코드 (8자리, 163종) |
| `sub_industry_name` | `Semiconductors` | 소분류명 |
| `sub_industry_definition` | `Manufacturers of semiconductors ...` | 소분류 정의문 (원문 영어, 문단은 공백으로 연결) |

소분류 1행에 상위 3단계가 반복된다. 코드는 상위 코드를 접두어로 포함하므로(`45301020` → `453010` → `4530` → `45`) 문자열로 다룬다.

### 5.1 검증 결과

스크립트가 실행 시 아래를 검사하고, 하나라도 어긋나면 CSV를 쓰지 않고 실패한다.

| 검사 | 결과 |
| --- | --- |
| 계층별 개수 = 문서 1.1절 기재값 (11 / 25 / 74 / 163) | 통과 |
| 하위 코드가 상위 코드를 접두어로 포함 | 통과 |
| 소분류 코드 중복 | 0건 |
| 1.2절 구조표 소분류 ↔ 7절 정의표 소분류 코드 1:1 대응 | 통과 |
| 두 표의 소분류명 일치 (공백 무시) | 163건 전부 일치 |

### 5.2 주의사항

- **코드는 연속적이지 않다.** 구조 개편으로 폐지된 코드가 비어 있다(예: `20201010` 다음이 `20201050`, 산업코드 `451010` 없음). 코드를 산술 연산으로 순회하지 않는다.
- **명칭만 있고 한국어명은 없다.** 원문이 영어이므로 번역을 넣지 않았다. UI용 한국어 섹터명이 필요하면 별도 매핑을 둔다.
- 7절 표에서 REIT 관련 소분류명에 붙은 `*`는 REIT 판정 기준 각주 표시이므로 제거했다. 각주 내용: 공식 문서에 REIT로 운영된다고 명시하고, 부동산·모기지 대출 중심의 신탁 구조에 과세소득 대부분을 배당하는 회사를 REIT로 분류한다(스테이플드 구조는 매출 60% 이상이 신탁에서 발생해야 함).
- `group_code`에는 이 파일의 **공식 숫자 코드**(`45` 등)를 그대로 쓴다 ([02 §7](../docs/02-domain-and-data-model.md#7-코드-체계-참고)). 문자열로 다루며 정수로 변환하지 않는다.
- 이 파일은 분류 **체계**일 뿐 종목 매핑이 아니다. 미국 종목 → GICS 매핑은 [§6](#6-gics_sp500_constituentscsv)을 본다.

---

## 6. `gics_sp500_constituents.csv`

S&P 500 구성종목별 GICS 섹터 / 산업그룹 / 산업 / 소분류 코드. 형식은 위 CSV들과 같다(UTF-8 with BOM, 텍스트 필드 따옴표).

```bash
python scripts/fetch_gics_sp500.py           # 인자 없이 실행하면 최신 데이터로 덮어쓴다
python scripts/fetch_gics_sp500.py --no-raw  # 원본 캐시 저장 생략
```

### 6.1 출처와 선정 이유

GICS 종목 분류는 S&P/MSCI의 유료 데이터라 **공식 무료 원천이 없다.** 그래서 종목별 소분류명은 공개 목록에서 얻고, 코드는 공식 분류표로 부여하고, 섹터는 S&P 지수 기반 ETF 보유종목으로 독립 검증하는 방식을 택했다.

| 역할 | 출처 | 기준 |
| --- | --- | --- |
| 종목별 GICS 섹터·소분류**명** | [Wikipedia *List of S&P 500 companies*](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies) `id="constituents"` 표 | revision `1373104626` (2026-09-04 02:14 UTC 편집) |
| 명칭 → 4단계 **코드** | `gics_classification.csv` (MSCI 2024-08판) | 소분류명 완전일치 |
| 유니버스 검증 | State Street **SPY** 일별 보유종목 xlsx | 2026-09-11 |
| 섹터 검증 | Select Sector SPDR 11종(`XLE XLB XLI XLY XLP XLV XLF XLK XLC XLU XLRE`) 일별 보유종목 xlsx — 각 ETF가 S&P 500 중 해당 GICS 섹터 종목만 편입 | 2026-09-11 |

- 보유종목 URL: `https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{etf}.xlsx` (etf는 소문자). xlsx의 `Sector` 컬럼은 전부 `-`라 쓸 수 없고, **어느 섹터 ETF에 편입됐는가**로 섹터를 판정한다.
- 원본은 `raw/wiki_sp500.html`, `raw/ssga_holdings_{etf}.xlsx`로 저장한다.
- S&P 400 / 600 위키 목록도 검토했으나 제외했다. 폐지된 소분류명(`Specialty Stores`, 7건)과 섹터-소분류 모순(예: `ALRM` Financials / Application Software, 8건)이 있고, 섹터 검증용 ETF도 없다.

### 6.2 컬럼

| 컬럼 | 예시 | 설명 |
| --- | --- | --- |
| `ticker` | `BRK.B` | 심볼. 클래스주는 **점(.)** 표기(`BRK.B`, `BF.B`) — 위키·SSGA 동일 |
| `company_name` | `Berkshire Hathaway` | 위키 표기 회사명 |
| `sector_code` / `sector_name` | `40` / `Financials` | 섹터 (2자리) |
| `industry_group_code` / `industry_group_name` | `4020` / `Financial Services` | 산업그룹 (4자리) |
| `industry_code` / `industry_name` | `402010` / `Financial Services` | 산업 (6자리) |
| `sub_industry_code` / `sub_industry_name` | `40201030` / `Multi-Sector Holdings` | 소분류 (8자리) |
| `cik` | `0001067983` | SEC CIK (10자리, 선행 0 유지) |
| `date_added` | `2010-02-16` | S&P 500 편입일 (위키) |
| `in_spy` | `Y` | SPY 보유종목 포함 여부 |
| `etf_sector_check` | `OK` | 섹터 ETF 편입 기준 섹터 일치 여부. `OK` / `MISMATCH:{ETF 섹터코드}` / `NOT_FOUND` |

행 정렬은 `sub_industry_code`, `ticker` 순이다. 코드는 모두 문자열이다.

### 6.3 검증 결과

| 검사 | 결과 |
| --- | --- |
| 위키 소분류명이 공식 분류표 163개 중 하나 | 503/503 (불일치 시 스크립트가 CSV를 쓰지 않고 실패) |
| 위키 섹터 = 소분류의 상위 섹터 | 503/503 |
| 티커 중복 | 0건 |
| 위키 구성종목 집합 = SPY 보유종목 집합 | 완전 일치 (503 = 503) |
| **섹터 ETF 편입 섹터 = 매핑 섹터** | **503/503 `OK`** |
| 계층별 등장 개수 | 섹터 11 / 산업그룹 25 / 산업 69 / 소분류 127 |

**산업·소분류 레벨 스팟체크 (1회성, 스크립트 미포함).** GICS 소분류로 편입 규칙을 두는 S&P Select Industry SPDR 17종(`XSD XBI XPH KRE KBE KIE XAR XOP XES XHE XHS XSW XTL XME XHB XRT XTN`)의 S&P 500 편입종목을 대조했다. 대부분 지수 정의와 부합했고, 아래 5건은 지수 정의와 어긋나 보여 **확인이 필요하다.** ETF는 분기 리밸런싱이라 분류 변경이 늦게 반영될 수 있고, 일부 지수는 인접 소분류를 함께 편입하므로 위키 오류로 단정하지 않았다.

| 티커 | 위키 소분류 | 편입된 ETF (지수 대상) |
| --- | --- | --- |
| `TFC` | Diversified Banks | KRE (Regional Banks) |
| `ROP` | Electronic Equipment & Instruments | XSW (Software & Services) |
| `APP` | Advertising | XSW (Software & Services) |
| `SOLV` | Health Care Technology | XHE (Health Care Equipment & Supplies) |
| `CMCSA` | Cable & Satellite | XTL (Telecom) |

### 6.4 산업 레벨(`GICS_IND`)로 집계할 때의 특이사항

`industry_code`(6자리)를 집계 단위로 쓰는 `GICS_IND` 스킴이 이 파일의 같은 행을 읽는다([02 §9](../docs/02-domain-and-data-model.md#9-결정-기록) S-2).
74개 산업 중 **S&P 500에 등장하는 것은 69개**다. 아래는 보정하지 않고 특이사항으로 남겨 따로 본다.

**1종목뿐인 산업 7개.** 집중도 지표(`hhi`, `top1_contrib_share`)가 상시 극단값이고, 산업 수익률이 곧 그 종목의 수익률이다.

| 산업 코드 | 산업명 | 종목 |
| --- | --- | --- |
| `251010` | Automobile Components | APTV |
| `252020` | Leisure Products | HAS |
| `253020` | Diversified Consumer Services | DASH |
| `255010` | Distributors | GPC |
| `551020` | Gas Utilities | ATO |
| `551040` | Water Utilities | AWK |
| `601025` | Industrial REITs | PLD |

**[§6.3](#63-검증-결과)의 스팟체크 5건 중 4건이 산업 레벨에서 갈린다.** 소분류가 바뀌면 상위 산업도 바뀌는 건들이라, 섹터 레벨에서는 없던 영향이 생긴다.

| 티커 | 현재 산업 | ETF 편입이 시사하는 산업 |
| --- | --- | --- |
| `ROP` | `452030` Electronic Equipment, Instruments & Components | `451030` Software |
| `APP` | `502010` Media | `451030` Software |
| `SOLV` | `351030` Health Care Technology | `351010` Health Care Equipment & Supplies |
| `CMCSA` | `502010` Media | `501010` Diversified Telecommunication Services |

`TFC`는 Diversified Banks · Regional Banks 어느 쪽이어도 `401010 Banks`라 산업 레벨에서는 차이가 없다.

### 6.5 주의사항

- **섹터(2자리)는 독립 출처로 전수 검증됐지만, 산업그룹·산업·소분류는 위키 1개 출처에 의존한다.** 섹터 레벨(`GICS`)에는 충분하나, 산업 레벨(`GICS_IND`)은 이 단일 출처 위에 서 있다([§6.4](#64-산업-레벨gics_ind로-집계할-때의-특이사항)).
- **스냅샷이지 이력이 아니다.** [§4.2](#42-스냅샷이지-이력이-아니다)와 같은 문제가 있다. 과거 구성·분류 변경을 반영하려면 위키 편집 이력(리비전)이나 반복 수집으로 `valid_from`/`valid_to`를 구성해야 한다.
- 503행은 500개 회사다. 복수 클래스 3쌍(`GOOG`/`GOOGL`, `FOX`/`FOXA`, `NWS`/`NWSA`)이 같은 CIK·같은 분류로 각각 들어 있다. 시가총액 가중 시 두 클래스를 모두 합산해야 회사 시총이 된다.
- 위키 `constituents` 표의 헤더·id가 바뀌면 스크립트는 명시적으로 실패한다. 공식 분류표가 개정되면(GICS 구조 변경) `parse_gics.py`로 `gics_classification.csv`를 먼저 갱신해야 명칭 매칭이 유지된다.

---

## 7. `wi26_wics_map.csv` / `wi26_sub_constituents.csv`

한국 시장을 **WI26 소분류 48개**로 집계하기 위한 종목 매핑(`WI26_SUB` 스킴). 형식은 위 CSV들과 같다.

```bash
python scripts/fetch_wi26_sub.py           # 검증을 통과해야 CSV 두 개를 쓴다
python scripts/fetch_wi26_sub.py --no-raw  # 원본 캐시 저장 생략
```

### 7.1 왜 두 단계를 거치는가

WiseIndex 구성종목 API는 **소분류를 받지 않는다.** 소분류는 지수가 아니라 분류 단계라서 Component 탭이 없고,
`sec_cd=WI11010`으로 물으면 `CNT = 0`이 온다(WICS 중분류 `G4530`은 정상 응답한다). 그래서 두 조각을 잇는다.

| 조각 | 출처 | 얻는 것 |
| --- | --- | --- |
| WI26 소분류 ↔ WICS 소분류 | `https://www.wiseindex.com/konannas/files/WI26-WICS섹터매핑.pdf` (2022-04판) | 48 ↔ 81 대응표 |
| 종목 ↔ WICS 소분류 | `https://m.stock.naver.com/api/stocks/industry` (목록) · `.../industry/{no}` (구성종목) | 네이버 **업종 = WICS 소분류**다 |

네이버 업종 79개 중 78개의 이름이 현행 WICS 소분류명과 일치한다(공백 표기만 다르다: `디스플레이 패널` / `디스플레이패널`).
나머지 하나는 ETN·ETF를 담는 `기타`(1,538종목)로, 분류 체계의 섹터가 아니므로 제외한다. 스크립트는 해석되지 않는
업종이 `기타` 외에 하나라도 있으면 실패한다.

### 7.2 짐작이 아니다 — 대분류 전수 대조

파생한 소분류의 **상위 대분류**가 [`wi26_constituents.csv`](#3-wi26_constituentscsv)의 대분류와 같아야 한다.
이 대분류는 WiseIndex에서 직접 받은 독립 값이므로, 2,373건 대조는 진짜 검증이다.

| 검사 | 결과 |
| --- | --- |
| PDF가 읽어 낸 소분류 48개 = `wi26_classification.csv` (코드·이름·상위 대분류) | 통과 |
| 네이버에서 한 종목이 두 업종에 속함 | 0건 |
| 소분류를 붙인 종목 | 2,373 / 2,373 |
| **파생 대분류 ≠ 원래 대분류** | **0건** |
| 소분류 48개 전부 구성종목 있음 | 통과 |

하나라도 어긋나면 스크립트는 CSV를 쓰지 않고 실패한다.

### 7.3 `source` 컬럼 — 예외를 숨기지 않는다

| 값 | 종목 | 뜻 |
| --- | --- | --- |
| `PDF` | 2,253 | 맵핑 PDF가 그대로 덮는다 |
| `BRIDGE:{옛 코드}` | 119 | PDF(2022-04) **이후 신설된 WICS 소분류**. 성격이 같은 옛 소분류를 거쳐 이었다 |
| `SECTOR_ONLY` | 1 | 네이버 업종이 없는 종목(`006380` 카프로). 대분류 `WI110 화학`의 소분류가 하나뿐이라 소분류가 정해진다 |

`BRIDGE`는 스크립트 상단 `WICS_BRIDGE`에 적혀 있다. 커뮤니케이션서비스 신설로 옮겨 간 것들이다.

| 신설 코드 | 이름 | ← 옛 코드 | → WI26 소분류 | 종목 |
| --- | --- | --- | --- | --- |
| `G502010` | 광고 | `G254010` 광고 | `WI33010` 미디어 | 18 |
| `G502020` | 방송과엔터테인먼트 | `G254020` 방송과엔터테인먼트 | `WI33010` 미디어 | 50 |
| `G502030` | 출판 | `G254030` 출판 | `WI33010` 미디어 | 7 |
| `G502040` | 게임엔터테인먼트 | `G451035` 게임소프트웨어와서비스 | `WI60020` 게임소프트웨어 | 29 |
| `G502050` | 양방향미디어와서비스 | `G451010` 인터넷소프트웨어와서비스 | `WI60010` 소프트웨어 | 15 |

**브리지도 대분류 대조를 그대로 통과한다** — 119종목 중 `wi26_constituents.csv`에 있는 전부가 일치했다. 즉 브리지는
짐작이 아니라 독립 출처로 확인된 연결이다. PDF도 브리지도 덮지 못하는 WICS 코드가 나오면(추가 개편) 스크립트는
실패하며, 그때 `WICS_BRIDGE`를 갱신한다.

### 7.4 컬럼

`wi26_wics_map.csv` (86행 = PDF 81 + 브리지 5)

| 컬럼 | 예시 | 설명 |
| --- | --- | --- |
| `wics_sub_code` / `wics_sub_name` | `G453010` / `반도체와반도체장비` | WICS 소분류 (현행 코드) |
| `sector_code` / `sub_sector_code` / `sub_sector_name` | `WI620` / `WI62010` / `반도체` | 대응하는 WI26 대분류·소분류 |
| `source` | `PDF` · `BRIDGE:G254010` | [§7.3](#73-source-컬럼--예외를-숨기지-않는다) |

`wi26_sub_constituents.csv` (2,373행). `wi26_constituents.csv`와 **같은 티커 집합**이라 `WI26`과 `WI26_SUB`의
유니버스 커버리지가 같다.

| 컬럼 | 예시 | 설명 |
| --- | --- | --- |
| `base_date` | `2026-09-21` | 수집일(KST). 네이버 업종 API에는 기준일자 파라미터가 없다 |
| `ticker` / `company_name` | `005930` / `삼성전자` | `wi26_constituents.csv` 표기 |
| `sector_code` / `sector_name` | `WI620` / `반도체` | 원래 대분류 (대조에 쓴 값) |
| `sub_sector_code` / `sub_sector_name` | `WI62010` / `반도체` | **파생한 소분류** |
| `wics_sub_code` / `wics_sub_name` | `G453010` / `반도체와반도체장비` | 경유한 WICS 소분류. `SECTOR_ONLY`는 빈 값 |
| `naver_industry_no` | `278` | 네이버 업종 번호 |
| `source` | `PDF` | [§7.3](#73-source-컬럼--예외를-숨기지-않는다) |

### 7.5 특이사항

- **종목 수가 적은 소분류.** 집중도 지표가 상시 극단값이다. 보정하지 않고 그대로 본다.

  | 소분류 | 종목 |
  | --- | --- |
  | `WI40020` 필수소비재/담배 | 1 |
  | `WI64010` 디스플레이/디스플레이패널 | 4 |
  | `WI70010` 통신서비스/통신서비스 | 5 |
  | `WI25020` 상사,자본재/상사 | 6 |
  | `WI40030` 필수소비재/가정용품 | 6 |
  | `WI30010` 자동차/자동차 | 7 |

  반대쪽 끝은 `WI60010` 소프트웨어 187 · `WI62010` 반도체 163 · `WI41010` 제약 148종목이다.
- **`base_date`는 수집일이지 시장 기준일이 아니다.** 네이버 업종 API는 현재 구성만 준다. `wi26_constituents.csv`의
  `base_date`(WiseIndex 기준일)와 며칠 어긋날 수 있다.
- **스냅샷이지 이력이 아니다** ([§4.2](#42-스냅샷이지-이력이-아니다)). 소분류는 대분류보다 변경이 잦아 소급 계산 오차가 더 크다.
- **맵핑 PDF는 2022-04판이고 WICS는 그 뒤로 개편됐다.** PDF에만 있는 코드 7개(신용평가서비스·결제관련서비스 포함)는
  현행 WICS에 없고, 현행에만 있는 코드 5개는 [§7.3](#73-source-컬럼--예외를-숨기지-않는다)의 브리지가 덮는다.
  WiseIndex가 PDF를 갱신하면 브리지를 지울 수 있다.
