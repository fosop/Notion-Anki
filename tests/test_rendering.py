"""Formatting preservation: rich text, lists, tables, equations, images, Unicode."""

from __future__ import annotations

import pytest

from converter.rendering import clean_tex
from helpers import card, codes, convert_fixture, convert_text, notion_page, toggle


@pytest.fixture(scope="module")
def rich():
    return convert_fixture("notion_rich.html")


def test_inline_formatting_is_preserved(rich):
    back = card(rich, "Text formatting").back
    assert "<strong>bold</strong>" in back
    assert "<em>italic</em>" in back
    assert "<u>underlined</u>" in back  # Notion's border-bottom underline
    assert "<del>struck</del>" in back
    assert "<code>inline code</code>" in back
    assert '<span style="color: rgba(212, 76, 71, 1)">red text</span>' in back
    assert '<span style="background-color: rgba(251, 243, 219, 1); color: rgb(55, 53, 47)">highlighted</span>' in back
    assert "default colour" in back and "rgba(55, 53, 47, 1)" not in back  # "default" colour is not forced
    assert "<mark" not in back  # Notion's <mark class=…> is a colour, not a yellow highlight
    assert "E = mc<sup>2</sup>, H<sub>2</sub>O" in back


def test_front_keeps_inline_formatting(rich):
    assert card(rich, "Text formatting").front == "<strong>Text</strong> formatting"


def test_line_breaks_paragraphs_and_headings(rich):
    back = card(rich, "Text formatting").back
    assert "<p>First line<br>Second line after a soft break</p>" in back
    assert "<h3>A heading inside the answer</h3>" in back
    # An empty Notion block (<p>\n</p>) stays a blank line; a truly empty <p></p> is dropped.
    assert "<h3>A heading inside the answer</h3><p><br></p><p>After a blank line</p>" in back


def test_links(rich):
    back = card(rich, "Text formatting").back
    assert '<a href="https://pubmed.ncbi.nlm.nih.gov/">PubMed</a>' in back
    assert "a linked Notion page" in back and "Other%20Page" not in back
    assert "internal-link" in codes(rich)


def test_unicode_symbols_are_not_corrupted(rich):
    back = card(rich, "Unicode symbols").back
    for text in (
        "α β γ δ Δ μ σ Ω", "→ ← ↑ ↓ ⇌ ⟶", "≥ ≤ ± ≠ ≈ × ÷ √ ∞", "5 µg/kg", "37 °C",
        "Na⁺ K⁺ Ca²⁺ Cl⁻", "CO₂", "½ ¼", "naïve café", "💊", "心臓",
    ):
        assert text in back, text
    assert "&amp; &lt;angle&gt;" in back  # markup characters stay escaped text


def test_lists_including_nesting_numbering_and_todos(rich):
    back = card(rich, "Lists").back
    assert back.startswith(
        "<ul><li>Level 1<ul><li>Level 2<ul><li>Level 3</li></ul></li></ul></li><li>Second top-level item</li></ul>"
    )
    assert '<ol start="1" type="1"><li>First step</li><li>Second step<ol start="1" type="a"><li>Sub-step a</li></ol></li></ol>' in back
    assert '<ul style="list-style-type: none; padding-left: 0.4em"><li>☑ <s>Revise murmurs</s></li><li>☐ Revise valves</li></ul>' in back


def test_tables(rich):
    back = card(rich, "Table").back
    assert back.startswith('<table border="1" cellpadding="4" style="border-collapse: collapse"><thead><tr><th>Drug</th><th>Dose</th></tr></thead>')
    assert "<td>1 mg IV q3–5 min</td>" in back
    assert '<td colspan="2">Amiodarone 300 mg after the 3rd shock</td>' in back
    assert "simple-table" not in back and " id=" not in back


def test_code_blocks_quotes_and_dividers(rich):
    back = card(rich, "Code and quote").back
    assert '<pre style="white-space: pre-wrap"><code>dose = weight_kg * 0.1<br>print(f"{dose} mg")</code></pre>' in back
    assert '<blockquote style="margin: 0.5em 0; padding-left: 0.9em; border-left: 3px solid rgba(128, 128, 128, 0.6)">Primum non nocere</blockquote>' in back
    assert back.endswith("<hr>")


def test_callouts_and_columns(rich):
    back = card(rich, "Callout and columns").back
    assert "💡" in back and "Remember: <strong>time is muscle</strong>" in back
    assert "background-color: rgba(241, 241, 239, 1)" in back
    assert "Left column" in back and "Right column" in back
    assert "display: flex; flex-wrap: wrap" in back


def test_equations_become_anki_mathjax(rich):
    back = card(rich, "Equations").back
    assert "<div>\\[CO = HR \\times SV\\]</div>" in back  # the TeX comment is removed
    assert "Inline: \\(p &lt; 0.05\\) is significant." in back
    assert "katex" not in back and "CO=HR×SV" not in back and "﻿" not in back


def test_clean_tex():
    assert clean_tex("a % comment\n+ b") == "a + b"
    assert clean_tex(r"100\% \\ x") == r"100\% \\ x"


def test_images_without_assets_are_reported(rich):
    back = card(rich, "Images").back
    assert "[Image not available: Lead II rhythm strip]" in back
    assert '<div style="font-size: 0.85em; opacity: 0.8">Lead II rhythm strip</div>' in back
    # Web images are kept as references (never downloaded) and flagged; Notion's self-link is dropped.
    assert '<div><img src="https://upload.wikimedia.org/heart.png" style="max-width: 100%; height: auto"></div>' in back
    assert {"media-not-uploaded", "external-image"} <= codes(rich)


def test_unsupported_media_is_reported_not_silently_removed(rich):
    item = card(rich, "Audio and files")
    assert "[Audio not available: heart sound.mp3]" in item.back
    assert "[Video not embedded: echo loop.mp4]" in item.back
    assert "[Attachment not embedded: guideline.pdf]" in item.back
    assert '<a href="https://www.escardio.org/">ESC Guidelines</a><br><small>European Society of Cardiology</small>' in item.back
    assert "favicon" not in item.back
    assert {"video-not-embedded", "attachment-not-embedded"} <= codes(rich)
    assert len(item.notes) >= 3


def test_bookmark_without_images():
    bookmark = (
        '<figure><a href="https://www.nice.org.uk/" class="bookmark source"><div class="bookmark-info">'
        '<div class="bookmark-text"><div class="bookmark-title">NICE</div></div>'
        '<div class="bookmark-href">https://www.nice.org.uk/</div></div></a></figure>'
    )
    back = convert_text(notion_page(toggle("Q", bookmark))).cards[0].back
    assert back == '<div><div><a href="https://www.nice.org.uk/">NICE</a></div></div>'


def test_notion_page_icons_header_and_metadata_are_not_content(rich):
    assert all("Rich Content" not in c.front for c in rich.cards)
    assert rich.pages[0].title == "Rich Content"


def test_whitespace_in_notion_pages():
    html = notion_page(toggle("Q", "<p><strong>Dose:</strong>\n<em>5 mg</em></p><p>a\n\nb</p>"))
    assert convert_text(html).cards[0].back == "<p><strong>Dose:</strong><br><em>5 mg</em></p><p>a<br><br>b</p>"


def test_whitespace_in_generic_html_collapses():
    html = "<html><body><details><summary>Q</summary>\n  <p>a\n   b\tc</p>\n</details></body></html>"
    assert convert_text(html).cards[0].back == "<p>a b c</p>"


def test_inline_style_formatting_from_other_tools():
    html = notion_page(toggle(
        "Q",
        '<p><span style="font-weight:700">B</span> <span style="font-style:italic">I</span> '
        '<span style="text-decoration: underline line-through">UL</span> '
        '<font color="#00aa00">green</font> <span style="color: red !important">red</span></p>',
    ))
    back = convert_text(html).cards[0].back
    assert "<b>B</b>" in back and "<i>I</i>" in back and "<u><s>UL</s></u>" in back
    assert '<span style="color: #00aa00">green</span>' in back
    assert '<span style="color: red">red</span>' in back


def test_rtl_text_gets_automatic_direction():
    back = convert_text(notion_page(toggle("Q", "<p>שלום עולם</p>"))).cards[0].back
    assert back == '<p dir="auto">שלום עולם</p>'


def test_title_blocks_are_flattened_to_one_line():
    html = notion_page('<details><summary><p>Line one</p><p>Line two</p></summary><p>A</p></details>')
    assert convert_text(html).cards[0].front == "Line one<br>Line two"
