"""SEC EDGAR: 공시 발행주식수와 티커 목록 (직접 호출, docs/07 §8.3).

SEC 접근 정책에 따라 연락처가 든 User-Agent로 호출한다(config.local.toml의 sec.user_agent).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from theme_radar.prices.net import Fetcher, HttpError

SHARES_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/dei/EntityCommonStockSharesOutstanding.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"


@dataclass(frozen=True)
class SharesFact:
    end: str
    shares: int
    filed: str


@dataclass(frozen=True)
class TickerInfo:
    cik: str
    name: str
    exchange: str | None


def parse_shares(body: bytes) -> tuple[list[SharesFact], bool]:
    """(기준일별 공시 주식수, 복수 클래스 여부).

    같은 기준일(end)에 정정 공시로 같은 값이 여러 번 오면 늦게 제출된 값을 쓴다.
    한 공시(accn) 안에 기준일이 같은 값이 여러 개면 클래스별 값으로 보고 복수 클래스로 판정한다.
    값이 0 이하인 사실은 공시 오류로 보고 버린다.
    """
    shares = json.loads(body)["units"].get("shares") or []   # 데이터가 없으면 빈 객체({})로 온다 (예: Coca-Cola)
    facts = [f for f in shares if f["val"] > 0]
    per_filing: dict[tuple[str, str], set[int]] = {}
    latest: dict[str, SharesFact] = {}
    for f in facts:
        per_filing.setdefault((f["accn"], f["end"]), set()).add(int(f["val"]))
        current = latest.get(f["end"])
        if current is None or f["filed"] >= current.filed:
            latest[f["end"]] = SharesFact(f["end"], int(f["val"]), f["filed"])
    multi_class = any(len(values) > 1 for values in per_filing.values())
    return sorted(latest.values(), key=lambda x: x.end), multi_class


def fetch_shares(fetcher: Fetcher, cik: str) -> tuple[list[SharesFact], bool] | None:
    """공시 데이터가 없으면(404) None."""
    try:
        body = fetcher.get(SHARES_URL.format(cik=cik), raw=f"sec_dei/{cik}.json")
    except HttpError as err:
        if err.status == 404:
            return None
        raise
    return parse_shares(body)


PUBLIC_FLOAT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/dei/EntityPublicFloat.json"


def fetch_public_float(fetcher: Fetcher, cik: str) -> float | None:
    """10-K 표지의 유동 시가총액(USD) 최신값. 이력이 없는 편출 종목의 특이사항 규모로 쓴다 (docs/07 §8.4)."""
    try:
        facts = json.loads(fetcher.get(PUBLIC_FLOAT_URL.format(cik=cik), raw=f"sec_float/{cik}.json"))["units"].get("USD", [])
    except HttpError as err:
        if err.status == 404:
            return None
        raise
    return float(max(facts, key=lambda f: (f["end"], f["filed"]))["val"]) if facts else None


def parse_tickers(body: bytes) -> dict[str, TickerInfo]:
    d = json.loads(body)
    col = {name: i for i, name in enumerate(d["fields"])}
    out = {}
    for row in d["data"]:
        ticker = str(row[col["ticker"]]).replace("-", ".")
        out.setdefault(ticker, TickerInfo(str(row[col["cik"]]).zfill(10), row[col["name"]], row[col["exchange"]]))
    return out


def fetch_tickers(fetcher: Fetcher) -> dict[str, TickerInfo]:
    return parse_tickers(fetcher.get(TICKERS_URL, raw="sec_tickers/company_tickers_exchange.json"))
