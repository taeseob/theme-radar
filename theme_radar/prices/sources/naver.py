"""네이버 증권: 모바일 일별시세(무수정 종가)와 siseJson(수정 종가) (직접 호출, docs/07 §7.2, §7.3)."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass

from theme_radar.prices.net import Fetcher

MOBILE_URL = "https://m.stock.naver.com/api/stock/{code}/price?pageSize=60&page={page}"
SISE_URL = ("https://api.finance.naver.com/siseJson.naver?symbol={symbol}&requestType=1"
            "&startTime={start}&endTime={end}&timeframe=day")
MOBILE_PAGE_SIZE = 60  # 이보다 크면 HTTP 400


@dataclass(frozen=True)
class RawClose:
    trade_date: str
    close: float


@dataclass(frozen=True)
class SiseBar:
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def no_trade(self) -> bool:
        """거래가 없던 날은 시가·고가·저가가 0이고 종가에 직전 값이 들어 있다."""
        return self.open == 0 and self.high == 0 and self.low == 0


def parse_mobile_page(body: bytes) -> list[RawClose]:
    return [RawClose(r["localTradedAt"], float(r["closePrice"].replace(",", ""))) for r in json.loads(body)]


def fetch_raw_closes(fetcher: Fetcher, code: str, since: str) -> list[RawClose]:
    """since 이후(포함) 무수정 종가를 최신일부터 거슬러 받는다. 상장폐지 종목은 빈 목록이다."""
    closes: list[RawClose] = []
    page = 1
    while True:
        rows = parse_mobile_page(fetcher.get(MOBILE_URL.format(code=code, page=page), raw=f"naver_mprice/{code}_p{page}.json"))
        closes.extend(r for r in rows if r.trade_date >= since)
        if len(rows) < MOBILE_PAGE_SIZE or rows[-1].trade_date < since:
            return closes
        page += 1


def parse_sise(body: bytes) -> list[SiseBar]:
    """응답은 JSON이 아니다. 작은따옴표 헤더 행과 공백이 섞인 파이썬 리터럴이다."""
    rows = ast.literal_eval(re.sub(r"\s+", "", body.decode("utf-8")))
    bars = []
    for d, o, h, lo, c, v, *_ in rows[1:]:
        bars.append(SiseBar(f"{d[:4]}-{d[4:6]}-{d[6:]}", float(o), float(h), float(lo), float(c), int(v)))
    return bars


def fetch_sise(fetcher: Fetcher, symbol: str, start: str, end: str) -> list[SiseBar]:
    """symbol은 종목코드 또는 지수(KOSPI, KOSDAQ). 기간 전체를 한 번에 받는다. 상장폐지 종목도 폐지 직전까지 있다."""
    url = SISE_URL.format(symbol=symbol, start=start.replace("-", ""), end=end.replace("-", ""))
    return parse_sise(fetcher.get(url, raw=f"naver_sisejson/{symbol}.txt"))
