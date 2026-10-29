from __future__ import annotations

import csv
import io
import zipfile
from html.parser import HTMLParser
from pathlib import Path


class TableCellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c for c in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):  # noqa: ANN001
        if self._cell is not None:
            self._cell.append(data)


def _read_xlsx(path: Path) -> list[list]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True)
    sheet = workbook.active
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    workbook.close()
    return rows


def _read_xls(path: Path) -> list[list]:
    try:
        import xlrd

        book = xlrd.open_workbook(str(path))
        sheet = book.sheet_by_index(0)
        return [sheet.row_values(r) for r in range(sheet.nrows)]
    except Exception:
        return _read_html(path)


def _read_html(path: Path) -> list[list]:
    raw = path.read_bytes()
    for encoding in ("utf-8", "gb18030", "gbk", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")
    parser = TableCellParser()
    parser.feed(text)
    return parser.rows


def _read_csv(path: Path) -> list[list]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")
    return [row for row in csv.reader(io.StringIO(text))]


def read_rows(path: Path) -> list[list]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.namelist() if not m.endswith("/")]
            if not members:
                return []
            target = sorted(members, key=lambda m: (Path(m).suffix.lower() not in {".xls", ".xlsx", ".csv"}, m))[0]
            extract_dir = path.parent / f"{path.stem}_extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)
            archive.extract(target, extract_dir)
            return read_rows(extract_dir / target)
    if suffix == ".xlsx":
        return _read_xlsx(path)
    if suffix == ".xls":
        with open(path, "rb") as handle:
            magic = handle.read(8)
        if magic.startswith(b"\xd0\xcf\x11\xe0"):
            return _read_xls(path)
        return _read_html(path)
    if suffix in (".csv", ".txt"):
        return _read_csv(path)
    if suffix in (".html", ".htm"):
        return _read_html(path)
    raise ValueError(f"不支持的文件类型: {path.suffix}")
