"""임시 DB에 작은 시장을 만들어 집계 전 과정을 확인한다."""
import pytest

from theme_radar.calc import aggregate
from theme_radar.db import transaction
from theme_radar.jobs import run_job

DAYS = ["2025-01-02", "2025-01-03",                                     # 2025-W01
        "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10",   # 2025-W02
        "2025-01-13", "2025-01-14", "2025-01-15", "2025-01-16", "2025-01-17"]   # 2025-W03
NOW = "2025-01-20T00:00:00Z"


def add_security(con, ticker, delisting_date=None):
    return con.execute(
        "INSERT INTO security (market_code, board, ticker, name_local, security_type, listing_date, delisting_date, currency) "
        "VALUES ('KR', 'KOSPI', ?, ?, 'COMMON', '2020-01-02', ?, 'KRW')", (ticker, ticker, delisting_date)).lastrowid


def add_prices(con, sid, prices, shares=1_000_000, cap=True):
    con.executemany(
        "INSERT INTO price_daily (security_id, trade_date, close_raw, adj_factor, close_adj, shares_listed, market_cap, "
        "trade_status, price_source, adj_source, fetched_at) VALUES (?, ?, ?, 1.0, ?, ?, ?, 'NORMAL', 'T', 'T', ?)",
        [(sid, d, p, p, shares, p * shares if cap else None, NOW) for d, p in prices.items()])


@pytest.fixture
def market(con):
    """A: 계속 거래 · B: 기간 중 신규상장 · C: 기간 중 폐지 · D: 시총 없음 · E: 기간 말 거래정지"""
    with transaction(con):
        con.executemany("INSERT INTO trading_calendar VALUES ('KR', ?)", [(d,) for d in DAYS])
        con.execute("INSERT INTO classification_group (scheme_code, group_code, group_name, valid_from) VALUES "
                    "('WI26', 'WI620', '반도체', '2020-01-01')")
        con.execute("INSERT INTO classification_group (scheme_code, group_code, group_name, valid_from) VALUES "
                    "('WI26', 'WI500', '은행', '2020-01-01')")
        ids = {}
        ids["A"] = add_security(con, "000001")
        add_prices(con, ids["A"], {d: 100 + i for i, d in enumerate(DAYS)}, shares=10_000_000)
        ids["B"] = add_security(con, "000002")
        add_prices(con, ids["B"], {d: 50 for d in DAYS[4:]})                      # 2025-01-08 상장
        ids["C"] = add_security(con, "000003", delisting_date="2025-01-09")
        add_prices(con, ids["C"], {d: 200 for d in DAYS[:4]})                     # 2025-01-07까지 거래
        ids["D"] = add_security(con, "000004")
        add_prices(con, ids["D"], {d: 30 for d in DAYS}, cap=False)               # 시가총액 없음
        ids["E"] = add_security(con, "000005")
        add_prices(con, ids["E"], {d: 70 for d in DAYS[:8]})                      # 2025-01-13 이후 거래정지
        for name, sid in ids.items():
            valid_to = "2025-01-08" if name == "C" else "9999-12-31"
            con.execute("INSERT INTO universe_membership VALUES ('KR_COMMON', ?, '2020-01-02', ?)", (sid, valid_to))
            group = "WI500" if name in ("B", "E") else "WI620"
            con.execute("INSERT INTO security_group_map (scheme_code, security_id, group_code, valid_from) VALUES "
                        "('WI26', ?, ?, '2020-01-01')", (sid, group))
    return ids


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
