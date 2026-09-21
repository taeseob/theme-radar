#!/usr/bin/env python3
"""WI26 소분류 종목 매핑 수집 스크립트.

WiseIndex 구성종목 API 는 소분류를 받지 않는다. 소분류는 지수가 아니라 분류 단계라서
Component 탭이 없고, `sec_cd=WI11010` 으로 물으면 `CNT = 0` 이 온다. 그래서 두 조각을 잇는다.

1) WI26-WICS 섹터맵핑 PDF   -> WI26 소분류 ↔ WICS 소분류      -> data/wi26_wics_map.csv
2) 네이버 업종(= WICS 소분류) 구성종목 -> 종목 ↔ WICS 소분류
   둘을 조인해                        종목 ↔ WI26 소분류       -> data/wi26_sub_constituents.csv

조인 결과는 짐작이 아니다. 이미 가진 data/wi26_constituents.csv 에 종목별 **대분류**가 있으므로,
파생한 소분류의 상위 대분류와 전건 대조한다. 한 건이라도 어긋나면 CSV 를 쓰지 않고 실패한다.

사용법::

    python scripts/fetch_wi26_sub.py            # 수집 후 검증, 통과하면 CSV 두 개를 쓴다
    python scripts/fetch_wi26_sub.py --no-raw   # 원본 캐시 저장 생략

의존성: Python 3.9+ 표준 라이브러리만 사용한다. 같은 디렉터리의 fetch_wi26.py(HTTP·CSV·표 파서)와
parse_gics.py(PDF 파서)를 재사용한다.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_wi26 import DATA_DIR, KST, RAW_DIR, _TableParser, http_get, write_csv  # noqa: E402
from parse_gics import PdfDocument, page_runs  # noqa: E402

MAP_PDF_URL = "https://www.wiseindex.com/konannas/files/WI26-WICS%EC%84%B9%ED%84%B0%EB%A7%A4%ED%95%91.pdf"
WICS_URL = "https://www.wiseindex.com/About/WICS"
NAVER_LIST_URL = "https://m.stock.naver.com/api/stocks/industry?page=1&pageSize=100"
NAVER_MEMBERS_URL = "https://m.stock.naver.com/api/stocks/industry/{no}?page={page}&pageSize=100"

# 네이버 업종은 WICS 소분류지만, 지수가 아닌 잡동사니 묶음이 하나 섞여 있다(ETN·ETF).
# WICS 소분류명으로 해석되지 않는 업종은 이것뿐이어야 한다.
NAVER_NOT_A_SECTOR = "기타"

# 맵핑 PDF(2022-04판) 이후 WICS 가 개편되며 생긴 소분류. 성격이 같은 옛 소분류를 거쳐 WI26 에 잇는다.
# 이어 붙인 결과도 대분류 대조를 그대로 통과해야 한다.
WICS_BRIDGE = {
    "G502010": "G254010",   # 광고
    "G502020": "G254020",   # 방송과엔터테인먼트
    "G502030": "G254030",   # 출판
    "G502040": "G451035",   # 게임엔터테인먼트     <- 게임소프트웨어와서비스
    "G502050": "G451010",   # 양방향미디어와서비스 <- 인터넷소프트웨어와서비스
}

CODE = re.compile(r"WI\d{3}|WI\d{5}|G\d{6}")


def squash(text: str) -> str:
    """이름 비교용. 출처마다 띄어쓰기가 달라서(`디스플레이 패널` / `디스플레이패널`) 공백을 없앤다."""
    return re.sub(r"\s+", "", text)


# ---------------------------------------------------------------- 1. 맵핑 PDF


def _rows_by_y(runs: Iterable[Any]) -> dict[float, list[Any]]:
    rows: dict[float, list[Any]] = collections.defaultdict(list)
    for run in runs:
        rows[round(run.y, 1)].append(run)
    return {y: sorted(rs, key=lambda r: r.x0) for y, rs in rows.items()}


def _cells(page_rows: dict[float, list[Any]], pattern: str) -> list[tuple[float, str, str]]:
    """(y, 코드, 이름) 목록. 이름은 코드 오른쪽의 런을 다음 코드가 나올 때까지 이어 붙인다."""
    found = []
    for y, runs in page_rows.items():
        fields: list[list[str]] = []
        for run in runs:
            text = run.text.strip()
            if CODE.fullmatch(text):
                fields.append([text, ""])
            elif fields:
                fields[-1][1] += text
        found += [(y, code, name.strip()) for code, name in fields if re.fullmatch(pattern, code)]
    return sorted(found)


def _spans(anchors: list[tuple[float, str, str]], ys: list[float]) -> list[tuple[str, str, int, int]]:
    """세로로 병합된 셀은 자기 구간의 한가운데에 놓인다. 중심에서 구간 [시작, 끝] 을 되짚는다.

    구간이 표를 빈틈없이 덮으므로 첫 구간은 0 에서 시작하고, 중심 c 인 구간의 끝은 2c - 시작이다.
    """
    def fractional_index(y: float) -> float:
        if y <= ys[0]:
            return 0.0
        if y >= ys[-1]:
            return float(len(ys) - 1)
        for i in range(len(ys) - 1):
            if ys[i] <= y <= ys[i + 1]:
                return i + (y - ys[i]) / (ys[i + 1] - ys[i])
        raise AssertionError("도달할 수 없다")

    spans, start = [], 0
    for y, code, name in anchors:
        end = round(2 * fractional_index(y) - start)
        spans.append((code, name, start, end))
        start = end + 1
    return spans


def parse_map(pdf: Path) -> dict[str, tuple[str, str, str, str]]:
    """WICS 소분류 코드 -> (WI26 대분류, WI26 소분류, 소분류명, WICS 소분류명)."""
    mapping: dict[str, tuple[str, str, str, str]] = {}
    document = PdfDocument(pdf)
    for page in document.pages():
        rows = _rows_by_y(page_runs(document, page))
        wics = _cells(rows, r"G\d{6}")
        if not wics:
            continue
        ys = [y for y, _, _ in wics]
        sub_sectors = _spans(_cells(rows, r"WI\d{5}"), ys)
        sectors = _spans(_cells(rows, r"WI\d{3}"), ys)
        for sub_code, sub_name, start, end in sub_sectors:
            sector = next(code for code, _, s, e in sectors if s <= start <= e)
            for _, wics_code, wics_name in wics[start:end + 1]:
                mapping[wics_code] = (sector, sub_code, sub_name, wics_name)
    if not mapping:
        raise RuntimeError("맵핑 PDF 에서 표를 읽지 못했습니다. 문서 구조가 바뀌었을 수 있습니다.")
    return mapping


def fetch_map(save_raw: bool = True) -> dict[str, tuple[str, str, str, str]]:
    print(f"[1/4] WI26-WICS 맵핑 수집: {MAP_PDF_URL}")
    cache = RAW_DIR / "wi26_wics_map.pdf"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(http_get(MAP_PDF_URL, cache if save_raw else None))
    mapping = parse_map(cache)
    if not save_raw:
        cache.unlink()
    print(f"      WI26 소분류 {len({v[1] for v in mapping.values()})}개 ↔ WICS 소분류 {len(mapping)}개")
    return mapping


# ---------------------------------------------------------------- 2. 현행 WICS 소분류


def fetch_wics_sub_sectors(save_raw: bool = True) -> dict[str, tuple[str, str]]:
    """공백을 없앤 WICS 소분류명 -> (코드, 표기명). 네이버 업종명을 코드로 바꿀 때 쓴다."""
    print(f"[2/4] 현행 WICS 소분류 수집: {WICS_URL}")
    raw = http_get(WICS_URL, RAW_DIR / "about_wics.html" if save_raw else None)
    parser = _TableParser()
    parser.feed(raw.decode("utf-8"))
    table = next((t for t in parser.tables if any(re.fullmatch(r"\d{6}", c) for row in t for c in row)), None)
    if table is None:
        raise RuntimeError("WICS 분류표 <table> 을 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.")

    codes: dict[str, tuple[str, str]] = {}
    for row in table:
        sub = [c for c in row if re.fullmatch(r"\d{6}", c)]
        if not sub:
            continue
        name = "".join(row[row.index(sub[-1]) + 1:])          # 소분류명에 쉼표가 있어 셀이 나뉘기도 한다
        codes[squash(name)] = ("G" + sub[-1], name)
    print(f"      소분류 {len(codes)}개")
    return codes


# ---------------------------------------------------------------- 3. 네이버 업종 구성종목


def fetch_json(url: str, cache_file: Path | None) -> Any:
    return json.loads(http_get(url, cache_file).decode("utf-8"))


def fetch_naver_industries(save_raw: bool = True) -> list[tuple[int, str, int]]:
    payload = fetch_json(NAVER_LIST_URL, RAW_DIR / "naver_industries.json" if save_raw else None)
    return [(g["no"], g["name"], g["totalCount"]) for g in payload.get("groups") or []]


def fetch_naver_members(no: int, total: int, save_raw: bool = True) -> list[tuple[str, str]]:
    members: list[tuple[str, str]] = []
    page = 1
    while len(members) < total:
        cache = RAW_DIR / f"naver_industry_{no}_{page}.json" if save_raw else None
        stocks = fetch_json(NAVER_MEMBERS_URL.format(no=no, page=page), cache).get("stocks") or []
        if not stocks:
            break
        members += [(s["itemCode"], s["stockName"]) for s in stocks]
        page += 1
    return members


def fetch_naver(wics_codes: dict[str, tuple[str, str]], save_raw: bool = True) -> dict[str, tuple[str, str, int]]:
    """종목코드 -> (WICS 소분류 코드, 소분류명, 네이버 업종 번호)."""
    print(f"[3/4] 네이버 업종 구성종목 수집: {NAVER_LIST_URL}")
    industries = fetch_naver_industries(save_raw)
    unknown = [name for _, name, _ in industries if squash(name) not in wics_codes]
    if unknown != [NAVER_NOT_A_SECTOR]:
        raise RuntimeError(f"WICS 소분류로 해석되지 않는 네이버 업종: {unknown}")

    placed: dict[str, tuple[str, str, int]] = {}
    twice: list[str] = []
    for no, name, total in industries:
        found = wics_codes.get(squash(name))
        if found is None:
            continue
        wics_code, wics_name = found
        for ticker, _ in fetch_naver_members(no, total, save_raw):
            if ticker in placed:
                twice.append(ticker)
            placed[ticker] = (wics_code, wics_name, no)
    if twice:
        raise RuntimeError(f"한 종목이 두 업종에 들어 있습니다(배타성 위반): {sorted(set(twice))[:20]}")
    print(f"      업종 {len(industries) - 1}개 / {len(placed)}종목 (잡동사니 '{NAVER_NOT_A_SECTOR}' 제외)")
    return placed


# ---------------------------------------------------------------- 4. 조인과 검증


def read_csv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def check_against_classification(mapping: dict[str, tuple[str, str, str, str]]) -> None:
    """PDF 가 읽어 낸 소분류가 이미 가진 분류표와 코드·이름·상위 대분류까지 같은지 본다."""
    known = {r["sub_sector_code"]: (r["sector_code"], r["sub_sector_name"])
             for r in read_csv(DATA_DIR / "wi26_classification.csv")}
    got = {code: (sector, name) for sector, code, name, _ in mapping.values()}
    if got != known:
        wrong = {c: (got.get(c), known.get(c)) for c in set(got) | set(known) if got.get(c) != known.get(c)}
        raise RuntimeError(f"PDF 소분류가 wi26_classification.csv 와 다릅니다 (읽은 값, 분류표): {wrong}")


def build_constituents(mapping: dict[str, tuple[str, str, str, str]],
                       placed: dict[str, tuple[str, str, int]]) -> tuple[str, list[list[Any]]]:
    """WI26 구성종목에 소분류를 붙인다. 붙인 소분류의 상위 대분류는 원래 대분류와 같아야 한다."""
    print("[4/4] 조인 및 검증")
    base_date = datetime.now(KST).strftime("%Y-%m-%d")
    members = read_csv(DATA_DIR / "wi26_constituents.csv")
    sub_sectors = collections.defaultdict(list)          # 대분류 -> 소분류 목록
    for row in read_csv(DATA_DIR / "wi26_classification.csv"):
        sub_sectors[row["sector_code"]].append((row["sub_sector_code"], row["sub_sector_name"]))

    uncovered = collections.Counter()
    for wics_code, _, _ in placed.values():
        if wics_code not in mapping and WICS_BRIDGE.get(wics_code) not in mapping:
            uncovered[wics_code] += 1
    if uncovered:
        raise RuntimeError(f"맵핑 PDF 도 WICS_BRIDGE 도 덮지 못한 WICS 소분류가 있습니다. "
                           f"WICS 개편이 있었는지 확인하고 WICS_BRIDGE 를 갱신하세요: {dict(uncovered)}")

    rows: list[list[Any]] = []
    mismatched: list[tuple[str, str, str, str]] = []
    for member in members:
        ticker, sector = member["ticker"], member["sector_code"]
        found = placed.get(ticker)
        if found is None:
            # 네이버에 업종이 없는 종목. 대분류에 소분류가 하나뿐이면 그것으로 정해진다
            alone = sub_sectors[sector]
            if len(alone) != 1:
                raise RuntimeError(f"{ticker} {member['company_name']}: 네이버 업종이 없고 "
                                   f"{sector} 의 소분류가 {len(alone)}개라 소분류를 정할 수 없습니다")
            sub_code, sub_name = alone[0]
            wics_code, wics_name, no, source = "", "", "", "SECTOR_ONLY"
        else:
            wics_code, wics_name, no = found
            lookup, source = wics_code, "PDF"
            if wics_code not in mapping:                      # PDF 이후 신설된 소분류는 옛 코드를 거친다
                lookup = WICS_BRIDGE[wics_code]
                source = f"BRIDGE:{lookup}"
            derived_sector, sub_code, sub_name, _ = mapping[lookup]
            if derived_sector != sector:
                mismatched.append((ticker, member["company_name"], derived_sector, sector))
        rows.append([base_date, ticker, member["company_name"], sector, member["sector_name"],
                     sub_code, sub_name, wics_code, wics_name, no, source])

    if mismatched:
        raise RuntimeError(f"파생한 소분류의 상위 대분류가 wi26_constituents.csv 와 다릅니다 "
                           f"{len(mismatched)}건 (티커, 종목명, 파생, 원래): {mismatched[:20]}")
    duplicated = [t for t, n in collections.Counter(r[1] for r in rows).items() if n > 1]
    if duplicated:
        raise RuntimeError(f"티커가 중복됩니다: {duplicated[:20]}")

    by_source = collections.Counter(r[-1] for r in rows)
    print(f"      {len(rows)}종목 전부 소분류 확정 · 대분류 대조 불일치 0건")
    print(f"      출처: " + " · ".join(f"{k} {v}종목" for k, v in sorted(by_source.items())))
    return base_date, rows


# ---------------------------------------------------------------- 출력


def write_outputs(mapping: dict[str, tuple[str, str, str, str]], live: dict[str, tuple[str, str]],
                  base_date: str, rows: list[list[Any]]) -> None:
    names = {code: name for code, name in live.values()}
    map_rows = []
    for wics_code, (sector, sub_code, sub_name, wics_name) in sorted(mapping.items()):
        map_rows.append([wics_code, names.get(wics_code, wics_name), sector, sub_code, sub_name, "PDF"])
    for new_code, old_code in sorted(WICS_BRIDGE.items()):
        sector, sub_code, sub_name, _ = mapping[old_code]
        map_rows.append([new_code, names.get(new_code, ""), sector, sub_code, sub_name, f"BRIDGE:{old_code}"])
    path = DATA_DIR / "wi26_wics_map.csv"
    write_csv(path, ["wics_sub_code", "wics_sub_name", "sector_code", "sub_sector_code",
                     "sub_sector_name", "source"], map_rows)
    print(f"      {len(map_rows)}행 -> {path}")

    path = DATA_DIR / "wi26_sub_constituents.csv"
    write_csv(path, ["base_date", "ticker", "company_name", "sector_code", "sector_name",
                     "sub_sector_code", "sub_sector_name", "wics_sub_code", "wics_sub_name",
                     "naver_industry_no", "source"], rows)
    print(f"      {len(rows)}행 -> {path}  (기준일 {base_date})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WI26 소분류 종목 매핑 수집")
    ap.add_argument("--no-raw", action="store_true", help="원본 PDF/HTML/JSON 캐시를 data/raw 에 저장하지 않는다.")
    args = ap.parse_args(argv)
    save_raw = not args.no_raw

    try:
        mapping = fetch_map(save_raw)
        check_against_classification(mapping)
        live = fetch_wics_sub_sectors(save_raw)
        placed = fetch_naver(live, save_raw)
        base_date, rows = build_constituents(mapping, placed)
    except RuntimeError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1

    write_outputs(mapping, live, base_date, rows)
    counts = collections.Counter(r[5] for r in rows)
    print(f"\n완료. 소분류 {len(counts)}개 / {len(rows)}종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
