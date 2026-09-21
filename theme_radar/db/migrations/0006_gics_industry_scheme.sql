-- 0006_gics_industry_scheme: GICS 산업(6자리) 스킴 추가
--
-- 섹터 11개보다 잘게 나눈 산업 74개를 집계 단위로 쓰는 스킴이다 (docs/02 §9 S-2 개정).
-- 기존 GICS 섹터 스킴은 그대로 두고 나란히 둔다. 종목 매핑은 같은 파일
-- (data/gics_sp500_constituents.csv)의 industry_code 컬럼에서 온다.
--
-- 스킴 행만 넣는다. 그룹과 매핑은 load-mapping --scheme GICS_IND 가 적재한다.

INSERT INTO classification_scheme (scheme_code, scheme_name, market_code, scheme_type, is_exclusive, source_note) VALUES
    ('GICS_IND', 'GICS 산업', 'US', 'SECTOR', 1, 'MSCI GICS Methodology 2024-08판 산업 (data/gics_classification.csv)');
