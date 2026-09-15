"""KIND 상장법인목록 (직접 호출, docs/07 §7.1)."""
from __future__ import annotations

from dataclasses import dataclass

from theme_radar.prices.net import Fetcher
from theme_radar.prices.sources.html_table import parse_tables

URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
BOARDS = {"유가": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}


@dataclass(frozen=True)
class Listing:
    code: str
    name: str
    board: str             # KOSPI, KOSDAQ, KONEX
    industry: str
    listing_date: str | None


def parse_listing(body: bytes) -> list[Listing]:
    """본문은 Content-Type과 달리 EUC-KR HTML 표다. 같은 종목코드 행이 반복되므로 첫 행만 쓴다."""
    tables = parse_tables(body.decode("euc-kr"))
    if not tables:
        raise ValueError("KIND 상장법인목록에서 표를 찾지 못했다")
    header, *rows = tables[0].rows
    need = ["회사명", "시장구분", "종목코드", "업종", "상장일"]
    missing = [h for h in need if h not in header]
    if missing:
        raise ValueError(f"KIND 상장법인목록 컬럼이 없다: {missing}")
    col = {h: header.index(h) for h in need}
    listings: dict[str, Listing] = {}
    for row in rows:
        code = row[col["종목코드"]]
        if code in listings:
            continue
        board = BOARDS.get(row[col["시장구분"]])
        if board is None:
            raise ValueError(f"KIND 시장구분 값을 모른다: {row[col['시장구분']]} ({code})")
        listings[code] = Listing(code, row[col["회사명"]], board, row[col["업종"]], row[col["상장일"]] or None)
    return list(listings.values())


def fetch_listing(fetcher: Fetcher) -> list[Listing]:
    return parse_listing(fetcher.get(URL, raw="kind/corp_list.html"))
