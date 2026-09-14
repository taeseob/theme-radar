#!/usr/bin/env python3
"""GICS (Global Industry Classification Standard) 분류표 생성 스크립트.

references/1_MSCI_Global_Industry_Classification_Standard_GICS_Methodology_20240801.pdf
  - Section 1.2 The GICS Structure        -> 4단계 계층 (Sector / Industry Group / Industry / Sub-Industry)
  - Section 7  GICS Sub-Industry Definitions -> 소분류 정의문
  -> data/gics_classification.csv

사용법::

    python scripts/parse_gics.py
    python scripts/parse_gics.py --pdf path/to/GICS_Methodology.pdf

의존성: Python 3.9+ 표준 라이브러리만 사용한다 (PDF 파서 내장).
지원 범위는 이 문서가 쓰는 기능(FlateDecode, Object Stream, Type0/TrueType + ToUnicode)에 한정한다.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import zlib
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_PDF = ROOT / "references" / "1_MSCI_Global_Industry_Classification_Standard_GICS_Methodology_20240801.pdf"

# 문서 1.1절에 명시된 계층별 개수 (검증용)
EXPECTED_COUNTS = {"sector": 11, "industry_group": 25, "industry": 74, "sub_industry": 163}

# Section 7 정의표의 열 경계 (pt). 좌: 산업그룹/산업, 중: 소분류 코드·명칭, 우: 정의문
DEF_SUB_COL_X = 140.0
DEF_TEXT_COL_X = 270.0
# 페이지 머리말/꼬리말 제외 영역 (pt)
HEADER_Y = 740.0
FOOTER_Y = 60.0

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- pdf: lexer


_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"
_ESCAPES = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}


class Ref:
    def __init__(self, num: int) -> None:
        self.num = num


class Name(str):
    pass


class Op(str):
    pass


class Lexer:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data, self.pos = data, pos

    def _skip(self) -> None:
        d = self.data
        while self.pos < len(d):
            c = d[self.pos]
            if c in _WS:
                self.pos += 1
            elif c == 0x25:  # % comment
                while self.pos < len(d) and d[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def token(self) -> Any:
        self._skip()
        d, p = self.data, self.pos
        if p >= len(d):
            return None
        c = d[p]
        if c == 0x2F:  # /Name
            q = p + 1
            while q < len(d) and d[q] not in _WS and d[q] not in _DELIM:
                q += 1
            self.pos = q
            raw = re.sub(rb"#([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), d[p + 1:q])
            return Name(raw.decode("latin1"))
        if c == 0x28:  # (literal string)
            return self._literal()
        if c == 0x3C:  # <hex> or <<
            if d[p + 1] == 0x3C:
                self.pos = p + 2
                return "<<"
            q = d.index(b">", p)
            self.pos = q + 1
            hexs = re.sub(rb"\s", b"", d[p + 1:q])
            if len(hexs) % 2:
                hexs += b"0"
            return bytes.fromhex(hexs.decode())
        if c == 0x3E and d[p + 1:p + 2] == b">":
            self.pos = p + 2
            return ">>"
        if c in b"[]{}":
            self.pos = p + 1
            return chr(c)
        q = p
        while q < len(d) and d[q] not in _WS and d[q] not in _DELIM:
            q += 1
        q = max(q, p + 1)
        self.pos = q
        word = d[p:q]
        if re.fullmatch(rb"[+-]?\d+", word):
            return int(word)
        if re.fullmatch(rb"[+-]?\d*\.\d*", word):
            return float(word) if re.search(rb"\d", word) else 0.0
        return Op(word.decode("latin1"))

    def _literal(self) -> bytes:
        d, q, depth, out = self.data, self.pos + 1, 1, bytearray()
        while True:
            ch = d[q]
            if ch == 0x5C:  # backslash
                q += 1
                e = d[q]
                if e in _ESCAPES:
                    out.append(_ESCAPES[e])
                elif 0x30 <= e <= 0x37:
                    k = 1
                    while k < 3 and 0x30 <= d[q + k] <= 0x37:
                        k += 1
                    out.append(int(d[q:q + k], 8) & 0xFF)
                    q += k - 1
                elif e in b"\r\n":
                    if e == 13 and d[q + 1] == 10:
                        q += 1
                else:
                    out.append(e)
            elif ch == 0x28:
                depth += 1
                out.append(ch)
            elif ch == 0x29:
                depth -= 1
                if depth == 0:
                    self.pos = q + 1
                    return bytes(out)
                out.append(ch)
            else:
                out.append(ch)
            q += 1

    def value(self, tok: Any = None) -> Any:
        t = self.token() if tok is None else tok
        if t == "<<":
            dct = {}
            while (k := self.token()) != ">>":
                dct[k] = self.value()
            return dct
        if t == "[":
            arr = []
            while (k := self.token()) != "]":
                arr.append(self.value(k))
            return arr
        if isinstance(t, int):
            save = self.pos
            gen, r = self.token(), self.token()
            if isinstance(gen, int) and r == "R":
                return Ref(t)
            self.pos = save
        return t


# ---------------------------------------------------------------- pdf: document


class PdfDocument:
    def __init__(self, path: Path) -> None:
        self.data = path.read_bytes()
        self._offsets: dict[int, int | None] = {
            int(m.group(1)): m.end()
            for m in re.finditer(rb"(?<!\d)(\d+)\s+\d+\s+obj\b", self.data)
        }
        self._cache: dict[int, Any] = {}
        self._load_object_streams()

    def _load_object_streams(self) -> None:
        for num in list(self._offsets):
            obj = self.get(num)
            if not (isinstance(obj, tuple) and obj[0].get("Type") == "ObjStm"):
                continue
            dct, _ = obj
            body = self.stream_data(obj)
            lx = Lexer(body)
            header = [lx.token() for _ in range(2 * dct["N"])]
            for i in range(dct["N"]):
                child, offset = header[2 * i], header[2 * i + 1]
                if child not in self._offsets:
                    self._cache[child] = Lexer(body, dct["First"] + offset).value()
                    self._offsets[child] = None

    def get(self, num: int | Ref) -> Any:
        if isinstance(num, Ref):
            num = num.num
        if num in self._cache:
            return self._cache[num]
        pos = self._offsets.get(num)
        if pos is None:
            return None
        lx = Lexer(self.data, pos)
        obj = lx.value()
        if lx.token() == "stream":
            p = lx.pos
            p += 2 if self.data[p:p + 2] == b"\r\n" else 1
            length = self.resolve(obj.get("Length"))
            if not isinstance(length, int):
                length = self.data.index(b"endstream", p) - p
            obj = (obj, self.data[p:p + length])
        self._cache[num] = obj
        return obj

    def resolve(self, value: Any) -> Any:
        while isinstance(value, Ref):
            value = self.get(value)
        return value

    def stream_data(self, value: Any) -> bytes:
        dct, raw = self.resolve(value)
        filters = self.resolve(dct.get("Filter"))
        for f in filters if isinstance(filters, list) else [filters] if filters else []:
            if f != "FlateDecode":
                return b""
            raw = zlib.decompressobj().decompress(raw)
        return raw

    def pages(self) -> list[dict]:
        root = None
        for m in re.finditer(rb"/Root\s+(\d+)\s+\d+\s+R", self.data):
            root = int(m.group(1))
        if root is None:
            raise RuntimeError("PDF 카탈로그(/Root)를 찾지 못했습니다.")

        out: list[dict] = []

        def walk(node: Any, inherited: dict) -> None:
            node = self.resolve(node)
            if "Resources" in node:
                inherited = {**inherited, "Resources": node["Resources"]}
            if node.get("Type") == "Pages":
                for kid in self.resolve(node["Kids"]):
                    walk(kid, inherited)
            else:
                out.append({**inherited, **node})

        walk(self.resolve(self.get(root))["Pages"], {})
        return out


# ---------------------------------------------------------------- pdf: text


class Font:
    def __init__(self, doc: PdfDocument, ref: Any) -> None:
        font = doc.resolve(ref)
        self.two_byte = font.get("Subtype") == "Type0"
        self.to_unicode: dict[int, str] = {}
        self.widths: dict[int, float] = {}
        self.default_width = 1000.0

        if self.two_byte:
            desc = doc.resolve(doc.resolve(font["DescendantFonts"])[0])
            self.default_width = desc.get("DW", 1000)
            w = doc.resolve(desc.get("W")) or []
            i = 0
            while i < len(w):
                first = w[i]
                if isinstance(w[i + 1], list):
                    for k, width in enumerate(w[i + 1]):
                        self.widths[first + k] = width
                    i += 2
                else:
                    for cid in range(first, w[i + 1] + 1):
                        self.widths[cid] = w[i + 2]
                    i += 3
        else:
            first = font.get("FirstChar", 0)
            for k, width in enumerate(doc.resolve(font.get("Widths")) or []):
                self.widths[first + k] = width

        if "ToUnicode" in font:
            self._parse_cmap(doc.stream_data(font["ToUnicode"]).decode("latin1"))

    @staticmethod
    def _utf16(hexs: str) -> str:
        try:
            return bytes.fromhex(hexs).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return ""

    def _parse_cmap(self, cmap: str) -> None:
        for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
            for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
                self.to_unicode[int(src, 16)] = self._utf16(dst)
        for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
            pattern = r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)"
            for lo, hi, dst in re.findall(pattern, block):
                lo_i, hi_i = int(lo, 16), int(hi, 16)
                if dst.startswith("["):
                    for k, h in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", dst)):
                        self.to_unicode[lo_i + k] = self._utf16(h)
                else:
                    base = int(dst[1:-1], 16)
                    for k in range(hi_i - lo_i + 1):
                        self.to_unicode[lo_i + k] = chr(base + k)

    def codes(self, s: bytes) -> list[int]:
        if self.two_byte:
            return [(s[i] << 8) | s[i + 1] for i in range(0, len(s) - 1, 2)]
        return list(s)

    def decode(self, s: bytes) -> tuple[str, float]:
        """(텍스트, 글리프 폭 합계[1/1000 em]) 를 반환한다."""
        text, width = [], 0.0
        for code in self.codes(s):
            if code in self.to_unicode:
                text.append(self.to_unicode[code])
            elif not self.two_byte:
                text.append(bytes([code]).decode("cp1252", "replace"))
            width += self.widths.get(code, self.default_width)
        return "".join(text), width


class Run:
    """한 번의 텍스트 출력 조각. x0~x1 은 수평 범위(pt)."""

    __slots__ = ("x0", "x1", "y", "text")

    def __init__(self, x0: float, x1: float, y: float, text: str) -> None:
        self.x0, self.x1, self.y, self.text = x0, x1, y, text


def page_runs(doc: PdfDocument, page: dict) -> list[Run]:
    resources = doc.resolve(page.get("Resources")) or {}
    fonts = {k: Font(doc, v) for k, v in (doc.resolve(resources.get("Font")) or {}).items()}
    contents = doc.resolve(page.get("Contents"))
    refs = contents if isinstance(contents, list) else [page.get("Contents")]
    data = b"\n".join(doc.stream_data(r) for r in refs)

    runs: list[Run] = []
    lx = Lexer(data)
    operands: list[Any] = []
    font: Font | None = None
    size = 1.0
    tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    lm = list(tm)
    leading = 0.0

    def move_line(tx: float, ty: float) -> None:
        nonlocal tm, lm
        lm = [lm[0], lm[1], lm[2], lm[3], lm[4] + tx * lm[0] + ty * lm[2], lm[5] + tx * lm[1] + ty * lm[3]]
        tm = list(lm)

    def show(items: list[Any]) -> None:
        if font is None:
            return
        x0, parts, advance = tm[4], [], 0.0
        for item in items:
            if isinstance(item, bytes):
                text, width = font.decode(item)
                parts.append(text)
                advance += width
            elif isinstance(item, (int, float)):
                if item < -250:  # 큰 음수 조정 = 단어 간격
                    parts.append(" ")
                advance -= item
        dx = advance / 1000.0 * size * tm[0]
        runs.append(Run(x0, x0 + dx, tm[5], "".join(parts)))
        tm[4] += dx

    while (tok := lx.token()) is not None:
        if not isinstance(tok, Op):
            if tok == "[":
                arr = []
                while (k := lx.token()) not in ("]", None):
                    arr.append(k)
                operands.append(arr)
            elif tok == "<<":
                lx.pos -= 2
                lx.value()
            else:
                operands.append(tok)
            continue

        if tok == "BI":  # 인라인 이미지 건너뛰기
            end = data.find(b"EI", lx.pos)
            lx.pos = end + 2 if end >= 0 else len(data)
        elif tok == "BT":
            tm, lm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        elif tok == "Tf" and len(operands) >= 2:
            font, size = fonts.get(operands[-2]), float(operands[-1])
        elif tok == "Tm" and len(operands) >= 6:
            tm = [float(v) for v in operands[-6:]]
            lm = list(tm)
        elif tok in ("Td", "TD") and len(operands) >= 2:
            if tok == "TD":
                leading = -operands[-1]
            move_line(operands[-2], operands[-1])
        elif tok == "TL" and operands:
            leading = operands[-1]
        elif tok == "T*":
            move_line(0, -leading)
        elif tok == "Tj" and operands:
            show([operands[-1]])
        elif tok in ("'", '"') and operands:
            move_line(0, -leading)
            show([operands[-1]])
        elif tok == "TJ" and operands:
            show(operands[-1])
        operands = []
    return runs


def group_lines(runs: Iterable[Run], y_tol: float = 2.5) -> list[tuple[float, list[Run]]]:
    """같은 기준선의 조각을 묶어 위→아래, 좌→우 순으로 반환한다."""
    lines: list[tuple[float, list[Run]]] = []
    for run in sorted(runs, key=lambda r: (-r.y, r.x0)):
        if lines and abs(lines[-1][0] - run.y) <= y_tol:
            lines[-1][1].append(run)
        else:
            lines.append((run.y, [run]))
    return [(y, sorted(rs, key=lambda r: r.x0)) for y, rs in lines]


def join_runs(runs: list[Run], gap: float = 1.2) -> str:
    """조각 사이 간격이 gap(pt) 이상이면 공백을 넣어 한 줄 텍스트로 만든다."""
    out, prev_x1 = "", None
    for run in runs:
        if prev_x1 is not None and run.x0 - prev_x1 >= gap and not out.endswith(" ") and not run.text.startswith(" "):
            out += " "
        out += run.text
        prev_x1 = run.x1
    return " ".join(out.split())


def join_wrapped(lines: list[str]) -> str:
    """줄바꿈된 문장을 이어붙인다. 'Sub-' 처럼 하이픈으로 끝난 줄은 공백 없이 붙인다."""
    text = ""
    for line in lines:
        if not line:
            continue
        if not text:
            text = line
        elif text.endswith("-") and not text.endswith(" -"):
            text += line
        else:
            text += " " + line
    return text


# ---------------------------------------------------------------- gics: parsing


def body_lines(runs: list[Run]) -> list[tuple[float, list[Run]]]:
    return group_lines(r for r in runs if FOOTER_Y < r.y < HEADER_Y and r.text.strip())


def find_page(texts: list[str], pattern: str, start: int = 0) -> int:
    for i in range(start, len(texts)):
        if re.search(pattern, texts[i], re.M):
            return i
    raise RuntimeError(f"PDF 에서 '{pattern}' 구간을 찾지 못했습니다.")


def parse_structure(pages_runs: list[list[Run]]) -> list[dict[str, str]]:
    """Section 1.2 의 코드/명칭 목록을 4단계 계층 행(소분류 단위)으로 평탄화한다."""
    levels = ["sector", "industry_group", "industry", "sub_industry"]
    level_by_len = {2 * (i + 1): lv for i, lv in enumerate(levels)}
    current: dict[str, tuple[str, str]] = {}
    rows: list[dict[str, str]] = []

    for runs in pages_runs:
        for _, line_runs in body_lines(runs):
            m = re.fullmatch(r"([\d ]+?)\s*([A-Z].*)", join_runs(line_runs, gap=99))
            if not m:
                continue
            code = m.group(1).replace(" ", "")
            if len(code) not in level_by_len:
                continue
            # 코드 조각 사이 간격은 무시하고, 명칭 부분만 글리프 간격으로 띄어쓰기를 복원한다.
            name_runs = [r for r in line_runs if not re.fullmatch(r"[\d ]+", r.text)]
            name = re.sub(r"^[\d\s]+", "", join_runs(name_runs))
            level = level_by_len[len(code)]
            current[level] = (code, name)
            for lower in levels[levels.index(level) + 1:]:
                current.pop(lower, None)
            if level == "sub_industry":
                row: dict[str, str] = {}
                for lv in levels:
                    parent = current.get(lv)
                    if parent is None or not code.startswith(parent[0]):
                        raise RuntimeError(f"계층 불일치: {code} {name} ({lv} 상위 코드 없음)")
                    row[f"{lv}_code"], row[f"{lv}_name"] = parent
                rows.append(row)
    return rows


def parse_definitions(pages_runs: list[list[Run]]) -> dict[str, tuple[str, str]]:
    """Section 7 표에서 {소분류코드: (명칭, 정의문)} 을 만든다.

    표는 페이지를 넘어 이어지므로 모든 페이지의 줄을 (페이지, 위→아래) 순서로 훑으며
    소분류 열의 8자리 코드가 새 레코드의 시작점이 된다.
    """
    records: dict[str, dict[str, list[str]]] = {}
    order: list[str] = []
    events: list[tuple[int, float, str, str]] = []  # (page, -y, column, text)

    for page_no, runs in enumerate(pages_runs):
        # 표 아래 각주('* A company will be classified as a REIT ...')는 전체 폭으로 흐르므로 잘라낸다.
        footnote_y = max(
            (y for y, rs in body_lines(runs) if rs[0].x0 < DEF_SUB_COL_X and rs[0].text.lstrip().startswith("*")),
            default=None,
        )
        if footnote_y is not None:
            runs = [r for r in runs if r.y > footnote_y + 2.5]
        sub_col = [r for r in runs if DEF_SUB_COL_X <= r.x0 < DEF_TEXT_COL_X]
        def_col = [r for r in runs if r.x0 >= DEF_TEXT_COL_X]
        for column, col_runs in (("sub", sub_col), ("def", def_col)):
            for y, line_runs in body_lines(col_runs):
                text = join_runs(line_runs)
                if text:
                    # 코드와 정의문 첫 줄은 같은 기준선이므로 코드를 약간 앞에 정렬한다.
                    events.append((page_no, -y - (0.5 if column == "sub" else 0.0), column, text))

    current: str | None = None
    for _, _, column, text in sorted(events, key=lambda e: (e[0], e[1])):
        if column == "sub" and re.fullmatch(r"\d{8}", text):
            current = text
            records[current] = {"name": [], "def": []}
            order.append(current)
        elif current is not None:
            records[current]["name" if column == "sub" else "def"].append(text)

    # 명칭 끝의 '*' 는 REIT 분류 기준 각주 표시다.
    return {
        code: (join_wrapped(records[code]["name"]).rstrip("* "), join_wrapped(records[code]["def"]))
        for code in order
    }


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", name).lower()


# ---------------------------------------------------------------- csv


def write_csv(path: Path, header: Iterable[str], rows: Iterable[Iterable[Any]]) -> None:
    """UTF-8 with BOM, LF 개행, 텍스트 필드만 따옴표로 감싼 CSV 를 쓴다 (fetch_wi26.py 와 동일 형식)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow(header)
        csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\n").writerows(rows)


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GICS Methodology PDF 에서 분류표 CSV 생성")
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="GICS Methodology PDF 경로")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "gics_classification.csv", help="출력 CSV 경로")
    args = ap.parse_args(argv)

    try:
        print(f"[1/3] PDF 읽기: {args.pdf}")
        doc = PdfDocument(args.pdf)
        all_runs = [page_runs(doc, p) for p in doc.pages()]
        texts = ["\n".join(join_runs(rs) for _, rs in body_lines(runs)) for runs in all_runs]
        print(f"      {len(all_runs)} 페이지")

        struct_start = find_page(texts, r"^1\.2 ?The GICS Structure$")
        struct_end = find_page(texts, r"^1\.3 ?Philosophy", struct_start)
        def_start = find_page(texts, r"^Section 7: GICS Sub-Industry Definitions$", struct_end)
        def_end = find_page(texts, r"^Section 8:", def_start)

        print(f"[2/3] 계층 구조 파싱 (p.{struct_start + 1}-{struct_end}) / 정의문 파싱 (p.{def_start + 1}-{def_end})")
        rows = parse_structure(all_runs[struct_start:struct_end])
        definitions = parse_definitions(all_runs[def_start:def_end])

        # ---- 검증
        errors: list[str] = []
        counts = {
            lv: len({r[f"{lv}_code"] for r in rows}) for lv in EXPECTED_COUNTS
        }
        for lv, expected in EXPECTED_COUNTS.items():
            if counts[lv] != expected:
                errors.append(f"{lv} 개수 {counts[lv]} != 문서 기재 {expected}")
        if len(rows) != len({r["sub_industry_code"] for r in rows}):
            errors.append("소분류 코드 중복")

        struct_codes = [r["sub_industry_code"] for r in rows]
        missing = sorted(set(struct_codes) - set(definitions))
        extra = sorted(set(definitions) - set(struct_codes))
        if missing:
            errors.append(f"정의문 없는 소분류: {missing}")
        if extra:
            errors.append(f"구조표에 없는 정의문 코드: {extra}")
        for r in rows:
            d = definitions.get(r["sub_industry_code"])
            if d and normalize_name(d[0]) != normalize_name(r["sub_industry_name"]):
                errors.append(f"명칭 불일치 {r['sub_industry_code']}: 구조표 '{r['sub_industry_name']}' / 정의표 '{d[0]}'")
            if d and not d[1]:
                errors.append(f"빈 정의문: {r['sub_industry_code']}")
        if errors:
            raise RuntimeError("검증 실패\n  - " + "\n  - ".join(errors))

        print(
            "      섹터 {sector} / 산업그룹 {industry_group} / 산업 {industry} / 소분류 {sub_industry} (문서 기재값과 일치)".format(**counts)
        )

        header = [
            "sector_code", "sector_name",
            "industry_group_code", "industry_group_name",
            "industry_code", "industry_name",
            "sub_industry_code", "sub_industry_name",
            "sub_industry_definition",
        ]
        out_rows = [
            [r[h] for h in header[:-1]] + [definitions[r["sub_industry_code"]][1]]
            for r in rows
        ]
        print(f"[3/3] CSV 저장")
        write_csv(args.out, header, out_rows)
        print(f"      {len(out_rows)}행 -> {args.out}")
    except (OSError, RuntimeError) as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
