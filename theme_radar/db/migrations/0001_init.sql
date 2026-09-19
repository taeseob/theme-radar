-- 0001_init: theme-radar 초기 스키마
--
-- 이 파일이 테이블 정의의 단일 출처다. 테이블의 목적과 조회·적재 규약은 docs/02-domain-and-data-model.md에 있다.
-- 마이그레이션 파일에는 트랜잭션 문장(BEGIN, COMMIT)을 쓰지 않는다. theme_radar/db/migrate.py가 파일 하나를 한 트랜잭션으로 감싼다.
--
-- 공통 규약
--   날짜      TEXT 'YYYY-MM-DD' (거래소 현지 날짜). 형식을 CHECK로 강제한다. 문자열 비교로 as-of 조회를 하기 때문이다
--   시각      TEXT ISO-8601 UTC (예: 2026-09-15T09:31:22Z)
--   수치      REAL (배정밀도, docs/03 §11)
--   참거짓    INTEGER 0/1
--   유효기간  valid_from ~ valid_to 양끝 포함. 종료가 없으면 '9999-12-31'
--   외래키    마스터·원천·운영 테이블에만 건다. 파생 테이블은 원천에서 전량 재생성하므로 걸지 않는다

-- ============================================================ 마스터

CREATE TABLE market (
    market_code TEXT PRIMARY KEY,                 -- KR, US
    market_name TEXT NOT NULL,
    currency    TEXT NOT NULL,                    -- KRW, USD
    timezone    TEXT NOT NULL                     -- Asia/Seoul, America/New_York
) STRICT;

CREATE TABLE universe (
    universe_code TEXT PRIMARY KEY,               -- KR_COMMON, US_SP500
    universe_name TEXT NOT NULL,
    market_code   TEXT NOT NULL REFERENCES market (market_code)
) STRICT;

CREATE TABLE security (
    security_id    INTEGER PRIMARY KEY,           -- 내부 불변 식별자. 티커·사명 변경과 무관하다
    market_code    TEXT    NOT NULL REFERENCES market (market_code),
    board          TEXT    NOT NULL,              -- KOSPI, KOSDAQ, KONEX, NYSE, NASDAQ 등
    ticker         TEXT    NOT NULL,              -- KR 종목코드(영문자 포함 가능, 선행 0 유지), US 심볼(점 표기, 예: BRK.B)
    isin           TEXT,
    cik            TEXT CHECK (cik GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),  -- US SEC CIK 10자리
    name_local     TEXT    NOT NULL,
    name_en        TEXT,
    security_type  TEXT    NOT NULL CHECK (security_type IN ('COMMON', 'PREFERRED', 'ETF', 'REIT', 'SPAC', 'DR', 'OTHER')),
    listing_date   TEXT CHECK (listing_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    delisting_date TEXT CHECK (delisting_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- NULL이면 상장 유지
    currency       TEXT    NOT NULL,
    CHECK (listing_date IS NULL OR delisting_date IS NULL OR listing_date <= delisting_date)
) STRICT;
-- 상장 중인 종목끼리는 티커가 겹치지 않는다. 폐지된 종목의 티커는 다른 종목이 다시 쓸 수 있다
CREATE UNIQUE INDEX ux_security_active_ticker ON security (market_code, ticker) WHERE delisting_date IS NULL;
CREATE INDEX ix_security_ticker ON security (market_code, ticker);
CREATE INDEX ix_security_type ON security (market_code, security_type);

CREATE TABLE universe_membership (
    universe_code TEXT    NOT NULL REFERENCES universe (universe_code),
    security_id   INTEGER NOT NULL REFERENCES security (security_id),
    valid_from    TEXT    NOT NULL CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    valid_to      TEXT    NOT NULL DEFAULT '9999-12-31' CHECK (valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    PRIMARY KEY (universe_code, security_id, valid_from),
    CHECK (valid_from <= valid_to)
) STRICT;
CREATE INDEX ix_um_asof ON universe_membership (universe_code, valid_from, valid_to);

CREATE TABLE classification_scheme (
    scheme_code  TEXT    PRIMARY KEY,             -- WI26, GICS, THEME_*
    scheme_name  TEXT    NOT NULL,
    market_code  TEXT    NOT NULL REFERENCES market (market_code),
    scheme_type  TEXT    NOT NULL CHECK (scheme_type IN ('SECTOR', 'THEME')),
    is_exclusive INTEGER NOT NULL CHECK (is_exclusive IN (0, 1)),  -- 1이면 한 종목이 동시에 한 그룹에만 속한다
    source_note  TEXT,
    CHECK ((scheme_type = 'SECTOR') = (is_exclusive = 1))
) STRICT;

-- 집계 단위(섹터)만 등록한다. 하위 산업 단계는 등록하지 않는다 (docs/02 §9 S-2)
CREATE TABLE classification_group (
    scheme_code   TEXT    NOT NULL REFERENCES classification_scheme (scheme_code),
    group_code    TEXT    NOT NULL,               -- 분류 체계의 공식 코드 (WI620, 45). 다른 뜻으로 재사용하지 않는다
    group_name    TEXT    NOT NULL,               -- 화면 표시명 (WI26 한국어, GICS 영문)
    group_name_en TEXT,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    color_hex     TEXT CHECK (color_hex GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'),  -- 차트 고정 색상
    valid_from    TEXT    NOT NULL CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- 그룹 신설일
    valid_to      TEXT    NOT NULL DEFAULT '9999-12-31' CHECK (valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- 그룹 폐지일
    PRIMARY KEY (scheme_code, group_code),
    CHECK (group_code <> 'UNMAPPED'),              -- 미매핑 의사 그룹은 파생 테이블에만 쓴다
    CHECK (valid_from <= valid_to)
) STRICT;

CREATE TABLE security_group_map (
    scheme_code  TEXT    NOT NULL,
    security_id  INTEGER NOT NULL REFERENCES security (security_id),
    group_code   TEXT    NOT NULL,
    valid_from   TEXT    NOT NULL CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    valid_to     TEXT    NOT NULL DEFAULT '9999-12-31' CHECK (valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    source_batch TEXT,                            -- 적재 배치·파일 식별자
    PRIMARY KEY (scheme_code, security_id, group_code, valid_from),
    FOREIGN KEY (scheme_code, group_code) REFERENCES classification_group (scheme_code, group_code),
    CHECK (valid_from <= valid_to)
) STRICT;
CREATE INDEX ix_sgm_asof ON security_group_map (scheme_code, valid_from, valid_to);

-- ============================================================ 원천 시계열

-- 거래일만 저장한다. 지수 시계열에서 만든다 (docs/07 §12). 그 지수 일봉은 market_index_daily에 남는다
CREATE TABLE trading_calendar (
    market_code TEXT NOT NULL REFERENCES market (market_code),
    trade_date  TEXT NOT NULL CHECK (trade_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    PRIMARY KEY (market_code, trade_date)
) STRICT;

CREATE TABLE price_daily (
    security_id    INTEGER NOT NULL REFERENCES security (security_id),
    trade_date     TEXT    NOT NULL CHECK (trade_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    close_raw      REAL CHECK (close_raw > 0),    -- 무수정 종가. 적재 후 바꾸지 않는다. 폐지 종목에서 원종가를 확정할 수 없는 날만 NULL (docs/07 §7.2)
    adj_factor     REAL CHECK (adj_factor > 0),   -- 누적 수정계수, 최신 거래일 = 1. close_raw가 NULL이면 NULL
    close_adj      REAL    NOT NULL CHECK (close_adj > 0),  -- 수정 종가. 수익률 계산 전용
    shares_listed  INTEGER CHECK (shares_listed > 0),
    market_cap     REAL CHECK (market_cap > 0),   -- close_raw × shares_listed. 수정 종가로 계산하지 않는다
    volume         INTEGER CHECK (volume >= 0),
    trade_status   TEXT    NOT NULL CHECK (trade_status IN ('NORMAL', 'SUSPENDED', 'HALTED', 'NO_TRADE')),
    price_source   TEXT    NOT NULL,              -- NAVER_MPRICE, NAVER_SISEJSON, YFINANCE
    adj_source     TEXT    NOT NULL,              -- NAVER_SISEJSON, YFINANCE
    fetched_at     TEXT    NOT NULL,
    adj_updated_at TEXT,                          -- 마지막 소급 갱신 시각
    PRIMARY KEY (security_id, trade_date),
    CHECK ((close_raw IS NULL) = (adj_factor IS NULL)),
    CHECK (close_raw IS NOT NULL OR market_cap IS NULL)
) STRICT;
CREATE INDEX ix_pd_date ON price_daily (trade_date);

CREATE TABLE shares_observation (
    security_id INTEGER NOT NULL REFERENCES security (security_id),
    as_of_date  TEXT    NOT NULL CHECK (as_of_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    shares      INTEGER NOT NULL CHECK (shares > 0),
    basis       TEXT    NOT NULL CHECK (basis IN ('AS_REPORTED', 'SPLIT_ADJUSTED')),
    source      TEXT    NOT NULL,                 -- DAUM_QUOTE, FDR_KRX_CACHE, SEC_DEI, YF_INFO
    observed_at TEXT    NOT NULL,
    PRIMARY KEY (security_id, as_of_date, source)
) STRICT;

CREATE TABLE corporate_action (
    security_id  INTEGER NOT NULL REFERENCES security (security_id),
    ex_date      TEXT    NOT NULL CHECK (ex_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- 수정계수가 바뀌는 첫 거래일
    action_type  TEXT    NOT NULL CHECK (action_type IN ('SPLIT', 'REVERSE_SPLIT', 'BONUS_ISSUE', 'STOCK_DIVIDEND', 'SPINOFF', 'UNKNOWN')),
    price_factor REAL    NOT NULL CHECK (price_factor > 0),  -- ex_date 이전 가격에 곱하는 값. 10:1 분할 = 0.1
    share_ratio  REAL CHECK (share_ratio > 0),    -- 이후 주식수 / 이전 주식수. 주식수에 반영하지 않는 이벤트는 NULL
    source       TEXT    NOT NULL,                -- NAVER_FACTOR_JUMP, YFINANCE_SPLIT, MANUAL
    detected_at  TEXT    NOT NULL,
    PRIMARY KEY (security_id, ex_date, source)
) STRICT;

-- 가격이 아닌 이유로 섹터 시총을 바꾸거나 계산에 오차를 남기는 사건 (docs/07 §11.2)
CREATE TABLE special_event (
    event_id     INTEGER PRIMARY KEY,
    market_code  TEXT    NOT NULL REFERENCES market (market_code),
    event_date   TEXT    NOT NULL CHECK (event_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- 구간 사건이면 시작일
    end_date     TEXT CHECK (end_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    event_type   TEXT    NOT NULL CHECK (event_type IN ('LISTING', 'DELISTING', 'RECLASS', 'SHARE_CHANGE', 'CORP_ACTION', 'DATA_GAP')),
    security_id  INTEGER REFERENCES security (security_id),  -- 시장 단위 사건이면 NULL
    group_code   TEXT,                            -- 사건 시점 섹터 (KR WI26, US GICS)
    market_cap   REAL,                            -- 사건 규모. 시장 통화. 모르면 NULL
    sector_share REAL,                            -- market_cap / 사건 시점 섹터 시총
    detail       TEXT    NOT NULL,
    created_at   TEXT    NOT NULL
) STRICT;
-- 같은 사건은 한 번만 기록한다. 시장 단위 사건(security_id NULL)도 중복을 막기 위해 식을 쓴다
CREATE UNIQUE INDEX ux_special_event ON special_event (market_code, event_type, IFNULL(security_id, 0), event_date);

-- ============================================================ 파생 (원천에서 전량 재생성)

CREATE TABLE period_calendar (
    market_code  TEXT    NOT NULL REFERENCES market (market_code),
    period_type  TEXT    NOT NULL CHECK (period_type IN ('W', 'M')),
    period_id    TEXT    NOT NULL,                -- 2026-W03 | 2026-01
    period_seq   INTEGER NOT NULL,                -- 시장·단위별 정렬키. 존재하는 기간마다 1씩 증가
    cal_start    TEXT    NOT NULL CHECK (cal_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    cal_end      TEXT    NOT NULL CHECK (cal_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    base_date    TEXT CHECK (base_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- 직전 기간의 마지막 거래일. 첫 기간은 NULL
    end_date     TEXT    NOT NULL CHECK (end_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- 이 기간의 마지막 거래일
    trading_days INTEGER NOT NULL CHECK (trading_days > 0),
    is_closed    INTEGER NOT NULL CHECK (is_closed IN (0, 1)),
    PRIMARY KEY (market_code, period_type, period_id),
    UNIQUE (market_code, period_type, period_seq),
    CHECK ((period_type = 'W' AND period_id GLOB '[0-9][0-9][0-9][0-9]-W[0-5][0-9]')
        OR (period_type = 'M' AND period_id GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]'))
) STRICT;

CREATE TABLE security_period_return (
    universe_code   TEXT    NOT NULL,
    period_type     TEXT    NOT NULL CHECK (period_type IN ('W', 'M')),
    period_id       TEXT    NOT NULL,
    security_id     INTEGER NOT NULL,
    base_close_adj  REAL,
    end_close_adj   REAL,
    ret             REAL,                         -- 기간 수익률. 제외 종목은 NULL
    base_market_cap REAL,                         -- 기준일 시가총액 (가중치 분자)
    weight_universe REAL,                         -- 유니버스 내 비중
    incl_status     TEXT    NOT NULL CHECK (incl_status IN ('INCLUDED', 'NEW_LISTING', 'DELISTED', 'NO_BASE_PRICE', 'SUSPENDED', 'NO_MCAP')),
    PRIMARY KEY (universe_code, period_type, period_id, security_id)
) STRICT;

CREATE TABLE universe_period_stat (
    universe_code      TEXT    NOT NULL,
    period_type        TEXT    NOT NULL CHECK (period_type IN ('W', 'M')),
    period_id          TEXT    NOT NULL,
    period_seq         INTEGER NOT NULL,
    ret                REAL    NOT NULL,          -- 시가총액 가중 유니버스 수익률
    ret_equal          REAL,
    ret_median         REAL,
    base_market_cap    REAL    NOT NULL,
    member_cnt         INTEGER NOT NULL,
    up_cnt             INTEGER NOT NULL,
    unmapped_cap_ratio REAL,                      -- 분류 미매핑 시총 비중 (품질 지표)
    is_provisional     INTEGER NOT NULL DEFAULT 0 CHECK (is_provisional IN (0, 1)),
    calc_version       TEXT    NOT NULL,
    calculated_at      TEXT    NOT NULL,
    PRIMARY KEY (universe_code, period_type, period_id),
    UNIQUE (universe_code, period_type, period_seq)
) STRICT;

-- 범프 차트·스냅샷의 주 조회 대상. 화면 하나를 단일 테이블 조회로 그리도록 비정규화한다
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
    -- 순위
    rank_ret               INTEGER NOT NULL,      -- 수익률 내림차순 경쟁 순위
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

-- 드릴다운 전용. 포함된 전 종목을 저장한다. '기타' 합산은 API가 조회할 때 만든다 (docs/02 §9 S-1)
CREATE TABLE group_member_contribution (
    universe_code   TEXT    NOT NULL,
    scheme_code     TEXT    NOT NULL,
    period_type     TEXT    NOT NULL CHECK (period_type IN ('W', 'M')),
    period_id       TEXT    NOT NULL,
    group_code      TEXT    NOT NULL,
    security_id     INTEGER NOT NULL,
    weight_in_group REAL    NOT NULL,
    ret             REAL    NOT NULL,
    contribution    REAL    NOT NULL,             -- weight_in_group × ret
    contrib_rank    INTEGER NOT NULL,             -- 그룹 내 contribution 내림차순 순위
    PRIMARY KEY (universe_code, scheme_code, period_type, period_id, group_code, security_id)
) STRICT;

-- ============================================================ 운영

CREATE TABLE batch_run (
    run_id       INTEGER PRIMARY KEY,
    job_name     TEXT    NOT NULL,                -- daily, prices-daily, aggregate, recalc, backup ...
    market_code  TEXT REFERENCES market (market_code),
    target_scope TEXT,                            -- 예: 'W:2026-W03..2026-W05'
    status       TEXT    NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED')),
    calc_version TEXT,
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    row_count    INTEGER,
    message      TEXT
) STRICT;
CREATE INDEX ix_batch_run_job ON batch_run (job_name, started_at);

-- 수집 검증(C-1 ~ C-13)과 계산 검증(V-1 ~ V-9) 결과. 경고 행이 점검 대상 목록을 겸한다
CREATE TABLE validation_result (
    run_id    INTEGER NOT NULL REFERENCES batch_run (run_id),
    rule_code TEXT    NOT NULL,                   -- C-1 ... C-13, V-1 ... V-9
    scope     TEXT    NOT NULL,                   -- 예: 'KR_COMMON/WI26/W/2026-W36', 'KR/005930/2026-09-14'
    severity  TEXT    NOT NULL CHECK (severity IN ('BLOCK', 'WARN')),
    passed    INTEGER NOT NULL CHECK (passed IN (0, 1)),
    observed  REAL,
    tolerance REAL,
    detail    TEXT,
    PRIMARY KEY (run_id, rule_code, scope)
) STRICT;

CREATE TABLE restatement_log (
    run_id         INTEGER NOT NULL REFERENCES batch_run (run_id),
    security_id    INTEGER NOT NULL REFERENCES security (security_id),
    reason         TEXT    NOT NULL CHECK (reason IN ('FACTOR_CHANGED', 'NEW_SPLIT_EVENT', 'RECONCILE', 'SOURCE_SWITCH')),
    affected_from  TEXT    NOT NULL CHECK (affected_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    affected_to    TEXT    NOT NULL CHECK (affected_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    rows_updated   INTEGER NOT NULL,
    max_rel_change REAL,                          -- max |new_factor / old_factor - 1|
    created_at     TEXT    NOT NULL,
    PRIMARY KEY (run_id, security_id)
) STRICT;

-- 소급 변경으로 생긴 재계산 요청 (docs/04 §4). aggregate 시작 시 기간 오름차순으로 처리한다
CREATE TABLE recalc_request (
    request_id   INTEGER PRIMARY KEY,
    market_code  TEXT    NOT NULL REFERENCES market (market_code),
    scheme_code  TEXT REFERENCES classification_scheme (scheme_code),  -- NULL이면 해당 시장의 전 스킴
    from_date    TEXT    NOT NULL CHECK (from_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- 이 날짜가 속한 기간부터 최신 기간까지
    reason       TEXT    NOT NULL CHECK (reason IN ('ADJ_FACTOR', 'PRICE_CORRECTION', 'MAPPING', 'UNIVERSE', 'GROUP', 'CALC_VERSION')),
    detail       TEXT,
    requested_at TEXT    NOT NULL,
    run_id       INTEGER REFERENCES batch_run (run_id),  -- 처리한 실행
    processed_at TEXT                             -- NULL이면 대기 중. 대기 중인 구간의 API 응답은 stale = true
) STRICT;
CREATE INDEX ix_recalc_pending ON recalc_request (market_code, from_date) WHERE processed_at IS NULL;

-- ============================================================ 유효기간 중첩 방지

-- 같은 유니버스에서 한 종목의 편입 기간은 겹치지 않는다. PK가 같은 행(UPSERT 대상)은 검사에서 뺀다
CREATE TRIGGER tr_um_no_overlap_insert
BEFORE INSERT ON universe_membership
WHEN EXISTS (
    SELECT 1 FROM universe_membership u
    WHERE u.universe_code = NEW.universe_code
      AND u.security_id = NEW.security_id
      AND u.valid_from <> NEW.valid_from
      AND u.valid_from <= NEW.valid_to AND NEW.valid_from <= u.valid_to
)
BEGIN
    SELECT RAISE(ABORT, 'universe_membership: 편입 기간이 겹친다');
END;

CREATE TRIGGER tr_um_no_overlap_update
BEFORE UPDATE OF universe_code, security_id, valid_from, valid_to ON universe_membership
WHEN EXISTS (
    SELECT 1 FROM universe_membership u
    WHERE u.rowid <> OLD.rowid
      AND u.universe_code = NEW.universe_code
      AND u.security_id = NEW.security_id
      AND u.valid_from <= NEW.valid_to AND NEW.valid_from <= u.valid_to
)
BEGIN
    SELECT RAISE(ABORT, 'universe_membership: 편입 기간이 겹친다');
END;

-- 배타 스킴에서는 한 종목이 동시에 두 그룹에 속할 수 없다. 비배타 스킴에서도 같은 그룹 매핑은 기간이 겹치지 않는다
CREATE TRIGGER tr_sgm_no_overlap_insert
BEFORE INSERT ON security_group_map
WHEN EXISTS (
    SELECT 1 FROM security_group_map m
    WHERE m.scheme_code = NEW.scheme_code
      AND m.security_id = NEW.security_id
      AND NOT (m.group_code = NEW.group_code AND m.valid_from = NEW.valid_from)
      AND m.valid_from <= NEW.valid_to AND NEW.valid_from <= m.valid_to
      AND (m.group_code = NEW.group_code
           OR (SELECT s.is_exclusive FROM classification_scheme s WHERE s.scheme_code = NEW.scheme_code) = 1)
)
BEGIN
    SELECT RAISE(ABORT, 'security_group_map: 유효기간이 겹치는 매핑이 있다');
END;

CREATE TRIGGER tr_sgm_no_overlap_update
BEFORE UPDATE OF scheme_code, security_id, group_code, valid_from, valid_to ON security_group_map
WHEN EXISTS (
    SELECT 1 FROM security_group_map m
    WHERE m.rowid <> OLD.rowid
      AND m.scheme_code = NEW.scheme_code
      AND m.security_id = NEW.security_id
      AND m.valid_from <= NEW.valid_to AND NEW.valid_from <= m.valid_to
      AND (m.group_code = NEW.group_code
           OR (SELECT s.is_exclusive FROM classification_scheme s WHERE s.scheme_code = NEW.scheme_code) = 1)
)
BEGIN
    SELECT RAISE(ABORT, 'security_group_map: 유효기간이 겹치는 매핑이 있다');
END;

-- ============================================================ 기준 코드

INSERT INTO market (market_code, market_name, currency, timezone) VALUES
    ('KR', '한국', 'KRW', 'Asia/Seoul'),
    ('US', '미국', 'USD', 'America/New_York');

INSERT INTO universe (universe_code, universe_name, market_code) VALUES
    ('KR_COMMON', '국내 보통주(KOSPI/KOSDAQ)', 'KR'),
    ('US_SP500', 'S&P 500 구성종목', 'US');

INSERT INTO classification_scheme (scheme_code, scheme_name, market_code, scheme_type, is_exclusive, source_note) VALUES
    ('WI26', 'WI26 산업분류', 'KR', 'SECTOR', 1, 'WiseIndex WI26 대분류 (data/wi26_classification.csv)'),
    ('GICS', 'GICS 섹터', 'US', 'SECTOR', 1, 'MSCI GICS Methodology 2024-08판 섹터 (data/gics_classification.csv)');
