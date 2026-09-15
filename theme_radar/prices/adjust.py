"""수정계수·기업행위·상장주식수 계산. 입출력이 없는 순수 함수만 둔다 (docs/07 §7 ~ §9)."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

# ---------------------------------------------------------------- KR 호가단위 (C-1)

_KRX_TICKS = [(2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)]


def krx_tick(price: float) -> int:
    for limit, tick in _KRX_TICKS:
        if price < limit:
            return tick
    return 1_000


def is_valid_krx_price(price: float) -> bool:
    return price > 0 and price == int(price) and int(price) % krx_tick(price) == 0


# ---------------------------------------------------------------- 가격 행

@dataclass
class PriceRow:
    trade_date: str
    close_raw: float | None
    adj_factor: float | None
    close_adj: float
    volume: int | None
    trade_status: str


def kr_rows(bars, raw_closes: dict[str, float], stored_raw: dict[str, float | None]) -> tuple[list[PriceRow], list[str]]:
    """siseJson 수정 종가(bars)와 무수정 종가를 날짜로 맞춘다. (가격 행, C-1로 거부한 날짜)를 돌려준다.

    - 저장된 원종가가 있으면 그 값을 쓴다(원값 불변).
    - 새로 받은 원종가가 호가단위에 맞지 않으면 그날 행을 만들지 않는다(C-1).
    - 원종가를 받지 못한 날(상장폐지 종목)은 kr_delisted_raw()로 채운 값을 raw_closes로 넘긴다.
    """
    rows, rejected = [], []
    for bar in sorted(bars, key=lambda b: b.trade_date):
        status = "NO_TRADE" if bar.no_trade else "NORMAL"
        if bar.trade_date in stored_raw and stored_raw[bar.trade_date] is not None:
            raw = stored_raw[bar.trade_date]
        elif bar.trade_date in raw_closes:
            raw = raw_closes[bar.trade_date]
            if raw is not None and not is_valid_krx_price(raw):
                rejected.append(bar.trade_date)
                continue
        else:
            raw = None
        if raw is None and bar.no_trade and rows and rows[-1].close_raw is not None and rows[-1].close_adj == bar.close:
            raw = rows[-1].close_raw   # 거래 없는 날 원종가가 비어 있으면 수정 종가가 같은 전날 원종가를 잇는다
        factor = None if raw is None else bar.close / raw
        rows.append(PriceRow(bar.trade_date, raw, factor, bar.close, bar.volume, status))
    return rows, rejected


def kr_delisted_raw(bars) -> dict[str, float | None]:
    """원종가 API가 비어 있는 상장폐지 종목: siseJson 종가를 원종가로 인정하되, 최근부터 거슬러 호가단위 검사를
    처음 실패한 날과 그 이전은 수정 여부를 알 수 없으므로 NULL로 둔다 (docs/07 §7.2)."""
    out: dict[str, float | None] = {}
    valid = True
    for bar in sorted(bars, key=lambda b: b.trade_date, reverse=True):
        if valid and not is_valid_krx_price(bar.close):
            valid = False
        out[bar.trade_date] = bar.close if valid else None
    return out


def us_rows(bars, stored_raw: dict[str, float | None]) -> list[PriceRow]:
    """yfinance 종가(분할 소급)에 이후 분할 비율을 곱해 원종가를 역산한다 (docs/07 §8.2).

    저장된 원종가가 있으면 그 값을 쓰고 계수만 새로 계산한다.
    """
    bars = sorted(bars, key=lambda b: b.trade_date)
    rows = []
    later = 1.0                     # 이 날 이후(초과)에 일어난 분할 비율의 곱
    for bar in reversed(bars):
        stored = stored_raw.get(bar.trade_date)
        raw = stored if stored is not None else round(bar.close * later, 2)
        rows.append(PriceRow(bar.trade_date, raw, bar.close / raw, bar.close, bar.volume, "NORMAL"))
        if bar.split_ratio > 0:
            later *= bar.split_ratio
    rows.reverse()
    return rows


def factor_changed(new: float | None, old: float | None, close_adj: float, market: str) -> bool:
    """수정계수 변화 판정. 허용오차는 수정 종가 반올림 오차 KR 0.5원, US 0.005달러 (docs/07 §9.2)."""
    if new is None or old is None:
        return (new is None) != (old is None)
    unit = 0.5 if market == "KR" else 0.005
    return abs(new / old - 1) > unit / close_adj + 1e-6


# ---------------------------------------------------------------- 기업행위

@dataclass(frozen=True)
class Action:
    ex_date: str
    action_type: str       # SPLIT, REVERSE_SPLIT, UNKNOWN
    price_factor: float    # ex_date 이전 가격에 곱하는 값
    share_ratio: float | None


def classify_share_ratio(ratio: float) -> tuple[str, float | None]:
    """이후 ÷ 이전 주식수 비율 r. r 또는 1/r이 2 이상의 정수면 분할·병합, 그 밖에는 주식수에 반영하지 않는다 (docs/07 §8.3)."""
    n = round(ratio)
    if n >= 2 and abs(ratio - n) / n < 1e-3:
        return "SPLIT", float(n)
    m = round(1 / ratio)
    if m >= 2 and abs(1 / ratio - m) / m < 1e-3:
        return "REVERSE_SPLIT", 1 / m
    return "UNKNOWN", None


MIN_ACTION_CHANGE = 0.005   # 네이버 수정 종가의 반올림 흔들림(실측 0.1% 이하)을 기업행위로 보지 않는다


def kr_actions(rows: list[PriceRow]) -> list[Action]:
    """수정계수가 0.5% 넘게 바뀐 경계일을 기업행위로 본다. price_factor = 경계 전 계수 ÷ 경계 후 계수.

    거래정지 중 기업행위가 있으면 경계일은 거래가 재개된 날이 된다(예: 207940 인적분할).
    """
    known = [r for r in sorted(rows, key=lambda r: r.trade_date) if r.adj_factor is not None]
    actions = []
    for prev, cur in zip(known, known[1:]):
        tol = MIN_ACTION_CHANGE + 0.5 / prev.close_adj + 0.5 / cur.close_adj
        if abs(prev.adj_factor / cur.adj_factor - 1) > tol:
            price_factor = prev.adj_factor / cur.adj_factor
            action_type, share_ratio = classify_share_ratio(1 / price_factor)
            actions.append(Action(cur.trade_date, action_type, price_factor, share_ratio))
    return actions


def us_actions(bars) -> list[Action]:
    actions = []
    for bar in bars:
        if bar.split_ratio > 0 and bar.split_ratio != 1:
            action_type, share_ratio = classify_share_ratio(bar.split_ratio)
            actions.append(Action(bar.trade_date, action_type, 1 / bar.split_ratio, share_ratio))
    return actions


# ---------------------------------------------------------------- 상장주식수

@dataclass(frozen=True)
class Observation:
    as_of_date: str
    shares: int


# 시장 통화 기준 시가총액 범위. KR 하한은 정리매매(2원 × 3천만 주)를 걸러내지 않도록 낮게 둔다
PLAUSIBLE_MARKET_CAP = {"KR": (1e6, 1e16), "US": (1e8, 2e13)}


def drop_implausible(observations: list[Observation], prices: list[tuple[str, float | None]],
                     bounds: tuple[float, float]) -> tuple[list[Observation], list[Observation]]:
    """관측치 × 그 무렵 원종가로 만든 시가총액이 범위를 벗어나는 관측치를 버린다. (남길 것, 버린 것)

    합병 전 지주회사의 1,000주(PSKY), 자리수가 틀린 공시(AEP 481조 주, PKG) 같은 값이 시총을 수천~수만 배
    틀어지게 해서다. 관측일 이전 가격이 없으면 가장 이른 가격을 쓴다. 가격이 전혀 없으면 검사하지 않는다.
    """
    known = sorted((d, p) for d, p in prices if p)
    if not known:
        return list(observations), []
    dates = [d for d, _ in known]
    keep, dropped = [], []
    for o in observations:
        i = bisect_right(dates, o.as_of_date) - 1
        price = known[max(i, 0)][1]
        (keep if bounds[0] <= o.shares * price <= bounds[1] else dropped).append(o)
    return keep, dropped


def fill_shares(rows: list[tuple[str, float | None]], observations: list[Observation],
                splits: list[tuple[str, float]]) -> list[int | None]:
    """가격 행 (날짜, 수정계수)마다 상장주식수를 정한다 (docs/07 §7.4, §8.3).

    - 날짜 이전(포함) 관측치가 있으면 가장 최근 값에, 관측일 이후 ~ 그날까지의 분할·병합 비율을 곱한다.
    - 첫 관측일보다 이른 날은 첫 관측치를 수정계수 비율로 환산한다(가동 전 근사, G-1·G-3).
    observations는 날짜별 하나(출처 우선순위 적용 후), splits는 (권리락일, share_ratio)다.
    """
    obs = sorted(observations, key=lambda o: o.as_of_date)
    splits = sorted(splits)
    if not obs:
        return [None] * len(rows)
    first = obs[0]
    ordered = sorted(rows)
    # 첫 관측일의 수정계수: 그날 이후 첫 행, 없으면(폐지 전날 관측치처럼 마지막 거래일보다 늦으면) 직전 행
    anchor_factor = next((f for d, f in ordered if d >= first.as_of_date and f is not None), None)
    if anchor_factor is None:
        anchor_factor = next((f for d, f in reversed(ordered) if d < first.as_of_date and f is not None), None)
    out: list[int | None] = []
    i = -1
    for trade_date, factor in rows:           # rows는 날짜 오름차순
        while i + 1 < len(obs) and obs[i + 1].as_of_date <= trade_date:
            i += 1
        if i >= 0:
            base = obs[i]
            ratio = 1.0
            for ex_date, share_ratio in splits:
                if base.as_of_date < ex_date <= trade_date:
                    ratio *= share_ratio
            out.append(round(base.shares * ratio))
        elif factor is not None and anchor_factor is not None:
            out.append(round(first.shares * factor / anchor_factor))
        else:
            out.append(None)
    return out
