"""시장 요약 API (docs/05 §4)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Response

from theme_radar.api import deps
from theme_radar.api.models import Contributor, MarketSummary, Meta, SummaryResponse, r8
from theme_radar.api.routes.sectors import UNMAPPED, latest_period, group_label

router = APIRouter(tags=["market"])
DISCLAIMER = "유니버스 수익률은 자체 산출값이며 KOSPI/KOSDAQ/S&P 500 공식 지수와 다릅니다."
CONTRIBUTORS = 5


def default_scheme(con: sqlite3.Connection, market: str) -> str | None:
    row = con.execute("SELECT s.scheme_code FROM classification_scheme s WHERE s.market_code = ? AND s.is_exclusive = 1 "
                      "AND EXISTS (SELECT 1 FROM security_group_map m WHERE m.scheme_code = s.scheme_code) "
                      "ORDER BY s.scheme_code LIMIT 1", (market,)).fetchone()
    return row[0] if row else None


@router.get("/market/summary", response_model=SummaryResponse)
def summary(response: Response, universe: str, period: str = "W", period_id: str | None = None,
            scheme: str | None = None, lang: str = "ko",
            con: sqlite3.Connection = Depends(deps.get_con)) -> SummaryResponse:
    scope = deps.scope(con, universe, period, scheme)
    period_id = period_id or latest_period(con, scope)
    rng = deps.period_range(con, scope, period_id, period_id)
    info = deps.calc_info(con, scope, rng.from_seq, rng.to_seq)
    stale = deps.is_stale(con, scope.market, rng.end_date)
    row = con.execute("SELECT ret, ret_equal, ret_median, member_cnt, up_cnt, unmapped_cap_ratio, is_provisional "
                      "FROM universe_period_stat WHERE universe_code = ? AND period_type = ? AND period_id = ?",
                      (scope.universe, scope.period_type, period_id)).fetchone()
    if row is None:
        raise deps.ApiError(409, "NOT_AVAILABLE", f"{period_id} 집계가 아직 없다")
    deps.apply_cache(response, info, stale)

    scheme_code = scope.scheme or default_scheme(con, scope.market)
    top: list[Contributor] = []
    bottom: list[Contributor] = []
    if scheme_code:
        names = deps.group_names(con, scheme_code)
        groups = con.execute("SELECT group_code, contribution FROM group_period_stat WHERE universe_code = ? "
                             "AND scheme_code = ? AND period_type = ? AND period_id = ? AND group_code <> ? "
                             "AND contribution IS NOT NULL ORDER BY contribution DESC",
                             (scope.universe, scheme_code, scope.period_type, period_id, UNMAPPED)).fetchall()

        def contributor(r: sqlite3.Row) -> Contributor:
            return Contributor(group_code=r["group_code"], name=group_label(names, r["group_code"], lang),
                               contribution=r8(r["contribution"]))

        top = [contributor(r) for r in groups[:CONTRIBUTORS] if r["contribution"] > 0]
        bottom = [contributor(r) for r in reversed(groups[-CONTRIBUTORS:]) if r["contribution"] < 0]

    period_row = rng.rows[0]
    data = MarketSummary(
        period_id=period_id, base_date=period_row["base_date"], end_date=period_row["end_date"],
        is_provisional=bool(row["is_provisional"]), universe_return=r8(row["ret"]),
        universe_return_equal=r8(row["ret_equal"]), universe_return_median=r8(row["ret_median"]),
        member_cnt=row["member_cnt"], up_cnt=row["up_cnt"],
        up_ratio=r8(row["up_cnt"] / row["member_cnt"]) if row["member_cnt"] else None,
        unmapped_cap_ratio=r8(row["unmapped_cap_ratio"]), top_contributors=top, bottom_contributors=bottom,
        disclaimer=DISCLAIMER)
    meta = Meta(**deps.envelope(scope, info, stale, period_id=period_id, scheme=scheme_code))
    return SummaryResponse(meta=meta, data=data)
