-- 0002_rank_nullable: 미매핑 의사 그룹에 순위를 주지 않는다
--
-- UNMAPPED는 분류 체계의 섹터가 아니므로 순위에서 빼고(rank_ret NULL), 기여도 가산성을 위해 행은 남긴다
-- (docs/03 §6, §7.2). group_period_stat은 원천에서 다시 만드는 파생 테이블이라 비우고 새로 만든다.

DROP TABLE group_period_stat;

CREATE TABLE group_period_stat (
    universe_code          TEXT    NOT NULL,
    scheme_code            TEXT    NOT NULL,
    period_type            TEXT    NOT NULL CHECK (period_type IN ('W', 'M')),
    period_id              TEXT    NOT NULL,
    group_code             TEXT    NOT NULL,      -- 미매핑 종목은 'UNMAPPED'
    period_seq             INTEGER NOT NULL,
    -- 수익률
    ret                    REAL    NOT NULL,      -- 시가총액 가중 그룹 수익률
    ret_equal              REAL,
    ret_median             REAL,
    -- 비중·기여도 (비배타 스킴은 NULL)
    base_weight            REAL,                  -- 기준일 유니버스 내 그룹 비중
    contribution           REAL,                  -- base_weight × ret
    contrib_share          REAL,                  -- contribution / 유니버스 수익률. |유니버스 수익률| < 5bp이면 NULL
    -- 순위 (UNMAPPED는 NULL)
    rank_ret               INTEGER,               -- 수익률 내림차순 경쟁 순위
    rank_ret_prev          INTEGER,               -- 직전 기간 순위. 직전 기간에 그룹이 없으면 NULL
    rank_delta             INTEGER,               -- rank_ret_prev - rank_ret (양수면 상승)
    rank_contrib           INTEGER,               -- |contribution| 내림차순 순위
    -- 집중도
    member_cnt             INTEGER NOT NULL,
    up_cnt                 INTEGER NOT NULL,
    hhi                    REAL,                  -- 그룹 내 비중 제곱합
    effective_n            REAL,                  -- 1 / hhi
    top1_contrib_share     REAL,                  -- 상위 K 기여 / 양의 기여 총합. 양의 기여가 없으면 NULL
    top3_contrib_share     REAL,
    top5_contrib_share     REAL,
    top1_neg_contrib_share REAL,                  -- 하위 K 기여 / 음의 기여 총합 (docs/03 §9.1). 음의 기여가 없으면 NULL
    top3_neg_contrib_share REAL,
    top5_neg_contrib_share REAL,
    cap_weight_spread      REAL,                  -- ret - ret_equal
    is_provisional         INTEGER NOT NULL DEFAULT 0 CHECK (is_provisional IN (0, 1)),
    calc_version           TEXT    NOT NULL,
    calculated_at          TEXT    NOT NULL,
    PRIMARY KEY (universe_code, scheme_code, period_type, period_id, group_code)
) STRICT;
CREATE INDEX ix_gps_series ON group_period_stat (universe_code, scheme_code, period_type, period_seq);
CREATE INDEX ix_gps_group ON group_period_stat (universe_code, scheme_code, period_type, group_code, period_seq);
