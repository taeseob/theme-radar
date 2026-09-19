"""yfinance: US 종가·분할 이벤트·거래일·복수 클래스 주식수 (docs/07 §8.2, §8.3, 08 §4)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from theme_radar.prices.net import save_raw


@dataclass(frozen=True)
class Bar:
    trade_date: str
    close: float           # 분할이 소급 반영된 종가 (배당 조정 없음)
    volume: int | None
    split_ratio: float     # 권리락일에 분할 비율 (10:1 분할 = 10), 그 밖의 날은 0


def setup_cache(cache_dir: Path) -> None:
    """시간대·쿠키 캐시를 프로젝트 안에 둔다."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))


def yahoo_symbol(ticker: str) -> str:
    return ticker.replace(".", "-")


def frame_to_bars(df: pd.DataFrame, tickers: list[str]) -> dict[str, list[Bar]]:
    """(컬럼 종류, 야후 심볼) 2단 컬럼 DataFrame을 티커별 Bar 목록으로 푼다."""
    if df.empty:
        return {t: [] for t in tickers}
    if "Adj Close" not in df.columns.get_level_values(0):
        # C-12: auto_adjust=True로 받으면 Adj Close가 빠지고 Close가 배당 조정 값이 된다
        raise ValueError("yfinance 결과에 'Adj Close'가 없다. auto_adjust=False로 받아야 한다 (C-12)")
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    out = {}
    for ticker in tickers:
        symbol = yahoo_symbol(ticker)
        if ("Close", symbol) not in df.columns:
            out[ticker] = []
            continue
        close = df[("Close", symbol)]
        volume = df[("Volume", symbol)] if ("Volume", symbol) in df.columns else None
        splits = df[("Stock Splits", symbol)] if ("Stock Splits", symbol) in df.columns else None
        bars = []
        for i, d in enumerate(dates):
            c = close.iloc[i]
            if pd.isna(c):
                continue
            v = None if volume is None or pd.isna(volume.iloc[i]) else int(volume.iloc[i])
            s = 0.0 if splits is None or pd.isna(splits.iloc[i]) else float(splits.iloc[i])
            bars.append(Bar(d, float(c), v, s))
        out[ticker] = bars
    return out


def _download(symbols: list[str], start: str, threads: bool) -> pd.DataFrame:
    return yf.download(symbols, start=start, auto_adjust=False, actions=True, threads=threads,
                       progress=False, multi_level_index=True)


def download_bars(tickers: list[str], start: str, raw_dir: Path | None) -> dict[str, list[Bar]]:
    """start 이후 일봉. 결과가 빈 티커는 순차로 한 번 더 받는다(병렬 다운로드 중 캐시 잠금 오류가 난다).

    이력이 없는 티커는 예외 없이 빈 목록이다 (C-11은 호출하는 쪽에서 검사).
    """
    result: dict[str, list[Bar]] = {}
    batch = sorted(set(tickers))
    for attempt, threads in enumerate((True, False)):
        if not batch:
            break
        df = _download([yahoo_symbol(t) for t in batch], start, threads)
        if raw_dir is not None:
            save_raw(raw_dir, f"yfinance/download_{start}_try{attempt + 1}.csv", df.to_csv().encode("utf-8"))
        bars = frame_to_bars(df, batch)
        result.update({t: b for t, b in bars.items() if b})
        batch = [t for t in batch if not bars.get(t)]
    result.update({t: [] for t in batch})
    return result


@dataclass(frozen=True)
class SharesInfo:
    shares_outstanding: int | None       # 클래스(라인)별 현재 주식수
    implied_shares_outstanding: int | None  # 회사 합계


def fetch_shares_info(tickers: list[str], raw_dir: Path | None) -> dict[str, SharesInfo]:
    """quoteSummary는 쿠키·crumb 토큰이 필요해 직접 호출하면 401이다. yfinance가 처리한다."""
    out = {}
    for ticker in tickers:
        info = yf.Ticker(yahoo_symbol(ticker)).info
        if raw_dir is not None:
            save_raw(raw_dir, f"yf_info/{ticker}.json", json.dumps(info, default=str).encode("utf-8"))

        def as_int(key):
            value = info.get(key)
            return int(value) if isinstance(value, (int, float)) and not math.isnan(value) else None

        out[ticker] = SharesInfo(as_int("sharesOutstanding"), as_int("impliedSharesOutstanding"))
    return out


@dataclass(frozen=True)
class IndexBar:
    """지수 일봉. 지수는 분할·배당 조정이 없어 받은 값을 그대로 쓴다."""
    trade_date: str
    open: float
    high: float
    low: float
    close: float


def frame_to_index_bars(df: pd.DataFrame, symbol: str) -> list[IndexBar]:
    """(컬럼 종류, 심볼) 2단 컬럼 DataFrame에서 지수 일봉을 뽑는다. 값이 빈 날은 건너뛴다."""
    if df.empty:
        return []
    column = {name: df[(name, symbol)].tolist() for name in ("Open", "High", "Low", "Close")}
    bars = []
    for i, day in enumerate(df.index):
        values = [column[name][i] for name in ("Open", "High", "Low", "Close")]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) or v <= 0 for v in values):
            continue
        bars.append(IndexBar(day.strftime("%Y-%m-%d"), *(float(v) for v in values)))
    return bars


def fetch_index(symbol: str, start: str, raw_dir: Path | None) -> list[IndexBar]:
    """지수 일봉. US 거래일 캘린더도 S&P 500 지수(^GSPC)의 날짜로 만든다 (docs/07 §8.4)."""
    df = yf.download(symbol, start=start, auto_adjust=False, progress=False, multi_level_index=True)
    if raw_dir is not None:
        save_raw(raw_dir, f"yfinance/{symbol.lstrip('^').lower()}.csv", df.to_csv().encode("utf-8"))
    return frame_to_index_bars(df, symbol)
