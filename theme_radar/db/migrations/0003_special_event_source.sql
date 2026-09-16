-- 0003_special_event_source: 특이사항에 출처를 남긴다
--
-- 사건마다 어느 수집 출처에서 나온 값인지 적는다 (docs/07 §11.2). 값은 원천 테이블의 출처 코드와 같다
-- (corporate_action.source, shares_observation.source). 편입·편출은 시장별 유니버스 출처, 데이터 공백은
-- 우리 판정이라 INTERNAL이다.
--
-- special_event는 실행마다 시장 단위로 다시 뽑으므로 여기서 기존 행을 채우지 않는다. 다음 수집
-- (daily·backfill·reconcile·shares)에서 채워진다. 그 전까지는 NULL이다.

ALTER TABLE special_event ADD COLUMN source TEXT;
