"""종목 마스터(security)와 유니버스 편입 이력(universe_membership) (docs/07 §7.1, §8.1).

규칙 함수는 입출력 없이 두고, DB 반영은 apply_* 함수가 한 트랜잭션 안에서 한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

MAX_DATE = "9999-12-31"
REIT_INDUSTRIES = {"부동산 임대 및 공급업", "신탁업 및 집합투자업"}


@dataclass(frozen=True)
class SecurityRecord:
    market_code: str
    board: str
    ticker: str
    name_local: str
    security_type: str
    currency: str
    listing_date: str | None = None
    delisting_date: str | None = None
    isin: str | None = None
    cik: str | None = None


@dataclass(frozen=True)
class Interval:
    ticker: str
    valid_from: str
    valid_to: str


def day_before(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- KR

def kr_security_type(code: str, name: str, industry: str, dept: str, excluded_funds: set[str]) -> str:
    """KR_COMMON 판정용 증권 종류 (docs/07 §7.1).

    - 인프라·부동산 펀드: 수기 목록
    - 외국기업: 종목코드가 9로 시작. 950은 주식예탁증권(DR), 900은 외국주권
    - 스팩: KOSDAQ 소속부가 SPAC이거나 이름에 '스팩'
    - 리츠: 이름에 '리츠' + 업종이 부동산 임대업 또는 신탁·집합투자업 (메리츠금융지주 등을 거른다)
    """
    if code in excluded_funds:
        return "OTHER"
    if code.startswith("950"):
        return "DR"
    if code.startswith("9"):
        return "OTHER"
    if dept.startswith("SPAC") or "스팩" in name:
        return "SPAC"
    if "리츠" in name and industry in REIT_INDUSTRIES:
        return "REIT"
    return "COMMON"


def kr_records(listings, snapshot, delistings, start: str, excluded_funds: set[str]) -> list[SecurityRecord]:
    """KIND 상장 목록(KOSPI·KOSDAQ)과 start 이후 상장폐지 보통주로 종목 레코드를 만든다.

    폐지 목록에 있어도 현재 상장 목록에 있는 코드는 이전상장이므로 폐지로 보지 않는다.
    """
    records = []
    listed = set()
    for item in listings:
        if item.board not in ("KOSPI", "KOSDAQ"):
            continue
        listed.add(item.code)
        snap = snapshot.get(item.code) if snapshot else None
        records.append(SecurityRecord(
            market_code="KR", board=item.board, ticker=item.code, name_local=item.name,
            security_type=kr_security_type(item.code, item.name, item.industry, snap.dept if snap else "", excluded_funds),
            currency="KRW", listing_date=item.listing_date, isin=snap.isin if snap else None,
        ))
    for item in delistings:
        if item.board not in ("KOSPI", "KOSDAQ") or item.code in listed or item.delisting_date < start:
            continue
        records.append(SecurityRecord(
            market_code="KR", board=item.board, ticker=item.code, name_local=item.name,
            security_type=kr_security_type(item.code, item.name, "", "", excluded_funds),
            currency="KRW", listing_date=item.listing_date, delisting_date=item.delisting_date,
        ))
    return records


# ---------------------------------------------------------------- US

def us_intervals(current, changes, start: str) -> tuple[list[Interval], dict[str, str], list[str]]:
    """현재 구성에서 변경표를 거꾸로 되돌려 start 이후 편입 기간을 만든다 (docs/07 §8.1).

    - 편입 종목은 valid_from = 효력일, 편출 종목은 valid_to = 효력일 전날
    - 편입됐는데 현재 구성에 없고 이후 편출 기록도 없는 티커는 편입 뒤 티커를 바꾼 것으로 본다.
      현재 구성에서 편입일이 효력일과 같은 종목이 하나뿐이면 그 티커로 연결한다(예: SATS → ECHO).
    반환: (편입 기간, 티커 변경 {옛 티커: 현재 티커}, 경고)
    """
    current_tickers = {c.ticker for c in current}
    relevant = sorted((c for c in changes if c.effective_date > start), key=lambda c: c.effective_date, reverse=True)
    added_tickers = {c.added for c in relevant if c.added}
    warnings: list[str] = []

    renames: dict[str, str] = {}
    for change in relevant:
        ticker = change.added
        if not ticker or ticker in current_tickers:
            continue
        removed_later = any(c.removed == ticker and c.effective_date >= change.effective_date for c in relevant)
        if removed_later:
            continue
        candidates = [c.ticker for c in current if c.date_added == change.effective_date
                      and c.ticker not in added_tickers and c.ticker not in renames.values()]
        if len(candidates) == 1:
            renames[ticker] = candidates[0]
        else:
            warnings.append(f"{change.effective_date} 편입 {ticker}: 현재 구성에 없고 바뀐 티커를 특정하지 못했다 (후보 {candidates})")

    open_until = {t: MAX_DATE for t in current_tickers}
    intervals: list[Interval] = []
    for change in relevant:
        if change.removed:
            if change.removed in open_until:
                warnings.append(f"{change.effective_date} 편출 {change.removed}: 이후에도 구성종목으로 남아 있다")
            else:
                open_until[change.removed] = day_before(change.effective_date)
        if change.added:
            ticker = renames.get(change.added, change.added)
            if ticker in open_until:
                intervals.append(Interval(ticker, change.effective_date, open_until.pop(ticker)))
            elif change.added not in renames:
                warnings.append(f"{change.effective_date} 편입 {change.added}: 편입 기간을 만들지 못했다")
    intervals.extend(Interval(t, start, end) for t, end in open_until.items())
    return sorted(intervals, key=lambda i: (i.ticker, i.valid_from)), renames, warnings


def member_counts(intervals: list[Interval], dates: list[str]) -> dict[str, int]:
    return {d: sum(1 for i in intervals if i.valid_from <= d <= i.valid_to) for d in dates}


# ---------------------------------------------------------------- DB 반영

def upsert_security(con: sqlite3.Connection, rec: SecurityRecord, loading_tickers: set[str] = frozenset()) -> int:
    """같은 시장·티커의 상장 중인 종목(또는 상장일이 같은 종목)이 있으면 갱신하고, 없으면 새로 만든다.

    US는 티커가 바뀐 경우를 위해, 새 티커가 없고 같은 CIK이면서 이번 적재 목록(loading_tickers)에 없는
    종목이 하나 있으면 그 종목의 티커를 갱신한다. 적재 목록을 빼는 이유는 GOOG·GOOGL처럼 CIK가 같은
    복수 클래스 라인끼리 티커를 덮어쓰지 않기 위해서다.
    """
    row = con.execute(
        "SELECT security_id FROM security WHERE market_code = ? AND ticker = ? "
        "AND (delisting_date IS NULL OR listing_date IS ?) ORDER BY delisting_date IS NULL DESC LIMIT 1",
        (rec.market_code, rec.ticker, rec.listing_date),
    ).fetchone()
    if row is None and rec.cik:
        same_cik = [r for r in con.execute(
            "SELECT security_id, ticker FROM security WHERE market_code = ? AND cik = ? AND delisting_date IS NULL",
            (rec.market_code, rec.cik)) if r[1] not in loading_tickers]
        if len(same_cik) == 1:
            row = same_cik[0]
    values = (rec.board, rec.ticker, rec.name_local, rec.security_type, rec.listing_date, rec.delisting_date,
              rec.isin, rec.cik, rec.currency)
    if row is None:
        return con.execute(
            "INSERT INTO security (board, ticker, name_local, security_type, listing_date, delisting_date, isin, cik, currency, market_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, rec.market_code)).lastrowid
    con.execute(
        "UPDATE security SET board = ?, ticker = ?, name_local = ?, security_type = ?, listing_date = COALESCE(?, listing_date), "
        "delisting_date = ?, isin = COALESCE(?, isin), cik = COALESCE(?, cik), currency = ? WHERE security_id = ?",
        (*values, row[0]))
    return row[0]


def rebuild_kr_membership(con: sqlite3.Connection) -> int:
    """KR_COMMON은 종목 마스터 조건에서 만든다 (docs/02 §3.3). 폐지일 전날까지 편입."""
    con.execute("DELETE FROM universe_membership WHERE universe_code = 'KR_COMMON'")
    return con.execute(
        "INSERT INTO universe_membership (universe_code, security_id, valid_from, valid_to) "
        "SELECT 'KR_COMMON', security_id, COALESCE(listing_date, '1900-01-01'), "
        "       COALESCE(date(delisting_date, '-1 day'), '9999-12-31') "
        "FROM security WHERE market_code = 'KR' AND board IN ('KOSPI', 'KOSDAQ') AND security_type = 'COMMON' "
        "AND (listing_date IS NULL OR delisting_date IS NULL OR listing_date < delisting_date)"
    ).rowcount


def rebuild_us_membership(con: sqlite3.Connection, intervals: list[Interval], ids: dict[str, int]) -> int:
    con.execute("DELETE FROM universe_membership WHERE universe_code = 'US_SP500'")
    con.executemany("INSERT INTO universe_membership (universe_code, security_id, valid_from, valid_to) VALUES ('US_SP500', ?, ?, ?)",
                    [(ids[i.ticker], i.valid_from, i.valid_to) for i in intervals])
    return len(intervals)
