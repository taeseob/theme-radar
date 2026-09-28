#!/usr/bin/env python3
"""KR 테마 구성종목 수집 스크립트.

어느 테마를 볼지는 data/theme_kr_list.csv 에 사람이 정한다(테마 코드, 화면 이름, 네이버 테마 번호).
이 스크립트는 그 목록의 네이버 테마 구성종목을 받아 data/theme_kr_constituents.csv 로 쓴다.
한 종목이 여러 테마에 들어갈 수 있다(비배타 스킴, docs/01 §4).

테마 구성은 받은 날의 스냅샷이다. 그 전 기간에 적용하면 오늘 구성을 거꾸로 적용한 소급 계산이 되므로,
base_date 를 남겨 화면과 문서가 그 날짜를 적게 한다(docs/02 §9 S-20).

사용법::

    python scripts/fetch_themes_kr.py            # 수집 후 CSV 를 쓴다
    python scripts/fetch_themes_kr.py --no-raw   # 원본 JSON 캐시 저장 생략

의존성: Python 3.9+ 표준 라이브러리만 사용한다. 같은 디렉터리의 fetch_wi26.py(HTTP·CSV)를 재사용한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_wi26 import DATA_DIR, KST, RAW_DIR, http_get, write_csv  # noqa: E402
from fetch_wi26_sub import read_csv  # noqa: E402

LIST_URL = "https://m.stock.naver.com/api/stocks/theme?page={page}&pageSize=100"
MEMBERS_URL = "https://m.stock.naver.com/api/stocks/theme/{no}?page={page}&pageSize=100"
PAGE_SIZE = 100


def fetch_json(url: str, cache_file: Path | None) -> Any:
    return json.loads(http_get(url, cache_file).decode("utf-8"))


def fetch_theme_names(save_raw: bool) -> dict[int, tuple[str, int]]:
    """네이버 테마 번호 -> (테마 이름, 종목 수). 목록에 적은 번호가 아직 있는지 확인하는 데 쓴다."""
    names: dict[int, tuple[str, int]] = {}
    page = 1
    while True:
        cache = RAW_DIR / f"naver_themes_{page}.json" if save_raw else None
        payload = fetch_json(LIST_URL.format(page=page), cache)
        groups = payload.get("groups") or []
        names.update({g["no"]: (g["name"], g["totalCount"]) for g in groups})
        # 목록에 같은 테마가 두 번 오는 경우가 있어(2026-09 기준 3건) 번호 수가 아니라 쪽수로 끝을 판단한다
        if not groups or page * PAGE_SIZE >= payload.get("totalCount", 0):
            return names
        page += 1


def fetch_members(no: int, total: int, save_raw: bool) -> list[tuple[str, str]]:
    """테마 구성종목 (티커, 종목명). 주식만 남긴다(ETF 등이 섞이면 뺀다)."""
    members: list[tuple[str, str]] = []
    page = 1
    while (page - 1) * PAGE_SIZE < total:
        cache = RAW_DIR / f"naver_theme_{no}_{page}.json" if save_raw else None
        stocks = fetch_json(MEMBERS_URL.format(no=no, page=page), cache).get("stocks") or []
        if not stocks:
            break
        members += [(s["itemCode"], s["stockName"]) for s in stocks if s.get("stockEndType") == "stock"]
        page += 1
    return members


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KR 테마 구성종목 수집")
    ap.add_argument("--no-raw", action="store_true", help="원본 JSON 캐시를 data/raw 에 저장하지 않는다.")
    args = ap.parse_args(argv)
    save_raw = not args.no_raw

    themes = read_csv(DATA_DIR / "theme_kr_list.csv")
    base_date = datetime.now(KST).date().isoformat()
    try:
        live = fetch_theme_names(save_raw)
        rows: list[list[Any]] = []
        for theme in themes:
            no = int(theme["naver_theme_no"])
            if no not in live:
                raise RuntimeError(f"네이버 테마 {no}({theme['theme_name']})가 목록에 없다. theme_kr_list.csv 를 고친다")
            naver_name, total = live[no]
            members = fetch_members(no, total, save_raw)
            rows += [[base_date, theme["theme_code"], theme["theme_name"], ticker, name, no, naver_name]
                     for ticker, name in sorted(set(members))]
            print(f"  {theme['theme_code']} {theme['theme_name']:<10} {len(members):>4}종목  (네이버 {no}: {naver_name})")
    except RuntimeError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1

    path = DATA_DIR / "theme_kr_constituents.csv"
    write_csv(path, ["base_date", "theme_code", "theme_name", "ticker", "company_name", "naver_theme_no",
                     "naver_theme_name"], rows)
    print(f"\n완료. 테마 {len(themes)}개 / {len(rows)}행 ({len({r[3] for r in rows})}종목) -> {path}  (기준일 {base_date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
