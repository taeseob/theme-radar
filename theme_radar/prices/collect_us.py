"""US 수집: 유니버스 → 거래일 → 가격·분할 → 상장주식수 (docs/07 §8)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from theme_radar.db import transaction
from theme_radar.prices import adjust, store
from theme_radar.prices.context import Context
from theme_radar.prices.sources import sec, wiki, yf
from theme_radar.prices.universe import MAX_DATE, SecurityRecord, member_counts, rebuild_us_membership, upsert_security, us_intervals

INCREMENTAL_TRADING_DAYS = 10   # 증분은 마지막 저장일 − 10거래일부터 다시 받는다
BOARDS = {"NYSE": "NYSE", "Nasdaq": "NASDAQ", "CBOE": "CBOE"}
SP500_LINES = (495, 510)        # C-9


@dataclass(frozen=True)
class Target:
    security_id: int
    ticker: str
    cik: str | None
    valid_to: str               # 마지막 편입 기간의 끝

    @property
    def current(self) -> bool:
        return self.valid_to == MAX_DATE


def update_universe(ctx: Context) -> None:
    current = wiki.fetch_constituents(ctx.fetcher)
    changes = wiki.fetch_changes(ctx.fetcher)
    at_start = {c.ticker: c for c in wiki.fetch_constituents_at(ctx.fetcher, ctx.start)}
    sec_tickers = sec.fetch_tickers(ctx.sec_fetcher)
    intervals, renames, warnings = us_intervals(current, changes, ctx.start)

    by_current = {c.ticker: c for c in current}
    change_names = {}
    for ch in changes:
        change_names.setdefault(ch.added, ch.added_name)
        change_names.setdefault(ch.removed, ch.removed_name)
    loading = {i.ticker for i in intervals}
    records, no_cik = [], []
    for ticker in sorted(loading):
        listed = by_current.get(ticker) or at_start.get(ticker)
        info = sec_tickers.get(ticker)
        cik = (listed.cik if listed else None) or (info.cik if info else None)
        if cik is None:
            no_cik.append(ticker)
        name = listed.name if listed else change_names.get(ticker) or (info.name if info else ticker)
        board = BOARDS.get(info.exchange, info.exchange) if info and info.exchange else "UNKNOWN"
        records.append(SecurityRecord("US", board, ticker, name, "COMMON", "USD", cik=cik))
    with transaction(ctx.con):
        ids = {rec.ticker: upsert_security(ctx.con, rec, loading) for rec in records}
        rebuild_us_membership(ctx.con, intervals, ids)

    check_dates = sorted({ctx.start, ctx.today, *(c.effective_date for c in changes if c.effective_date > ctx.start)})
    counts = member_counts(intervals, check_dates)
    low, high = min(counts.values()), max(counts.values())
    ctx.run.check("C-9", "US/universe", "BLOCK", SP500_LINES[0] <= low and high <= SP500_LINES[1],
                  observed=low if low < SP500_LINES[0] else high, detail=f"as-of 라인 수 범위 {low} ~ {high}")
    ctx.run.check("U-2", "US/universe", "WARN", not warnings, observed=len(warnings), detail="; ".join(warnings) or None)
    ctx.run.check("U-3", "US/universe", "WARN", not no_cik, observed=len(no_cik),
                  detail=f"CIK를 찾지 못한 티커(주식수 없음): {no_cik}" if no_cik else None)
    ctx.log(f"유니버스: 현재 {len(current)}, 편입 기간 {len(intervals)}, 티커 변경 {renames or '없음'}, 라인 수 {low}~{high}")


INDEX_CODE = "SPX"
INDEX_SYMBOL = "^GSPC"


def update_calendar(ctx: Context) -> None:
    """거래일 캘린더와 시장 지수 일봉을 S&P 500 지수 일봉 하나로 함께 만든다 (docs/07 §12)."""
    bars = [b for b in yf.fetch_index(INDEX_SYMBOL, ctx.start, ctx.raw_dir)
            if b.trade_date != ctx.today or ctx.market_closed]
    days = [b.trade_date for b in bars]
    with transaction(ctx.con):
        store.replace_trading_days(ctx.con, "US", days, ctx.start)
        stored = store.replace_index_bars(ctx.con, "US", INDEX_CODE, bars, "YFINANCE", ctx.start)
    ctx.log(f"거래일 {len(days)}일 ({days[0]} ~ {days[-1]}), {INDEX_CODE} 지수 {stored}일")


def targets(ctx: Context, since: str, tickers: list[str] | None = None) -> list[Target]:
    rows = ctx.con.execute(
        "SELECT s.security_id, s.ticker, s.cik, MAX(m.valid_to) FROM security s "
        "JOIN universe_membership m ON m.security_id = s.security_id AND m.universe_code = 'US_SP500' "
        "WHERE s.market_code = 'US' GROUP BY s.security_id HAVING MAX(m.valid_to) >= ? ORDER BY s.ticker", (since,))
    out = [Target(*r) for r in rows]
    return [t for t in out if t.ticker in tickers] if tickers else out


def collect_prices(ctx: Context, tickers: list[str] | None = None) -> None:
    days = store.trading_days(ctx.con, "US")
    since = ctx.start
    if not ctx.full:
        last = ctx.con.execute("SELECT MAX(p.trade_date) FROM price_daily p JOIN security s USING (security_id) "
                               "WHERE s.market_code = 'US'").fetchone()[0]
        earlier = [d for d in days if last and d <= last]
        since = earlier[-INCREMENTAL_TRADING_DAYS] if len(earlier) >= INCREMENTAL_TRADING_DAYS else ctx.start
    todo = targets(ctx, since, tickers)
    restate = _sync(ctx, todo, since, restate_mode=ctx.full)
    if restate:
        ctx.log(f"분할 등으로 수정 종가가 바뀐 종목 {len(restate)}개를 전 구간 재수정한다: {[t.ticker for t in restate]}")
        _sync(ctx, restate, ctx.start, restate_mode=True)


def _sync(ctx: Context, todo: list[Target], since: str, restate_mode: bool) -> list[Target]:
    # 전 구간 모드는 수집 시작일 1년 전부터 받아 그 사이 분할도 기록한다. 시작일 직전 SEC 공시(분할 전 주식수)에
    # 분할 비율을 곱하려면 필요하다(예: TSCO 2024-12 5:1 분할). 가격 행은 시작일 이후만 저장한다.
    download_since = (date.fromisoformat(ctx.start) - timedelta(days=365)).isoformat() if since == ctx.start else since
    ctx.log(f"yfinance 일봉 {len(todo)}종목, {download_since} 이후")
    bars_by_ticker = yf.download_bars([t.ticker for t in todo], download_since, ctx.raw_dir)
    restate, empty = [], []
    for target in todo:
        all_bars = [b for b in bars_by_ticker.get(target.ticker, []) if b.trade_date != ctx.today or ctx.market_closed]
        bars = [b for b in all_bars if b.trade_date >= since]
        if not bars:
            empty.append(target)
            continue
        stored = store.stored_prices(ctx.con, target.security_id)
        rows = adjust.us_rows(bars, {d: v[0] for d, v in stored.items()})
        with transaction(ctx.con):
            result = store.write_prices(ctx.con, target.security_id, rows, stored, "US", "YFINANCE", "YFINANCE", ctx.now)
            if result.changed_dates:
                if restate_mode:
                    store.record_restatement(ctx.con, ctx.run.run_id, target.security_id, "US",
                                             result.changed_dates, result.max_rel_change, ctx.now)
                else:
                    restate.append(target)
            store.record_actions(ctx.con, target.security_id, adjust.us_actions(all_bars), "YFINANCE_SPLIT", ctx.now)
        ctx.run.row_count += result.inserted
    current_empty = [t.ticker for t in empty if t.current]
    past_empty = [t.ticker for t in empty if not t.current]
    ctx.run.check("C-11", "US/prices/current", "BLOCK", not current_empty, observed=len(current_empty),
                  detail=f"현재 구성종목인데 yfinance 결과가 비었다: {current_empty}" if current_empty else None)
    ctx.run.check("C-11", "US/prices/removed", "WARN", not past_empty, observed=len(past_empty),
                  detail=f"편출 종목의 yfinance 결과가 비었다(이력 삭제): {past_empty}" if past_empty else None)
    return restate


def collect_shares(ctx: Context, tickers: list[str] | None = None) -> None:
    """단일 클래스는 SEC 공시 주식수, 복수 클래스(또는 SEC 데이터 없음)는 yfinance 현재 주식수 (docs/07 §8.3).

    복수 클래스 판정(같은 CIK의 편입 라인 수)은 전 종목 기준으로 하고, tickers를 주면 그 종목만 받는다.
    """
    everyone = targets(ctx, ctx.start)
    lines_per_cik: dict[str, int] = defaultdict(int)
    for t in everyone:
        if t.cik:
            lines_per_cik[t.cik] += 1
    all_targets = [t for t in everyone if t.ticker in tickers] if tickers else everyone
    by_cik: dict[str, list[Target]] = defaultdict(list)
    for t in all_targets:
        if t.cik:
            by_cik[t.cik].append(t)
    stale_before = (date.fromisoformat(ctx.start) - timedelta(days=365)).isoformat()
    observations, yf_lines = [], []
    for i, (cik, lines) in enumerate(sorted(by_cik.items()), 1):
        result = sec.fetch_shares(ctx.sec_fetcher, cik)
        # SEC 값을 쓰지 않는 경우: 데이터 없음(404·빈 목록), 한 공시에 클래스별 값, 같은 CIK의 편입 라인이 여럿,
        # 최신 공시가 수집 시작일 1년 전보다 오래됨(예: Berkshire는 2011년 A주 수가 마지막이다)
        if (result is None or result[1] or lines_per_cik[cik] > 1 or not result[0]
                or result[0][-1].end < stale_before):
            yf_lines.extend(lines)
            continue
        for fact in result[0]:
            observations.extend((t.security_id, fact.end, fact.shares, "SEC_DEI") for t in lines)
        if i % 100 == 0:
            ctx.log(f"  SEC 주식수 {i}/{len(by_cik)}")

    # yfinance 현재 주식수는 아직 거래 중인 라인만 받을 수 있다(편출됐어도 상장 중이면 받는다)
    recent = store.trading_days(ctx.con, "US")[-10]
    trading = {sid for (sid,) in ctx.con.execute("SELECT DISTINCT security_id FROM price_daily WHERE trade_date >= ?", (recent,))}
    current_yf = [t for t in yf_lines if t.security_id in trading]
    infos = yf.fetch_shares_info([t.ticker for t in current_yf], ctx.raw_dir)
    as_of = store.trading_days(ctx.con, "US")[-1]
    groups: dict[str, list[Target]] = defaultdict(list)
    for t in current_yf:
        groups[t.cik].append(t)
    for cik, lines in groups.items():
        # D-5: 편입되지 않은 클래스는 회사 합계를 편입 라인에 주식수 비율로 나눠 반영한다
        known = [t for t in lines if infos[t.ticker].shares_outstanding]
        total = sum(infos[t.ticker].shares_outstanding for t in known)
        implied = max((infos[t.ticker].implied_shares_outstanding or 0) for t in known) if known else 0
        for t in known:
            shares = infos[t.ticker].shares_outstanding
            if implied > total:
                shares = round(shares * implied / total)
            observations.append((t.security_id, as_of, shares, "YF_INFO"))
    with transaction(ctx.con):
        # 라인마다 이번에 쓰기로 한 출처의 관측치만 남긴다 (예: SEC 값이 오래돼 yfinance로 바뀐 BRK.B)
        yf_ids = {t.security_id for t in yf_lines}
        store.remove_observations(ctx.con, [t.security_id for t in all_targets if t.security_id in yf_ids], "SEC_DEI")
        store.remove_observations(ctx.con, [t.security_id for t in all_targets if t.security_id not in yf_ids], "YF_INFO")
        store.add_observations(ctx.con, observations, "AS_REPORTED", ctx.now)
        earliest, dropped = store.refresh_shares(ctx.con, "US")
        if earliest and not ctx.full:
            store.request_recalc(ctx.con, "US", earliest, "PRICE_CORRECTION", "상장주식수 소급 변경", ctx.now)
    ctx.run.check("U-5", "US/shares", "WARN", not dropped, observed=len(dropped),
                  detail=f"믿기 어려워 쓰지 않은 주식수 관측치: {dropped[:20]}" if dropped else None)
    no_shares = ([t.ticker for t in yf_lines if t.security_id not in trading]
                 + [t.ticker for t in current_yf if not infos[t.ticker].shares_outstanding])
    ctx.run.check("U-4", "US/shares", "WARN", not no_shares, observed=len(no_shares),
                  detail=f"주식수를 얻지 못한 라인: {no_shares}" if no_shares else None)
    ctx.log(f"SEC 공시 CIK {len(by_cik)}개, yfinance 주식수 라인 {[t.ticker for t in current_yf]}")
