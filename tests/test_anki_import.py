"""Optional integration test: import exports with Anki's own importer.

Skipped unless the official ``anki`` package is installed (``pip install anki``).
It uses a throwaway collection in a temporary folder, never your real Anki profile.
"""

from __future__ import annotations

import io
import os
import time
import zipfile

import pytest

from converter import TEXT_FORMATS, OutputFormat, build_media_bundle, convert, export_apkg, export_cards, load_uploads
from converter.apkg import NOTETYPE_NAME
from helpers import FIXTURES, notion_page, toggle

anki_collection = pytest.importorskip("anki.collection")
from anki.import_export_pb2 import ImportAnkiPackageOptions, ImportAnkiPackageRequest, ImportCsvRequest  # noqa: E402


def _import(tmp_path, data: bytes, fmt: OutputFormat, bundle: bytes | None = None):
    path = tmp_path / f"cards{fmt.extension}"
    path.write_bytes(data)
    col = anki_collection.Collection(str(tmp_path / "collection.anki2"))
    try:
        meta = col.get_csv_metadata(path=str(path), delimiter=None)
        assert meta.is_html and list(meta.column_labels) == ["Front", "Back"]
        log = col.import_csv(ImportCsvRequest(path=str(path), metadata=meta)).log
        notes = [col.get_note(nid) for nid in col.find_notes("")]
        fields = sorted((note.fields[0], note.fields[1]) for note in notes)
        missing = None
        if bundle is not None:
            media_dir = tmp_path / "collection.media"
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                for name in archive.namelist():
                    if name.startswith("media/"):
                        (media_dir / name.split("/", 1)[1]).write_bytes(archive.read(name))
            check = col.media.check()
            missing = (list(check.missing), list(check.unused))
        return log, fields, missing
    finally:
        col.close()


@pytest.mark.parametrize("fmt", TEXT_FORMATS)
def test_anki_imports_every_card_unchanged_with_media(tmp_path, fmt):
    result = convert(load_uploads([("export.zip", (FIXTURES / "notion_export.zip").read_bytes())]))
    output = export_cards(result.cards, fmt)
    bundle = build_media_bundle(output.filename, output.data, result.media)
    log, fields, media_check = _import(tmp_path, output.data, fmt, bundle)
    assert len(log.new) == len(result.cards) == 6
    assert fields == sorted((c.front, c.back) for c in result.cards)
    assert media_check == ([], [])


def test_anki_keeps_duplicate_fronts_on_first_import(tmp_path):
    result = convert(load_uploads([("d.html", (FIXTURES / "notion_empty_duplicates.html").read_bytes())]))
    output = export_cards(result.cards, OutputFormat.TSV)
    log, fields, _ = _import(tmp_path, output.data, OutputFormat.TSV)
    assert len(log.new) == len(result.cards)
    assert fields.count(("Duplicate question", "<p>First answer</p>")) == 1
    assert fields.count(("Duplicate question", "<p>Second answer</p>")) == 1


# Anki packages (.apkg) ----------------------------------------------------------------


def _import_package(col, path, data: bytes):
    path.write_bytes(data)
    request = ImportAnkiPackageRequest(package_path=str(path), options=ImportAnkiPackageOptions())
    return col.import_anki_package(request).log


def _fields(col) -> list[tuple[str, str]]:
    return sorted((note.fields[0], note.fields[1]) for note in (col.get_note(nid) for nid in col.find_notes("")))


def test_anki_imports_the_package_with_its_media(tmp_path):
    result = convert(load_uploads([("export.zip", (FIXTURES / "notion_export.zip").read_bytes())]))
    col = anki_collection.Collection(str(tmp_path / "collection.anki2"))
    try:
        log = _import_package(col, tmp_path / "cards.apkg", export_apkg(result.cards, result.media, "Cardiology::Export").data)
        assert len(log.new) == len(result.cards) == 6
        assert _fields(col) == sorted((c.front, c.back) for c in result.cards)
        assert len(col.find_cards('deck:"Cardiology::Export"')) == 6
        assert col.models.by_name(NOTETYPE_NAME) is not None
        check = col.media.check()
        assert (list(check.missing), list(check.unused)) == ([], [])
        assert sorted(os.listdir(col.media.dir())) == sorted({name for c in result.cards for name in c.media})
    finally:
        col.close()


def test_reimporting_an_updated_package_updates_notes_and_keeps_progress(tmp_path):
    def export(title: str, answer: str, now: float) -> bytes:
        block = f'<ul id="1f2e3d4c-5b6a-4798-8a7b-6c5d4e3f2a1b" class="toggle"><li><details open=""><summary>{title}</summary><p>{answer}</p></details></li></ul>'
        html = notion_page(block + toggle("Same", "<p>one</p>") + toggle("Same", "<p>two</p>"))
        result = convert(load_uploads([("Lecture 0123456789abcdef0123456789abcdef.html", html.encode())]))
        return export_apkg(result.cards, result.media, "Lecture", now=now)

    col = anki_collection.Collection(str(tmp_path / "collection.anki2"))
    try:
        first = _import_package(col, tmp_path / "first.apkg", export("Question", "Old answer", time.time()).data)
        assert len(first.new) == 3
        # Progress made in Anki: the edited card is now a review card due in 12 days,
        # and the note type got a custom style.
        nid = next(n for n in col.find_notes("") if col.get_note(n).fields[0] == "Question")
        card = col.get_note(nid).cards()[0]
        card.type, card.queue, card.ivl, card.due = 2, 2, 12, col.sched.today + 12
        col.update_card(card)
        notetype = col.models.by_name(NOTETYPE_NAME)
        notetype["css"] += "\n.card { text-align: left; }\n"
        col.models.update_dict(notetype)

        # The title and the answer were edited in Notion; the block ID is unchanged.
        second = _import_package(col, tmp_path / "second.apkg", export("Question (edited)", "New answer", time.time() + 60).data)
        # Anki rewrites every note it recognises from a newer file; none is added again.
        assert (len(second.new), len(second.updated)) == (0, 3)
        assert _fields(col) == [("Question (edited)", "<p>New answer</p>"), ("Same", "<p>one</p>"), ("Same", "<p>two</p>")]
        card = col.get_card(card.id)
        assert (card.type, card.ivl) == (2, 12)
        assert "text-align: left" in col.models.by_name(NOTETYPE_NAME)["css"]
    finally:
        col.close()


def test_anki_keeps_duplicate_fronts_from_a_package_across_reimports(tmp_path):
    result = convert(load_uploads([("d.html", (FIXTURES / "notion_empty_duplicates.html").read_bytes())]))
    expected = sorted((c.front, c.back) for c in result.cards)
    col = anki_collection.Collection(str(tmp_path / "collection.anki2"))
    try:
        first = _import_package(col, tmp_path / "first.apkg", export_apkg(result.cards, result.media, now=time.time()).data)
        assert len(first.new) == len(result.cards)
        again = _import_package(col, tmp_path / "again.apkg", export_apkg(result.cards, result.media, now=time.time() + 60).data)
        assert len(again.new) == 0
        assert _fields(col) == expected
    finally:
        col.close()
