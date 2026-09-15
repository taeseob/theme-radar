"""HTML 문서의 <table>을 셀 텍스트 행 목록으로 읽는다 (KIND, 위키백과)."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass
class Table:
    id: str | None
    rows: list[list[str]]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self._stack: list[Table] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append(Table(dict(attrs).get("id"), []))
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1].rows.append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_tables(html: str) -> list[Table]:
    parser = _TableParser()
    parser.feed(html)
    return parser.tables


def find_table(html: str, table_id: str) -> Table:
    for table in parse_tables(html):
        if table.id == table_id:
            return table
    raise ValueError(f'id="{table_id}" 표를 찾지 못했다. 페이지 구조가 바뀌었을 수 있다')
