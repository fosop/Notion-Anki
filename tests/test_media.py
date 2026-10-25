"""ZIP exports, image/audio resolution, unique media names and the media bundle."""

from __future__ import annotations

import base64
import io
import re
import zipfile

import pytest

from converter import (
    ConversionOptions,
    NestedToggleMode,
    build_media_bundle,
    convert,
    export_cards,
    load_uploads,
    media_filename,
    scan_pages,
)
from converter.media import MEDIA_FOLDER
from helpers import FIXTURES, card, codes, convert_files, fronts, make_zip, notion_page, toggle

CARDIO = "Cardiology 3f2a9c1e5b7d4e8f9a0b1c2d3e4f5a6b"
ARRHY = "Arrhythmias 9b8a7c6d5e4f3a2b1c0d9e8f7a6b5c4d"
PHARMA = "Pharmacology – β-Blocker 1a1b1c1d1e1f1a2b2c2d2e2f3a3b3c3d"
FLAT_NAME = re.compile(r"^[A-Za-z0-9_]+_[0-9a-f]{12}\.[a-z0-9]+$")


def load_export(name: str = "notion_export.zip"):
    return load_uploads([(name, (FIXTURES / name).read_bytes())])


@pytest.fixture(scope="module")
def export_result():
    return convert(load_export())


def _sources(html: str) -> list[str]:
    return re.findall(r'<img src="([^"]+)"', html)


def _sounds(html: str) -> list[str]:
    return re.findall(r"\[sound:([^\]]+)\]", html)


# 11. ZIP exports with multiple HTML files and image assets -----------------------------

def test_zip_pages_are_discovered_in_parent_then_subpage_order():
    inputs = load_export()
    assert [p.path for p in inputs.pages] == [
        f"{CARDIO}.html",
        f"{CARDIO}/{ARRHY}.html",
        f"{PHARMA}.html",
    ]
    summaries = scan_pages(inputs)
    assert [s.title for s in summaries] == ["Cardiology", "Arrhythmias", "Pharmacology – β-Blocker"]
    assert [s.toggles for s in summaries] == [2, 3, 1]


def test_zip_conversion_covers_all_pages_in_order(export_result):
    assert fronts(export_result) == [
        "Label the chambers of the heart",
        "What does this rhythm strip show?",
        "Atrial fibrillation on ECG",
        "Another image named Untitled",
        "Which murmur is this?",
        "Mechanism of β-blockers",
    ]
    assert export_result.pages_processed == 3
    assert "Cardiology overview" not in " ".join(c.back for c in export_result.cards)


def test_selected_pages_only():
    result = convert(load_export(), [f"{PHARMA}.html"])
    assert fronts(result) == ["Mechanism of β-blockers"]
    assert [p.title for p in result.pages] == ["Pharmacology – β-Blocker"]


def test_images_are_resolved_renamed_and_collected(export_result):
    chambers = card(export_result, "Label the chambers of the heart")
    [src] = _sources(chambers.back)
    assert FLAT_NAME.match(src) and src.startswith("heart_diagram_")
    assert chambers.media == (src,)
    assert export_result.media[src].data.startswith(b"\x89PNG")
    assert f'<img src="{src}" width="480" style="max-width: 100%; height: auto">' in chambers.back
    assert "Anterior view" in chambers.back


def test_same_filename_in_different_folders_does_not_collide(export_result):
    first = _sources(card(export_result, "What does this rhythm strip show?").back)[0]
    second = _sources(card(export_result, "Another image named Untitled").back)[0]
    assert first != second
    assert first.startswith("Untitled_") and second.startswith("Untitled_")
    assert export_result.media[first].data != export_result.media[second].data


# 12. Filenames with spaces or special characters ------------------------------------------

def test_percent_encoded_and_special_character_paths_resolve(export_result):
    ecg = _sources(card(export_result, "Atrial fibrillation on ECG").back)[0]
    assert ecg.startswith("ECG_Blocker_1_")  # from "ECG – β-Blocker (1).png"
    pharma = _sources(card(export_result, "Mechanism of β-blockers").back)
    assert len(pharma) == 1 and pharma[0].startswith("Image_1_")
    assert all(FLAT_NAME.match(name) for name in export_result.media)


def test_missing_images_are_reported(export_result):
    back = card(export_result, "Mechanism of β-blockers").back
    assert "[Image not available: does-not-exist.png]" in back
    issue = next(i for i in export_result.issues if i.code == "media-missing")
    assert "does-not-exist.png" in issue.message
    assert issue.cards == (6,)


def test_media_names_are_stable_between_runs(export_result):
    again = convert(load_export())
    assert sorted(again.media) == sorted(export_result.media)
    assert media_filename("folder/My Image.PNG", b"abc") == media_filename("other/My Image.PNG", b"abc")
    assert media_filename("a.png", b"abc") != media_filename("a.png", b"abd")


def test_audio_becomes_an_anki_sound_tag(export_result):
    murmur = card(export_result, "Which murmur is this?")
    [sound] = _sounds(murmur.back)
    assert sound.startswith("mitral_murmur_") and sound.endswith(".mp3")
    assert export_result.media[sound].kind == "audio"
    assert murmur.media == (sound,)


def test_parent_card_includes_media_of_expanded_children(export_result):
    parent = card(export_result, "Atrial fibrillation on ECG")
    child = card(export_result, "Another image named Untitled")
    assert set(child.media) < set(parent.media)
    assert len(parent.media) == 2


def test_titles_only_parent_does_not_reference_child_media():
    result = convert(load_export())
    titles_only = convert(load_export(), options=ConversionOptions(nested_mode=NestedToggleMode.TITLES_ONLY))
    assert len(card(result, "Atrial fibrillation on ECG").media) == 2
    assert len(card(titles_only, "Atrial fibrillation on ECG").media) == 1


def test_nested_part_zips_are_merged():
    nested = convert(load_export("notion_export_nested.zip"))
    flat = convert(load_export())
    assert fronts(nested) == fronts(flat)
    assert sorted(nested.media) == sorted(flat.media)
    assert [c.back for c in nested.cards] == [c.back for c in flat.cards]


def test_fallback_to_a_unique_file_with_the_same_name():
    page = notion_page(toggle("Q", '<img src="wrong%20folder/scan.png"/>'))
    result = convert_files({"page.html": page.encode(), "assets/scan.png": b"\x89PNG\r\n\x1a\nscan"})
    assert _sources(result.cards[0].back)[0].startswith("scan_")


def test_zip_without_utf8_flag_still_resolves_accented_names():
    placeholder, real = "ecg-QQ.png", "ecg-β.png"
    assert len(placeholder.encode()) == len(real.encode())
    page = notion_page(toggle("Q", f'<img src="{real}"/>'))
    raw = make_zip({"page.html": page.encode(), placeholder: b"\x89PNG\r\n\x1a\nbeta"})
    raw = raw.replace(placeholder.encode(), real.encode())  # UTF-8 bytes, but no UTF-8 flag
    result = convert(load_uploads([("legacy.zip", raw)]))
    assert _sources(result.cards[0].back)[0].startswith("ecg_")


def test_data_uri_images_become_media_files():
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\ninline").decode()
    result = convert_files({"page.html": notion_page(toggle("Q", f'<img src="data:image/png;base64,{payload}"/>')).encode(), "x.txt": b""})
    [src] = _sources(result.cards[0].back)
    assert src.startswith("embedded_image_") and src.endswith(".png")
    assert result.media[src].data == b"\x89PNG\r\n\x1a\ninline"


def test_single_html_upload_explains_that_images_need_the_zip():
    page = notion_page(toggle("Q", '<img src="Page%20abc/img.png"/>'))
    result = convert(load_uploads([("Page abc.html", page.encode())]))
    issue = next(i for i in result.issues if i.code == "media-not-uploaded")
    assert "ZIP" in issue.message
    assert result.media == {}


# 15. Media references in the export and the bundle ---------------------------------------

def test_every_media_reference_is_in_the_collection(export_result):
    referenced = set()
    for item in export_result.cards:
        names = _sources(item.back) + _sounds(item.back)
        assert set(names) == set(item.media)
        referenced.update(names)
    assert referenced == set(export_result.media)
    assert len(export_result.media) == 6


def test_media_bundle_layout(export_result):
    output = export_cards(export_result.cards, filename="Cardiology deck")
    bundle = zipfile.ZipFile(io.BytesIO(build_media_bundle(output.filename, output.data, export_result.media)))
    names = bundle.namelist()
    assert "Cardiology deck.tsv" in names and "README.txt" in names
    media_entries = [n for n in names if n.startswith(f"{MEDIA_FOLDER}/")]
    assert sorted(n.split("/", 1)[1] for n in media_entries) == sorted(export_result.media)
    assert all(n.count("/") == 1 for n in media_entries)  # flat: no sub-folders
    for name in export_result.media:
        assert bundle.read(f"{MEDIA_FOLDER}/{name}") == export_result.media[name].data
    assert bundle.read("Cardiology deck.tsv") == output.data
    readme = bundle.read("README.txt").decode()
    assert "collection.media" in readme and "Allow HTML in fields" in readme
