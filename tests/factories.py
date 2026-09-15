"""테스트용 작은 시장 데이터. 계산·API 테스트가 함께 쓴다."""
from __future__ import annotations

from theme_radar.db import transaction

DAYS = ["2025-01-02", "2025-01-03",                                             # 2025-W01
        "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10",   # 2025-W02
        "2025-01-13", "2025-01-14", "2025-01-15", "2025-01-16", "2025-01-17"]   # 2025-W03
NOW = "2025-01-20T00:00:00Z"


def add_security(con, ticker, delisting_date=None, name=None):
    return con.execute(
        "INSERT INTO security (market_code, board, ticker, name_local, name_en, security_type, listing_date, "
        "delisting_date, currency) VALUES ('KR', 'KOSPI', ?, ?, ?, 'COMMON', '2020-01-02', ?, 'KRW')",
        (ticker, name or ticker, f"Company {ticker}", delisting_date)).lastrowid


def add_prices(con, sid, prices, shares=1_000_000, cap=True):
    con.executemany(
        "INSERT INTO price_daily (security_id, trade_date, close_raw, adj_factor, close_adj, shares_listed, market_cap, "
        "trade_status, price_source, adj_source, fetched_at) VALUES (?, ?, ?, 1.0, ?, ?, ?, 'NORMAL', 'T', 'T', ?)",
        [(sid, d, p, p, shares, p * shares if cap else None, NOW) for d, p in prices.items()])


def build_market(con) -> dict[str, int]:
    """A: 계속 거래 · B: 기간 중 신규상장 · C: 기간 중 폐지 · D: 시총 없음 · E: 기간 말 거래정지"""
    with transaction(con):
        con.executemany("INSERT INTO trading_calendar VALUES ('KR', ?)", [(d,) for d in DAYS])
        con.executemany("INSERT INTO classification_group (scheme_code, group_code, group_name, group_name_en, "
                        "sort_order, color_hex, valid_from) VALUES ('WI26', ?, ?, ?, ?, ?, '2020-01-01')",
                        [("WI620", "반도체", "Semiconductors", 1, "#2a78d6"),
                         ("WI500", "은행", "Banks", 2, "#008300")])
        ids = {}
        ids["A"] = add_security(con, "000001", name="에이사")
        add_prices(con, ids["A"], {d: 100 + i for i, d in enumerate(DAYS)}, shares=10_000_000)
        ids["B"] = add_security(con, "000002", name="비사")
        add_prices(con, ids["B"], {d: 50 for d in DAYS[4:]})                      # 2025-01-08 상장
        ids["C"] = add_security(con, "000003", delisting_date="2025-01-09", name="씨사")
        add_prices(con, ids["C"], {d: 200 for d in DAYS[:4]})                     # 2025-01-07까지 거래
        ids["D"] = add_security(con, "000004", name="디사")
        add_prices(con, ids["D"], {d: 30 for d in DAYS}, cap=False)               # 시가총액 없음
        ids["E"] = add_security(con, "000005", name="이사")
        add_prices(con, ids["E"], {d: 70 for d in DAYS[:8]})                      # 2025-01-13 이후 거래정지
        for name, sid in ids.items():
            valid_to = "2025-01-08" if name == "C" else "9999-12-31"
            con.execute("INSERT INTO universe_membership VALUES ('KR_COMMON', ?, '2020-01-02', ?)", (sid, valid_to))
            group = "WI500" if name in ("B", "E") else "WI620"
            con.execute("INSERT INTO security_group_map (scheme_code, security_id, group_code, valid_from) VALUES "
                        "('WI26', ?, ?, '2020-01-01')", (sid, group))
    return ids
