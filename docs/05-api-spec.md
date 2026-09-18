# 05. API 명세

Base URL: `/api/v1`
인코딩: UTF-8 · 응답: `application/json` · 메서드: 전부 `GET`(읽기 전용 서비스)

## 1. 공통 규약

### 1.1 공통 쿼리 파라미터

| 파라미터 | 타입 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- | --- |
| `universe` | string | Y | — | `KR_COMMON` \| `US_SP500` |
| `scheme` | string | Y | — | `WI26` \| `GICS` \| `THEME_*` |
| `period` | string | N | `W` | `W`(ISO Week) \| `M`(Month) |
| `from` | string | N | `to` 기준 52기간 전 | 시작 기간 식별자 (`2025-W01`, `2025-01`) |
| `to` | string | N | 최신 기간 | 종료 기간 식별자 |
| `lang` | string | N | `ko` | `ko` \| `en` — 그룹/종목 명칭 언어 |

- 구간은 **양끝 포함**이다.
- `from`/`to`는 `period_seq`로 변환해 조회한다. 존재하지 않는 식별자는 400.
- 최대 조회 구간: `W` 260기간(5년), `M` 120기간(10년). 초과 시 400.

### 1.2 공통 응답 봉투

```json
{
  "meta": {
    "universe": "KR_COMMON",
    "scheme": "WI26",
    "period": "W",
    "from": "2025-W38",
    "to": "2026-W37",
    "currency": "KRW",
    "calc_version": "1.2.0",
    "calculated_at": "2026-09-14T09:31:22Z",
    "stale": false
  },
  "data": { }
}
```

| 필드 | 설명 |
| --- | --- |
| `calc_version` | 응답에 포함된 값들의 계산 로직 버전 |
| `stale` | 재계산 진행 중이어서 값이 갱신될 수 있음 |

### 1.3 오류 응답

```json
{
  "error": {
    "code": "INVALID_PERIOD_ID",
    "message": "period_id '2026-W60' is not a valid ISO week identifier",
    "field": "from"
  }
}
```

| HTTP | `code` | 상황 |
| --- | --- | --- |
| 400 | `INVALID_PARAMETER` | 파라미터 형식 오류 |
| 400 | `INVALID_PERIOD_ID` | 존재하지 않는 기간 식별자 |
| 400 | `RANGE_TOO_LARGE` | 조회 구간 상한 초과 |
| 400 | `SCHEME_MARKET_MISMATCH` | 유니버스와 스킴의 시장이 불일치 (예: `US_SP500` + `WI26`) |
| 404 | `NOT_FOUND` | 존재하지 않는 그룹/종목 |
| 409 | `NOT_AVAILABLE` | 해당 기간 계산 미완료 또는 검증 실패로 미배포 |
| 500 | `INTERNAL_ERROR` | 서버 오류 |

### 1.4 수치 표기

- 수익률·기여도: **소수**(0.0123 = +1.23%), 소수점 8자리 반올림
- 비중: 소수, 10자리 반올림
- 백분율/bp 변환은 클라이언트 책임

## 2. 메타 API

### 2.1 `GET /meta/universes`

```json
{
  "data": [
    { "universe": "KR_COMMON", "name": "국내 보통주(KOSPI/KOSDAQ)", "market": "KR", "currency": "KRW" },
    { "universe": "US_SP500",  "name": "S&P 500 구성종목",          "market": "US", "currency": "USD" }
  ]
}
```

### 2.2 `GET /meta/schemes?universe=KR_COMMON`

```json
{
  "data": [
    { "scheme": "WI26", "name": "WI26 산업분류", "type": "SECTOR", "exclusive": true,  "group_count": 26 },
    { "scheme": "THEME_AI", "name": "AI 테마", "type": "THEME", "exclusive": false, "group_count": 1 }
  ]
}
```

- `exclusive = false`인 스킴은 기여도 분해 API에서 `contribution` 필드가 `null`이다.

### 2.3 `GET /meta/groups?universe=&scheme=`

```json
{
  "data": [
    { "group_code": "WI620", "name": "반도체", "name_en": "Semiconductors", "color": "#3B6FD4", "sort_order": 21 }
  ]
}
```

- `color`는 차트 색상 고정을 위해 서버가 제공한다. 클라이언트가 순위 순으로 색을 배정하지 않는다.

### 2.4 `GET /meta/periods?universe=&period=W&from=&to=&last=`

```json
{
  "data": [
    {
      "period_id": "2026-W36",
      "period_seq": 3142,
      "cal_start": "2026-08-31",
      "cal_end": "2026-09-06",
      "base_date": "2026-08-28",
      "end_date": "2026-09-04",
      "trading_days": 5,
      "is_closed": true
    }
  ]
}
```

- `last=N`은 `to`(기본 최신 기간)에서 거슬러 N기간을 준다. 화면의 "최근 N기간" 컨트롤이 범프 차트의 `from`/`to`를 정하는 데 쓴다. 상한은 §1.1과 같다.

## 3. 범프 차트 API (메인)

### 3.1 `GET /sectors/ranks`

메인 화면의 단일 호출 엔드포인트. 순위 시계열 + 각 점의 수익률/기여도를 함께 반환한다.

**추가 파라미터**

| 파라미터 | 기본값 | 설명 |
| --- | --- | --- |
| `rank_by` | `return` | `return` \| `contribution` — 순위 기준 |
| `top_n` | `0`(전체) | 조회 구간 내 최고 순위가 N 이내인 그룹만 반환. 나머지는 `others_count`로 집계 |

**응답**

```json
{
  "meta": { "universe": "KR_COMMON", "scheme": "WI26", "period": "W",
            "from": "2026-W24", "to": "2026-W36", "rank_by": "return",
            "group_count": 26, "calc_version": "1.2.0", "stale": false },
  "data": {
    "periods": [
      { "period_id": "2026-W35", "end_date": "2026-08-28", "is_provisional": false,
        "universe_return": -0.0132, "group_count": 26 },
      { "period_id": "2026-W36", "end_date": "2026-09-04", "is_provisional": true,
        "universe_return":  0.0241, "group_count": 26 }
    ],
    "series": [
      {
        "group_code": "WI620",
        "name": "반도체",
        "color": "#3B6FD4",
        "points": [
          { "period_id": "2026-W35", "rank": 4, "rank_delta": -2,
            "return": 0.0088, "base_weight": 0.2731, "contribution": 0.0024,
            "top1_contrib_share": 0.62, "member_cnt": 118 },
          { "period_id": "2026-W36", "rank": 1, "rank_delta": 3,
            "return": 0.0615, "base_weight": 0.2748, "contribution": 0.0169,
            "top1_contrib_share": 0.71, "member_cnt": 118 }
        ]
      }
    ]
  }
}
```

**규약**

- `series[].points`는 `periods` 배열과 **같은 순서**로 정렬된다. 그룹이 해당 기간에 존재하지 않으면 그 `period_id`의 점을 **생략**한다(널 점을 넣지 않는다). 클라이언트는 결측 구간에서 선을 끊는다.
- `rank_delta`는 직전 기간이 없으면 `null`.
- `is_provisional = true`인 기간은 차트에서 점선으로 표시한다.
- 비배타 스킴에서는 `base_weight`, `contribution`이 `null`.

### 3.2 `GET /sectors/returns`

단일 기간의 섹터 전체 스냅샷(막대/표용).

**추가 파라미터**: `period_id` (필수), `sort` (`return` \| `contribution` \| `weight`, 기본 `return`)

```json
{
  "meta": { "period_id": "2026-W36", "is_provisional": true, "universe_return": 0.0241 },
  "data": [
    {
      "group_code": "WI620", "name": "반도체", "color": "#3B6FD4",
      "return": 0.0615, "return_equal": 0.0312, "return_median": 0.0205,
      "base_weight": 0.2748, "contribution": 0.0169, "contribution_share": 0.7012,
      "rank": 1, "rank_prev": 4, "rank_delta": 3,
      "member_cnt": 118, "up_cnt": 79, "up_ratio": 0.6695,
      "hhi": 0.4812, "effective_n": 2.08,
      "top1_contrib_share": 0.71, "top3_contrib_share": 0.88, "top5_contrib_share": 0.93,
      "cap_weight_spread": 0.0303,
      "badges": ["1종목 주도", "대형주 주도"]
    }
  ]
}
```

- `contribution_share`는 `|universe_return| < 0.0005`일 때 `null` ([03 §7.3](03-metrics-spec.md#73-기여-비중)).
- `badges` 판정 임계값은 `config.toml`의 `[badges]`다 ([03 §9.5](03-metrics-spec.md#95-판정-가이드-ui-배지용)).
- `period_id`를 생략하면 가장 최근 계산 기간이다.
- `UNMAPPED` 그룹이 존재하면 목록 마지막에 포함된다(`group_code = "UNMAPPED"`).

### 3.3 `GET /sectors/market-caps`

섹터 전체의 **기간 말 시가총액**. 화면은 이 값으로 이동평균과 그 상승률·순위·이격도를 만든다([03 §14.3~14.4](03-metrics-spec.md#143-이동평균)). 단일 섹터의 시가·고가·저가·종가는 [§5.3](#53-get-sectorsgroup_codemarket-cap)이다.

```json
{
  "meta": { "universe": "KR_COMMON", "scheme": "WI26", "period": "W", "currency": "KRW",
            "from": "2026-W30", "to": "2026-W38", "group_count": 26, "calc_version": "1.2.0", "stale": false },
  "data": [
    { "group_code": "WI620", "name": "반도체", "color": "#3B6FD4",
      "points": [
        { "period_id": "2026-W37", "close": 2977591039595888, "member_cnt": 163 },
        { "period_id": "2026-W38", "close": 3011884550219315, "member_cnt": 163 }
      ] }
  ]
}
```

- `close`는 [§5.3](#53-get-sectorsgroup_codemarket-cap)의 `close`와 같은 값이다(기간 마지막 거래일의 섹터 시총, 시장 통화 단위 정수).
- 그룹이 없는 기간은 점을 **생략**한다. 순위를 갖지 않는 `UNMAPPED`는 아예 싣지 않는다.
- 이동평균은 주지 않는다. 화면이 계산하며, 구간 앞 기간이 필요하면 `from`을 앞당겨 받는다([06 §3.5](06-ui-spec.md#35-이동평균-기준)).
- 섹터 일별 시총을 아직 한 번도 쓰지 않았으면 409 `NOT_AVAILABLE`다.

## 4. 시장 요약 API

### 4.1 `GET /market/summary?universe=&period=&period_id=`

```json
{
  "data": {
    "period_id": "2026-W36",
    "base_date": "2026-08-28",
    "end_date": "2026-09-04",
    "is_provisional": true,
    "universe_return": 0.0241,
    "universe_return_equal": 0.0087,
    "universe_return_median": 0.0051,
    "member_cnt": 2384,
    "up_cnt": 1290,
    "up_ratio": 0.5411,
    "unmapped_cap_ratio": 0.0012,
    "top_contributors": [
      { "group_code": "WI620", "name": "반도체", "contribution": 0.0169 },
      { "group_code": "WI500", "name": "은행", "contribution": 0.0031 }
    ],
    "bottom_contributors": [
      { "group_code": "WI110", "name": "화학", "contribution": -0.0022 }
    ]
  }
}
```

- `universe_return`은 자체 산출값이며 공식 지수 수익률이 아니다. 응답에 `"disclaimer"` 문자열을 포함해 UI에 그대로 노출한다.

## 5. 드릴다운 API

### 5.1 `GET /sectors/{group_code}/breakdown`

"성과가 일부 대형 종목에 집중되어 있는가"에 답하는 엔드포인트.

**파라미터**: 공통 + `period_id`(필수), `limit`(기본 20, 최대 100), `side`(`top` \| `bottom` \| `both`, 기본 `both`)

```json
{
  "meta": { "group_code": "WI620", "name": "반도체", "period_id": "2026-W36" },
  "data": {
    "summary": {
      "return": 0.0615, "return_equal": 0.0312, "cap_weight_spread": 0.0303,
      "base_weight": 0.2748, "contribution": 0.0169,
      "member_cnt": 118, "up_cnt": 79, "up_ratio": 0.6695,
      "hhi": 0.4812, "effective_n": 2.08,
      "top1_contrib_share": 0.71, "top3_contrib_share": 0.88, "top5_contrib_share": 0.93,
      "badges": ["1종목 주도"]
    },
    "members": [
      {
        "security_id": 1001, "ticker": "005930", "name": "삼성전자",
        "weight_in_group": 0.5921, "weight_in_universe": 0.1627,
        "return": 0.0738,
        "contribution_in_group": 0.0437,
        "contribution_in_universe": 0.0120,
        "contrib_rank": 1
      }
    ],
    "others": {
      "member_cnt": 78,
      "weight_in_group": 0.0912,
      "contribution_in_group": 0.0031
    }
  }
}
```

**규약**

- `members`는 `contribution_in_group` 내림차순이다. `side=both`이면 상위 `limit`개와 하위 `limit`개를 모두 담고, 중복은 제거한다.
- `limit`은 한쪽 방향의 개수다. `side = both`이면 상위 `limit`개와 하위 `limit`개를 담고 겹치면 한 번만 넣는다.
- `others`는 반환되지 않은 나머지 종목의 합산이다. DB에는 포함 종목 전체의 기여도가 저장되어 있으므로([02 §5.5](02-domain-and-data-model.md#55-group_member_contribution)) 조회 시 더해 만든다. `sum(members.contribution_in_group) + others.contribution_in_group = summary.return` (오차 1e-9).
- `contribution_in_universe = base_weight × contribution_in_group`.

### 5.2 `GET /sectors/{group_code}/history`

단일 섹터의 시계열(스파크라인·상세 패널용).

```json
{
  "data": [
    { "period_id": "2026-W35", "return": 0.0088, "rank": 4, "contribution": 0.0024,
      "base_weight": 0.2731, "top1_contrib_share": 0.62, "hhi": 0.4790,
      "up_ratio": 0.5085, "cap_weight_spread": 0.0041, "is_provisional": false }
  ]
}
```

### 5.3 `GET /sectors/{group_code}/market-cap`

섹터 시가총액의 기간별 시가·고가·저가·종가(드릴다운 시가총액 차트용). 계산 정의는 [03 §14.2](03-metrics-spec.md#142-기간-값)다.

**파라미터**: 공통(`from`, `to` 포함)

```json
{
  "meta": { "universe": "KR_COMMON", "scheme": "WI26", "period": "W", "currency": "KRW",
            "from": "2026-W35", "to": "2026-W38", "group_code": "WI620", "name": "반도체" },
  "data": [
    { "period_id": "2026-W36", "base_date": "2026-08-28", "end_date": "2026-09-04", "is_provisional": false,
      "open": 2987521533408255, "high": 3042975057022089, "low": 2893185252173515, "close": 2977591039595888,
      "member_cnt": 163 }
  ]
}
```

- 시가총액은 `meta.currency` 단위이고 정수로 반올림한다.
- `open`은 기준일(직전 기간 종료일) 값이다. 그래서 한 기간의 `open`은 앞 기간의 `close`와 같다.
- `member_cnt`는 기간 마지막 거래일에 더한 종목 수다.
- 그룹이 없는 기간은 점을 생략한다. 이동평균은 주지 않는다. 화면이 `close`로 계산하고, 필요한 앞 기간은 `from`을 앞당겨 받는다([06 §5.4](06-ui-spec.md#54-섹터-시가총액-차트)).
- 섹터 일별 시총을 아직 한 번도 쓰지 않았으면 409 `NOT_AVAILABLE`, 그 그룹의 값이 없으면 404 `NOT_FOUND`다.

## 6. 부가 API

### 6.1 `GET /securities/search?universe=&q=&limit=20`

티커/종목명 부분 일치 검색. 드릴다운에서 특정 종목을 찾을 때 사용.

```json
{
  "data": [
    { "security_id": 1001, "ticker": "005930", "name": "삼성전자",
      "board": "KOSPI", "group_code": "WI620", "group_name": "반도체" }
  ]
}
```

### 6.2 `GET /export/sectors.csv?...`

`/sectors/ranks`와 동일한 파라미터를 받아 CSV를 반환한다.

```csv
period_id,end_date,group_code,group_name,return,rank,base_weight,contribution,member_cnt
2026-W36,2026-09-04,WI620,반도체,0.0615,1,0.2748,0.0169,118
```

`Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment`. 파일명은 `sectors_{universe}_{scheme}_{from}_{to}.csv`다.

### 6.3 `GET /events`

수집 단계에서 기록한 특이사항 조회 ([07 §11.2](07-price-ingestion.md#112-특이사항)). 수익률이 가격 외 이유로 흔들린 구간을 확인하는 데 쓴다.

| 파라미터 | 기본값 | 설명 |
| --- | --- | --- |
| `universe` | 필수 | 시장을 정한다 |
| `type` | 전체 | `LISTING`, `DELISTING`, `RECLASS`, `SHARE_CHANGE`, `CORP_ACTION`, `DATA_GAP` |
| `group_code` | 전체 | 그 섹터의 사건만. 섹터 시가총액 차트의 표시가 쓴다 ([06 §5.4](06-ui-spec.md#54-섹터-시가총액-차트)) |
| `from` / `to` | 전체 | 사건 날짜 구간 (구간 사건은 종료일로 비교) |
| `min_sector_share` | 0 | 섹터 시총 대비 비중 하한 |
| `sort` | `sector_share` | `sector_share`(영향이 큰 순) 또는 `date` |
| `limit` | 50 | 최대 500 |

```json
{
  "meta": { "universe": "KR_COMMON", "market": "KR", "count": 50,
            "by_type": { "CORP_ACTION": 292, "SHARE_CHANGE": 346, "LISTING": 125, "DELISTING": 72, "DATA_GAP": 1 } },
  "data": [
    { "event_id": 412, "market": "KR", "event_date": "2025-11-24", "end_date": null, "event_type": "CORP_ACTION",
      "ticker": "207940", "name": "삼성바이오로직스", "group_code": "WI410", "group_name": "건강관리",
      "market_cap": 8.23e13, "sector_share": 0.243, "detail": "207940 가격계수 1.471744, 주식수 미반영",
      "source": "NAVER_FACTOR_JUMP" }
  ]
}
```

- `sector_share`는 사건 시점 섹터 시총 대비 사건 규모다. 이 값으로 정렬하면 섹터 흐름에 영향이 큰 사건부터 나온다.
- 시장 단위 사건(데이터 공백 등)은 `ticker`와 `group_code`가 `null`이다. 그래서 `group_code`로 거르면 빠진다.
- 사건의 `group_code`는 시장의 **배타 스킴**(KR WI26 · US GICS) 코드다. 테마 스킴을 보는 중이면 걸리는 사건이 없다.
- `source`는 그 사건을 만든 값의 수집 출처다. 유형별 값과 뜻은 [07 §11.2](07-price-ingestion.md#112-특이사항)에 있다. 한 사건에 출처가 둘 이상이면 쉼표로 잇는다. 아직 다시 뽑지 않은 사건은 `null`이다.

## 7. 캐싱

| 엔드포인트 | 조건 | `Cache-Control` |
| --- | --- | --- |
| `/meta/*` | — | `max-age=3600` |
| `/sectors/ranks`, `/sectors/returns`, `/sectors/market-caps` | 구간이 확정 기간만 포함 | `max-age=86400` |
| 〃 | 구간에 잠정 기간 포함 | `max-age=300` |
| `/sectors/{g}/breakdown` | 확정 기간 | `max-age=86400` |
| `/sectors/{g}/history`, `/sectors/{g}/market-cap` | `/sectors/ranks`와 같다 | 〃 |
| 전체 | `stale = true` | `no-store` |

`ETag`는 `calc_version` + 구간 내 최대 `calculated_at`으로 생성한다.
