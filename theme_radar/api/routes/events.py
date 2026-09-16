"""특이사항 조회 API (docs/07 §11.2).

가격이 아닌 이유로 섹터 시총을 바꾸거나 계산에 오차를 남긴 사건을, 섹터 비중이 큰 순서나 날짜순으로 본다.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from theme_radar.api import deps
from theme_radar.api.models import SpecialEvent

router = APIRouter(tags=["events"])
EVENT_TYPES = ("LISTING", "DELISTING", "RECLASS", "SHARE_CHANGE", "CORP_ACTION", "DATA_GAP")

EVENT_SQL = """
SELECT e.event_id, e.market_code, e.event_date, e.end_date, e.event_type, e.group_code, e.market_cap,
       e.sector_share, e.detail, e.source, s.ticker, s.name_local, s.name_en, cg.group_name, cg.group_name_en
FROM special_event e
LEFT JOIN security s USING (security_id)
LEFT JOIN classification_group cg ON cg.group_code = e.group_code
  AND cg.scheme_code = (SELECT scheme_code FROM classification_scheme
                        WHERE market_code = e.market_code AND is_exclusive = 1 ORDER BY scheme_code LIMIT 1)
WHERE {where}
ORDER BY {order}
LIMIT ?
"""


@router.get("/events")
def events(universe: str, event_type: str | None = Query(None, alias="type"),
           from_: str | None = Query(None, alias="from"), to: str | None = None,
           min_sector_share: float = Query(0.0, ge=0), limit: int = Query(50, ge=1, le=500),
           sort: str = Query("sector_share", pattern="^(sector_share|date)$"), lang: str = "ko",
           con: sqlite3.Connection = Depends(deps.get_con)) -> dict:
    scope = deps.scope(con, universe, "W")
    if event_type and event_type not in EVENT_TYPES:
        raise deps.ApiError(400, "INVALID_PARAMETER", f"모르는 특이사항 유형이다: {event_type}", "type")
    where = ["e.market_code = ?"]
    params: list = [scope.market]
    if event_type:
        where.append("e.event_type = ?")
        params.append(event_type)
    if from_:
        where.append("COALESCE(e.end_date, e.event_date) >= ?")
        params.append(from_)
    if to:
        where.append("e.event_date <= ?")
        params.append(to)
    if min_sector_share:
        where.append("ABS(COALESCE(e.sector_share, 0)) >= ?")
        params.append(min_sector_share)
    order = ("ABS(COALESCE(e.sector_share, 0)) DESC, e.event_date DESC" if sort == "sector_share"
             else "e.event_date DESC, e.event_id DESC")
    rows = con.execute(EVENT_SQL.format(where=" AND ".join(where), order=order), (*params, limit)).fetchall()
    counts = dict(con.execute("SELECT event_type, COUNT(*) FROM special_event WHERE market_code = ? GROUP BY 1",
                              (scope.market,)))
    data = [SpecialEvent(
        event_id=r["event_id"], market=r["market_code"], event_date=r["event_date"], end_date=r["end_date"],
        event_type=r["event_type"], ticker=r["ticker"],
        name=((r["name_en"] or r["name_local"]) if lang == "en" else r["name_local"]) if r["ticker"] else None,
        group_code=r["group_code"],
        group_name=(r["group_name_en"] or r["group_name"]) if lang == "en" else r["group_name"],
        market_cap=r["market_cap"], sector_share=r["sector_share"], detail=r["detail"], source=r["source"]) for r in rows]
    return {"meta": {"universe": scope.universe, "market": scope.market, "count": len(data), "by_type": counts},
            "data": data}
