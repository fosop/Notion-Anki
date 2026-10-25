"""Anki package (.apkg) export: package layout, database contents, stable note IDs and deck names.

Anki's own importer reads these packages in tests/test_anki_import.py (needs the ``anki`` package).
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import pytest

from converter import Card, ExportError, OutputFormat, export_apkg, safe_deck_name, suggest_deck_name
from converter.apkg import NOTETYPE_ID, NOTETYPE_MOD, NOTETYPE_NAME, note_guids
from helpers import convert_files, convert_fixture, convert_text, notion_page, toggle

BLOCK = "1f2e3d4c-5b6a-4798-8a7b-6c5d4e3f2a1b"


def open_package(data: bytes) -> tuple[zipfile.ZipFile, sqlite3.Connection, dict[str, str]]:
    """The package's members, its database and its media map."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    db = sqlite3.connect(":memory:")
    db.deserialize(archive.read("collection.anki2"))
    return archive, db, json.loads(archive.read("media"))


def col_json(db: sqlite3.Connection, column: str) -> dict:
    return json.loads(db.execute(f"SELECT {column} FROM col").fetchone()[0])


# Package layout and contents ------------------------------------------------------

def test_package_holds_every_card_and_the_media_it_uses():
    result = convert_fixture("notion_export.zip")
    output = export_apkg(result.cards, result.media, "Cardiology")
    assert (output.format, output.filename, output.rows) == (OutputFormat.APKG, "Cardiology.apkg", len(result.cards))
    archive, db, media_map = open_package(output.data)
    fields = [flds.split("\x1f") for (flds,) in db.execute("SELECT flds FROM notes ORDER BY id")]
    assert fields == [[c.front, c.back] for c in result.cards]
    assert sorted(media_map.values()) == sorted({name for c in result.cards for name in c.media})
    assert len(media_map) == 6
    for index, name in media_map.items():
        assert archive.read(index) == result.media[name].data
    assert set(archive.namelist()) == {"collection.anki2", "media", *media_map}


def test_cards_are_new_cards_of_one_note_type_in_the_chosen_deck():
    result = convert_fixture("notion_nested.html")
    _archive, db, media_map = open_package(export_apkg(result.cards, {}, "Neuro::Lecture 2.3").data)
    assert media_map == {}
    models = col_json(db, "models")
    assert list(models) == [str(NOTETYPE_ID)]
    notetype = models[str(NOTETYPE_ID)]
    assert (notetype["name"], notetype["mod"]) == (NOTETYPE_NAME, NOTETYPE_MOD)
    assert [f["name"] for f in notetype["flds"]] == ["Front", "Back"]
    decks = {d["name"]: d["id"] for d in col_json(db, "decks").values()}
    assert set(decks) == {"Default", "Neuro", "Neuro::Lecture 2.3"}
    rows = db.execute("SELECT did, ord, type, queue, due FROM cards ORDER BY due").fetchall()
    assert rows == [(decks["Neuro::Lecture 2.3"], 0, 0, 0, n) for n in range(1, len(result.cards) + 1)]
    assert {mid for (mid,) in db.execute("SELECT mid FROM notes")} == {NOTETYPE_ID}


def test_only_media_used_by_the_exported_cards_is_packed():
    result = convert_fixture("notion_export.zip")
    one = next(c for c in result.cards if c.media)
    _archive, _db, media_map = open_package(export_apkg([one], result.media).data)
    assert sorted(media_map.values()) == sorted(set(one.media))
    assert len(media_map) < len(result.media)


def test_field_separators_inside_content_cannot_add_fields():
    item = Card(front="Q\x1fX", back="A\x00", front_text="Q", page_title="P", page_path="p.html")
    _archive, db, _media = open_package(export_apkg([item], {}).data)
    assert db.execute("SELECT flds FROM notes").fetchone()[0].split("\x1f") == ["QX", "A"]


def test_the_default_deck_is_reused_not_duplicated():
    result = convert_fixture("notion_basic.html")
    _archive, db, _media = open_package(export_apkg(result.cards, {}, "default").data)
    assert [d["name"] for d in col_json(db, "decks").values()] == ["Default"]
    assert {did for (did,) in db.execute("SELECT did FROM cards")} == {1}


def test_empty_exports_and_missing_media_are_refused():
    with pytest.raises(ExportError):
        export_apkg([], {})
    with pytest.raises(ExportError, match="empty front"):
        export_apkg([Card(front=" ", back="A", front_text="", page_title="P", page_path="p.html")], {})
    result = convert_fixture("notion_export.zip")
    with pytest.raises(ExportError, match="media file"):
        export_apkg(result.cards, {})


# Stable note IDs (GUIDs) ------------------------------------------------------------

def test_note_ids_are_unique_even_for_identical_toggles():
    result = convert_fixture("notion_empty_duplicates.html")
    guids = note_guids(result.cards)
    assert len(set(guids)) == len(guids) == len(result.cards)


def test_note_ids_are_stable_across_exports():
    assert note_guids(convert_fixture("notion_rich.html").cards) == note_guids(convert_fixture("notion_rich.html").cards)


def test_a_notion_block_id_keeps_a_card_s_identity_when_its_title_changes():
    def page(title: str) -> str:
        return notion_page(f'<ul id="{BLOCK}" class="toggle"><li><details open=""><summary>{title}</summary><p>A</p></details></li></ul>')

    before, after = convert_text(page("Old title")), convert_text(page("New title"))
    assert before.cards[0].source_id == BLOCK.replace("-", "")
    assert note_guids(before.cards) == note_guids(after.cards)


def test_newer_exports_carry_the_block_id_on_the_details_element():
    html = notion_page(f'<details id="{BLOCK}" class="toggle" open=""><summary>Q</summary><p>A</p></details>')
    assert convert_text(html).cards[0].source_id == BLOCK.replace("-", "")


def test_without_block_ids_the_notion_page_and_the_front_identify_a_card():
    page = notion_page(toggle("Question", "<p>Answer</p>")).encode()
    original = convert_files({"Lecture 1 0123456789abcdef0123456789abcdef.html": page})
    renamed = convert_files({"Renamed 0123456789abcdef0123456789abcdef.html": page})
    other_page = convert_files({"Lecture 2 fedcba9876543210fedcba9876543210.html": page})
    assert original.cards[0].source_id == ""
    assert note_guids(original.cards) == note_guids(renamed.cards) != note_guids(other_page.cards)


# Deck names -------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Cardiology", "Cardiology"),
        ("  Neuro :: Lecture   2.3 ", "Neuro::Lecture 2.3"),
        ("A::::B::", "A::B"),
        ("Tab\tand\nnewline", "Tab and newline"),
        ("2.3 Cerebral vasculature & disease (1 of 2)", "2.3 Cerebral vasculature & disease (1 of 2)"),
        ("", "Notion"),
        (None, "Notion"),
        (" :: ", "Notion"),
    ],
)
def test_deck_names_are_cleaned(name, expected):
    assert safe_deck_name(name) == expected


def test_suggested_deck_name():
    assert suggest_deck_name(["2.3 Cerebral vasculature"]) == "2.3 Cerebral vasculature"
    assert suggest_deck_name(["A", "B"]) == suggest_deck_name([]) == "Notion"
