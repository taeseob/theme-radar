"""수집 결과의 DB 반영. 호출하는 쪽이 transaction()으로 묶는다."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from theme_radar.prices.adjust import (PLAUSIBLE_MARKET_CAP, Action, Observation, PriceRow, drop_implausible, factor_changed,
                                      fill_shares)

# 같은 날 관측치가 여러 출처에 있을 때의 우선순위 (docs/07 §6)
SHARES_PRIORITY = ["DAUM_QUOTE", "FDR_KRX_CACHE", "FDR_KRX_DELISTING", "SEC_DEI", "YF_INFO"]


def replace_trading_days(con: sqlite3.Connection, market: str, days: list[str], since: str) -> None:
    con.execute("DELETE FROM trading_calendar WHERE market_code = ? AND trade_date >= ?", (market, since))
    con.executemany("INSERT INTO trading_calendar (market_code, trade_date) VALUES (?, ?)", [(market, d) for d in days if d >= since])


def replace_index_bars(con: sqlite3.Connection, market: str, index_code: str, bars, source: str, since: str) -> int:
    """시장 지수 일봉을 since 이후 구간만 다시 쓴다 (docs/07 §12).

    지수는 소급 수정이 없어 받은 값을 그대로 둔다. 값이 어긋난 날(고가 < 종가 등)은 넣지 않는다.
    바로잡을 근거가 없는 한 날이라 버리고, 그 자리는 차트에서 빈다.
    """
    rows = [(market, index_code, b.trade_date, b.open, b.high, b.low, b.close, source) for b in bars
            if b.trade_date >= since and min(b.open, b.high, b.low, b.close) > 0
            and b.low <= min(b.open, b.close) and max(b.open, b.close) <= b.high]
    con.execute("DELETE FROM market_index_daily WHERE market_code = ? AND index_code = ? AND trade_date >= ?",
                (market, index_code, since))
    con.executemany("INSERT INTO market_index_daily (market_code, index_code, trade_date, open, high, low, close, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def trading_days(con: sqlite3.Connection, market: str) -> list[str]:
    return [r[0] for r in con.execute("SELECT trade_date FROM trading_calendar WHERE market_code = ? ORDER BY trade_date", (market,))]


def stored_prices(con: sqlite3.Connection, security_id: int) -> dict[str, tuple[float | None, float | None]]:
    """{날짜: (close_raw, adj_factor)}"""
    return {d: (raw, f) for d, raw, f in con.execute(
        "SELECT trade_date, close_raw, adj_factor FROM price_daily WHERE security_id = ?", (security_id,))}


@dataclass
class WriteResult:
    inserted: int = 0
    changed_dates: list[str] | None = None
    max_rel_change: float = 0.0


def write_prices(con: sqlite3.Connection, security_id: int, rows: list[PriceRow], stored: dict, market: str,
                 price_source: str, adj_source: str, now: str) -> WriteResult:
    """새 날짜는 넣고, 저장된 날짜는 수정계수가 허용오차를 넘게 바뀐 행만 갱신한다. 원종가는 비어 있을 때만 채운다."""
    result = WriteResult(changed_dates=[])
    inserts, updates = [], []
    for r in rows:
        if r.trade_date not in stored:
            inserts.append((security_id, r.trade_date, r.close_raw, r.adj_factor, r.close_adj, r.volume, r.trade_status,
                            price_source, adj_source, now))
            continue
        old_raw, old_factor = stored[r.trade_date]
        raw_filled = old_raw is None and r.close_raw is not None
        if raw_filled or factor_changed(r.adj_factor, old_factor, r.close_adj, market):
            if old_factor is not None and r.adj_factor is not None:
                result.max_rel_change = max(result.max_rel_change, abs(r.adj_factor / old_factor - 1))
            if not raw_filled:
                result.changed_dates.append(r.trade_date)
            updates.append((r.close_raw, r.adj_factor, r.close_adj, adj_source, now, security_id, r.trade_date))
    con.executemany(
        "INSERT INTO price_daily (security_id, trade_date, close_raw, adj_factor, close_adj, volume, trade_status, "
        "price_source, adj_source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", inserts)
    con.executemany(
        "UPDATE price_daily SET close_raw = COALESCE(close_raw, ?), adj_factor = ?, close_adj = ?, adj_source = ?, "
        "adj_updated_at = ? WHERE security_id = ? AND trade_date = ?", updates)
    result.inserted = len(inserts)
    return result


def record_actions(con: sqlite3.Connection, security_id: int, actions: list[Action], source: str, now: str) -> int:
    before = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO corporate_action (security_id, ex_date, action_type, price_factor, share_ratio, source, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(security_id, a.ex_date, a.action_type, a.price_factor, a.share_ratio, source, now) for a in actions])
    return con.total_changes - before


def record_restatement(con: sqlite3.Connection, run_id: int, security_id: int, market: str, changed: list[str],
                       max_rel: float, now: str) -> None:
    changed = sorted(changed)
    con.execute(
        "INSERT OR REPLACE INTO restatement_log (run_id, security_id, reason, affected_from, affected_to, rows_updated, "
        "max_rel_change, created_at) VALUES (?, ?, 'FACTOR_CHANGED', ?, ?, ?, ?, ?)",
        (run_id, security_id, changed[0], changed[-1], len(changed), max_rel, now))
    request_recalc(con, market, changed[0], "ADJ_FACTOR", f"security_id={security_id}", now)


def request_recalc(con: sqlite3.Connection, market: str, from_date: str, reason: str, detail: str, now: str) -> None:
    con.execute("INSERT INTO recalc_request (market_code, from_date, reason, detail, requested_at) VALUES (?, ?, ?, ?, ?)",
                (market, from_date, reason, detail, now))


def remove_observations(con: sqlite3.Connection, security_ids: list[int], source: str) -> None:
    con.executemany("DELETE FROM shares_observation WHERE security_id = ? AND source = ?", [(sid, source) for sid in security_ids])


def add_observations(con: sqlite3.Connection, rows: list[tuple[int, str, int, str]], basis: str, now: str) -> None:
    """rows: (security_id, as_of_date, shares, source)"""
    con.executemany(
        "INSERT OR REPLACE INTO shares_observation (security_id, as_of_date, shares, basis, source, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", [(*r[:3], basis, r[3], now) for r in rows])


def refresh_shares(con: sqlite3.Connection, market: str) -> tuple[str | None, list[str]]:
    """시장 전 종목의 shares_listed·market_cap을 관측치와 분할·병합 기록으로 다시 채운다.

    반환: (값이 바뀐 가장 이른 날짜, 믿기 어려워 버린 관측치 설명)
    """
    priority = {s: i for i, s in enumerate(SHARES_PRIORITY)}
    earliest: str | None = None
    dropped_all: list[str] = []
    ids = [r[0] for r in con.execute(
        "SELECT DISTINCT p.security_id FROM price_daily p JOIN security s USING (security_id) WHERE s.market_code = ?", (market,))]
    for sid in ids:
        rows = con.execute("SELECT trade_date, close_raw, adj_factor, shares_listed, market_cap FROM price_daily "
                           "WHERE security_id = ? ORDER BY trade_date", (sid,)).fetchall()
        best: dict[str, tuple[int, int]] = {}
        for as_of, shares, source in con.execute("SELECT as_of_date, shares, source FROM shares_observation WHERE security_id = ?", (sid,)):
            rank = priority.get(source, len(priority))
            if as_of not in best or rank < best[as_of][0]:
                best[as_of] = (rank, shares)
        splits = [(d, r) for d, r in con.execute(
            "SELECT ex_date, MAX(share_ratio) FROM corporate_action WHERE security_id = ? AND share_ratio IS NOT NULL GROUP BY ex_date", (sid,))]
        observations, dropped = drop_implausible([Observation(d, s) for d, (_, s) in best.items()],
                                                 [(d, raw) for d, raw, _, _, _ in rows], PLAUSIBLE_MARKET_CAP[market])
        dropped_all.extend(f"security_id={sid} {o.as_of_date} {o.shares:,}" for o in dropped)
        shares = fill_shares([(d, f) for d, _, f, _, _ in rows], observations, splits)
        updates = []
        for (d, raw, _, old_shares, old_cap), new_shares in zip(rows, shares):
            new_cap = raw * new_shares if raw is not None and new_shares is not None else None
            if new_shares != old_shares or (new_cap is None) != (old_cap is None) or (new_cap and abs(new_cap - old_cap) > 1e-6 * new_cap):
                updates.append((new_shares, new_cap, sid, d))
                if old_shares is not None and (earliest is None or d < earliest):
                    earliest = d
        con.executemany("UPDATE price_daily SET shares_listed = ?, market_cap = ? WHERE security_id = ? AND trade_date = ?", updates)
    return earliest, dropped_all
