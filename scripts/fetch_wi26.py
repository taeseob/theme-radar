#!/usr/bin/env python3
"""WI26 (Wise Sector Classification) 수집 스크립트.

1) https://www.wiseindex.com/About/WI26
   -> WI26 대분류 / 소분류 분류표          -> data/wi26_classification.csv
2) https://www.wiseindex.com/Index/GetIndexComponets
   -> 대분류 26개 각각의 구성종목          -> data/wi26_constituents.csv

사용법::

    python scripts/fetch_wi26.py                 # 최근 영업일 자동 탐색
    python scripts/fetch_wi26.py --date 20260911 # 기준일자 지정
    python scripts/fetch_wi26.py --no-raw        # 원본 캐시 저장 생략

의존성: Python 3.9+ 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

BASE = "https://www.wiseindex.com"
KST = timezone(timedelta(hours=9))
USER_AGENT = "Mozilla/5.0 (theme-radar data collector)"
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3

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
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "ko-KR,ko;q=0.9",
                },
            )
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


def as_number(value: Any) -> Any:
    """JSON 수치를 CSV 출력용으로 정규화한다 (1.0 -> 1)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def write_csv(path: Path, header: Iterable[str], rows: Iterable[Iterable[Any]]) -> None:
    """UTF-8 with BOM, LF 개행, 텍스트 필드만 따옴표로 감싼 CSV 를 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        # 헤더는 따옴표 없이, 본문은 텍스트 필드만 따옴표로 감싼다.
        csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow(header)
        csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\n").writerows(rows)


# ---------------------------------------------------------------- 1. 분류표


class _TableParser(HTMLParser):
    """문서 내 모든 <table> 을 행 단위 셀 텍스트로 수집한다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._table = []
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
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def fetch_classification(save_raw: bool = True) -> list[tuple[str, str]]:
    """분류표를 수집해 CSV 로 저장하고, 대분류 (코드, 명칭) 목록을 반환한다."""
    print(f"[1/2] WI26 분류표 수집: {BASE}/About/WI26")
    raw = http_get(f"{BASE}/About/WI26", RAW_DIR / "about_wi26.html" if save_raw else None)
    html = raw.decode("utf-8")

    parser = _TableParser()
    parser.feed(html)

    table = next(
        (
            t
            for t in parser.tables
            if any(re.fullmatch(r"WI\s*대분류", cell) for row in t for cell in row)
        ),
        None,
    )
    if table is None:
        raise RuntimeError("분류표 <table> 을 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.")

    rows: list[tuple[str, str, str, str]] = []
    sector_code = sector_name = ""
    for cells in table:
        if len(cells) == 4 and cells[0].startswith("WI"):
            # rowspan 시작 행: 대분류 + 첫 소분류
            sector_code, sector_name = cells[0], cells[1]
            rows.append((sector_code, sector_name, cells[2], cells[3]))
        elif len(cells) == 2 and cells[0].startswith("WI") and sector_code:
            # rowspan 이어지는 행: 소분류만 (대분류는 직전 값 유지)
            rows.append((sector_code, sector_name, cells[0], cells[1]))

    if not rows:
        raise RuntimeError("분류표 행을 파싱하지 못했습니다.")

    path = DATA_DIR / "wi26_classification.csv"
    write_csv(path, ["sector_code", "sector_name", "sub_sector_code", "sub_sector_name"], rows)

    sectors: list[tuple[str, str]] = []
    seen: set[str] = set()
    for code, name, _, _ in rows:
        if code not in seen:
            seen.add(code)
            sectors.append((code, name))

    print(f"      대분류 {len(sectors)}개 / 소분류 {len(rows)}개 -> {path}")
    return sectors


# ---------------------------------------------------------------- 2. 구성종목


def parse_dotnet_date(raw: str | None) -> str:
    """'/Date(1789052400000)/' -> 'YYYY-MM-DD' (KST 자정 기준 타임스탬프)."""
    if not raw:
        return ""
    m = re.search(r"(-?\d+)", raw)
    if not m:
        return ""
    moment = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
    return moment.astimezone(KST).strftime("%Y-%m-%d")


def fetch_components(sec_cd: str, dt: str, save_raw: bool = True) -> dict[str, Any]:
    url = f"{BASE}/Index/GetIndexComponets?ceil_yn=0&dt={dt}&sec_cd={sec_cd}"
    cache = RAW_DIR / f"components_{sec_cd}_{dt}.json" if save_raw else None
    return json.loads(http_get(url, cache).decode("utf-8"))


def resolve_base_date(requested: str | None, save_raw: bool = True) -> tuple[str, str]:
    """(dt, trade_date) 를 반환한다. requested 가 없으면 최근 영업일을 역순 탐색한다."""
    if requested:
        info = fetch_components("WI620", requested, save_raw).get("info", {})
        if not info.get("CNT"):
            raise RuntimeError(f"지정한 기준일자({requested})에 데이터가 없습니다.")
        return requested, parse_dotnet_date(info.get("TRD_DT"))

    print("      기준일자 탐색 중...")
    today = datetime.now(KST)
    for back in range(15):
        dt = (today - timedelta(days=back)).strftime("%Y%m%d")
        try:
            info = fetch_components("WI620", dt, save_raw).get("info", {})
        except RuntimeError:
            continue
        if info.get("CNT"):
            return dt, parse_dotnet_date(info.get("TRD_DT"))
    raise RuntimeError("최근 15일 내에 데이터가 있는 영업일을 찾지 못했습니다.")


def fetch_all_constituents(
    sectors: list[tuple[str, str]],
    requested_dt: str | None,
    save_raw: bool = True,
) -> tuple[str, int]:
    print("[2/2] WI26 대분류 구성종목 수집")

    dt, trade_date = resolve_base_date(requested_dt, save_raw)
    print(f"      기준일자: {trade_date} (dt={dt})")

    header = [
        "base_date", "sector_code", "sector_name", "seq",
        "ticker", "company_name",
        "market_cap_mn_krw", "weight_pct", "cum_weight_pct", "cap_factor",
        "applied_shares", "wics_sector_code", "wics_sector_name",
        "sector_market_cap_mn_krw",
    ]

    rows: list[list[Any]] = []
    for code, name in sectors:
        payload = fetch_components(code, dt, save_raw)
        members = payload.get("list") or []
        if not members:
            print(f"      경고: {code}({name}) 구성종목이 0건입니다.", file=sys.stderr)

        for r in members:
            sector_name = re.sub(r"^WI26\s*", "", r.get("IDX_NM_KOR") or "") or name
            rows.append([
                trade_date,
                r.get("IDX_CD") or code,
                sector_name,
                as_number(r.get("SEQ")),
                r.get("CMP_CD") or "",
                r.get("CMP_KOR") or "",
                as_number(r.get("MKT_VAL")),
                as_number(r.get("WGT")),
                as_number(r.get("S_WGT")),
                as_number(r.get("CAL_WGT")),
                as_number(r.get("APT_SHR_CNT")),
                r.get("SEC_CD") or "",
                r.get("SEC_NM_KOR") or "",
                as_number(r.get("ALL_MKT_VAL")),
            ])
        print(f"      {code:<6} {name:<12} {len(members):>4}종목")

    path = DATA_DIR / "wi26_constituents.csv"
    write_csv(path, header, rows)
    print(f"      합계 {len(rows)}종목 ({len(sectors)}개 대분류) -> {path}")
    return trade_date, len(rows)


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WI26 분류표 및 대분류 구성종목 수집")
    ap.add_argument(
        "--date",
        metavar="YYYYMMDD",
        help="기준일자. 생략하면 데이터가 존재하는 가장 최근 영업일을 사용한다.",
    )
    ap.add_argument(
        "--no-raw",
        action="store_true",
        help="원본 HTML/JSON 캐시를 data/raw 에 저장하지 않는다.",
    )
    args = ap.parse_args(argv)

    if args.date and not re.fullmatch(r"\d{8}", args.date):
        ap.error(f"기준일자는 YYYYMMDD 형식이어야 합니다: {args.date}")

    save_raw = not args.no_raw
    try:
        sectors = fetch_classification(save_raw)
        trade_date, total = fetch_all_constituents(sectors, args.date, save_raw)
    except RuntimeError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1

    print(f"\n완료. 기준일자 {trade_date}, 총 {total} 종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
