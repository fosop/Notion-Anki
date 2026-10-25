"""End-to-end test of the Streamlit interface using Streamlit's headless AppTest."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from converter import NestedToggleMode, OutputFormat, parse_export
from helpers import FIXTURES

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = Path(__file__).resolve().parents[1] / "app.py"


def start() -> "AppTest":
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    assert not app.exception
    return app


def button(app, label: str):
    return next(b for b in app.button if b.label == label)


def download_labels(app) -> list[str]:
    return [d.proto.label for d in app.get("download_button")]


def metric(app, label: str) -> str:
    return next(m.value for m in app.metric if m.label == label)


def package_decks(data: bytes) -> set[str]:
    db = sqlite3.connect(":memory:")
    db.deserialize(zipfile.ZipFile(io.BytesIO(data)).read("collection.anki2"))
    return {deck["name"] for deck in json.loads(db.execute("SELECT decks FROM col").fetchone()[0]).values()}


def test_start_screen_explains_how_to_export():
    app = start()
    assert any("How to export from Notion" in info.value for info in app.info)
    assert not any(b.label == "Convert" for b in app.button)


def test_full_workflow_with_a_notion_zip():
    app = start()
    app.file_uploader[0].upload("notion_export.zip", (FIXTURES / "notion_export.zip").read_bytes(), "application/zip").run()
    assert not app.exception
    assert not app.error

    button(app, "Convert").click().run()
    assert not app.exception
    assert any("Conversion finished: 6 card(s) from 3 page(s)" in s.value for s in app.success)
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Cards"] == "6"
    assert metrics["Pages processed"] == "3"
    assert metrics["Media files"] == "6"
    assert any("warning" in w.value for w in app.warning)  # the missing image is reported

    labels = [e.label for e in app.expander]
    assert any("Which murmur is this" in label for label in labels)

    app.text_input(key="card_search").set_value("murmur").run()
    card_labels = [e.label for e in app.expander if e.label.startswith("#")]
    assert len(card_labels) == 1 and "murmur" in card_labels[0]

    # Default: one Anki package with the cards and their media.
    apkg_label = next(label for label in download_labels(app) if ".apkg" in label)
    assert "Cardiology" in apkg_label or "notion_anki_cards" in apkg_label
    assert not any("_with_media.zip" in label for label in download_labels(app))
    package = zipfile.ZipFile(io.BytesIO(app.session_state["export"].data))
    assert len(json.loads(package.read("media"))) == 6

    # TSV: a text file plus the optional media bundle.
    app.radio[0].set_value(OutputFormat.TSV).run()
    assert not app.exception
    tsv_label = next(label for label in download_labels(app) if label.endswith(".tsv"))
    assert "Cardiology" in tsv_label or "notion_anki_cards" in tsv_label
    assert any(label.endswith(")") and "_with_media.zip" in label for label in download_labels(app))

    state = app.session_state
    output = state["export"]
    _headers, rows = parse_export(output.text, OutputFormat.TSV)
    assert len(rows) == 6 and all(len(r) == 2 for r in rows)
    bundle = zipfile.ZipFile(io.BytesIO(state["bundle"]))
    assert sum(1 for n in bundle.namelist() if n.startswith("media/")) == 6


def test_the_package_goes_into_the_chosen_deck():
    app = start()
    app.file_uploader[0].upload("notion_nested.html", (FIXTURES / "notion_nested.html").read_bytes(), "text/html").run()
    button(app, "Convert").click().run()
    assert app.session_state["export"].filename == "Nested Toggles.apkg"
    assert "Nested Toggles" in package_decks(app.session_state["export"].data)

    next(t for t in app.text_input if t.label == "Deck name").set_value("Neuro::Lecture 2.3").run()
    assert not app.exception
    assert "Neuro::Lecture 2.3" in package_decks(app.session_state["export"].data)


def test_duplicate_front_warning_only_concerns_text_formats():
    app = start()
    page = (FIXTURES / "notion_empty_duplicates.html").read_bytes()
    app.file_uploader[0].upload("notion_empty_duplicates.html", page, "text/html").run()
    button(app, "Convert").click().run()
    apkg_warnings = int(metric(app, "Warnings"))
    app.radio[0].set_value(OutputFormat.TSV).run()
    assert int(metric(app, "Warnings")) == apkg_warnings + 1


def test_changing_settings_requires_reconversion_and_csv_export():
    app = start()
    app.file_uploader[0].upload("notion_nested.html", (FIXTURES / "notion_nested.html").read_bytes(), "text/html").run()
    button(app, "Convert").click().run()
    assert any("4 card(s)" in s.value for s in app.success)

    app.radio[1].set_value(NestedToggleMode.TITLES_ONLY).run()
    assert any("Click **Convert** to update" in i.value for i in app.info)
    button(app, "Convert").click().run()
    app.radio[0].set_value(OutputFormat.CSV).run()
    output = app.session_state["export"]
    assert output.filename == "Nested Toggles.csv"
    _headers, rows = parse_export(output.text, OutputFormat.CSV)
    assert "Explanation B" not in rows[0][1]  # titles-only parent answer


def test_page_without_toggles_shows_a_diagnostic():
    app = start()
    app.file_uploader[0].upload("no_toggles.html", (FIXTURES / "no_toggles.html").read_bytes(), "text/html").run()
    assert not app.exception
    assert any("No toggles were detected" in e.value and "no_toggles.html" in e.value for e in app.error)
    assert not any(b.label == "Convert" for b in app.button)
