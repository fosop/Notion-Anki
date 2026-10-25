"""Shared helpers for the test-suite."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from converter import Card, ConversionOptions, ConversionResult, convert, load_uploads

FIXTURES = Path(__file__).resolve().parent / "fixtures"

NOTION_STYLE = (
    "<style>body { line-height: 1.5; white-space: pre-wrap; } "
    ".toggle { padding-inline-start: 0em; list-style-type: none; }</style>"
)


def notion_page(body: str, title: str = "Test Page", style: str = NOTION_STYLE) -> str:
    """A minimal page with the same skeleton as a Notion HTML export."""
    return (
        '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
        f"<title>{title}</title>{style}</head><body><article class=\"page sans\">"
        f'<header><h1 class="page-title">{title}</h1></header>'
        f'<div class="page-body">{body}</div></article></body></html>'
    )


def toggle(title: str, inner: str = "") -> str:
    return f'<ul class="toggle"><li><details open=""><summary>{title}</summary>{inner}</details></li></ul>'


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def convert_text(html: str, name: str = "page.html", **options) -> ConversionResult:
    return convert(load_uploads([(name, html.encode("utf-8"))]), options=ConversionOptions(**options))


def convert_fixture(name: str, **options) -> ConversionResult:
    return convert(load_uploads([(name, (FIXTURES / name).read_bytes())]), options=ConversionOptions(**options))


def convert_files(files: dict[str, bytes], zip_name: str = "export.zip", **options) -> ConversionResult:
    return convert(load_uploads([(zip_name, make_zip(files))]), options=ConversionOptions(**options))


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def fronts(result: ConversionResult) -> list[str]:
    return [card.front_text for card in result.cards]


def card(result: ConversionResult, front_text: str) -> Card:
    matches = [c for c in result.cards if c.front_text == front_text]
    assert len(matches) == 1, f"expected one card titled {front_text!r}, found {len(matches)}"
    return matches[0]


def codes(result: ConversionResult) -> set[str]:
    return {issue.code for issue in result.issues}
