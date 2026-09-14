# 04. 아키텍처 및 데이터 파이프라인

## 1. 구성 요소

```
[ 데이터 공급 ]                [ 적재/계산 ]                [ 서비스 ]
                                                         
 일별 시세 파일        ──▶  Ingestion Job     ──▶  원천 테이블
 상장 마스터           ──▶  (검증 + 정규화)          price_daily
 S&P500 구성종목       ──▶                           security
 분류 매핑(WI26/GICS)  ──▶                           security_group_map
                                    │
                                    ▼
                            Period Close Job
                            (기간 경계 확정)   ──▶  period_calendar
                                    │
                                    ▼
                            Aggregation Job
                            (수익률/기여도/순위/집중도)
                                    │
                                    ▼
                            Validation Job (V-1~V-9)
                                    │
                                    ▼
                            파생 테이블 ──▶ API 서버 ──▶ 대시보드
                                            (+ 캐시)
```

- **계산과 조회를 분리**한다. API는 파생 테이블을 읽기만 하며 요청 시점에 수익률을 계산하지 않는다.
- 파생 테이블은 원천으로부터 전량 재생성 가능해야 한다. 어떤 파생 값도 수기 보정하지 않는다.

## 2. 배치 스케줄

| 작업 | 시장 | 실행 시점 | 설명 |
| --- | --- | --- | --- |
| `ingest-price` | KR | 거래일 18:00 KST | 당일 종가·시총·상태 적재 |
| `ingest-price` | US | 거래일 익일 08:00 KST | 미국 정규장 종료 후 |
| `ingest-master` | KR/US | 일 1회, 시세 적재 전 | 상장/폐지/종류 변경 |
| `ingest-universe` | US | 일 1회 | S&P 500 구성종목 변경 |
| `ingest-mapping` | KR/US | 온디맨드(파일 도착 시) | 분류 매핑 적재 |
| `period-close` | KR/US | 시세 적재 직후 | 기간 경계·`is_closed` 갱신 |
| `aggregate` | KR/US | `period-close` 직후 | W·M 단위 파생 산출 |
| `validate` | KR/US | `aggregate` 직후 | 검증 실패 시 배포 차단 |
| `cache-warm` | KR/US | `validate` 성공 후 | 기본 조회 조건 프리로드 |

### 2.1 잠정치 갱신

- 매 거래일 `aggregate`는 **현재 진행 중인 주/월**도 계산해 `is_provisional = true`로 저장한다.
- 기간 종료 후 첫 실행에서 `is_provisional = false`로 확정한다.
- 잠정치와 확정치는 같은 행을 갱신한다(별도 테이블을 두지 않는다).

## 3. 계산 순서 (aggregate)

기간 `t`에 대해 다음 순서로 수행한다. 각 단계는 앞 단계의 산출물에 의존한다.

1. `period_calendar` 확인 — `base_date`, `end_date` 확정
2. `universe_membership` as-of `base_date` 조회 → 대상 종목 집합
3. 종목별 `ret`, `base_market_cap`, `incl_status` 산출 → `security_period_return`
4. 유니버스 집계 → `universe_period_stat`
5. 분류 매핑 as-of `base_date` 조인 → 그룹 배정 (미매핑은 `UNMAPPED`)
6. 그룹 수익률·비중·기여도·집중도 산출 → `group_period_stat`
7. 순위 부여 (`rank_ret`, `rank_contrib`) 및 직전 기간 순위 조인 (`rank_delta`)
8. 종목 기여도 상·하위 N + `OTHERS` → `group_member_contribution`
9. 검증 (V-1 ~ V-9)
10. `calc_version`, `calculated_at` 기록 후 커밋

- 4~8단계는 `(universe, scheme, period_type)` 조합별로 병렬 실행 가능하다.
- 7단계의 `rank_delta`는 직전 기간 결과에 의존하므로 **기간 순서대로** 처리한다.

## 4. 재계산 정책

소급 변경이 발생하면 영향 범위를 산정해 재계산 작업을 큐잉한다.

| 변경 유형 | 영향 범위 | 재계산 대상 |
| --- | --- | --- |
| `adj_factor` 소급 변경 (종목 i, 일자 d 이후) | 종목 i가 포함된 모든 그룹 | `d`가 속한 기간부터 최신 기간까지 전체 |
| `close_raw` / `shares_listed` 정정 | 해당 종목 및 그룹 | 해당 일자가 `base_date` 또는 `end_date`인 기간 |
| 분류 매핑 `valid_from` 소급 삽입/수정 | 해당 스킴 전체 | `valid_from` 이후 전 기간 |
| 유니버스 구성 소급 변경 | 해당 유니버스 전체 | 변경 시점 이후 전 기간 |
| 분류 그룹 신설/폐지 | 해당 스킴 전체 | 순위가 바뀌므로 `valid_from` 이후 전 기간 |
| 계산 로직 변경 | 전체 | 전 기간 (`calc_version` 증가) |

**운영 규칙**

- 재계산은 **기간 오름차순**으로 수행한다(`rank_delta` 의존성).
- 재계산 중에는 해당 구간의 API 응답에 `stale = true` 플래그를 실어 보낸다.
- `calc_version`이 바뀌는 전량 재계산은 신규 버전으로 산출한 뒤 검증을 통과한 시점에 스위치한다(blue-green).

## 5. 데이터 품질 검증

### 5.1 적재 단계 (차단)

| 검사 | 실패 시 |
| --- | --- |
| 필수 컬럼 누락, 타입 불일치 | 배치 실패 |
| `ticker` → `security_id` 해석 실패 | 배치 실패 |
| 미등록 `scheme_code` / `group_code` | 배치 실패 |
| 배타 스킴의 유효기간 중첩 | 배치 실패 |
| 거래일 대비 시세 행 수가 직전 거래일 대비 ±5% 초과 변동 | 배치 보류 + 알림 |

### 5.2 계산 단계 (검증 규칙 V-1 ~ V-9)

[03 §12](03-metrics-spec.md#12-검증-규칙) 참조. V-1 ~ V-7 위반은 배포 차단, V-8 · V-9는 경고.

### 5.3 상시 모니터링 지표

| 지표 | 임계 |
| --- | --- |
| 분류 미매핑 시총 비중 (`unmapped_cap_ratio`) | 0.5% 초과 시 알림 |
| 유니버스 종목 수 전기 대비 변동 | ±3% 초과 시 알림 |
| 그룹별 `member_cnt`가 0이 된 섹터 | 즉시 알림 |
| 배치 지연 (예정 시각 + 60분) | 즉시 알림 |
| 수익률 이상치 건수 (V-9) | 일 10건 초과 시 알림 |

## 6. 성능 설계

### 6.1 데이터 규모 추정

| 항목 | 규모 |
| --- | --- |
| 종목 수 | KR 약 2,400 + US 500 |
| `price_daily` | 약 2,900 종목 × 250 거래일/년 ≈ 73만 행/년 |
| `security_period_return` | 2,900 × (52주 + 12월) ≈ 18.6만 행/년 |
| `group_period_stat` | (26 + 11 + 테마) × 64 기간 ≈ 수천 행/년 |
| `group_member_contribution` | 그룹당 최대 41행 × 그룹 수 × 64 ≈ 10만 행/년 |

`group_period_stat`은 매우 작다. **범프 차트 조회는 수백 행 단위**이므로 단일 인덱스 스캔으로 해결된다.

### 6.2 조회 경로

- 범프 차트: `ix_gps_series (universe_code, scheme_code, period_type, period_seq)` 범위 스캔
- 드릴다운: `group_member_contribution` PK 선두 컬럼 일치 조회
- 캐시: `(universe, scheme, period_type, from, to)` 키로 응답 캐싱. 확정 기간만 포함된 응답은 TTL 24시간, 잠정 기간을 포함하면 TTL 5분.

### 6.3 목표

| 항목 | 목표 |
| --- | --- |
| 범프 차트 API 응답 (104주 구간) | p95 300ms 이하 |
| 드릴다운 API 응답 | p95 200ms 이하 |
| 일별 `aggregate` 전체 소요 | 10분 이내 |
| 전량 재계산 (10년, 2시장, 2기간단위) | 2시간 이내 |

## 7. 운영 메타데이터

```sql
CREATE TABLE batch_run (
    run_id        BIGINT PRIMARY KEY,
    job_name      VARCHAR(32) NOT NULL,
    market_code   VARCHAR(8),
    target_scope  VARCHAR(128),          -- 예: 'W:2026-W03..2026-W05'
    status        VARCHAR(16) NOT NULL,  -- RUNNING, SUCCEEDED, FAILED, BLOCKED
    calc_version  VARCHAR(16),
    started_at    TIMESTAMP NOT NULL,
    finished_at   TIMESTAMP,
    row_count     BIGINT,
    message       TEXT
);

CREATE TABLE validation_result (
    run_id     BIGINT NOT NULL REFERENCES batch_run(run_id),
    rule_code  VARCHAR(8) NOT NULL,      -- V-1 ... V-9
    scope      VARCHAR(128) NOT NULL,
    passed     BOOLEAN NOT NULL,
    observed   NUMERIC(24,12),
    tolerance  NUMERIC(24,12),
    detail     TEXT,
    PRIMARY KEY (run_id, rule_code, scope)
);
```

- `calc_version`은 계산 로직의 시맨틱 버전이다. 로직 변경 시 반드시 증가시키고, 파생 행에 기록해 어떤 로직으로 산출된 값인지 추적 가능하게 한다.
