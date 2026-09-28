-- 0008_theme_kr_scheme: KR 테마 스킴 추가
--
-- 산업분류(WI26) 옆에 두는 두 번째 층이다. 방산·원전·전력설비처럼 여러 업종에 걸친 흐름을 본다.
-- 비배타 스킴이라 한 종목이 여러 테마에 속하고, 기여도 분해는 없다 (docs/01 §4).
--
-- 볼 테마는 data/theme_kr_list.csv 에 사람이 정하고, 구성종목은 scripts/fetch_themes_kr.py 가
-- 네이버 테마에서 받아 data/theme_kr_constituents.csv 로 쓴다. 구성은 받은 날의 스냅샷이며
-- 그 전 기간은 소급 계산이다 (docs/02 §9 S-20).
--
-- 스킴 행만 넣는다. 그룹과 매핑은 load-mapping --scheme THEME_KR 가 적재한다.

INSERT INTO classification_scheme (scheme_code, scheme_name, market_code, scheme_type, is_exclusive, source_note) VALUES
    ('THEME_KR', 'KR 테마', 'KR', 'THEME', 0, '네이버 테마 중 사람이 고른 것 (data/theme_kr_list.csv, data/theme_kr_constituents.csv)');
