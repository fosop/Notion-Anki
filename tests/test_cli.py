"""Command line: .apkg by default, otherwise the format from --format or the extension of -o."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile

from converter.__main__ import main
from helpers import FIXTURES

EXPORT = str(FIXTURES / "notion_export.zip")
NESTED = str(FIXTURES / "notion_nested.html")


def test_default_output_is_an_anki_package_with_the_media(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([EXPORT]) == 0
    written = [p.name for p in tmp_path.iterdir()]
    assert written == ["notion_anki_cards.apkg"]
    with zipfile.ZipFile(tmp_path / written[0]) as package:
        assert len(json.loads(package.read("media"))) == 6


def test_the_output_extension_selects_a_text_format(tmp_path):
    assert main([EXPORT, "-o", str(tmp_path / "cards.tsv"), "--bundle"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cards.tsv", "cards.zip"]


def test_explicit_format_and_deck(tmp_path):
    assert main([NESTED, "--format", "csv", "-o", str(tmp_path / "cards")]) == 0
    assert (tmp_path / "cards.csv").read_text(encoding="utf-8").startswith("#separator:Comma")

    assert main([NESTED, "--deck", "Neuro::Lecture 2.3", "-o", str(tmp_path / "deck.apkg")]) == 0
    db = sqlite3.connect(":memory:")
    db.deserialize(zipfile.ZipFile(io.BytesIO((tmp_path / "deck.apkg").read_bytes())).read("collection.anki2"))
    decks = {d["name"] for d in json.loads(db.execute("SELECT decks FROM col").fetchone()[0]).values()}
    assert "Neuro::Lecture 2.3" in decks


def test_duplicate_front_warning_only_concerns_text_formats(tmp_path, capsys):
    source = str(FIXTURES / "notion_empty_duplicates.html")
    assert main([source, "-o", str(tmp_path / "d.apkg")]) == 0
    assert "share their front" not in capsys.readouterr().err
    assert main([source, "-o", str(tmp_path / "d.tsv")]) == 0
    assert "share their front" in capsys.readouterr().err
