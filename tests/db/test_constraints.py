"""0001_init.sql의 제약이 잘못된 데이터를 막는지 확인한다."""
import sqlite3

import pytest

NOW = "2026-09-15T00:00:00Z"


def add_security(con, ticker="005930", delisting_date=None):
    return con.execute(
        "INSERT INTO security (market_code, board, ticker, name_local, security_type, listing_date, delisting_date, currency) "
        "VALUES ('KR', 'KOSPI', ?, '종목', 'COMMON', '2000-01-04', ?, 'KRW')",
        (ticker, delisting_date),
    ).lastrowid


def add_groups(con, scheme, *codes):
    for code in codes:
        con.execute("INSERT INTO classification_group (scheme_code, group_code, group_name, valid_from) VALUES (?, ?, ?, '2000-01-01')",
                    (scheme, code, code))


def add_map(con, sid, group, valid_from, valid_to="9999-12-31", scheme="WI26"):
    con.execute("INSERT INTO security_group_map (scheme_code, security_id, group_code, valid_from, valid_to) VALUES (?, ?, ?, ?, ?)",
                (scheme, sid, group, valid_from, valid_to))


def add_price(con, sid, trade_date="2026-09-14", close_raw=71200.0, adj_factor=1.0, close_adj=71200.0, market_cap=None):
    con.execute(
        "INSERT INTO price_daily (security_id, trade_date, close_raw, adj_factor, close_adj, market_cap, trade_status, "
        "price_source, adj_source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, 'NORMAL', 'NAVER_MPRICE', 'NAVER_SISEJSON', ?)",
        (sid, trade_date, close_raw, adj_factor, close_adj, market_cap, NOW),
    )


# ------------------------------------------------------------ 타입·형식·외래키

def test_strict_rejects_wrong_type(con):
    sid = add_security(con)
    with pytest.raises(sqlite3.IntegrityError, match="cannot store TEXT"):
        add_price(con, sid, close_raw="71,200")


@pytest.mark.parametrize("bad_date", ["2026/09/14", "2026-09-14 00:00:00", "20260914"])
def test_date_format_is_enforced(con, bad_date):
    sid = add_security(con)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_price(con, sid, trade_date=bad_date)


def test_foreign_key_is_enforced(con):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        add_price(con, 999)


def test_code_values_are_enforced(con):
    sid = add_security(con)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        con.execute("UPDATE security SET security_type = 'COMMON_STOCK' WHERE security_id = ?", (sid,))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        con.execute("INSERT INTO classification_scheme VALUES ('X', 'x', 'KR', 'SECTOR', 0, NULL)")


def test_period_id_must_match_period_type(con):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        con.execute("INSERT INTO period_calendar VALUES ('KR', 'W', '2026-09', 1, '2026-09-01', '2026-09-30', NULL, '2026-09-30', 21, 1)")
    con.execute("INSERT INTO period_calendar VALUES ('KR', 'W', '2026-W37', 1, '2026-09-07', '2026-09-13', NULL, '2026-09-11', 5, 1)")


def test_unmapped_is_not_a_registered_group(con):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_groups(con, "WI26", "UNMAPPED")


# ------------------------------------------------------------ price_daily

def test_close_raw_null_requires_adj_factor_and_market_cap_null(con):
    sid = add_security(con)
    add_price(con, sid, "2026-07-16", close_raw=None, adj_factor=None, close_adj=87300.0)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_price(con, sid, "2026-07-17", close_raw=None, adj_factor=1.0, close_adj=87300.0)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_price(con, sid, "2026-07-20", close_raw=None, adj_factor=None, close_adj=87300.0, market_cap=1e12)


# ------------------------------------------------------------ security 티커

def test_active_ticker_is_unique_but_reusable_after_delisting(con):
    add_security(con, "123450", delisting_date="2025-03-31")
    add_security(con, "123450")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        add_security(con, "123450")


# ------------------------------------------------------------ 유효기간 중첩

def test_exclusive_scheme_rejects_overlapping_groups(con):
    add_groups(con, "WI26", "WI610", "WI620")
    sid = add_security(con)
    add_map(con, sid, "WI620", "2020-01-01")
    with pytest.raises(sqlite3.IntegrityError, match="겹치는"):
        add_map(con, sid, "WI610", "2025-01-01")


def test_exclusive_scheme_allows_close_out_then_insert(con):
    add_groups(con, "WI26", "WI610", "WI620")
    sid = add_security(con)
    add_map(con, sid, "WI620", "2020-01-01")
    con.execute("UPDATE security_group_map SET valid_to = '2024-12-31' WHERE security_id = ? AND group_code = 'WI620'", (sid,))
    add_map(con, sid, "WI610", "2025-01-01")
    assert con.execute("SELECT COUNT(*) FROM security_group_map").fetchone()[0] == 2


def test_update_that_creates_overlap_is_rejected(con):
    add_groups(con, "WI26", "WI610", "WI620")
    sid = add_security(con)
    add_map(con, sid, "WI620", "2020-01-01", "2024-12-31")
    add_map(con, sid, "WI610", "2025-01-01")
    with pytest.raises(sqlite3.IntegrityError, match="겹치는"):
        con.execute("UPDATE security_group_map SET valid_to = '2025-06-30' WHERE group_code = 'WI620'")


def test_upsert_of_same_mapping_is_allowed(con):
    add_groups(con, "WI26", "WI620")
    sid = add_security(con)
    add_map(con, sid, "WI620", "2020-01-01")
    con.execute(
        "INSERT INTO security_group_map (scheme_code, security_id, group_code, valid_from, source_batch) "
        "VALUES ('WI26', ?, 'WI620', '2020-01-01', 'batch-2') "
        "ON CONFLICT DO UPDATE SET source_batch = excluded.source_batch",
        (sid,),
    )
    assert con.execute("SELECT source_batch FROM security_group_map").fetchone()[0] == "batch-2"


def test_theme_scheme_allows_multiple_groups_but_not_same_group_overlap(con):
    con.execute("INSERT INTO classification_scheme VALUES ('THEME_TEST', '테스트 테마', 'KR', 'THEME', 0, NULL)")
    add_groups(con, "THEME_TEST", "AI", "ROBOT")
    sid = add_security(con)
    add_map(con, sid, "AI", "2025-01-01", scheme="THEME_TEST")
    add_map(con, sid, "ROBOT", "2025-01-01", scheme="THEME_TEST")
    with pytest.raises(sqlite3.IntegrityError, match="겹치는"):
        add_map(con, sid, "AI", "2025-06-01", scheme="THEME_TEST")


def test_universe_membership_rejects_overlap_and_allows_adjacent(con):
    sid = add_security(con)
    con.execute("INSERT INTO universe_membership VALUES ('KR_COMMON', ?, '2020-01-02', '2024-12-30')", (sid,))
    con.execute("INSERT INTO universe_membership VALUES ('KR_COMMON', ?, '2024-12-31', '9999-12-31')", (sid,))
    with pytest.raises(sqlite3.IntegrityError, match="겹친다"):
        con.execute("INSERT INTO universe_membership VALUES ('KR_COMMON', ?, '2024-06-01', '9999-12-31')", (sid,))


# ------------------------------------------------------------ special_event

def test_market_level_special_event_is_recorded_once(con):
    sql = ("INSERT INTO special_event (market_code, event_date, end_date, event_type, detail, created_at) "
           "VALUES ('KR', '2025-01-02', '2026-03-06', 'DATA_GAP', 'G-1 상장주식수 근사 구간', ?)")
    con.execute(sql, (NOW,))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        con.execute(sql, (NOW,))
