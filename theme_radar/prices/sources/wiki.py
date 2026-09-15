"""위키백과: S&P 500 현재 구성종목과 구성 변경표 (직접 호출, docs/07 §8.1)."""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

from theme_radar.prices.net import Fetcher
from theme_radar.prices.sources.html_table import find_table

LIST_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
REVISION_API = ("https://en.wikipedia.org/w/api.php?action=query&prop=revisions&titles={title}"
                "&rvlimit=1&rvstart={timestamp}&rvdir=older&rvprop=ids|timestamp&format=json")
OLDID_URL = "https://en.wikipedia.org/w/index.php?oldid={revid}"


@dataclass(frozen=True)
class Constituent:
    ticker: str            # 점 표기 (BRK.B)
    name: str
    cik: str | None        # 10자리
    date_added: str | None


@dataclass(frozen=True)
class Change:
    effective_date: str
    added: str | None
    added_name: str | None
    removed: str | None
    removed_name: str | None
    reason: str


def _date(text: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_constituents(html: str) -> list[Constituent]:
    header, *rows = find_table(html, "constituents").rows
    need = ["Symbol", "Security", "Date added", "CIK"]
    missing = [h for h in need if h not in header]
    if missing:
        raise ValueError(f"구성종목 표에 컬럼이 없다: {missing}")
    col = {h: header.index(h) for h in need}
    out = []
    for r in rows:
        cik = r[col["CIK"]].strip()
        out.append(Constituent(r[col["Symbol"]], r[col["Security"]], cik.zfill(10) if cik.isdigit() else None,
                               _date(r[col["Date added"]])))
    return out


def parse_changes(html: str) -> list[Change]:
    rows = find_table(html, "changes").rows
    if rows[0][:3] != ["Effective Date", "Added", "Removed"] or rows[1][:4] != ["Ticker", "Security", "Ticker", "Security"]:
        raise ValueError(f"변경표 헤더가 예상과 다르다: {rows[:2]}")
    out = []
    for r in rows[2:]:
        date = _date(r[0])
        if date is None:
            raise ValueError(f"변경표 날짜를 읽지 못했다: {r}")
        out.append(Change(date, r[1] or None, r[2] or None, r[3] or None, r[4] or None, r[5] if len(r) > 5 else ""))
    return out


def fetch_constituents(fetcher: Fetcher) -> list[Constituent]:
    return parse_constituents(fetcher.get(LIST_URL, raw="wiki/sp500_list.html").decode("utf-8"))


def fetch_constituents_at(fetcher: Fetcher, date: str) -> list[Constituent]:
    """date 00:00 UTC 직전 리비전의 구성종목. 편출 종목의 CIK를 찾는 데 쓴다."""
    api = REVISION_API.format(title=urllib.parse.quote("List of S&P 500 companies"), timestamp=f"{date}T00:00:00Z")
    pages = json.loads(fetcher.get(api))["query"]["pages"]
    revid = next(iter(pages.values()))["revisions"][0]["revid"]
    html = fetcher.get(OLDID_URL.format(revid=revid), raw=f"wiki/sp500_list_rev{revid}.html").decode("utf-8")
    return parse_constituents(html)


def fetch_changes(fetcher: Fetcher) -> list[Change]:
    return parse_changes(fetcher.get(CHANGES_URL, raw="wiki/sp500_changes.html").decode("utf-8"))
