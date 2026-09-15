"""FinanceDataReader KRX 캐시: 날짜별 KRX 스냅샷과 상장폐지 목록 (docs/07 §7.1, §7.4, 08 §3)."""
from __future__ import annotations

import io
from dataclasses import dataclass

import FinanceDataReader as fdr
import pandas as pd

from theme_radar.prices.net import Fetcher, HttpError, save_raw

SNAPSHOT_URL = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
                "refs/heads/master/data/listing/krx/{date}.csv")
BOARDS = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KOSDAQ GLOBAL": "KOSDAQ", "KONEX": "KONEX"}


@dataclass(frozen=True)
class SnapshotRow:
    code: str
    isin: str
    name: str
    dept: str              # KOSDAQ 소속부. KOSPI는 빈 문자열
    stocks: int            # 상장주식수


@dataclass(frozen=True)
class Delisting:
    code: str
    name: str
    board: str
    listing_date: str | None
    delisting_date: str
    listing_shares: int | None   # 폐지 시점 상장주식수
    reason: str


def parse_snapshot(body: bytes) -> dict[str, SnapshotRow] | None:
    """주말·휴장일 파일은 가격이 전부 '-'이므로 None. 가격은 확정값이 아닐 수 있어 쓰지 않는다."""
    df = pd.read_csv(io.BytesIO(body), index_col=0, dtype={"Code": str, "ISU_CD": str, "Dept": str, "Close": str})
    if (df["Close"].astype(str) == "-").all():
        return None
    rows = {}
    for r in df.itertuples(index=False):
        if pd.isna(r.Stocks):
            continue
        rows[r.Code] = SnapshotRow(r.Code, r.ISU_CD, r.Name, "" if pd.isna(r.Dept) else r.Dept, int(r.Stocks))
    return rows


def fetch_snapshot(fetcher: Fetcher, date: str) -> dict[str, SnapshotRow] | None:
    """date('YYYY-MM-DD', 수집일 KST) 파일. 파일이 없거나 휴장일 파일이면 None."""
    try:
        body = fetcher.get(SNAPSHOT_URL.format(date=date), raw=f"fdr_krx/{date}.csv")
    except HttpError as err:
        if err.status == 404:
            return None
        raise
    return parse_snapshot(body)


def delistings_from_frame(df: pd.DataFrame) -> list[Delisting]:
    """보통주 주권만 남긴다. 리츠(부동산투자회사)·신주인수권 등은 증권구분이 달라 여기서 빠진다."""
    common = df[(df["SecuGroup"] == "주권") & (df["Kind"] == "보통주")]
    out = []
    for r in common.itertuples(index=False):
        board = BOARDS.get(r.Market)
        if board is None:
            raise ValueError(f"FDR 상장폐지 목록의 시장 값을 모른다: {r.Market} ({r.Symbol})")
        out.append(Delisting(
            code=str(r.Symbol),
            name=str(r.Name),
            board=board,
            listing_date=None if pd.isna(r.ListingDate) else pd.Timestamp(r.ListingDate).strftime("%Y-%m-%d"),
            delisting_date=pd.Timestamp(r.DelistingDate).strftime("%Y-%m-%d"),
            listing_shares=None if pd.isna(r.ListingShares) else int(r.ListingShares),
            reason="" if pd.isna(r.Reason) else str(r.Reason),
        ))
    return out


def fetch_delistings(raw_dir, start: str) -> list[Delisting]:
    df = fdr.StockListing("KRX-DELISTING", start=start)
    if raw_dir is not None:
        save_raw(raw_dir, "fdr_delisting/krx_delisting.csv", df.to_csv(index=False).encode("utf-8"))
    return delistings_from_frame(df)
