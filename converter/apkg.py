"""Anki package (.apkg) export: cards, note type, deck and media in one file.

Opening the file in Anki (double-click, or File → Import…) adds the cards *and*
their images and audio, so nothing has to be copied into ``collection.media``
by hand.

The package uses Anki's classic layout (checked with Anki 26.09's importer, see
``tests/test_anki_import.py``)::

    collection.anki2   SQLite database (schema 11): notes, cards, note type, deck
    media              JSON map {"0": "heart_3fa1c2d4e5b6.png", …}
    0, 1, …            the media files, stored under their index

Everything is built in memory; nothing is written to disk.

Re-importing an updated export is safe:

* Each note's GUID comes from the toggle's Notion block ID when the export has
  one, otherwise from the page and the front text. Anki recognises notes by
  their GUID, so a re-import updates the existing notes and keeps their review
  history instead of adding copies, even after a title was edited in Notion.
* The note type has a fixed ID and modification time, so every export shares
  one note type, and styling changes you make to it in Anki are kept.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import string
import time
import zipfile
from typing import Iterable, Mapping, Sequence

from .exporters import ExportError, ExportOutput, safe_filename
from .media import zip_compression_for
from .models import Card, MediaFile, OutputFormat

DEFAULT_DECK = "Notion"

NOTETYPE_ID = 1_581_237_462
NOTETYPE_NAME = "Notion → Anki (Basic)"
NOTETYPE_MOD = 1_792_886_400
"""2026-10-25. Bump it whenever the CSS or templates change: on import, Anki keeps
whichever version of the note type is newer."""

# Anki's stock "Basic" note type (Anki 26.09), so cards look the same as after a TSV/CSV import.
CARD_CSS = """.card {
    font-family: arial;
    font-size: 20px;
    line-height: 1.5;
    text-align: center;
    color: black;
    background-color: white;
}
"""
FRONT_TEMPLATE = "{{Front}}"
BACK_TEMPLATE = "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}"
LATEX_PRE = (
    "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n\\usepackage[utf8]{inputenc}\n"
    "\\usepackage{amssymb,amsmath}\n\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n"
)
LATEX_POST = "\\end{document}"

_SCHEMA = """
CREATE TABLE col (
    id integer PRIMARY KEY, crt integer NOT NULL, mod integer NOT NULL, scm integer NOT NULL,
    ver integer NOT NULL, dty integer NOT NULL, usn integer NOT NULL, ls integer NOT NULL,
    conf text NOT NULL, models text NOT NULL, decks text NOT NULL, dconf text NOT NULL, tags text NOT NULL
);
CREATE TABLE notes (
    id integer PRIMARY KEY, guid text NOT NULL, mid integer NOT NULL, mod integer NOT NULL,
    usn integer NOT NULL, tags text NOT NULL, flds text NOT NULL, sfld integer NOT NULL,
    csum integer NOT NULL, flags integer NOT NULL, data text NOT NULL
);
CREATE TABLE cards (
    id integer PRIMARY KEY, nid integer NOT NULL, did integer NOT NULL, ord integer NOT NULL,
    mod integer NOT NULL, usn integer NOT NULL, type integer NOT NULL, queue integer NOT NULL,
    due integer NOT NULL, ivl integer NOT NULL, factor integer NOT NULL, reps integer NOT NULL,
    lapses integer NOT NULL, left integer NOT NULL, odue integer NOT NULL, odid integer NOT NULL,
    flags integer NOT NULL, data text NOT NULL
);
CREATE TABLE revlog (
    id integer PRIMARY KEY, cid integer NOT NULL, usn integer NOT NULL, ease integer NOT NULL,
    ivl integer NOT NULL, lastIvl integer NOT NULL, factor integer NOT NULL, time integer NOT NULL,
    type integer NOT NULL
);
CREATE TABLE graves (usn integer NOT NULL, oid integer NOT NULL, type integer NOT NULL);
CREATE INDEX ix_notes_usn ON notes (usn);
CREATE INDEX ix_cards_usn ON cards (usn);
CREATE INDEX ix_revlog_usn ON revlog (usn);
CREATE INDEX ix_cards_nid ON cards (nid);
CREATE INDEX ix_cards_sched ON cards (did, queue, due);
CREATE INDEX ix_revlog_cid ON revlog (cid);
CREATE INDEX ix_notes_csum ON notes (csum);
"""

# Anki's default deck options. Anki only reads them when told to import deck options.
_DECK_CONFIG = {
    "id": 1, "name": "Default", "mod": 0, "usn": 0, "dyn": False,
    "maxTaken": 60, "autoplay": True, "timer": 0, "replayq": True,
    "new": {"bury": True, "delays": [1, 10], "initialFactor": 2500, "ints": [1, 4, 0], "order": 1, "perDay": 20, "separate": True},
    "rev": {"bury": True, "ease4": 1.3, "fuzz": 0.05, "ivlFct": 1, "maxIvl": 36500, "minSpace": 1, "perDay": 200},
    "lapse": {"delays": [10], "leechAction": 1, "leechFails": 8, "minInt": 1, "mult": 0},
}

_BASE91 = string.ascii_letters + string.digits + "!#$%&()*+,-./:;<=>?@[]^_`{|}~"
_NOTION_PAGE_ID = re.compile(r"([0-9a-f]{32})\.html?$", re.IGNORECASE)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def export_apkg(
    cards: Iterable[Card],
    media: Mapping[str, MediaFile],
    deck_name: str | None = None,
    filename: str | None = None,
    now: float | None = None,
) -> ExportOutput:
    """Write *cards*, and the media files they use, as an Anki package."""
    cards = list(cards)
    if not cards:
        raise ExportError("There are no cards to export.")
    for number, item in enumerate(cards, start=1):
        if not item.front.strip():
            raise ExportError(f"Card {number} has an empty front; Anki would reject it.")
    deck = safe_deck_name(deck_name)
    names = sorted({name for item in cards for name in item.media})
    missing = [name for name in names if name not in media]
    if missing:
        raise ExportError(f"The media file '{missing[0]}' is used by a card but is not part of the conversion result.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("collection.anki2", _collection(cards, deck, time.time() if now is None else now))
        package.writestr("media", json.dumps({str(index): name for index, name in enumerate(names)}))
        for index, name in enumerate(names):
            if "/" in name or "\\" in name:  # defensive: names are generated flat
                raise ValueError(f"Media filename must be flat: {name!r}")
            package.writestr(str(index), media[name].data, compress_type=zip_compression_for(name))
    return ExportOutput(safe_filename(filename or deck, OutputFormat.APKG), buffer.getvalue(), len(cards), OutputFormat.APKG)


def safe_deck_name(name: str | None, default: str = DEFAULT_DECK) -> str:
    """A clean deck name. ``::`` separates sub-decks, e.g. ``Neurology::Lecture 2.3``."""
    parts = (" ".join(part.split())[:120].strip() for part in _CONTROL_CHARS.sub(" ", name or "").split("::"))
    return "::".join(part for part in parts if part) or default


def suggest_deck_name(page_titles: Sequence[str]) -> str:
    """Default deck: the page title for one page, otherwise a generic name."""
    titles = [t for t in page_titles if t.strip()]
    return safe_deck_name(titles[0]) if len(titles) == 1 else DEFAULT_DECK


def note_guids(cards: Sequence[Card]) -> list[str]:
    """One stable GUID per card, unique within the package (see the module docstring)."""
    seen: dict[str, int] = {}
    guids: list[str] = []
    for item in cards:
        if item.source_id:
            key = f"notion-block:{item.source_id}"
        else:
            page = _NOTION_PAGE_ID.search(item.page_path)
            key = f"notion-page:{page.group(1).lower() if page else item.page_title}\x1f{item.front_text}"
        repeat = seen.get(key, 0)
        seen[key] = repeat + 1
        guids.append(_guid(key if repeat == 0 else f"{key}\x1f{repeat}"))
    return guids


# ---------------------------------------------------------------------------
# The collection database
# ---------------------------------------------------------------------------


def _collection(cards: list[Card], deck: str, timestamp: float) -> bytes:
    seconds, millis = int(timestamp), int(timestamp * 1000)
    decks, deck_id = _decks(deck, seconds)
    conf = {
        "activeDecks": [deck_id], "curDeck": deck_id, "curModel": NOTETYPE_ID, "nextPos": len(cards) + 1,
        "newSpread": 0, "collapseTime": 1200, "timeLim": 0, "estTimes": True, "dueCounts": True,
        "sortType": "noteFld", "sortBackwards": False, "addToCur": True,
    }
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(_SCHEMA)
        db.execute(
            "INSERT INTO col VALUES (1, ?, ?, ?, 11, 0, 0, 0, ?, ?, ?, ?, '{}')",
            (
                seconds, millis, millis, json.dumps(conf),
                json.dumps({str(NOTETYPE_ID): _notetype(deck_id)}), json.dumps(decks), json.dumps({"1": _DECK_CONFIG}),
            ),
        )
        for position, (item, guid) in enumerate(zip(cards, note_guids(cards))):
            note_id = millis + position
            db.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, -1, '', ?, ?, ?, 0, '')",
                (
                    note_id, guid, NOTETYPE_ID, seconds,
                    f"{_field(item.front)}\x1f{_field(item.back)}", item.front_text, _checksum(item.front_text),
                ),
            )
            # A new card (type 0, queue 0), shown in export order.
            db.execute(
                "INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                (note_id, note_id, deck_id, seconds, position + 1),
            )
        db.commit()
        return db.serialize()
    finally:
        db.close()


def _decks(name: str, timestamp: int) -> tuple[dict[str, dict], int]:
    """Anki's Default deck plus *name* and its parent decks; returns them and *name*'s ID."""
    decks = {"1": _deck(1, "Default", timestamp)}
    parts = name.split("::")
    deck_id = 1
    for depth in range(1, len(parts) + 1):
        full = "::".join(parts[:depth])
        if full.casefold() == "default":
            deck_id = 1
            continue
        deck_id = (1 << 31) + int(hashlib.sha256(full.casefold().encode("utf-8", "surrogatepass")).hexdigest()[:7], 16)
        decks[str(deck_id)] = _deck(deck_id, full, timestamp)
    return decks, deck_id


def _deck(deck_id: int, name: str, timestamp: int) -> dict:
    return {
        "id": deck_id, "name": name, "desc": "", "mod": timestamp, "usn": -1,
        "collapsed": False, "browserCollapsed": False, "dyn": 0, "conf": 1, "extendNew": 0, "extendRev": 0,
        "newToday": [0, 0], "revToday": [0, 0], "lrnToday": [0, 0], "timeToday": [0, 0],
    }


def _notetype(deck_id: int) -> dict:
    field = {"sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []}
    return {
        "id": NOTETYPE_ID, "name": NOTETYPE_NAME, "type": 0, "mod": NOTETYPE_MOD, "usn": -1,
        "sortf": 0, "did": deck_id, "tags": [], "vers": [],
        "flds": [{"name": "Front", "ord": 0, **field}, {"name": "Back", "ord": 1, **field}],
        "tmpls": [{
            "name": "Card 1", "ord": 0, "qfmt": FRONT_TEMPLATE, "afmt": BACK_TEMPLATE,
            "bqfmt": "", "bafmt": "", "did": None,
        }],
        "css": CARD_CSS, "latexPre": LATEX_PRE, "latexPost": LATEX_POST, "latexsvg": False,
        "req": [[0, "any", [0]]],
    }


def _field(value: str) -> str:
    # \x1f separates the fields of a note in Anki's database.
    return value.replace("\x00", "").replace("\x1f", "")


def _checksum(text: str) -> int:
    """Anki's first-field checksum (used for its duplicate check)."""
    return int(hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()[:8], 16)


def _guid(key: str) -> str:
    """A GUID in Anki's own style: a 64-bit number written in base 91."""
    number = int.from_bytes(hashlib.sha256(key.encode("utf-8", "surrogatepass")).digest()[:8], "big")
    digits: list[str] = []
    while True:
        number, digit = divmod(number, 91)
        digits.append(_BASE91[digit])
        if not number:
            return "".join(reversed(digits))
