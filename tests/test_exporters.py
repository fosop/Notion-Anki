"""TSV/CSV generation: quoting, field counts, Anki headers, file names."""

from __future__ import annotations

import codecs
import csv
import io

import pytest

from converter import TEXT_FORMATS, ExportError, OutputFormat, export_cards, parse_export, safe_filename, suggest_filename
from helpers import convert_fixture

TRICKY = [
    ("Tab\there", 'Comma, quote " and newline\nsecond line'),
    ('"Quoted front"', "Back with ,,, and \t\t tabs"),
    ("#hash at the start", "Windows\r\nline break"),
    ("Unicode αβγ → ≥ µg 心臓 💊", "Ünïcödé, «guillemets», “curly” ‘quotes’"),
    ("<b>HTML</b> front", '<p class="x">Back; with semicolons; and "quotes"</p>'),
]


# 13 + 14. Tabs, commas, quotes and line breaks in TSV and CSV ----------------------------

@pytest.mark.parametrize("fmt", TEXT_FORMATS)
def test_tricky_fields_round_trip_exactly(fmt):
    output = export_cards(TRICKY, fmt)
    headers, rows = parse_export(output.text, fmt)
    assert headers == [f"#separator:{fmt.anki_separator}", "#html:true", f"#columns:Front{fmt.delimiter}Back"]
    assert rows == [list(pair) for pair in TRICKY]
    assert all(len(row) == 2 for row in rows)
    assert output.rows == len(TRICKY)


@pytest.mark.parametrize("fmt", TEXT_FORMATS)
def test_plain_csv_reader_sees_two_columns(fmt):
    output = export_cards(TRICKY, fmt, include_headers=False)
    rows = list(csv.reader(io.StringIO(output.text), delimiter=fmt.delimiter))
    assert [len(r) for r in rows] == [2] * len(TRICKY)


def test_every_record_is_quoted_so_no_line_can_look_like_a_comment():
    output = export_cards([("#1 cause of death", "IHD")], OutputFormat.TSV)
    lines = output.text.splitlines()
    assert lines[3] == '"#1 cause of death"\t"IHD"'


@pytest.mark.parametrize("fmt", TEXT_FORMATS)
def test_header_lines_are_not_imported_as_cards(fmt):
    output = export_cards([("Q1", "A1"), ("Q2", "A2")], fmt)
    # Anki 2.1.55+: headers at the top are metadata.
    _headers, rows = parse_export(output.text, fmt)
    assert rows == [["Q1", "A1"], ["Q2", "A2"]]
    # Older Anki: every line starting with '#' is a comment.
    legacy = "\n".join(line for line in output.text.split("\n") if not line.startswith("#"))
    assert list(csv.reader(io.StringIO(legacy), delimiter=fmt.delimiter)) == [["Q1", "A1"], ["Q2", "A2"]]


def test_line_starting_with_hash_inside_a_field_is_escaped():
    output = export_cards([("Q", "first\n#second")], OutputFormat.TSV)
    _headers, rows = parse_export(output.text, OutputFormat.TSV)
    assert rows == [["Q", "first\n&#35;second"]]
    assert not any(line.startswith("#second") for line in output.text.splitlines())


def test_spreadsheet_formula_prefixes_are_neutralised():
    output = export_cards([("=1+1", "-5 mmol"), ("+K", "@risk"), ("a=b", "x-y")], OutputFormat.CSV)
    _headers, rows = parse_export(output.text, OutputFormat.CSV)
    assert rows == [["&#61;1+1", "&#45;5 mmol"], ["&#43;K", "&#64;risk"], ["a=b", "x-y"]]


def test_utf8_without_bom():
    output = export_cards([("Δ", "→")], OutputFormat.CSV)
    assert not output.data.startswith(codecs.BOM_UTF8)
    assert output.data.decode("utf-8").endswith('"Δ","→"\n')


# 15. Field counts and card counts -----------------------------------------------------------

@pytest.mark.parametrize("fmt", TEXT_FORMATS)
def test_each_card_is_exactly_one_record(fmt):
    result = convert_fixture("notion_rich.html")
    output = export_cards(result.cards, fmt)
    _headers, rows = parse_export(output.text, fmt)
    assert len(rows) == len(result.cards) == output.rows
    assert [row[0] for row in rows] == [c.front for c in result.cards]
    assert [row[1] for row in rows] == [c.back for c in result.cards]


def test_converted_fields_never_contain_raw_line_breaks():
    for name in ("notion_rich.html", "notion_nested.html", "generic_details.html", "malformed.html"):
        for item in convert_fixture(name).cards:
            assert "\n" not in item.front and "\n" not in item.back, name


def test_empty_exports_and_empty_fronts_are_refused():
    with pytest.raises(ExportError):
        export_cards([], OutputFormat.TSV)
    with pytest.raises(ExportError):
        export_cards([("  ", "back")], OutputFormat.TSV)


def test_the_text_exporter_does_not_write_packages():
    with pytest.raises(ValueError, match="export_apkg"):
        export_cards([("Q", "A")], OutputFormat.APKG)


@pytest.mark.parametrize(
    ("name", "fmt", "expected"),
    [
        ("My cards", OutputFormat.TSV, "My cards.tsv"),
        ("cards.csv", OutputFormat.TSV, "cards.tsv"),
        ("cards.TSV", OutputFormat.CSV, "cards.csv"),
        ("../../etc/passwd", OutputFormat.TSV, "passwd.tsv"),
        ("C:\\Users\\me\\deck", OutputFormat.TSV, "deck.tsv"),
        ('a<b>:c"d|e?f*g', OutputFormat.CSV, "a_b__c_d_e_f_g.csv"),
        ("con", OutputFormat.TSV, "_con.tsv"),
        ("", OutputFormat.TSV, "anki_cards.tsv"),
        ("  ...  ", OutputFormat.CSV, "anki_cards.csv"),
        ("Kardiologie – β-Blocker", OutputFormat.TSV, "Kardiologie – β-Blocker.tsv"),
        ("cards.tsv", OutputFormat.APKG, "cards.apkg"),
        ("deck.APKG", OutputFormat.TSV, "deck.tsv"),
        ("Neuro::Lecture 2.3", OutputFormat.APKG, "Neuro__Lecture 2.3.apkg"),
    ],
)
def test_safe_filenames(name, fmt, expected):
    assert safe_filename(name, fmt) == expected


def test_suggested_filename():
    assert suggest_filename(["Cardiology"], OutputFormat.TSV) == "Cardiology.tsv"
    assert suggest_filename(["A", "B"], OutputFormat.CSV) == "notion_anki_cards.csv"
    assert suggest_filename(["Cardiology"], OutputFormat.APKG) == "Cardiology.apkg"
