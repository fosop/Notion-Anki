"""Build the synthetic Notion-export ZIP fixtures used by the tests.

Run from the project root to regenerate the committed files:

    python tests/fixtures/build_fixtures.py

Everything here is synthetic. The structure mirrors real Notion "Export → HTML"
output: ``Page <32-hex id>.html`` files, a sibling folder with the same name for
that page's assets and sub-pages, percent-encoded relative ``src``/``href``
values, and (for large exports) ZIP "parts" nested inside an outer ZIP.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
FIXED_TIME = (2024, 1, 1, 12, 0, 0)

CARDIO = "Cardiology 3f2a9c1e5b7d4e8f9a0b1c2d3e4f5a6b"
ARRHY = "Arrhythmias 9b8a7c6d5e4f3a2b1c0d9e8f7a6b5c4d"
PHARMA = "Pharmacology – β-Blocker 1a1b1c1d1e1f1a2b2c2d2e2f3a3b3c3d"
ECG_IMAGE = "ECG – β-Blocker (1).png"

NOTION_STYLE = """<style>
/* cspell:disable-file */
html { -webkit-print-color-adjust: exact; }
body { line-height: 1.5; white-space: pre-wrap; }
.toggle { padding-inline-start: 0em; list-style-type: none; }
.indented { padding-left: 1.5em; }
.highlight-red { color: rgba(212, 76, 71, 1); fill: rgba(212, 76, 71, 1); }
.highlight-yellow_background { background: rgba(251, 243, 219, 1); }
.block-color-blue_background { background: rgba(231, 243, 248, 1); }
</style>"""


def notion_page(title: str, body: str) -> str:
    """Wrap page-body HTML in the skeleton Notion uses for every exported page."""
    return (
        '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
        f"<title>{title}</title>{NOTION_STYLE}</head><body>"
        '<article id="0f000000-0000-4000-8000-000000000000" class="page sans">'
        f'<header><h1 class="page-title">{title}</h1><p class="page-description"></p></header>'
        f'<div class="page-body">{body}</div></article>'
        '<span class="sans" style="font-size:14px;padding-top:2em"></span></body></html>'
    )


def png(rgb: tuple[int, int, int]) -> bytes:
    """A valid 1x1 PNG of the given colour (distinct colours give distinct bytes)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00" + bytes(rgb))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")


FAKE_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x0f" + b"\x00" * 32  # never decoded by the app


def u(path: str) -> str:
    """Percent-encode a relative path the way Notion does in src/href attributes."""
    return quote(path, safe="/()")


def toggle(title: str, inner: str) -> str:
    return f'<ul class="toggle"><li><details open=""><summary>{title}</summary>{inner}</details></li></ul>'


def image(src_path: str, width: int | None = None, caption: str | None = None) -> str:
    style = f' style="width:{width}px"' if width else ""
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f'<figure class="image"><a href="{u(src_path)}"><img{style} src="{u(src_path)}"/></a>{cap}</figure>'


def export_files() -> dict[str, bytes]:
    cardio_body = (
        "<p>Cardiology overview: this paragraph is outside every toggle.</p>"
        + toggle(
            "Label the chambers of the heart",
            image(f"{CARDIO}/heart diagram.png", 480, "Anterior view") + "<p>RA, RV, LA, LV</p>",
        )
        + toggle("What does this rhythm strip show?", image(f"{CARDIO}/Untitled.png") + "<p>Sinus rhythm</p>")
        + f'<figure class="link-to-page"><a href="{u(CARDIO + "/" + ARRHY + ".html")}"><span class="icon">\U0001f4c4</span>Arrhythmias</a></figure>'
    )
    arrhy_body = (
        toggle(
            "Atrial fibrillation on ECG",
            image(f"{ARRHY}/{ECG_IMAGE}")
            + "<p>Irregularly irregular, no P waves</p>"
            + toggle("Another image named Untitled", image(f"{ARRHY}/Untitled.png")),
        )
        + toggle(
            "Which murmur is this?",
            f'<figure><div class="source"><a href="{u(ARRHY + "/mitral murmur.mp3")}">{ARRHY}/mitral murmur.mp3</a></div></figure>'
            "<p>Mitral regurgitation: pansystolic murmur</p>",
        )
    )
    pharma_body = toggle(
        "Mechanism of β-blockers",
        "<p>Competitive antagonism at β₁ receptors → ↓HR, ↓contractility</p>"
        + image(f"{PHARMA}/Image (1).png")
        + f'<p>Missing figure: <img src="{u(PHARMA + "/does-not-exist.png")}"/></p>',
    )
    return {
        f"{CARDIO}.html": notion_page("Cardiology", cardio_body).encode("utf-8"),
        f"{CARDIO}/heart diagram.png": png((200, 30, 30)),
        f"{CARDIO}/Untitled.png": png((30, 200, 30)),
        f"{CARDIO}/{ARRHY}.html": notion_page("Arrhythmias", arrhy_body).encode("utf-8"),
        f"{CARDIO}/{ARRHY}/{ECG_IMAGE}": png((30, 30, 200)),
        f"{CARDIO}/{ARRHY}/Untitled.png": png((120, 120, 0)),
        f"{CARDIO}/{ARRHY}/mitral murmur.mp3": FAKE_MP3,
        f"{PHARMA}.html": notion_page("Pharmacology – β-Blocker", pharma_body).encode("utf-8"),
        f"{PHARMA}/Image (1).png": png((0, 120, 120)),
    }


def write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.flag_bits |= 0x800  # UTF-8 names, as Notion writes them
            archive.writestr(info, files[name])


def zip_bytes(files: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.flag_bits |= 0x800
            archive.writestr(info, files[name])
    return buffer.getvalue()


def build() -> None:
    files = export_files()
    write_zip(HERE / "notion_export.zip", files)

    # Large Notion exports: an outer ZIP holding "ExportBlock-…-Part-N.zip" files.
    part1 = {k: v for k, v in files.items() if k.startswith(CARDIO)}
    part2 = {k: v for k, v in files.items() if not k.startswith(CARDIO)}
    outer = {
        "ExportBlock-5e6f7a8b-Part-1.zip": zip_bytes(part1),
        "ExportBlock-5e6f7a8b-Part-2.zip": zip_bytes(part2),
    }
    write_zip(HERE / "notion_export_nested.zip", outer)
    print("Wrote notion_export.zip and notion_export_nested.zip")


if __name__ == "__main__":
    build()
