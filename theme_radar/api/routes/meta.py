"""메타 API (docs/05 §2)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from theme_radar.api import deps

router = APIRouter(tags=["meta"])


@router.get("/meta/universes")
def universes(con: sqlite3.Connection = Depends(deps.get_con)) -> dict:
    rows = con.execute("SELECT u.universe_code, u.universe_name, u.market_code, m.currency FROM universe u "
                       "JOIN market m USING (market_code) ORDER BY u.universe_code")
    return {"data": [{"universe": r[0], "name": r[1], "market": r[2], "currency": r[3]} for r in rows]}


@router.get("/meta/schemes")
def schemes(universe: str, con: sqlite3.Connection = Depends(deps.get_con)) -> dict:
    scope = deps.scope(con, universe, "W")
    rows = con.execute(
        "SELECT s.scheme_code, s.scheme_name, s.scheme_type, s.is_exclusive, "
        "(SELECT COUNT(*) FROM classification_group g WHERE g.scheme_code = s.scheme_code) AS group_count "
        "FROM classification_scheme s WHERE s.market_code = ? ORDER BY s.scheme_code", (scope.market,))
    return {"data": [{"scheme": r[0], "name": r[1], "type": r[2], "exclusive": bool(r[3]), "group_count": r[4]} for r in rows]}


@router.get("/meta/groups")
def groups(universe: str, scheme: str, lang: str = "ko", con: sqlite3.Connection = Depends(deps.get_con)) -> dict:
    scope = deps.scope(con, universe, "W", scheme)
    rows = con.execute("SELECT group_code, group_name, group_name_en, color_hex, sort_order FROM classification_group "
                       "WHERE scheme_code = ? ORDER BY sort_order, group_code", (scope.scheme,))
    return {"data": [{"group_code": r[0],
                      "name": (r[2] or r[1]) if lang == "en" else r[1],
                      "name_en": r[2], "color": r[3], "sort_order": r[4]} for r in rows]}


@router.get("/meta/periods")
def periods(universe: str, period: str = "W", from_: str | None = Query(None, alias="from"), to: str | None = None,
            con: sqlite3.Connection = Depends(deps.get_con)) -> dict:
    scope = deps.scope(con, universe, period)
    rng = deps.period_range(con, scope, from_, to)
    return {"data": [{"period_id": r["period_id"], "period_seq": r["period_seq"], "cal_start": r["cal_start"],
                      "cal_end": r["cal_end"], "base_date": r["base_date"], "end_date": r["end_date"],
                      "trading_days": r["trading_days"], "is_closed": bool(r["is_closed"])} for r in rng.rows]}
