"""종목 검색 API (docs/05 §6.1)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from theme_radar.api import deps
from theme_radar.api.models import SecurityHit

router = APIRouter(tags=["securities"])

SEARCH_SQL = """
SELECT s.security_id, s.ticker, s.name_local, s.name_en, s.board, g.group_code, cg.group_name, cg.group_name_en
FROM security s
LEFT JOIN security_group_map g ON g.security_id = s.security_id AND g.valid_to = '9999-12-31'
  AND g.scheme_code = (SELECT scheme_code FROM classification_scheme
                       WHERE market_code = s.market_code AND is_exclusive = 1 ORDER BY scheme_code LIMIT 1)
LEFT JOIN classification_group cg ON cg.scheme_code = g.scheme_code AND cg.group_code = g.group_code
WHERE s.market_code = ? AND s.security_type = 'COMMON'
  AND (s.ticker LIKE ? || '%' OR s.name_local LIKE '%' || ? || '%' OR s.name_en LIKE '%' || ? || '%')
ORDER BY s.delisting_date IS NOT NULL, s.ticker
LIMIT ?
"""


@router.get("/securities/search")
def search(universe: str, q: str = Query(min_length=1), limit: int = Query(20, ge=1, le=100), lang: str = "ko",
           con: sqlite3.Connection = Depends(deps.get_con)) -> dict:
    """티커·종목명 부분 일치. 드릴다운에서 종목을 찾을 때 쓴다. 상장 중인 종목을 먼저 준다."""
    scope = deps.scope(con, universe, "W")
    rows = con.execute(SEARCH_SQL, (scope.market, q, q, q, limit)).fetchall()
    return {"data": [SecurityHit(
        security_id=r["security_id"], ticker=r["ticker"],
        name=(r["name_en"] or r["name_local"]) if lang == "en" else r["name_local"], board=r["board"],
        group_code=r["group_code"],
        group_name=(r["group_name_en"] or r["group_name"]) if lang == "en" else r["group_name"]) for r in rows]}
