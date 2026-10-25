"""Uploaded HTML and ZIP files are untrusted: nothing active may survive or run."""

from __future__ import annotations

import io
import re
import zipfile

import pytest
from bs4 import BeautifulSoup

from converter import LoaderLimits, convert, load_uploads
from converter.loader import normalise_path
from converter.sanitize import OUTPUT_TAGS, classify_url, is_safe_href, is_safe_src, safe_color, sanitize_style
from helpers import card, codes, convert_fixture, make_zip, notion_page, toggle

ALLOWED_ATTRIBUTES = {"style", "dir", "start", "type", "border", "cellpadding", "colspan", "rowspan",
                      "href", "title", "src", "alt", "width", "height", "open"}


def assert_inert(fragment: str) -> None:
    """Parse an output fragment and check every tag/attribute against the allowlist."""
    soup = BeautifulSoup(f"<div id='root'>{fragment}</div>", "html.parser")
    root = soup.find(id="root")
    for tag in root.find_all(True):
        assert tag.name in OUTPUT_TAGS, f"unexpected tag <{tag.name}>"
        for name, value in tag.attrs.items():
            assert name in ALLOWED_ATTRIBUTES, f"unexpected attribute {name}"
            assert not name.startswith("on")
            if name == "href":
                assert is_safe_href(value), value
            if name == "src":
                assert is_safe_src(value), value
            if name == "style":
                lowered = value.lower()
                assert "url(" not in lowered and "expression" not in lowered and "behavior" not in lowered
    # Text is HTML-escaped, so every literal "<" in the fragment starts a real tag.
    markup = "".join(re.findall(r"<[^>]*>", fragment)).lower()
    for marker in ("<script", "<iframe", "<object", "<embed", "<svg", "<form", "<input", "<style", "<meta",
                   "<base", "<link", "javascript:", "vbscript:", "data:", "file:"):
        assert marker not in markup, marker


# 17. Scripts and active content are never kept --------------------------------------

def test_malicious_page_is_neutralised():
    result = convert_fixture("malicious.html")
    assert len(result.cards) == 1
    item = card(result, "Is this safe?")
    assert item.front == "Is this safe?"
    assert_inert(item.front)
    assert_inert(item.back)
    assert "Visible answer text" in item.back
    assert '<a href="https://example.org/safe">safe link</a>' in item.back
    for text in ("js link", "mixed case link", "tab link", "data link", "vb link", "file link", "css url", "Send"):
        assert text in item.back  # the text survives, the active part does not
    # id, class, data-* and title are dropped from <p>; "position: fixed" is dropped from the style.
    assert '<p style="color: red">Styled text</p>' in item.back
    assert {"unsafe-link", "embed-removed", "svg-removed"} <= codes(result)


def test_title_cannot_carry_active_content():
    html = notion_page(toggle('Q <img src=x onerror="alert(1)"><a href="javascript:x">link</a>', "<p>A</p>"))
    result = convert(load_uploads([("p.html", html.encode())]))
    assert_inert(result.cards[0].front)


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://example.org/a b", "web"),
        ("//cdn.example.org/x.png", "web"),
        ("mailto:me@example.org", "web"),
        ("javascript:alert(1)", "unsafe"),
        (" JaVaScRiPt:alert(1)", "unsafe"),
        ("java\tscript:alert(1)", "unsafe"),
        ("\x01javascript:alert(1)", "unsafe"),
        ("data:text/html;base64,AAAA", "unsafe"),
        ("vbscript:x", "unsafe"),
        ("file:///etc/passwd", "unsafe"),
        ("C:\\Windows\\win.ini", "unsafe"),
        ("Page%20abc/image.png", "local"),
        ("#section", "fragment"),
        ("", "empty"),
    ],
)
def test_url_classification(url, kind):
    assert classify_url(url)[0] == kind


def test_style_and_colour_validation():
    assert sanitize_style("color: red; position: fixed; background: url(x)") == "color: red"
    assert sanitize_style("width: expression(alert(1))") is None
    assert sanitize_style("color: rgb(1,2,3); -moz-binding: url(x)") == "color: rgb(1,2,3)"
    assert safe_color("rgba(212, 76, 71, 1)") == "rgba(212, 76, 71, 1)"
    assert safe_color("#ABCDEF") == "#ABCDEF"
    assert safe_color("red;background:url(x)") is None
    assert safe_color("expression(alert(1))") is None
    assert safe_color("inherit") is None


def test_media_src_must_be_a_flat_filename_or_web_url():
    assert is_safe_src("heart_0123456789ab.png")
    assert is_safe_src("https://example.org/x.png")
    assert not is_safe_src("../x.png")
    assert not is_safe_src("folder/x.png")
    assert not is_safe_src("javascript:alert(1)")
    assert not is_safe_src("mailto:x@example.org")


# Archive safety -----------------------------------------------------------------------

def _raw_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(zipfile.ZipInfo(name), data)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["../evil.html", "../../etc/passwd", "/abs/evil.html", "C:/evil.html", "a/../../evil.html", "a\x00b.html"])
def test_unsafe_member_names_are_rejected(name):
    assert normalise_path(name) is None


def test_path_traversal_entries_are_skipped_and_reported():
    page = notion_page(toggle("Q", "<p>A</p>")).encode()
    data = _raw_zip([("../evil.html", page), ("/abs/evil2.html", page), ("ok/page.html", page)])
    inputs = load_uploads([("export.zip", data)])
    assert [p.path for p in inputs.pages] == ["ok/page.html"]
    assert any(issue.code == "unsafe-paths" for issue in inputs.issues)


def test_symlink_entries_are_skipped():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        link = zipfile.ZipInfo("link.html")
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "/etc/passwd")
        archive.writestr("page.html", notion_page(toggle("Q", "<p>A</p>")))
    inputs = load_uploads([("export.zip", buffer.getvalue())])
    assert [p.path for p in inputs.pages] == ["page.html"]


def test_zip_bomb_member_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.html", b"<p>" + b"A" * (40 * 1024 * 1024))
        archive.writestr("page.html", notion_page(toggle("Q", "<p>A</p>")))
    inputs = load_uploads([("bomb.zip", buffer.getvalue())])
    assert [p.path for p in inputs.pages] == ["page.html"]
    assert any(issue.code == "suspicious-compression" for issue in inputs.issues)


def test_total_size_limit_is_enforced_while_reading():
    limits = LoaderLimits(max_total_bytes=50_000, max_compression_ratio=10_000)
    page = notion_page(toggle("Q", "<p>" + "x" * 100_000 + "</p>")).encode()
    inputs = load_uploads([("big.zip", make_zip({"page.html": page}))], limits)
    result = convert(inputs)
    assert result.cards == []
    assert result.pages[0].error and "zip bomb" in result.pages[0].error


def test_rereading_the_same_members_does_not_exhaust_the_budget():
    page = notion_page(toggle("Q", "<p>" + "x" * 30_000 + "</p>")).encode()
    limits = LoaderLimits(max_total_bytes=40_000, max_compression_ratio=10_000)
    inputs = load_uploads([("export.zip", make_zip({"page.html": page}))], limits)
    for _ in range(5):  # e.g. several conversions in one app session
        assert len(convert(inputs).cards) == 1


def test_too_many_entries_are_refused():
    limits = LoaderLimits(max_entries=5)
    data = make_zip({f"file{i}.html": b"<p>x</p>" for i in range(10)})
    inputs = load_uploads([("many.zip", data)], limits)
    assert inputs.pages == []
    assert any(issue.code == "too-many-files" for issue in inputs.issues)


def test_files_that_are_not_referenced_media_are_never_read():
    """Scripts or executables shipped in an archive are neither parsed, read nor exported."""
    page = notion_page(toggle(
        "Q",
        '<script src="evil.js"></script><img src="pic.png"/><a href="run.bat">run me</a>'
        '<link rel="import" href="evil.html"><iframe src="evil.html"></iframe>',
    ))
    data = make_zip({"page.html": page.encode(), "evil.js": b"alert(1)", "run.bat": b"del *", "pic.png": b"\x89PNG\r\n\x1a\n"})
    inputs = load_uploads([("export.zip", data)])
    reads: list[str] = []
    for entry in inputs.entries:
        original = entry._reader

        def spy(limit, _path=entry.path, _original=original):
            reads.append(_path)
            return _original(limit)

        entry._reader = spy
    result = convert(inputs)
    assert sorted(set(reads)) == ["page.html", "pic.png"]
    assert len(result.media) == 1
    assert_inert(result.cards[0].back)


def test_streamlit_config_binds_to_localhost_without_telemetry():
    from pathlib import Path
    import tomllib

    config = tomllib.loads((Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml").read_text())
    assert config["server"]["address"] == "127.0.0.1"
    assert config["browser"]["gatherUsageStats"] is False
