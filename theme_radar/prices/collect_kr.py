"""KR 수집: 유니버스 → 거래일 → 가격·수정계수 → 상장주식수 (docs/07 §7.5)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from theme_radar.db import transaction
from theme_radar.prices import adjust, store
from theme_radar.prices.context import Context
from theme_radar.prices.net import HttpError
from theme_radar.prices.sources import daum, fdr_krx, kind, naver
from theme_radar.prices.universe import day_before, kr_records, rebuild_kr_membership, upsert_security

INCREMENTAL_TRADING_DAYS = 15   # 증분은 마지막 저장일 − 15거래일부터 다시 받는다


@dataclass(frozen=True)
class Target:
    security_id: int
    code: str
    delisting_date: str | None


def update_universe(ctx: Context) -> list[fdr_krx.Delisting]:
    listings = kind.fetch_listing(ctx.fetcher)
    snapshot = None
    for day in reversed(store.trading_days(ctx.con, "KR")[-5:] or [ctx.today]):
        snapshot = fdr_krx.fetch_snapshot(ctx.fetcher, day)
        if snapshot:
            break
    delistings = fdr_krx.fetch_delistings(ctx.raw_dir, ctx.start)
    records = kr_records(listings, snapshot, delistings, ctx.start, set(ctx.config["kr"]["excluded_funds"]))
    with transaction(ctx.con):
        for rec in records:
            upsert_security(ctx.con, rec)
        members = rebuild_kr_membership(ctx.con)
    listed = {r.ticker for r in records if r.delisting_date is None}
    vanished = [r[0] for r in ctx.con.execute(
        "SELECT ticker FROM security WHERE market_code = 'KR' AND delisting_date IS NULL AND board IN ('KOSPI', 'KOSDAQ')")
        if r[0] not in listed]
    ctx.run.check("U-1", "KR", "WARN", not vanished, observed=len(vanished),
                  detail=f"KIND 목록에서 사라졌지만 상장폐지 목록에 없는 종목: {vanished[:20]}" if vanished else None)
    ctx.log(f"유니버스: 상장 {len(listed)}, 폐지 {len(records) - len(listed)}, KR_COMMON 편입 기간 {members}")
    return delistings


INDEX_CODE = "KOSPI"


def update_calendar(ctx: Context) -> None:
    """거래일 캘린더와 시장 지수 일봉을 KOSPI 지수 일봉 하나로 함께 만든다 (docs/07 §12)."""
    bars = naver.fetch_sise(ctx.fetcher, INDEX_CODE, ctx.start, ctx.today)
    closed = [b for b in bars if b.trade_date != ctx.today or ctx.market_closed]
    days = [b.trade_date for b in closed]
    with transaction(ctx.con):
        store.replace_trading_days(ctx.con, "KR", days, ctx.start)
        # 거래가 없던 날은 시가·고가·저가가 0이라 캔들을 만들 수 없다. 그날은 빈칸으로 둔다
        stored = store.replace_index_bars(ctx.con, "KR", INDEX_CODE, [b for b in closed if not b.no_trade],
                                          "NAVER_SISE", ctx.start)
    ctx.log(f"거래일 {len(days)}일 ({days[0]} ~ {days[-1]}), {INDEX_CODE} 지수 {stored}일")


def targets(ctx: Context, codes: list[str] | None = None) -> list[Target]:
    sql = ("SELECT security_id, ticker, delisting_date FROM security WHERE market_code = 'KR' AND security_type = 'COMMON' "
           "AND board IN ('KOSPI', 'KOSDAQ') AND (delisting_date IS NULL OR delisting_date >= ?) ORDER BY ticker")
    rows = [Target(*r) for r in ctx.con.execute(sql, (ctx.start,))]
    return [t for t in rows if t.code in codes] if codes else rows


def _since(ctx: Context, target: Target, days: list[str], full: bool) -> str:
    if full:
        return ctx.start
    last = ctx.con.execute("SELECT MAX(trade_date) FROM price_daily WHERE security_id = ?", (target.security_id,)).fetchone()[0]
    if last is None:
        return ctx.start
    earlier = [d for d in days if d <= last]
    return earlier[-INCREMENTAL_TRADING_DAYS] if len(earlier) >= INCREMENTAL_TRADING_DAYS else ctx.start


def _fetch(ctx: Context, target: Target, since: str, last_date: str | None, null_raw_dates: set[str]):
    """siseJson 수정 종가를 받고, 원종가가 필요한 날(새 날짜, 원종가가 비어 있던 날)이 있을 때만 일별시세를 받는다."""
    bars = [b for b in naver.fetch_sise(ctx.fetcher, target.code, since, ctx.today)
            if b.trade_date != ctx.today or ctx.market_closed]
    need = [b.trade_date for b in bars if last_date is None or b.trade_date > last_date or b.trade_date in null_raw_dates]
    raw: dict[str, float | None] = {}
    source = "NAVER_MPRICE"
    if need:
        closes = naver.fetch_raw_closes(ctx.fetcher, target.code, min(need))
        raw = {c.trade_date: c.close for c in closes if c.trade_date != ctx.today or ctx.market_closed}
        if not closes:
            raw, source = adjust.kr_delisted_raw(bars), "NAVER_SISEJSON"   # 상장폐지 종목은 원종가 API가 비어 있다
    return bars, raw, source


def collect_prices(ctx: Context, codes: list[str] | None = None) -> None:
    days = store.trading_days(ctx.con, "KR")
    todo = [(t, _since(ctx, t, days, ctx.full)) for t in targets(ctx, codes)]
    restate: list[Target] = []
    failures = _sync(ctx, todo, restate_mode=ctx.full, restate_out=restate)
    if restate:
        ctx.log(f"수정계수가 바뀐 종목 {len(restate)}개를 전 구간 재수정한다")
        failures += _sync(ctx, [(t, ctx.start) for t in restate], restate_mode=True, restate_out=[])
    total = len(todo)
    ctx.run.check("FETCH", "KR/prices", "BLOCK" if failures > total * 0.05 else "WARN", failures == 0,
                  observed=failures, tolerance=total * 0.05, detail=f"요청 실패 종목 {failures}/{total}")


def _sync(ctx: Context, todo: list[tuple[Target, str]], restate_mode: bool, restate_out: list[Target]) -> int:
    last_dates = dict(ctx.con.execute(
        "SELECT p.security_id, MAX(p.trade_date) FROM price_daily p JOIN security s USING (security_id) "
        "WHERE s.market_code = 'KR' GROUP BY p.security_id"))
    null_raw: dict[int, set[str]] = {}
    for sid, d in ctx.con.execute("SELECT security_id, trade_date FROM price_daily WHERE close_raw IS NULL"):
        null_raw.setdefault(sid, set()).add(d)
    workers = ctx.config["http"]["max_workers"]
    failures = 0
    rejected_total: list[str] = []

    def work(item):
        target, since = item
        try:
            fetched = _fetch(ctx, target, since, last_dates.get(target.security_id), null_raw.get(target.security_id, set()))
            return target, fetched, None
        except HttpError as err:
            return target, None, err

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (target, fetched, error) in enumerate(pool.map(work, todo), 1):
            if error is not None:
                failures += 1
                ctx.log(f"  {target.code} 요청 실패: {error}")
                continue
            bars, raw, price_source = fetched
            stored = store.stored_prices(ctx.con, target.security_id)
            rows, rejected = adjust.kr_rows(bars, raw, {d: v[0] for d, v in stored.items()})
            rejected_total.extend(f"{target.code}:{d}" for d in rejected)
            with transaction(ctx.con):
                result = store.write_prices(ctx.con, target.security_id, rows, stored, "KR", price_source, "NAVER_SISEJSON", ctx.now)
                if result.changed_dates:
                    if restate_mode:
                        store.record_restatement(ctx.con, ctx.run.run_id, target.security_id, "KR",
                                                 result.changed_dates, result.max_rel_change, ctx.now)
                    else:
                        restate_out.append(target)
                series = [adjust.PriceRow(d, raw_, f, adj, None, "")
                          for d, raw_, f, adj in ctx.con.execute(
                              "SELECT trade_date, close_raw, adj_factor, close_adj FROM price_daily WHERE security_id = ? ORDER BY trade_date",
                              (target.security_id,))]
                store.record_actions(ctx.con, target.security_id, adjust.kr_actions(series), "NAVER_FACTOR_JUMP", ctx.now)
            ctx.run.row_count += result.inserted
            if i % 200 == 0 or i == len(todo):
                ctx.log(f"  가격 {i}/{len(todo)}")
    ctx.run.check("C-1", "KR/prices", "WARN", not rejected_total, observed=len(rejected_total),
                  detail=f"호가단위에 맞지 않아 적재하지 않은 행: {rejected_total[:30]}" if rejected_total else None)
    return failures


def collect_shares(ctx: Context, delistings: list[fdr_krx.Delisting], codes: list[str] | None = None) -> None:
    all_targets = targets(ctx, codes)
    ids = {t.code: t.security_id for t in all_targets}
    days = store.trading_days(ctx.con, "KR")
    history_from = ctx.config["kr"]["shares_history_from"]

    # 1. FDR KRX 스냅샷: 전 구간 모드는 모든 거래일, 증분은 마지막으로 받은 날부터(당일 파일은 하루 중 갱신된다)
    last_fdr = ctx.con.execute("SELECT MAX(as_of_date) FROM shares_observation WHERE source = 'FDR_KRX_CACHE'").fetchone()[0]
    since_fdr = history_from if ctx.full or last_fdr is None else max(history_from, last_fdr)
    wanted = [d for d in days if d >= since_fdr]
    missing_run = 0
    for day in wanted:
        snapshot = fdr_krx.fetch_snapshot(ctx.fetcher, day)
        if snapshot is None:
            missing_run += 1
            continue
        missing_run = 0
        rows = [(ids[code], day, row.stocks, "FDR_KRX_CACHE") for code, row in snapshot.items() if code in ids]
        with transaction(ctx.con):
            store.add_observations(ctx.con, rows, "AS_REPORTED", ctx.now)
    ctx.run.check("C-13", "KR/fdr_krx", "WARN", missing_run < 7, observed=missing_run, tolerance=7,
                  detail="최근 거래일 FDR KRX 스냅샷이 연속으로 없다 (캐시 중단 의심)" if missing_run >= 7 else None)
    ctx.log(f"FDR KRX 스냅샷 {len(wanted)}일")

    # 2. 상장폐지 시점 상장주식수 (폐지일 전날 기준)
    rows = [(ids[d.code], day_before(d.delisting_date), d.listing_shares, "FDR_KRX_DELISTING")
            for d in delistings if d.code in ids and d.listing_shares]
    with transaction(ctx.con):
        store.add_observations(ctx.con, rows, "AS_REPORTED", ctx.now)

    # 3. Daum 당일 상장주식수 (상장 중인 종목)
    listed = [t for t in all_targets if t.delisting_date is None]

    def quote(target):
        try:
            return target, daum.fetch_quote(ctx.fetcher, target.code)
        except HttpError:
            return target, None

    rows, failed = [], 0
    with ThreadPoolExecutor(max_workers=ctx.config["http"]["max_workers"]) as pool:
        for target, q in pool.map(quote, listed):
            if q is None or q.trade_date is None or not q.listed_shares:
                failed += 1
                continue
            if q.trade_date in days or q.trade_date == ctx.today:
                rows.append((target.security_id, q.trade_date, q.listed_shares, "DAUM_QUOTE"))
    with transaction(ctx.con):
        store.add_observations(ctx.con, rows, "AS_REPORTED", ctx.now)
    ctx.log(f"Daum 상장주식수 {len(rows)}종목 (실패 {failed})")

    # 4. C-7: 같은 날 Daum과 FDR 스냅샷 값이 다르면 경고
    diff = ctx.con.execute(
        "SELECT s.ticker, d.as_of_date, d.shares, f.shares FROM shares_observation d "
        "JOIN shares_observation f ON f.security_id = d.security_id AND f.as_of_date = d.as_of_date AND f.source = 'FDR_KRX_CACHE' "
        "JOIN security s ON s.security_id = d.security_id WHERE d.source = 'DAUM_QUOTE' AND d.shares <> f.shares").fetchall()
    ctx.run.check("C-7", "KR/shares", "WARN", not diff, observed=len(diff),
                  detail=f"Daum ≠ FDR (티커, 날짜, Daum, FDR): {diff[:20]}" if diff else None)

    with transaction(ctx.con):
        earliest, dropped = store.refresh_shares(ctx.con, "KR")
        if earliest and not ctx.full:
            store.request_recalc(ctx.con, "KR", earliest, "PRICE_CORRECTION", "상장주식수 소급 변경", ctx.now)
    ctx.run.check("U-5", "KR/shares", "WARN", not dropped, observed=len(dropped),
                  detail=f"믿기 어려워 쓰지 않은 주식수 관측치: {dropped[:20]}" if dropped else None)

