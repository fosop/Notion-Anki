"""Malformed input, missing toggles, unexpected encodings and other edge cases."""

from __future__ import annotations

import codecs

import pytest

from converter import ExportError, OutputFormat, convert, export_cards, load_uploads, scan_pages
from converter.loader import decode_html
from helpers import card, codes, convert_files, convert_fixture, convert_text, fronts, notion_page, read_fixture, toggle


# 10. Malformed or unexpected HTML ---------------------------------------------------

def test_malformed_html_is_converted_without_crashing():
    result = convert_fixture("malformed.html")
    found = fronts(result)
    for expected in ("Unclosed paragraph toggle", "Stray closing tags", "Summary not first", "Mis-nested italic bold text"):
        assert expected in found
    assert "Still the answer" in card(result, "Stray closing tags").back
    not_first = card(result, "Summary not first")
    assert "Content before summary" in not_first.back and "Body text" in not_first.back
    assert "Summary not first" not in not_first.back
    everything = " ".join(c.front + c.back for c in result.cards)
    assert "comment that must not appear" not in everything
    assert "cdata" not in everything
    assert all(c.front for c in result.cards)
    export_cards(result.cards, OutputFormat.TSV)  # still a valid file


@pytest.mark.parametrize(
    "markup",
    [
        "",
        "   ",
        "<details>",
        "<summary>only a summary</summary>",
        "<<<>>><details><summary>x",
        "\x00\x01\x02 binary-ish <details><summary>Q</summary>A</details>",
        "<html><body>" + "<div>" * 3000 + "text",
    ],
)
def test_garbage_input_never_crashes(markup):
    result = convert_text(markup)
    assert result.pages_processed == 1
    for item in result.cards:
        assert item.front


def test_binary_file_with_html_extension():
    inputs = load_uploads([("photo.html", bytes(range(256)) * 50)])
    result = convert(inputs)
    assert result.cards == []
    assert "no-toggles" in codes(result)


# 16. A document without recognisable toggles ----------------------------------------

def test_page_without_toggles_gives_a_useful_diagnostic():
    result = convert_fixture("no_toggles.html")
    assert result.cards == []
    issue = next(i for i in result.issues if i.code == "no-toggles")
    assert "No toggles were detected" in issue.message
    assert "no_toggles.html" in issue.message  # names the inspected file
    assert "may be unsupported" in issue.message
    with pytest.raises(ExportError):
        export_cards(result.cards)


def test_scan_reports_counts_before_conversion():
    inputs = load_uploads([
        ("no_toggles.html", read_fixture("no_toggles.html").encode()),
        ("nested.html", read_fixture("notion_nested.html").encode()),
        ("empty.html", read_fixture("notion_empty_duplicates.html").encode()),
    ])
    summaries = {s.path: s for s in scan_pages(inputs)}
    assert summaries["no_toggles.html"].toggles == 0
    nested = summaries["nested.html"]
    assert (nested.toggles, nested.cards, nested.untitled, nested.max_depth) == (4, 4, 0, 3)
    assert nested.kind == "notion-export"
    assert nested.patterns == {"notion-toggle-list": 4}
    assert summaries["empty.html"].untitled == 4


def test_page_saved_from_the_notion_app_is_diagnosed():
    html = (
        '<html><body><div class="notion-page-content"><div class="notion-selectable notion-toggle-block">'
        '<div role="button" aria-expanded="false"></div><div contenteditable="true">Question</div></div></div></body></html>'
    )
    result = convert_text(html, name="Saved page.html")
    assert result.cards == []
    assert "unsupported-page" in codes(result)
    assert "Export" in next(i for i in result.issues if i.code == "no-toggles").message


def test_markdown_export_is_diagnosed():
    result = convert_files({"Notes abc.md": b"# Notes\n- item", "Notes abc/image.png": b"\x89PNG"})
    issue = next(i for i in result.issues if i.code == "no-cards")
    assert "Markdown" in issue.message and "HTML" in issue.message


# Encodings ------------------------------------------------------------------------

def _page_bytes(text: str, encoding: str, declared: str | None = None) -> bytes:
    meta = f'<meta charset="{declared}">' if declared else ""
    return f"<html><head>{meta}</head><body>{toggle(text, '<p>' + text + '</p>')}</body></html>".encode(encoding)


def test_utf8_with_byte_order_mark():
    data = codecs.BOM_UTF8 + _page_bytes("Δ ≥ 37 °C", "utf-8")
    result = convert(load_uploads([("bom.html", data)]))
    assert result.cards[0].front == "Δ ≥ 37 °C"


def test_utf16_with_byte_order_mark():
    result = convert(load_uploads([("utf16.html", _page_bytes("µg → mL", "utf-16"))]))
    assert result.cards[0].front == "µg → mL"


def test_windows_1252_without_declaration():
    result = convert(load_uploads([("cp1252.html", _page_bytes("Café – 37 °C “quoted”", "cp1252"))]))
    assert result.cards[0].front == "Café – 37 °C “quoted”"
    assert "encoding" in codes(result)


def test_declared_latin1_is_honoured():
    result = convert(load_uploads([("latin1.html", _page_bytes("Größe ±5", "latin-1", declared="iso-8859-1"))]))
    assert result.cards[0].front == "Größe ±5"


def test_mostly_utf8_with_a_corrupt_byte():
    data = _page_bytes("αβγ δεζ ηθι κλμ", "utf-8").replace("δ".encode(), b"\xff")
    decoded = decode_html(data)
    assert decoded.encoding == "utf-8" and decoded.note
    assert "αβγ" in decoded.text and "�" in decoded.text


def test_very_deep_nesting_does_not_hit_the_recursion_limit():
    depth = 150
    inner = ""
    for level in reversed(range(depth)):
        inner = toggle(f"L{level}", f"<p>A{level}</p>{inner}")
    result = convert_text(notion_page(inner))
    assert len(result.cards) == depth
    assert result.cards[0].back.count("<details open>") == depth - 1
    export_cards(result.cards)
