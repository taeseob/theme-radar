"""수집 단계 가격 검증 C-2 ~ C-4, C-6 (docs/07 §11.1). C-1·C-7·C-9 ~ C-13은 수집 흐름 안에서 검사한다.

C-8(외부 출처 표본 교차검증)은 아직 구현하지 않았다.
"""
from __future__ import annotations

from theme_radar.prices import store
from theme_radar.prices.context import Context

RECENT_TRADING_DAYS = 20


def run_checks(ctx: Context) -> None:
    market = ctx.market
    days = store.trading_days(ctx.con, market)
    since = ctx.start if ctx.full else days[-RECENT_TRADING_DAYS] if len(days) >= RECENT_TRADING_DAYS else ctx.start
    _jump(ctx, since)
    _missing_days(ctx, days, since)
    _cap_vs_price(ctx, since)


def _jump(ctx: Context, since: str) -> None:
    """원종가가 전일 대비 KR ±30% / US ±50%를 넘었는데 같은 날 기업행위 기록이 없다. 상장 직후 5거래일과 폐지 직전 10거래일은 뺀다.

    KR 가격제한폭이 정확히 30%라 상한가(16,500 → 21,450)가 부동소수점으로 걸리지 않게 0.1%p 여유를 둔다.
    """
    rule, limit = ("C-2", 0.301) if ctx.market == "KR" else ("C-3", 0.50)
    rows = ctx.con.execute(
        """WITH p AS (
             SELECT p.security_id, p.trade_date, p.close_raw,
                    LAG(p.close_raw) OVER w AS prev_raw,
                    ROW_NUMBER() OVER w AS rn,
                    ROW_NUMBER() OVER (PARTITION BY p.security_id ORDER BY p.trade_date DESC) AS rn_desc
             FROM price_daily p JOIN security s USING (security_id)
             WHERE s.market_code = ?
             WINDOW w AS (PARTITION BY p.security_id ORDER BY p.trade_date))
           SELECT s.ticker, p.trade_date, p.prev_raw, p.close_raw FROM p JOIN security s USING (security_id)
           WHERE p.trade_date >= ? AND p.prev_raw IS NOT NULL AND p.close_raw IS NOT NULL
             AND ABS(p.close_raw / p.prev_raw - 1) > ? AND p.rn > 5
             AND NOT (s.delisting_date IS NOT NULL AND p.rn_desc <= 10)
             AND NOT EXISTS (SELECT 1 FROM corporate_action c WHERE c.security_id = p.security_id AND c.ex_date = p.trade_date)
           ORDER BY p.trade_date""", (ctx.market, since, limit)).fetchall()
    ctx.run.check(rule, f"{ctx.market}/prices", "WARN", not rows, observed=len(rows), tolerance=limit,
                  detail=f"기업행위 기록 없는 급변 (티커, 날짜, 전일, 당일): {rows[:30]}" if rows else None)


def _missing_days(ctx: Context, days: list[str], since: str) -> None:
    """상장 중인 종목에 거래일 행이 없다. 최근 3거래일 연속으로 없으면 차단한다."""
    window = [d for d in days if d >= since]
    if not window:
        return
    if ctx.market == "KR":
        securities = ctx.con.execute(
            "SELECT security_id, ticker FROM security WHERE market_code = 'KR' AND security_type = 'COMMON' "
            "AND board IN ('KOSPI', 'KOSDAQ') AND delisting_date IS NULL").fetchall()
    else:
        securities = ctx.con.execute(
            "SELECT s.security_id, s.ticker FROM security s JOIN universe_membership m USING (security_id) "
            "WHERE m.universe_code = 'US_SP500' AND m.valid_to = '9999-12-31'").fetchall()
    dates: dict[int, set[str]] = {}
    for sid, d in ctx.con.execute("SELECT p.security_id, p.trade_date FROM price_daily p JOIN security s USING (security_id) "
                                  "WHERE s.market_code = ? AND p.trade_date >= ?", (ctx.market, since)):
        dates.setdefault(sid, set()).add(d)
    gaps, blocked = [], []
    for sid, ticker in securities:
        have = dates.get(sid)
        if not have:
            continue            # 이력이 없는 종목은 C-11·요청 실패 검사가 맡는다
        first = min(have)
        missing = [d for d in window if d >= first and d not in have]
        if missing:
            gaps.append((ticker, len(missing)))
            if all(d not in have for d in window[-3:]):
                blocked.append(ticker)
    ctx.run.check("C-4", f"{ctx.market}/prices/gaps", "WARN", not gaps, observed=len(gaps),
                  detail=f"거래일 행이 빠진 종목 (티커, 빠진 일수): {gaps[:30]}" if gaps else None)
    # 출처 장애를 잡는 검사다. 폐지 반영이 늦은 종목 몇 개로는 막지 않는다
    limit = max(5, len(securities) // 100)
    ctx.run.check("C-4", f"{ctx.market}/prices/recent", "BLOCK" if len(blocked) > limit else "WARN", not blocked,
                  observed=len(blocked), tolerance=limit,
                  detail=f"최근 3거래일 연속 행이 없는 상장 종목: {blocked[:30]}" if blocked else None)


def _cap_vs_price(ctx: Context, since: str) -> None:
    """전일 대비 시총 변화율과 수정 종가 변화율이 20% 넘게 다르다 (가격과 주식수 기준 불일치 의심)."""
    rows = ctx.con.execute(
        """WITH p AS (
             SELECT p.security_id, p.trade_date, p.market_cap, p.close_adj,
                    LAG(p.market_cap) OVER w AS prev_cap, LAG(p.close_adj) OVER w AS prev_adj
             FROM price_daily p JOIN security s USING (security_id)
             WHERE s.market_code = ?
             WINDOW w AS (PARTITION BY p.security_id ORDER BY p.trade_date))
           SELECT s.ticker, p.trade_date, ROUND((p.market_cap / p.prev_cap) / (p.close_adj / p.prev_adj) - 1, 4)
           FROM p JOIN security s USING (security_id)
           WHERE p.trade_date >= ? AND p.market_cap IS NOT NULL AND p.prev_cap IS NOT NULL
             AND ABS((p.market_cap / p.prev_cap) / (p.close_adj / p.prev_adj) - 1) > 0.2
           ORDER BY p.trade_date""", (ctx.market, since)).fetchall()
    ctx.run.check("C-6", f"{ctx.market}/prices", "WARN", not rows, observed=len(rows), tolerance=0.2,
                  detail=f"시총 변화와 수정 종가 변화 불일치 (티커, 날짜, 차이): {rows[:30]}" if rows else None)
