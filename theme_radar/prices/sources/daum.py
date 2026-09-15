"""Daum 금융 시세: 보통주 상장주식수 현재값 (직접 호출, docs/07 §7.4)."""
from __future__ import annotations

import json
from dataclasses import dataclass

from theme_radar.prices.net import Fetcher

URL = "https://finance.daum.net/api/quotes/A{code}?summary=false&changeStatistics=true"


@dataclass(frozen=True)
class Quote:
    trade_date: str | None     # 상장폐지 종목은 None일 수 있다
    listed_shares: int | None
    is_delisted: bool


def parse_quote(body: bytes) -> Quote:
    d = json.loads(body)
    t = d.get("tradeDate")
    trade_date = f"{t[:4]}-{t[4:6]}-{t[6:]}" if t else None
    return Quote(trade_date, d.get("listedShareCount"), bool(d.get("isDelisted")))


def fetch_quote(fetcher: Fetcher, code: str) -> Quote:
    """Referer 헤더가 없으면 HTTP 403이다."""
    body = fetcher.get(URL.format(code=code), headers={"Referer": f"https://finance.daum.net/quotes/A{code}"},
                       raw=f"daum_quote/{code}.json")
    return parse_quote(body)
