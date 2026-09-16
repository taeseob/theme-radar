-- 0004_group_daily_cap: 섹터 일별 시가총액
--
-- 드릴다운의 섹터 시가총액 차트(docs/06 §5.4)가 쓴다. 거래일마다 그날의 유니버스 구성원과 분류 매핑으로
-- 그룹별 시가총액을 더해 둔다 (docs/03 §14). 기간 단위 값(시가·고가·저가·종가)은 API가 조회할 때 만들고,
-- 이동평균은 화면이 계산하므로 저장하지 않는다.
-- 원천에서 다시 만드는 파생 테이블이다. 다음 aggregate가 비어 있는 날짜부터 채운다.

CREATE TABLE group_daily_cap (
    universe_code TEXT    NOT NULL,
    scheme_code   TEXT    NOT NULL,
    group_code    TEXT    NOT NULL,               -- 미매핑 종목은 'UNMAPPED'
    trade_date    TEXT    NOT NULL CHECK (trade_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    market_cap    REAL    NOT NULL CHECK (market_cap > 0),  -- 그날 시총이 있는 구성 종목의 market_cap 합. 시장 통화
    member_cnt    INTEGER NOT NULL CHECK (member_cnt > 0),  -- 더한 종목 수
    PRIMARY KEY (universe_code, scheme_code, group_code, trade_date)
) STRICT;
CREATE INDEX ix_gdc_date ON group_daily_cap (universe_code, scheme_code, trade_date);
