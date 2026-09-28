"""FinanceDataReader KRX 캐시: 날짜별 KRX 스냅샷과 상장폐지 목록 (docs/07 §7.1, §7.4, 08 §3)."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from theme_radar.prices.net import Fetcher, HttpError

SNAPSHOT_URL = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
                "refs/heads/master/data/listing/krx/{date}.csv")
DELISTING_URL = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
                 "refs/heads/master/data/listing/delisting/{date}.csv")
DELISTING_LOOKBACK_DAYS = 30
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


@dataclass(frozen=True)
class DelistingSnapshot:
    base_date: str               # 실제로 읽은 파일의 날짜. 오늘이 아닐 수 있다
    rows: list[Delisting]


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


def parse_delistings(body: bytes, start: str, end: str) -> list[Delisting]:
    """캐시 파일은 1956년부터 누적이다. 수집 구간에 폐지된 종목만 남긴다."""
    df = pd.read_csv(io.BytesIO(body), index_col=0, dtype={"Symbol": str, "ToSymbol": str}, thousands=",")
    delisted = pd.to_datetime(df["DelistingDate"], format="%Y-%m-%d", errors="coerce")
    return delistings_from_frame(df[(delisted >= pd.Timestamp(start)) & (delisted <= pd.Timestamp(end))])


def fetch_delistings(fetcher: Fetcher, start: str, today: str,
                     lookback_days: int = DELISTING_LOOKBACK_DAYS) -> DelistingSnapshot:
    """가장 최근 폐지 목록 파일을 읽는다 (docs/07 §7.1).

    날짜마다 파일 하나이고 내용은 누적이라, 며칠 전 파일을 읽어도 그 날까지의 폐지는 전부 들어 있다.
    휴장일에는 파일이 없고 캐시 생성이 끊기기도 하므로(2026-09-18~28 중단 확인. 같은 기간 krx
    스냅샷은 갱신됐다) 오늘부터 거슬러 올라가 처음 찾은 파일을 쓴다. 얼마나 뒤처졌는지는 부르는
    쪽이 C-13으로 본다.
    """
    day = date.fromisoformat(today)
    for _ in range(lookback_days):
        iso = day.isoformat()
        try:
            body = fetcher.get(DELISTING_URL.format(date=iso), raw=f"fdr_delisting/{iso}.csv")
        except HttpError as err:
            if err.status != 404:
                raise
            day -= timedelta(days=1)
            continue
        return DelistingSnapshot(iso, parse_delistings(body, start, today))
    raise RuntimeError(f"FDR 상장폐지 목록 캐시가 {today}부터 {lookback_days}일을 거슬러 올라가도 없다: "
                       f"{DELISTING_URL.format(date=today)}")
