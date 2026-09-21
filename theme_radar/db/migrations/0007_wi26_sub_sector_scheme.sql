-- 0007_wi26_sub_sector_scheme: WI26 소분류 스킴 추가
--
-- 대분류 26개보다 잘게 나눈 소분류 48개를 집계 단위로 쓰는 스킴이다 (docs/02 §9 S-2).
-- 기존 WI26 대분류 스킴은 그대로 두고 나란히 둔다.
--
-- 소분류는 WiseIndex에서 직접 받을 수 없어 WICS 소분류를 경유해 파생한 값이다. 근거와 검증은
-- data/README.md §7에 있다. 매핑 파일은 data/wi26_sub_constituents.csv 다.
--
-- 스킴 행만 넣는다. 그룹과 매핑은 load-mapping --scheme WI26_SUB 가 적재한다.

INSERT INTO classification_scheme (scheme_code, scheme_name, market_code, scheme_type, is_exclusive, source_note) VALUES
    ('WI26_SUB', 'WI26 소분류', 'KR', 'SECTOR', 1, 'WiseIndex WI26 소분류, WICS 소분류 경유 (data/wi26_sub_constituents.csv)');
