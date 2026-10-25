"""TSV/CSV export that Anki imports directly.

File layout (UTF-8, no byte-order mark)::

    #separator:Tab
    #html:true
    #columns:Front<TAB>Back
    "front html"<TAB>"back html"
    …

* The ``#`` lines are Anki file headers (Anki 2.1.55+). Older versions treat
  lines starting with ``#`` as comments, so they are never imported as cards.
* Every field is quoted (``csv.QUOTE_ALL``). Tabs, commas, quotes and newlines
  inside fields therefore cannot create extra columns or rows, and a front that
  starts with ``#`` cannot be mistaken for a comment.
* After writing, the file is parsed back the way Anki reads it, and the export
  fails loudly if any record doesn't have exactly two fields.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import Card, OutputFormat

COLUMNS = ("Front", "Back")

_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)


class ExportError(Exception):
    """The export could not be produced or failed validation."""


@dataclass(frozen=True)
class ExportOutput:
    filename: str
    data: bytes
    rows: int
    format: OutputFormat

    @property
    def text(self) -> str:
        return self.data.decode("utf-8")


def anki_headers(fmt: OutputFormat) -> list[str]:
    return [
        f"#separator:{fmt.anki_separator}",
        "#html:true",
        "#columns:" + fmt.delimiter.join(COLUMNS),
    ]


def export_cards(
    cards: Iterable[Card | tuple[str, str]],
    fmt: OutputFormat = OutputFormat.TSV,
    filename: str | None = None,
    include_headers: bool = True,
) -> ExportOutput:
    """Write cards as TSV or CSV with exactly two columns: Front and Back."""
    if not fmt.is_text:
        raise ValueError(f"export_cards() writes TSV/CSV; use converter.apkg.export_apkg() for {fmt.extension} files.")
    rows = [_row(card) for card in cards]
    if not rows:
        raise ExportError("There are no cards to export.")
    buffer = io.StringIO()
    if include_headers:
        for header in anki_headers(fmt):
            buffer.write(header + "\n")
    writer = csv.writer(
        buffer,
        delimiter=fmt.delimiter,
        quotechar='"',
        doublequote=True,
        quoting=csv.QUOTE_ALL,
        lineterminator="\n",
    )
    writer.writerows(rows)
    text = buffer.getvalue()
    validate_export(text, fmt, expected_rows=len(rows))
    return ExportOutput(safe_filename(filename, fmt), text.encode("utf-8"), len(rows), fmt)


def _row(card: Card | tuple[str, str]) -> tuple[str, str]:
    front, back = (card.front, card.back) if isinstance(card, Card) else card
    return _field(front), _field(back)


_FORMULA_START = {"=": "&#61;", "+": "&#43;", "-": "&#45;", "@": "&#64;"}


def _field(value: object) -> str:
    text = str(value).replace("\x00", "")
    # Spreadsheet programs run cells starting with = + - @ as formulas ("CSV
    # injection"). The HTML entity looks identical in Anki's HTML fields.
    if text[:1] in _FORMULA_START:
        text = _FORMULA_START[text[0]] + text[1:]
    # Older Anki versions drop *any* physical line that starts with '#', even
    # inside a quoted multi-line field. '&#35;' renders as '#' in HTML fields.
    return text.replace("\n#", "\n&#35;")


def parse_export(text: str, fmt: OutputFormat) -> tuple[list[str], list[list[str]]]:
    """Read an export the way Anki does: ``#`` header lines at the top, then CSV records."""
    headers: list[str] = []
    position = 0
    while text.startswith("#", position):
        end = text.find("\n", position)
        end = len(text) if end == -1 else end
        headers.append(text[position:end])
        position = end + 1
    reader = csv.reader(io.StringIO(text[position:]), delimiter=fmt.delimiter, quotechar='"', doublequote=True, strict=True)
    return headers, [row for row in reader]


def validate_export(text: str, fmt: OutputFormat, expected_rows: int) -> None:
    try:
        headers, rows = parse_export(text, fmt)
    except csv.Error as exc:
        raise ExportError(f"The generated file could not be read back: {exc}") from exc
    if len(rows) != expected_rows:
        raise ExportError(f"Expected {expected_rows} records but the file contains {len(rows)}.")
    for number, row in enumerate(rows, start=1):
        if len(row) != len(COLUMNS):
            raise ExportError(f"Record {number} has {len(row)} fields instead of {len(COLUMNS)}.")
        if not row[0].strip():
            raise ExportError(f"Record {number} has an empty Front field; Anki would reject it.")
    for header in headers:
        if not re.match(r"^#[a-z ]+:", header):
            raise ExportError(f"Unexpected header line: {header[:40]!r}")


def safe_filename(name: str | None, fmt: OutputFormat, default: str = "anki_cards") -> str:
    """A safe file name on Windows/macOS/Linux with the right extension."""
    raw = re.split(r"[\\/]", (name or "").strip())[-1]
    stem = raw
    lowered = stem.lower()
    for extension in (".apkg", ".tsv", ".csv", ".txt"):
        if lowered.endswith(extension):
            stem = stem[: -len(extension)]
            break
    stem = _ILLEGAL_FILENAME_CHARS.sub("_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if not stem:
        stem = default
    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    stem = stem[:120].rstrip(" .") or default
    return stem + fmt.extension


def suggest_filename(page_titles: Sequence[str], fmt: OutputFormat) -> str:
    """Default export name: the page title for one page, otherwise a generic name."""
    titles = [t for t in page_titles if t.strip()]
    if len(titles) == 1:
        return safe_filename(titles[0], fmt)
    return safe_filename("notion_anki_cards", fmt)
