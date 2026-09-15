# 03. 지표 계산 명세

이 문서가 계산 로직의 **단일 출처(single source of truth)** 다. API·UI·배치 구현은 모두 이 정의를 따른다.

## 0. 기호

| 기호 | 의미 |
| --- | --- |
| `t` | 대상 기간 (예: `2026-W03`) |
| `t-1` | 직전 기간 |
| `B(t)` | **기준일**. `t-1` 기간의 마지막 거래일 = `period_calendar.base_date` |
| `E(t)` | **종료일**. `t` 기간의 마지막 거래일 = `period_calendar.end_date` |
| `U` | 유니버스 (`KR_COMMON` 또는 `US_SP500`) |
| `S` | 그룹(섹터 또는 테마) |
| `i` | 개별 종목 |
| `P_adj(i, d)` | 종목 i의 d일 수정 종가 (`close_adj`) |
| `MC(i, d)` | 종목 i의 d일 시가총액 (`market_cap`) |

## 1. 기간 경계 결정

```
cal_start, cal_end  <- 기간 단위별 캘린더 경계
  W: ISO-8601 주 (월요일 시작, 일요일 종료), 식별자는 ISO week-year 기준 'YYYY-Www'
  M: 역월 1일 ~ 말일, 식별자 'YYYY-MM'

E(t) = max{ d : d in trading_calendar(market), cal_start <= d <= cal_end }   -- trading_calendar에는 거래일만 있다
B(t) = E(t-1)
```

- `E(t)`가 존재하지 않으면(거래일 0일) 해당 기간을 생성하지 않으며, 그 다음 기간의 `B`는 **직전에 존재하는 기간의 `E`** 가 된다.
- 시리즈의 첫 기간은 `B`가 없으므로 수익률을 계산하지 않는다.
- 진행 중 기간은 `E(t) = 현재까지의 마지막 거래일`, `is_provisional = true`로 계산한다.

### 1.1 ISO Week 주의사항

- `2026-W01`의 시작은 2025-12-29일 수 있다. **주 식별자의 연도는 ISO week-year이며 역월 연도와 다를 수 있다.**
- 정렬·구간 조회는 반드시 `period_seq`(정수)로 수행한다. `period_id` 문자열 정렬은 대부분의 경우 일치하지만 보장되지 않는다.
- ISO week-year는 52주 또는 53주다. 53주 연도를 특수 처리하지 않는다.

## 2. 종목 수익률

```
r(i, t) = P_adj(i, E(t)) / P_adj(i, B(t)) - 1
```

- **수정주가 기반 가격 수익률**이다. 배당 재투자는 반영하지 않는다 (범위 경계: [01 §8](01-requirements.md#8-범위-경계-out-of-scope)).
- 수정주가는 액면분할/병합, 무상증자, 주식배당 등 자본 변동을 반영한 값이다. 수정계수 소급 변경 시 재계산 대상이다.
- `P_adj(i, B(t))`가 없거나 0이면 수익률을 계산하지 않고 `incl_status`에 사유를 기록한다.

## 3. 가중치

### 3.1 유니버스 내 비중

```
W(i, t) = MC(i, B(t)) / SUM over j in U(B(t)) of MC(j, B(t))
```

### 3.2 그룹 내 비중

```
w_S(i, t) = MC(i, B(t)) / SUM over j in S(B(t)) of MC(j, B(t))
```

**핵심 규약**

- 가중치는 **직전 기간 말(`B(t)`) 시가총액**으로 고정한다. 기간 중 시총 변화는 가중치에 반영하지 않는다. 따라서 그룹 수익률은 "기준일에 시총 비중대로 매수해 기간 말까지 보유한 포트폴리오의 수익률"과 동일한 해석을 갖는다.
- 유동주식비율(free float)·투자가능비중(FIF) 조정은 적용하지 않는다. **전체 시가총액(full market cap)** 기준이다.
- 유니버스 구성원 판정과 그룹 소속 판정 모두 **`B(t)` 시점 as-of**로 한다. 기간 중 섹터가 변경되어도 해당 기간에는 `B(t)` 시점 섹터로 집계한다.

## 4. 그룹(섹터/테마) 수익률

```
R(S, t) = SUM over i in S of [ w_S(i, t) * r(i, t) ]
```

보조 지표:

```
R_eq(S, t)     = 산술평균 of r(i, t)           -- 동일가중 수익률
R_med(S, t)    = 중앙값 of r(i, t)
spread(S, t)   = R(S, t) - R_eq(S, t)          -- 양수면 대형주가 성과를 주도
```

- 그룹 구성원이 0종목이면 행을 생성하지 않는다.
- 테마(비배타 스킴)도 동일 공식으로 계산한다. 단, 테마 간에는 가산성이 성립하지 않으므로 **시장 기여도 분해는 배타 스킴에만 제공**한다.

## 5. 시장 수익률

```
R(U, t) = SUM over i in U of [ W(i, t) * r(i, t) ]
```

- 이는 **자체 계산한 유니버스 시가총액 가중 수익률**이며, KOSPI·KOSDAQ·S&P 500 공식 지수 수익률과 일치하지 않는다(유동비율 조정, 구성종목 범위, 지수 산출 규칙 차이). 화면에는 "시장 지수"가 아니라 "유니버스 수익률"로 표기한다.

## 6. 순위

```
rank_ret(S, t) = R(S, t) 내림차순 순위
```

- 동점 처리: **경쟁 순위(competition ranking)**. 동점이면 같은 순위를 부여하고 다음 순위를 건너뛴다 (1, 2, 2, 4).
- 결정적 정렬을 위한 tie-break 순서: `R(S,t)` desc → `base_weight` desc → `group_code` asc.
- `rank_delta(S, t) = rank_ret(S, t-1) - rank_ret(S, t)`. 양수면 순위 상승.
- 직전 기간에 그룹이 존재하지 않으면 `rank_ret_prev = NULL`, `rank_delta = NULL` (범프 차트에서 선을 잇지 않는다).
- 순위는 분류 체계의 그룹에만 준다. 미매핑 의사 그룹(`UNMAPPED`)은 `rank_ret = NULL`이며 순위 계산에서 빠진다. 기여도 가산성을 위해 행은 남는다([§7.2](#72-가산성)).
- 그룹 수가 기간마다 달라질 수 있으므로(신설/폐지 섹터), 순위의 최댓값은 기간별 그룹 수에 종속된다.

## 7. 시장 기여도 분해

### 7.1 정의

```
BW(S, t) = SUM over i in S of MC(i, B(t)) / SUM over j in U of MC(j, B(t))   -- 기준일 그룹 비중
C(S, t)  = BW(S, t) * R(S, t)                                                -- 기여도
```

### 7.2 가산성

배타 스킴에서 다음이 성립하며, 이는 검증 규칙이다 ([§10](#10-검증-규칙)).

```
SUM over all S of C(S, t) = R(U, t)
```

- 미매핑 종목은 `UNMAPPED` 의사 그룹으로 집계해 가산성을 유지한다.

### 7.3 기여 비중

```
contrib_share(S, t) = C(S, t) / R(U, t)
```

- **0 근처 가드**: `|R(U, t)| < 0.0005`(5bp)이면 `contrib_share = NULL`로 두고, 화면은 비율 대신 절대 기여도(bp)만 표시한다. 시장이 보합일 때 비율이 발산하기 때문이다.
- `R(U, t)`가 음수일 때 `contrib_share`의 부호 해석이 뒤집히므로, 화면에서는 비율보다 **기여도 절대값(bp)** 을 기본 표시로 한다.

## 8. 섹터 내 종목 기여도

```
c(i, S, t) = w_S(i, t) * r(i, t)
SUM over i in S of c(i, S, t) = R(S, t)
```

시장 전체 기여도로 환산하려면:

```
c_market(i, t) = W(i, t) * r(i, t)          -- 유니버스 비중 기준
c_market(i, t) = BW(S, t) * c(i, S, t)      -- 위와 동일
```

드릴다운 화면은 두 값을 모두 제공한다(섹터 내 기여 / 시장 전체 기여).

## 9. 집중도 지표

"성과가 일부 대형 종목에 집중되어 있는가"에 답하는 지표군. 모두 그룹·기간 단위로 산출한다.

### 9.1 상위 기여 집중도

```
G_plus(S, t)  = SUM over i where c(i) > 0 of c(i)      -- 양의 기여 총합
topK_share(S, t) = SUM of top-K c(i) (내림차순) / G_plus(S, t)
```

- 분모를 `R(S, t)`가 아니라 `G_plus`로 두는 이유: `R(S, t)`가 0에 가깝거나 음수일 때 비율이 발산·부호 반전되기 때문이다. `G_plus` 기준 값은 항상 `(0, 1]` 범위에 있다.
- `G_plus = 0`(전 종목 하락)이면 `NULL`. 이 경우 하락 기여 기준 `G_minus`로 동일하게 계산한 값을 별도 필드로 제공한다.
- K = 1, 3, 5를 기본 저장한다.

### 9.2 가중치 집중도

```
HHI(S, t)   = SUM over i in S of w_S(i, t)^2      -- 0 초과 1 이하
EffN(S, t)  = 1 / HHI(S, t)                        -- 유효 종목 수
```

- `EffN`은 "이 섹터는 실질적으로 몇 종목짜리인가"를 나타낸다. 구성종목 30개인데 `EffN`이 2.1이면 사실상 2종목 섹터다.
- `HHI`는 **수익률과 무관한 구조적 집중도**다. `topK_share`는 **해당 기간 성과의 집중도**다. 두 값을 함께 보여야 "원래 집중된 섹터"와 "이번 기간에만 쏠린 섹터"를 구분할 수 있다.

### 9.3 폭(breadth)

```
up_ratio(S, t) = (r(i, t) > 0 인 종목 수) / member_cnt
```

### 9.4 대형주 주도 스프레드

```
spread(S, t) = R(S, t) - R_eq(S, t)
```

- 양수: 시총 큰 종목이 평균보다 잘함 → 대형주 주도
- 음수: 소형주 주도

### 9.5 판정 가이드 (UI 배지용)

| 조건 | 배지 |
| --- | --- |
| `top1_contrib_share >= 0.50` | `1종목 주도` |
| `top3_contrib_share >= 0.70` 그리고 `member_cnt >= 10` | `소수 종목 집중` |
| `up_ratio >= 0.70` 그리고 `top3_contrib_share < 0.50` | `전반적 강세` |
| `spread > 0` 그리고 `up_ratio < 0.50` | `대형주 주도` |

임계값은 설정값으로 외부화한다.

## 10. 예외 처리

| 상황 | 판정 (`incl_status`) | 처리 |
| --- | --- | --- |
| 기준일 이후 신규 상장 (기준일 가격 없음) | `NEW_LISTING` | 해당 기간 **제외**. 다음 기간부터 편입 |
| 기간 중 상장폐지 | `DELISTED` | 마지막 거래일 종가로 `E(t)` 가격을 대체해 **수익률 반영**. 다음 기간부터 제외 |
| 기준일 거래정지, 종료일 정상 | `SUSPENDED` | 직전 정상 종가 캐리포워드 후 **포함** |
| 기간 전체 거래정지 | `SUSPENDED` | 수익률 0으로 **포함** (비중은 유지) |
| 기준일 시가총액 없음/0 | `NO_MCAP` | 가중치 산정 불가 → **제외** |
| 기준일 가격 없음 (신규 상장 외 사유) | `NO_BASE_PRICE` | **제외** |
| 분류 매핑 없음 | `INCLUDED` | 유니버스에는 포함, `UNMAPPED` 그룹으로 집계 |
| 기간 중 섹터 변경 | — | `B(t)` 시점 섹터로 집계 |
| 기간 중 유니버스 편출 (S&P 500 편출) | `DELISTED` 준용 | 편출 직전 종가까지 반영 |

**제외 종목의 효과**: 제외된 종목은 가중치 분모에서도 빠진다. 즉 남은 종목의 비중이 비례적으로 커진다(renormalize).

## 11. 정밀도 및 반올림

- 내부 계산은 배정밀도 부동소수점 또는 `NUMERIC`으로 수행하고, **저장 시점까지 반올림하지 않는다.**
- 저장: 수익률·기여도 `NUMERIC(18,10)`, 비중 `NUMERIC(18,12)`.
- API 응답: 수익률·기여도는 소수 8자리, 비중은 소수 10자리로 반올림.
- 화면 표시: 수익률 소수점 2자리 %, 기여도 bp 정수 또는 소수 1자리.
- **표시용 반올림 값으로 합계를 재계산하지 않는다.** 합계는 항상 원값으로 계산한 뒤 반올림한다.

## 12. 검증 규칙

배치 종료 시 자동 검증한다. 위반 시 해당 기간 결과를 `FAILED`로 마킹하고 배포하지 않는다.

| # | 규칙 | 허용 오차 |
| --- | --- | --- |
| V-1 | 유니버스 비중 합 = 1 | 1e-9 |
| V-2 | 각 그룹 내 비중 합 = 1 | 1e-9 |
| V-3 | 배타 스킴에서 `SUM C(S,t) = R(U,t)` | 1e-9 |
| V-4 | 그룹 내 종목 기여도 합 = 그룹 수익률 | 1e-9 |
| V-5 | 배타 스킴에서 그룹별 `member_cnt` 합 = 유니버스 `member_cnt` | 정확히 일치 |
| V-6 | `rank_ret`는 1부터 시작하는 연속 경쟁 순위 | 정확히 일치 |
| V-7 | `hhi > 0`, `0 < effective_n <= member_cnt` | — |
| V-8 | 미매핑 시총 비중 `unmapped_cap_ratio <= 0.005` | 경고(차단 아님) |
| V-9 | `\|r(i,t)\|`이 주 단위 100% / 월 단위 200% 초과 | 경고 + 수정주가 점검 큐 등록 |

## 13. 참조 구현 (의사코드)

```python
def compute_period(universe, scheme, period_type, period_id):
    pc = period_calendar(universe.market, period_type, period_id)
    if pc.base_date is None:
        return  # 시리즈 첫 기간

    members = universe_members_asof(universe, pc.base_date)

    rows = []
    for sec in members:
        p_base = adj_close(sec, pc.base_date)
        p_end  = adj_close_or_last(sec, pc.end_date)   # 상폐/정지 캐리포워드
        mc     = market_cap(sec, pc.base_date)
        status = classify_inclusion(sec, p_base, p_end, mc)
        if status_is_excluded(status):
            rows.append(Row(sec, ret=None, mc=None, status=status))
            continue
        rows.append(Row(sec, ret=p_end / p_base - 1, mc=mc, status='INCLUDED'))

    incl = [r for r in rows if r.status == 'INCLUDED']
    total_mc = sum(r.mc for r in incl)
    for r in incl:
        r.w_universe = r.mc / total_mc

    # 시장
    universe_ret = sum(r.w_universe * r.ret for r in incl)

    # 그룹
    groups = group_by(incl, lambda r: group_asof(scheme, r.sec, pc.base_date) or 'UNMAPPED')
    stats = []
    for gcode, gmembers in groups.items():
        g_mc = sum(r.mc for r in gmembers)
        for r in gmembers:
            r.w_group = r.mc / g_mc
            r.contrib = r.w_group * r.ret

        g_ret        = sum(r.contrib for r in gmembers)
        base_weight  = g_mc / total_mc
        g_ret_eq     = mean(r.ret for r in gmembers)
        g_plus       = sum(r.contrib for r in gmembers if r.contrib > 0)
        top          = sorted(gmembers, key=lambda r: -r.contrib)

        stats.append(GroupStat(
            group_code   = gcode,
            ret          = g_ret,
            ret_equal    = g_ret_eq,
            ret_median   = median(r.ret for r in gmembers),
            base_weight  = base_weight,
            contribution = base_weight * g_ret,
            member_cnt   = len(gmembers),
            up_cnt       = sum(1 for r in gmembers if r.ret > 0),
            hhi          = sum(r.w_group ** 2 for r in gmembers),
            top1_share   = share(top[:1], g_plus),
            top3_share   = share(top[:3], g_plus),
            top5_share   = share(top[:5], g_plus),
            spread       = g_ret - g_ret_eq,
        ))

    assign_ranks(stats)          # 경쟁 순위 + tie-break
    assign_contrib_share(stats, universe_ret)   # 0 근처 가드
    validate(stats, universe_ret)               # V-1 ~ V-9
    persist(stats)
```
