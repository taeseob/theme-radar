"""특이사항(special_event) 생성 (docs/07 §11.2).

가격이 아닌 이유로 섹터 시총을 바꾸거나 계산에 오차를 남기는 사건을 수집 데이터에서 실행마다 다시 뽑는다.
같은 사건은 (시장, 유형, 종목, 날짜)로 한 번만 기록하고, 계속 나오는 사건은 처음 기록한 시각을 유지한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from theme_radar.db import transaction
from theme_radar.prices.context import Context
from theme_radar.prices.sources import sec
from theme_radar.prices.store import SHARES_PRIORITY
from theme_radar.prices.universe import day_before

# 사건을 기록할 (배타 스킴, 유니버스). 시장마다 계층이 다른 배타 스킴이 여럿이지만(docs/02 §9 S-2),
# 사건 규모는 섹터 시총과 견주는 값이라 세분류가 아니라 섹터 레벨에 기록한다 (docs/07 §11.2)
SCHEMES = {"KR": ("WI26", "KR_COMMON"), "US": ("GICS", "US_SP500")}
# 유니버스 편입 이력의 출처는 시장마다 고정이다 (docs/07 §7.1, §8.1). 편입 이력 자체에는 행마다 출처가 없다
UNIVERSE_SOURCE = {"KR": {"LISTING": "KIND_LISTING", "DELISTING": "FDR_KRX_DELISTING"},
                   "US": {"LISTING": "WIKI_SP500", "DELISTING": "WIKI_SP500"}}
INTERNAL = "INTERNAL"               # 바깥 출처가 아니라 우리 판정이다. 근거는 detail의 G-번호 (docs/07 §2.2)


@dataclass
class Event:
    event_date: str
    event_type: str
    detail: str
    security_id: int | None = None
    end_date: str | None = None
    market_cap: float | None = None
    cap_date: str | None = None     # 섹터 비중을 계산할 거래일
    source: str | None = INTERNAL   # 이 사건을 만든 값의 출처 (docs/07 §11.2). 모르면 NULL


def build_events(ctx: Context) -> int:
    """시장의 특이사항을 모두 다시 만든다. 반환: 사건 수"""
    con, market = ctx.con, ctx.market
    universe = SCHEMES[market][1]
    events: list[Event] = []

    # LISTING / DELISTING: 유니버스 편입 시작·종료
    for sid, ticker, valid_from, valid_to in con.execute(
            "SELECT m.security_id, s.ticker, m.valid_from, m.valid_to FROM universe_membership m JOIN security s USING (security_id) "
            "WHERE m.universe_code = ? AND (m.valid_from > ? OR m.valid_to < '9999-12-31') AND m.valid_to >= ?",
            (universe, ctx.start, ctx.start)):
        if valid_from > ctx.start:
            cap, d = _cap_on_or_after(con, sid, valid_from)
            events.append(Event(valid_from, "LISTING", f"{ticker} 편입", sid, market_cap=cap, cap_date=d,
                                source=UNIVERSE_SOURCE[market]["LISTING"]))
        if valid_to < "9999-12-31":
            cap, d = _cap_on_or_before(con, sid, valid_to)
            removed = _day_after(valid_to)
            events.append(Event(removed, "DELISTING", f"{ticker} 편출 (마지막 편입일 {valid_to})", sid, market_cap=cap, cap_date=d,
                                source=UNIVERSE_SOURCE[market]["DELISTING"]))

    # CORP_ACTION: 주식수에 반영하지 않은 기업행위
    for sid, ticker, ex_date, factor, source in con.execute(
            "SELECT c.security_id, s.ticker, c.ex_date, MIN(c.price_factor), GROUP_CONCAT(DISTINCT c.source) "
            "FROM corporate_action c JOIN security s USING (security_id) "
            "WHERE s.market_code = ? AND c.share_ratio IS NULL AND c.ex_date >= ? GROUP BY c.security_id, c.ex_date",
            (market, ctx.start)):
        cap, d = _cap_on_or_before(con, sid, day_before(ex_date))
        events.append(Event(ex_date, "CORP_ACTION", f"{ticker} 가격계수 {factor:.6f}, 주식수 미반영", sid, market_cap=cap, cap_date=d,
                            source=source))

    # SHARE_CHANGE: 분할·병합 없이 상장주식수가 전일 대비 임계값 넘게 바뀜
    threshold = ctx.config["checks"]["share_change_threshold"]
    for sid, ticker, d, prev, shares, raw in con.execute(
            """WITH p AS (
                 SELECT p.security_id, p.trade_date, p.close_raw, p.shares_listed,
                        LAG(p.shares_listed) OVER (PARTITION BY p.security_id ORDER BY p.trade_date) AS prev
                 FROM price_daily p JOIN security s USING (security_id) WHERE s.market_code = ?)
               SELECT p.security_id, s.ticker, p.trade_date, p.prev, p.shares_listed, p.close_raw
               FROM p JOIN security s USING (security_id)
               WHERE p.prev IS NOT NULL AND p.shares_listed IS NOT NULL AND p.close_raw IS NOT NULL
                 AND ABS(1.0 * p.shares_listed / p.prev - 1) > ?
                 AND NOT EXISTS (SELECT 1 FROM corporate_action c WHERE c.security_id = p.security_id AND c.ex_date = p.trade_date)""",
            (market, threshold)):
        events.append(Event(d, "SHARE_CHANGE", f"{ticker} 상장주식수 {prev:,} → {shares:,} ({shares / prev - 1:+.1%})", sid,
                            market_cap=(shares - prev) * raw, cap_date=d, source=shares_source(con, sid, d)))

    # DATA_GAP: 알려진 데이터 공백과 근사 구간
    events.extend(_data_gaps(ctx))

    with transaction(con):
        # 특이사항은 수집 데이터에서 매번 다시 뽑는다. 더 이상 나오지 않는 사건(예: 고친 계산의 옛 결과)은 지우고,
        # 계속 나오는 사건은 처음 기록한 시각을 유지한다
        first_seen = {(t, sid or 0, d): created for t, sid, d, created in con.execute(
            "SELECT event_type, security_id, event_date, created_at FROM special_event WHERE market_code = ?", (market,))}
        con.execute("DELETE FROM special_event WHERE market_code = ?", (market,))
        for e in events:
            group, share = _sector(con, market, e)
            created = first_seen.get((e.event_type, e.security_id or 0, e.event_date), ctx.now)
            upsert_event(con, market, e, group, share, created)
    ctx.log(f"특이사항 {len(events)}건")
    return len(events)


def upsert_event(con, market: str, e: Event, group: str | None, share: float | None, now: str) -> None:
    """같은 사건(시장, 유형, 종목, 날짜)은 한 행만 둔다. 이미 있으면 규모·섹터·설명·출처를 갱신한다."""
    con.execute(
        "INSERT INTO special_event (market_code, event_date, end_date, event_type, security_id, group_code, market_cap, "
        "sector_share, detail, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (market_code, event_type, IFNULL(security_id, 0), event_date) DO UPDATE SET "
        "end_date = excluded.end_date, group_code = excluded.group_code, market_cap = excluded.market_cap, "
        "sector_share = excluded.sector_share, detail = excluded.detail, source = excluded.source",
        (market, e.event_date, e.end_date, e.event_type, e.security_id, group, e.market_cap, share, e.detail, e.source, now))


def _data_gaps(ctx: Context) -> list[Event]:
    con = ctx.con
    out = []
    if ctx.market == "KR":
        history_from = ctx.config["kr"]["shares_history_from"]
        out.append(Event(ctx.start, "DATA_GAP", "G-1: 상장주식수 이력이 없어 첫 관측치를 수정계수로 환산한 근사 구간. "
                         "유상증자·자사주 소각은 반영되지 않는다", end_date=day_before(history_from)))
        return out

    # G-2: 이력이 삭제된 편출 종목. 규모는 10-K 표지 유동 시가총액
    for sid, ticker, cik, valid_to in con.execute(
            "SELECT s.security_id, s.ticker, s.cik, MAX(m.valid_to) FROM security s JOIN universe_membership m USING (security_id) "
            "WHERE m.universe_code = 'US_SP500' AND NOT EXISTS (SELECT 1 FROM price_daily p WHERE p.security_id = s.security_id) "
            "GROUP BY s.security_id"):
        public_float = sec.fetch_public_float(ctx.sec_fetcher, cik) if cik else None
        out.append(Event(_day_after(valid_to) if valid_to < "9999-12-31" else ctx.start, "DATA_GAP",
                         f"G-2: {ticker} 가격 이력 없음, 계산에서 제외 (규모는 유동 시가총액)", sid,
                         market_cap=public_float, cap_date=ctx.start))

    # G-3: yfinance 현재 주식수로 과거를 근사한 라인 (복수 클래스, SEC 데이터 없음·오래됨)
    rows = con.execute("SELECT s.ticker, MIN(o.as_of_date) FROM shares_observation o JOIN security s USING (security_id) "
                       "WHERE o.source = 'YF_INFO' GROUP BY s.security_id ORDER BY s.ticker").fetchall()
    if rows:
        first = min(d for _, d in rows)
        out.append(Event(ctx.start, "DATA_GAP", f"G-3: SEC 공시 주식수를 쓸 수 없어 과거 주식수를 yfinance 현재 값으로 근사한 라인 {len(rows)}개: {[t for t, _ in rows]}",
                         end_date=day_before(first)))
    return out


def shares_source(con, sid: int, d: str) -> str | None:
    """그날 상장주식수로 쓴 관측치의 출처.

    as-of 규칙이라 그날 관측치가 없으면 그 앞의 최신 관측치가 쓰인다(store.refresh_shares).
    같은 날 여러 출처가 있으면 같은 우선순위로 고른다.
    """
    rows = [r[0] for r in con.execute(
        "SELECT source FROM shares_observation WHERE security_id = ? AND as_of_date = "
        "(SELECT MAX(as_of_date) FROM shares_observation WHERE security_id = ? AND as_of_date <= ?)", (sid, sid, d))]
    if not rows:
        return None
    return min(rows, key=lambda s: SHARES_PRIORITY.index(s) if s in SHARES_PRIORITY else len(SHARES_PRIORITY))


def _cap_on_or_after(con, sid: int, d: str) -> tuple[float | None, str | None]:
    row = con.execute("SELECT market_cap, trade_date FROM price_daily WHERE security_id = ? AND trade_date >= ? "
                      "ORDER BY trade_date LIMIT 1", (sid, d)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _cap_on_or_before(con, sid: int, d: str) -> tuple[float | None, str | None]:
    row = con.execute("SELECT market_cap, trade_date FROM price_daily WHERE security_id = ? AND trade_date <= ? "
                      "ORDER BY trade_date DESC LIMIT 1", (sid, d)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _day_after(iso: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def _sector(con, market: str, e: Event) -> tuple[str | None, float | None]:
    """사건 시점 섹터와, 그 거래일 섹터 시총 대비 사건 규모 비중."""
    if e.security_id is None:
        return None, None
    scheme, universe = SCHEMES[market]
    d = e.cap_date or e.event_date
    row = con.execute("SELECT group_code FROM security_group_map WHERE scheme_code = ? AND security_id = ? "
                      "AND valid_from <= ? AND ? <= valid_to", (scheme, e.security_id, d, d)).fetchone()
    if row is None:
        return None, None
    group = row[0]
    if e.market_cap is None:
        return group, None
    total = con.execute(
        "SELECT SUM(p.market_cap) FROM price_daily p "
        "JOIN universe_membership m ON m.security_id = p.security_id AND m.universe_code = ? AND m.valid_from <= p.trade_date AND p.trade_date <= m.valid_to "
        "JOIN security_group_map g ON g.security_id = p.security_id AND g.scheme_code = ? AND g.group_code = ? "
        "  AND g.valid_from <= p.trade_date AND p.trade_date <= g.valid_to "
        "WHERE p.trade_date = (SELECT MAX(trade_date) FROM trading_calendar WHERE market_code = ? AND trade_date <= ?)",
        (universe, scheme, group, market, d)).fetchone()[0]
    return group, (e.market_cap / total if total else None)
