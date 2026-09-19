-- 0005_market_index_daily: 시장 지수 일봉
--
-- 화면 오른쪽의 시장 지수 캔들 차트(docs/06 §3.6)가 쓴다. 거래일 캘린더를 만들 때 이미 받아 오던
-- 지수 일봉(KR 네이버 siseJson의 KOSPI, US yfinance의 ^GSPC)을 날짜만 쓰고 버리지 않고 남긴다.
-- 수집이 채우는 원천 테이블이다. 기간 단위 캔들 값은 API가 조회할 때 만든다 (docs/05 §4.2).
--
-- 지수는 우리가 계산하는 유니버스 수익률과 다른 값이다. 구성 종목도 산출식도 우리 것이 아니라
-- 거래소 공식 지수이며, 섹터 흐름을 볼 때 시장 전체의 배경으로만 쓴다 (docs/01 §8).

CREATE TABLE market_index_daily (
    market_code TEXT NOT NULL REFERENCES market (market_code),
    index_code  TEXT NOT NULL,                    -- KOSPI, SPX. 시장마다 기본 지수 하나를 쓴다
    trade_date  TEXT NOT NULL CHECK (trade_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    open        REAL NOT NULL CHECK (open > 0),   -- 지수 포인트. 통화 단위가 없다
    high        REAL NOT NULL CHECK (high > 0),
    low         REAL NOT NULL CHECK (low > 0),
    close       REAL NOT NULL CHECK (close > 0),
    source      TEXT NOT NULL,                    -- NAVER_SISE | YFINANCE
    PRIMARY KEY (market_code, index_code, trade_date),
    CHECK (low <= open AND low <= close AND open <= high AND close <= high)
) STRICT;
