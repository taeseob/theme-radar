from theme_radar.db import transaction
from theme_radar.prices import store
from theme_radar.prices.adjust import PriceRow
from theme_radar.prices.events import Event, shares_source, upsert_event

NOW = "2026-09-16T00:00:00Z"


def add_security(con, ticker="001080"):
    return con.execute("INSERT INTO security (market_code, board, ticker, name_local, security_type, currency) "
                       "VALUES ('KR', 'KOSPI', ?, '종목', 'COMMON', 'KRW')", (ticker,)).lastrowid


def test_write_prices_inserts_new_and_updates_only_changed_factors(con):
    sid = add_security(con)
    rows = [PriceRow("2026-03-06", 54_400, 1.0, 54_400, 10, "NORMAL"), PriceRow("2026-03-09", 5_010, 1.0, 5_010, 10, "NORMAL")]
    with transaction(con):
        first = store.write_prices(con, sid, rows, {}, "KR", "NAVER_MPRICE", "NAVER_SISEJSON", NOW)
    assert first.inserted == 2 and first.changed_dates == []

    # 10:1 분할이 소급 반영된 수정 종가를 다시 받았다
    restated = [PriceRow("2026-03-06", 54_400, 0.1, 5_440, 10, "NORMAL"), PriceRow("2026-03-09", 5_010, 1.0, 5_010, 10, "NORMAL")]
    with transaction(con):
        second = store.write_prices(con, sid, restated, store.stored_prices(con, sid), "KR", "NAVER_MPRICE", "NAVER_SISEJSON", NOW)
    assert second.inserted == 0 and second.changed_dates == ["2026-03-06"]
    assert con.execute("SELECT close_raw, adj_factor, close_adj FROM price_daily WHERE trade_date = '2026-03-06'").fetchone() == (54_400, 0.1, 5_440)


def test_refresh_shares_uses_source_priority_and_splits(con):
    sid = add_security(con)
    with transaction(con):
        store.write_prices(con, sid, [PriceRow("2026-03-06", 54_400, 0.1, 5_440, 1, "NORMAL"),
                                      PriceRow("2026-03-09", 5_010, 1.0, 5_010, 1, "NORMAL")], {}, "KR", "NAVER_MPRICE", "NAVER_SISEJSON", NOW)
        store.add_observations(con, [(sid, "2026-03-06", 4_150_000, "FDR_KRX_CACHE"), (sid, "2026-03-06", 4_000_000, "FDR_KRX_DELISTING")],
                               "AS_REPORTED", NOW)
        con.execute("INSERT INTO corporate_action VALUES (?, '2026-03-09', 'SPLIT', 0.1, 10, 'NAVER_FACTOR_JUMP', ?)", (sid, NOW))
        assert store.refresh_shares(con, "KR") == (None, [])
    assert con.execute("SELECT shares_listed, market_cap FROM price_daily ORDER BY trade_date").fetchall() == [
        (4_150_000, 54_400 * 4_150_000), (41_500_000, 5_010 * 41_500_000)]


def test_upsert_event_keeps_one_row_per_event_including_market_level(con):
    with transaction(con):
        for cap in (1.0, 2.0):
            upsert_event(con, "KR", Event("2025-01-01", "DATA_GAP", f"G-1 {cap}", end_date="2026-03-08", market_cap=cap), None, None, NOW)
    assert con.execute("SELECT COUNT(*), MAX(market_cap), MAX(detail), MAX(source) FROM special_event").fetchone() \
        == (1, 2.0, "G-1 2.0", "INTERNAL")


def test_shares_source_follows_as_of_rule_and_priority(con):
    sid = add_security(con)
    with transaction(con):
        store.add_observations(con, [(sid, "2026-03-02", 4_000_000, "SEC_DEI"),
                                     (sid, "2026-03-06", 4_150_000, "FDR_KRX_CACHE"),
                                     (sid, "2026-03-06", 4_150_000, "DAUM_QUOTE")], "AS_REPORTED", NOW)
    assert shares_source(con, sid, "2026-03-06") == "DAUM_QUOTE"      # 같은 날은 출처 우선순위대로
    assert shares_source(con, sid, "2026-03-09") == "DAUM_QUOTE"      # 그날 관측치가 없으면 앞의 최신 관측치
    assert shares_source(con, sid, "2026-03-03") == "SEC_DEI"
    assert shares_source(con, sid, "2026-03-01") is None
