"""임시 DB에 작은 시장을 만들어 집계 전 과정을 확인한다. 시장 구성은 tests/factories.py에 있다."""
import pytest

from theme_radar.calc import aggregate
from theme_radar.db import transaction
from theme_radar.jobs import run_job

NOW = "2025-01-20T00:00:00Z"


def aggregate_all(con, today="2025-01-20", full=True):
    with run_job(con, "aggregate", "KR") as run:
        aggregate.run(con, run, "KR", today, full, lambda message: None)
    return run


def test_aggregate_writes_all_derived_tables(con, market):
    run = aggregate_all(con)
    assert not run.blocked
    weeks = [r[0] for r in con.execute("SELECT period_id FROM universe_period_stat WHERE period_type = 'W' ORDER BY period_seq")]
    assert weeks == ["2025-W02", "2025-W03"]        # 첫 기간은 기준일이 없어 계산하지 않는다
    assert con.execute("SELECT COUNT(*) FROM period_calendar WHERE period_type = 'W'").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM group_member_contribution").fetchone()[0] > 0


def test_inclusion_status_per_member(con, market):
    aggregate_all(con)
    status = dict(con.execute("SELECT s.ticker, r.incl_status FROM security_period_return r JOIN security s USING (security_id) "
                              "WHERE r.period_type = 'W' AND r.period_id = '2025-W02'"))
    assert status == {"000001": "INCLUDED", "000002": "NEW_LISTING", "000003": "DELISTED", "000004": "NO_MCAP", "000005": "INCLUDED"}
    later = dict(con.execute("SELECT s.ticker, r.incl_status FROM security_period_return r JOIN security s USING (security_id) "
                             "WHERE r.period_type = 'W' AND r.period_id = '2025-W03'"))
    assert later["000002"] == "INCLUDED"            # 다음 기간부터 편입
    assert later["000005"] == "SUSPENDED"           # 종료일 행이 없어 직전 종가를 잇는다
    assert "000003" not in later                    # 폐지 종목은 기준일 유니버스에 없다


def test_group_stats_and_rank_delta(con, market):
    aggregate_all(con)
    rows = {r[0]: r[1:] for r in con.execute(
        "SELECT group_code, ret, base_weight, contribution, rank_ret, rank_ret_prev, rank_delta, member_cnt, is_provisional "
        "FROM group_period_stat WHERE period_type = 'W' AND period_id = '2025-W03'")}
    # 2025-W03은 기준일 2025-01-10(A 106), 종료일 2025-01-17(A 111). E는 거래정지로 70 유지, B는 50 유지
    assert rows["WI620"][0] == pytest.approx(111 / 106 - 1)
    assert rows["WI500"][0] == pytest.approx(0.0)
    assert rows["WI620"][3] == 1 and rows["WI500"][3] == 2          # 순위
    assert rows["WI620"][4] == 1 and rows["WI620"][5] == 0          # 직전 기간에도 1위 (변동 없음)
    assert rows["WI500"][6] == 2 and rows["WI620"][6] == 1          # 구성 종목 수 (D는 시총 없음으로 제외)
    assert rows["WI620"][7] == 0                                    # 캘린더 종료일이 지난 확정 기간

    universe = con.execute("SELECT ret, member_cnt, up_cnt, unmapped_cap_ratio FROM universe_period_stat "
                           "WHERE period_type = 'W' AND period_id = '2025-W03'").fetchone()
    assert universe[1] == 3 and universe[3] == 0
    assert universe[0] == pytest.approx(sum(r[2] for r in rows.values()), abs=1e-12)   # V-3: 기여도 합 = 시장 수익률


def test_provisional_period_is_marked_and_recomputed(con, market):
    aggregate_all(con, today="2025-01-16")           # 2025-W03이 아직 진행 중
    row = con.execute("SELECT is_provisional, ret FROM universe_period_stat WHERE period_id = '2025-W03'").fetchone()
    assert row[0] == 1
    aggregate_all(con, today="2025-01-20", full=False)
    assert con.execute("SELECT is_provisional FROM universe_period_stat WHERE period_id = '2025-W03'").fetchone()[0] == 0


def test_recalc_request_is_marked_processed(con, market):
    aggregate_all(con)
    with transaction(con):
        con.execute("INSERT INTO recalc_request (market_code, from_date, reason, requested_at) "
                    "VALUES ('KR', '2025-01-13', 'MAPPING', ?)", (NOW,))
    aggregate_all(con, full=False)
    assert con.execute("SELECT COUNT(*) FROM recalc_request WHERE processed_at IS NULL").fetchone()[0] == 0
