#!/usr/bin/env python3
"""S&P 500 구성종목 -> GICS 4단계 코드 매핑 수집 스크립트.

1) https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
   -> 티커별 GICS Sector / Sub-Industry 명칭
2) data/gics_classification.csv (MSCI GICS Methodology 2024-08판)
   -> 소분류명으로 섹터/산업그룹/산업/소분류 코드 부여
3) State Street SPDR ETF 일별 보유종목 (교차검증)
   - SPY                      -> 유니버스(구성종목 집합) 일치 여부
   - Select Sector SPDR 11종  -> 종목별 GICS 섹터 일치 여부
   -> data/gics_sp500_constituents.csv

사용법::

    python scripts/fetch_gics_sp500.py           # 수집 + 매핑 + 교차검증
    python scripts/fetch_gics_sp500.py --no-raw  # 원본 캐시 저장 생략

의존성: Python 3.9+ 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SSGA_URL = (
    "https://www.ssga.com/us/en/intermediary/library-content/products/"
    "fund-data/etfs/us/holdings-daily-us-en-{etf}.xlsx"
)
USER_AGENT = "Mozilla/5.0 (theme-radar data collector)"
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3

# Select Sector SPDR ETF -> GICS 섹터 코드. 각 ETF는 S&P 500 중 해당 섹터 종목만 담는다.
SECTOR_ETFS = {
    "XLE": "10", "XLB": "15", "XLI": "20", "XLY": "25", "XLP": "30", "XLV": "35",
    "XLF": "40", "XLK": "45", "XLC": "50", "XLU": "55", "XLRE": "60",
}

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

# Windows 콘솔 기본 코드페이지(cp949)에서 한글 로그가 깨지지 않게 한다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- http


def http_get(url: str, cache_file: Path | None = None) -> bytes:
    """URL 본문을 바이트로 반환한다. cache_file 을 주면 원본도 그대로 저장한다."""
    last_err: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read()
            break
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last_err = err
            if attempt == HTTP_RETRIES:
                raise RuntimeError(f"HTTP 요청 실패: {url}") from last_err
            time.sleep(2 * attempt)

    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(body)
    return body


# ---------------------------------------------------------------- csv


def write_csv(path: Path, header: Iterable[str], rows: Iterable[Iterable[Any]]) -> None:
    """UTF-8 with BOM, LF 개행, 텍스트 필드만 따옴표로 감싼 CSV 를 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow(header)
        csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\n").writerows(rows)


def load_gics_classification() -> dict[str, dict[str, str]]:
    """소분류명 -> 4단계 코드/명칭 행. 필드 앞뒤 정렬용 공백은 제거한다."""
    path = DATA_DIR / "gics_classification.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = [
            {k.strip(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(fh, skipinitialspace=True)
        ]
    by_name = {r["sub_industry_name"]: r for r in rows}
    if len(by_name) != 163:
        raise RuntimeError(f"{path} 소분류가 163개가 아닙니다: {len(by_name)}")
    return by_name


# ---------------------------------------------------------------- 1. Wikipedia


class _TableParser(HTMLParser):
    """문서 내 모든 <table> 을 (id, 행 단위 셀 텍스트) 로 수집한다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[str | None, list[list[str]]]] = []
        self._id: str | None = None
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._table, self._id = [], dict(attrs).get("id")
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append((self._id, self._table))
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def fetch_wikipedia(save_raw: bool) -> tuple[list[dict[str, str]], str]:
    """(구성종목 행 목록, 리비전 설명) 을 반환한다."""
    print(f"[1/3] Wikipedia S&P 500 구성종목 수집: {WIKI_URL}")
    html = http_get(WIKI_URL, RAW_DIR / "wiki_sp500.html" if save_raw else None).decode("utf-8")

    parser = _TableParser()
    parser.feed(html)
    table = next((t for tid, t in parser.tables if tid == "constituents"), None)
    if table is None:
        raise RuntimeError('id="constituents" 표를 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.')

    header = table[0]
    need = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Date added", "CIK"]
    missing = [h for h in need if h not in header]
    if missing:
        raise RuntimeError(f"구성종목 표에 컬럼이 없습니다: {missing} (헤더: {header})")

    rows = []
    for cells in table[1:]:
        if len(cells) != len(header):
            raise RuntimeError(f"셀 개수가 헤더와 다른 행: {cells}")
        rows.append({h: cells[header.index(h)] for h in need})

    rev = re.search(r'"wgRevisionId":(\d+)', html)
    edited = re.search(r"last edited on ([^<]+?), at ([0-9:]+)", html)
    revision = (
        f"revision {rev.group(1) if rev else '?'}"
        + (f", last edited {edited.group(1)} {edited.group(2)} UTC" if edited else "")
    )
    print(f"      {len(rows)}종목 ({revision})")
    return rows, revision


# ---------------------------------------------------------------- 2. SSGA


_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx_rows(body: bytes) -> list[list[str]]:
    """첫 번째 시트를 셀 문자열 행 목록으로 읽는다 (공유 문자열 / 인라인 문자열 지원)."""
    zf = zipfile.ZipFile(io.BytesIO(body))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).iter(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))

    rows: list[list[str]] = []
    for row in ET.fromstring(zf.read("xl/worksheets/sheet1.xml")).iter(f"{_NS}row"):
        cells: dict[int, str] = {}
        for c in row.iter(f"{_NS}c"):
            letters = re.match(r"[A-Z]+", c.get("r", "A")).group()
            col = 0
            for ch in letters:
                col = col * 26 + ord(ch) - 64
            v = c.find(f"{_NS}v")
            if c.get("t") == "s" and v is not None:
                cells[col - 1] = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                cells[col - 1] = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
            else:
                cells[col - 1] = v.text if v is not None and v.text else ""
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def fetch_holdings(etf: str, save_raw: bool) -> tuple[str, set[str]]:
    """(보유종목 기준일 YYYY-MM-DD, 주식 티커 집합) 을 반환한다."""
    cache = RAW_DIR / f"ssga_holdings_{etf.lower()}.xlsx" if save_raw else None
    rows = read_xlsx_rows(http_get(SSGA_URL.format(etf=etf.lower()), cache))

    as_of = ""
    start = None
    for i, r in enumerate(rows):
        if r[:1] == ["Holdings:"] and len(r) > 1:
            m = re.search(r"\d{1,2}-[A-Za-z]{3}-\d{4}", r[1])
            as_of = datetime.strptime(m.group(), "%d-%b-%Y").strftime("%Y-%m-%d") if m else ""
        if r[:2] == ["Name", "Ticker"]:
            start = i + 1
            break
    if start is None:
        raise RuntimeError(f"{etf} 보유종목 표 헤더(Name, Ticker)를 찾지 못했습니다.")

    tickers: set[str] = set()
    for r in rows[start:]:
        if not r or not r[0]:
            break
        ticker, currency = r[1], (r[7] if len(r) > 7 else "")
        # 현금('-'), 지수선물(IXTU6 등), 비상장 권리(2602335D 등)는 티커에 숫자가 있어 제외된다.
        if currency == "USD" and re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", ticker):
            tickers.add(ticker)
    return as_of, tickers


# ---------------------------------------------------------------- main


def build(save_raw: bool) -> int:
    gics = load_gics_classification()
    wiki_rows, revision = fetch_wikipedia(save_raw)

    # --- 소분류명 -> 코드
    errors: list[str] = []
    mapped: list[dict[str, str]] = []
    for r in wiki_rows:
        g = gics.get(r["GICS Sub-Industry"])
        if g is None:
            errors.append(f"{r['Symbol']}: 공식 분류표에 없는 소분류명 '{r['GICS Sub-Industry']}'")
            continue
        if g["sector_name"] != r["GICS Sector"]:
            errors.append(
                f"{r['Symbol']}: 섹터 불일치 (위키 '{r['GICS Sector']}' vs 소분류 상위 '{g['sector_name']}')"
            )
            continue
        mapped.append({**r, **g})

    tickers = [r["Symbol"] for r in wiki_rows]
    dups = sorted({t for t in tickers if tickers.count(t) > 1})
    if dups:
        errors.append(f"티커 중복: {dups}")
    if errors:
        for e in errors:
            print(f"      오류: {e}", file=sys.stderr)
        raise RuntimeError(f"GICS 코드 매핑 실패 {len(errors)}건 — CSV 를 쓰지 않습니다.")
    print(f"[2/3] GICS 코드 매핑: {len(mapped)}종목 전부 공식 소분류명과 일치")

    # --- SSGA 교차검증
    print("[3/3] State Street SPDR ETF 보유종목 교차검증")
    spy_date, spy = fetch_holdings("SPY", save_raw)
    wiki_set = set(tickers)
    only_wiki, only_spy = sorted(wiki_set - spy), sorted(spy - wiki_set)
    print(f"      SPY ({spy_date}) {len(spy)}종목 | 위키에만 {only_wiki or '없음'} | SPY에만 {only_spy or '없음'}")

    etf_sector: dict[str, str] = {}
    etf_dates: set[str] = set()
    for etf, code in SECTOR_ETFS.items():
        as_of, held = fetch_holdings(etf, save_raw)
        etf_dates.add(as_of)
        for t in held:
            etf_sector[t] = code
        print(f"      {etf:<5} ({as_of}) 섹터 {code}: {len(held)}종목")

    header = [
        "ticker", "company_name",
        "sector_code", "sector_name",
        "industry_group_code", "industry_group_name",
        "industry_code", "industry_name",
        "sub_industry_code", "sub_industry_name",
        "cik", "date_added",
        "in_spy", "etf_sector_check",
    ]
    out: list[list[Any]] = []
    mismatches = []
    for r in sorted(mapped, key=lambda x: (x["sub_industry_code"], x["Symbol"])):
        t = r["Symbol"]
        etf_code = etf_sector.get(t)
        if etf_code is None:
            check = "NOT_FOUND"
        elif etf_code == r["sector_code"]:
            check = "OK"
        else:
            check = f"MISMATCH:{etf_code}"
        if check != "OK":
            mismatches.append((t, r["sector_code"], check))
        out.append([
            t, r["Security"],
            r["sector_code"], r["sector_name"],
            r["industry_group_code"], r["industry_group_name"],
            r["industry_code"], r["industry_name"],
            r["sub_industry_code"], r["sub_industry_name"],
            r["CIK"], r["Date added"],
            "Y" if t in spy else "N", check,
        ])

    path = DATA_DIR / "gics_sp500_constituents.csv"
    write_csv(path, header, out)

    ok = sum(1 for row in out if row[-1] == "OK")
    print(f"      섹터 일치 {ok}/{len(out)} | 불일치/미발견 {mismatches or '없음'}")
    print(f"\n완료. {len(out)}종목 -> {path}")
    print(f"      출처: Wikipedia {revision} / SSGA 보유종목 기준일 {', '.join(sorted(etf_dates | {spy_date}))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S&P 500 구성종목 GICS 코드 매핑 수집")
    ap.add_argument("--no-raw", action="store_true", help="원본 HTML/XLSX 캐시를 data/raw 에 저장하지 않는다.")
    args = ap.parse_args(argv)
    try:
        return build(save_raw=not args.no_raw)
    except RuntimeError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
