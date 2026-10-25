"""Core toggle → flashcard rules."""

from __future__ import annotations

import pytest

from converter import NestedToggleMode
from converter.parser import parse_page
from converter.toggles import (
    PATTERN_HTML_DETAILS,
    PATTERN_TOGGLE_HEADING,
    PATTERN_TOGGLE_LIST,
    find_toggles,
    pattern_counts,
)
from helpers import card, codes, convert_fixture, convert_text, fronts, notion_page, read_fixture, toggle


# 1. A single toggle with a paragraph answer -----------------------------------

def test_single_toggle_with_paragraph_answer():
    result = convert_fixture("notion_basic.html")
    assert len(result.cards) == 1
    only = result.cards[0]
    assert only.front == "Question A"
    assert only.back == "<p>Answer A</p>"
    assert only.page_title == "Basic Toggle"
    assert result.pages_processed == 1
    assert result.skipped_untitled == 0
    assert not result.warnings


def test_content_outside_toggles_never_becomes_a_card():
    result = convert_fixture("notion_nested.html")
    everything = " ".join(c.front + c.back for c in result.cards)
    for outside in ("Cardiac physiology", "Overview text outside every toggle", "Nested Toggles"):
        assert outside not in everything
    basic = convert_fixture("notion_basic.html")
    assert fronts(basic) == ["Question A"]  # the heading and introduction are ignored


# 2. Multiple sibling toggles ----------------------------------------------------

def test_multiple_sibling_toggles_keep_document_order():
    html = notion_page("<h2>Intro</h2>" + "".join(toggle(f"Question {i}", f"<p>Answer {i}</p>") for i in range(1, 6)))
    result = convert_text(html)
    assert fronts(result) == [f"Question {i}" for i in range(1, 6)]
    assert [c.back for c in result.cards] == [f"<p>Answer {i}</p>" for i in range(1, 6)]
    assert [c.number for c in result.cards] == [1, 2, 3, 4, 5]
    assert all(c.depth == 0 and c.breadcrumbs == () for c in result.cards)


# 3. Nested toggles at three or more levels --------------------------------------

def test_every_nested_toggle_becomes_its_own_card():
    result = convert_fixture("notion_nested.html")
    assert fronts(result) == ["Toggle A", "Toggle B", "Toggle C", "Toggle D"]
    assert [c.depth for c in result.cards] == [0, 1, 2, 0]
    assert card(result, "Toggle C").breadcrumbs == ("Toggle A", "Toggle B")
    # A child's front is its own title only; parent titles are not prepended.
    assert card(result, "Toggle B").front == "Toggle B"
    assert card(result, "Toggle C").front == "Toggle C"


@pytest.mark.parametrize("depth", [4, 12, 40])
def test_nesting_depth_is_not_limited(depth):
    inner = ""
    for level in reversed(range(depth)):
        inner = toggle(f"Level {level}", f"<p>Answer {level}</p>{inner}")
    result = convert_text(notion_page(inner))
    assert fronts(result) == [f"Level {i}" for i in range(depth)]
    assert [c.depth for c in result.cards] == list(range(depth))
    assert result.cards[-1].back == f"<p>Answer {depth - 1}</p>"
    top = result.cards[0].back
    assert top.count("<details open>") == depth - 1
    assert f"Answer {depth - 1}" in top


# 4. Parent answers contain their expanded descendants ---------------------------

def test_parent_answer_expands_descendants_in_document_order():
    result = convert_fixture("notion_nested.html")
    back = card(result, "Toggle A").back
    expected_order = [
        "Paragraph 1", "Paragraph 2", "Toggle B", "Explanation B", "Toggle C",
        "Explanation C", "After C, still inside B", "After B, still inside A",
    ]
    positions = [back.index(text) for text in expected_order]
    assert positions == sorted(positions)
    assert back.count("<details open>") == 2
    assert "Toggle A" not in back  # the toggle's own title is not repeated on its back
    assert "Toggle D" not in back

    back_b = card(result, "Toggle B").back
    assert "Explanation C" in back_b and "After C, still inside B" in back_b
    assert "Paragraph 1" not in back_b
    assert card(result, "Toggle C").back == "<p>Explanation C</p>"


def test_expanded_nested_toggle_markup():
    result = convert_text(notion_page(toggle("Parent", "<p>Intro</p>" + toggle("Child", "<p>Child answer</p>"))))
    assert card(result, "Parent").back == (
        '<p>Intro</p><details open><summary>Child</summary>'
        '<div style="padding-left: 1.25em"><p>Child answer</p></div></details>'
    )


# 5. The alternative parent-answer setting ---------------------------------------

def test_titles_only_mode_shows_only_direct_child_titles():
    result = convert_fixture("notion_nested.html", nested_mode=NestedToggleMode.TITLES_ONLY)
    assert fronts(result) == ["Toggle A", "Toggle B", "Toggle C", "Toggle D"]
    back_a = card(result, "Toggle A").back
    assert "Toggle B" in back_a
    for hidden in ("Explanation B", "Toggle C", "Explanation C", "After C, still inside B"):
        assert hidden not in back_a
    assert back_a.index("Paragraph 2") < back_a.index("Toggle B") < back_a.index("After B, still inside A")
    assert "<div>▸ Toggle B</div>" in back_a
    back_b = card(result, "Toggle B").back
    assert "Toggle C" in back_b and "Explanation C" not in back_b
    assert card(result, "Toggle C").back == "<p>Explanation C</p>"


# 6. Nested toggles inside list structures ----------------------------------------

def test_toggles_inside_lists_keep_their_hierarchy():
    result = convert_fixture("notion_lists.html")
    assert fronts(result) == [
        "Causes of chest pain",
        "Acute coronary syndrome",
        "What does ABCDE stand for?",
        "Toggle inside a top-level bullet",
    ]
    assert card(result, "Acute coronary syndrome").breadcrumbs == ("Causes of chest pain",)
    assert card(result, "What does ABCDE stand for?").breadcrumbs == ("Causes of chest pain",)
    assert card(result, "Toggle inside a top-level bullet").depth == 0
    assert card(result, "Acute coronary syndrome").back == "<p>Troponin rise and ECG changes</p>"


def test_list_structure_is_preserved_around_nested_toggles():
    back = card(convert_fixture("notion_lists.html"), "Causes of chest pain").back
    # Notion writes one <ul>/<ol> per item; consecutive ones are merged into one list.
    assert back.startswith("<ul><li>Cardiac<details open><summary>Acute coronary syndrome</summary>")
    assert "<li>Pulmonary<ul><li>Pulmonary embolism</li><li>Pneumothorax</li></ul></li></ul>" in back
    assert '<ol start="1" type="1"><li>Assess ABCDE<details open><summary>What does ABCDE stand for?</summary>' in back
    assert back.endswith("<li>Obtain a 12-lead ECG</li></ol>")
    assert back.count("<ul>") == 2 and back.count("<ol") == 1


# 8. Empty answers and empty titles -------------------------------------------------

def test_untitled_toggles_are_skipped_and_reported():
    result = convert_fixture("notion_empty_duplicates.html")
    assert result.skipped_untitled == 4
    issue = next(i for i in result.issues if i.code == "untitled-toggles")
    assert issue.message.startswith("Skipped 4 toggle(s)")
    assert "Answer without a title" not in " ".join(c.back for c in result.cards)
    # The toggle nested inside an untitled toggle still becomes a card.
    nested = card(result, "Nested under an untitled toggle")
    assert nested.back == "<p>Nested answer</p>"


def test_empty_answers_are_kept_and_warned_about():
    result = convert_fixture("notion_empty_duplicates.html")
    empty = [c for c in result.cards if c.empty_answer]
    assert [c.front_text for c in empty] == ["Question with empty answer", "Question with blank paragraph answer"]
    assert all(c.back == "" for c in empty)
    assert result.empty_answers == 2
    flagged = {n for issue in result.issues if issue.code == "empty-answer" for n in issue.cards}
    assert flagged == {c.number for c in empty}


def test_empty_answers_can_be_skipped():
    result = convert_fixture("notion_empty_duplicates.html", skip_empty_answers=True)
    assert "Question with empty answer" not in fronts(result)
    assert result.skipped_empty == 2
    assert result.skipped_total == 6
    assert "skipped-empty" in codes(result)


def test_image_only_title_is_a_usable_title():
    result = convert_fixture("notion_empty_duplicates.html")
    image_card = next(c for c in result.cards if c.back == "<p>Image-only title</p>")
    assert '<img src="https://example.org/question.png"' in image_card.front
    assert image_card.front_text == "[image]"


# 9. Duplicate toggle titles ----------------------------------------------------------

def test_duplicate_titles_are_not_discarded():
    result = convert_fixture("notion_empty_duplicates.html")
    duplicates = [c for c in result.cards if c.front_text == "Duplicate question"]
    assert [c.back for c in duplicates] == ["<p>First answer</p>", "<p>Second answer</p>"]
    # Byte-for-byte identical toggles are distinct toggles too.
    identical = [c for c in result.cards if c.front_text == "Identical toggle"]
    assert len(identical) == 2
    assert identical[0].number != identical[1].number
    assert len(result.cards) == 8


def test_duplicate_fronts_come_with_anki_import_advice():
    result = convert_fixture("notion_empty_duplicates.html")
    issue = next(i for i in result.issues if i.code == "duplicate-fronts")
    expected = {c.number for c in result.cards if c.front_text in ("Duplicate question", "Identical toggle")}
    assert set(issue.cards) == expected and len(expected) == 4
    assert "Existing notes: Preserve" in issue.message


# Supported markup patterns -------------------------------------------------------

def test_detection_patterns_are_recognised():
    page = parse_page(read_fixture("notion_rich.html"), "Rich Content.html")
    counts = pattern_counts(page.toggles)
    assert counts[PATTERN_TOGGLE_HEADING] == 1
    assert counts[PATTERN_TOGGLE_LIST] == 11
    generic = parse_page(read_fixture("generic_details.html"), "generic.html")
    assert pattern_counts(generic.toggles) == {PATTERN_HTML_DETAILS: 3}


def test_parent_links_use_identity_not_equality():
    # Two structurally identical nested toggles must each get the right parent.
    inner = toggle("Same", "<p>x</p>")
    page = parse_page(notion_page(toggle("P1", inner) + toggle("P2", inner)), "p.html")
    toggles = find_toggles(page.root)
    parents = [t.parent.summary.get_text() if t.parent else None for t in toggles]
    assert parents == [None, "P1", None, "P2"]


def test_toggle_heading_becomes_a_card_with_its_indented_content():
    result = convert_fixture("notion_rich.html")
    heading = card(result, "Toggle heading question")
    assert heading.back.startswith("<p>Toggle heading answer</p><details open><summary>Toggle inside a toggle heading</summary>")
    assert "padding-left: 1.5em" not in heading.back  # the wrapper div.indented is unwrapped
    assert card(result, "Toggle inside a toggle heading").breadcrumbs == ("Toggle heading question",)


def test_nested_toggle_heading_keeps_heading_style_in_parent():
    heading = (
        '<details open=""><summary style="font-weight:600;font-size:1.5em;line-height:1.3;margin:0">Sub-heading</summary>'
        '<div class="indented"><p>Text</p></div></details>'
    )
    result = convert_text(notion_page(toggle("Outer", heading)))
    assert '<summary style="font-weight: 600; font-size: 1.5em">Sub-heading</summary>' in card(result, "Outer").back
    assert card(result, "Sub-heading").back == "<p>Text</p>"


def test_new_notion_export_format_with_display_contents_wrappers():
    result = convert_fixture("notion_new_export.html")
    assert fronts(result) == ["Which drug reverses heparin?", "Anatomy terms in Japanese", "心臓", "肺 (hai)"]
    assert card(result, "Which drug reverses heparin?").back == "<p>Protamine sulfate</p>"
    assert card(result, "心臓").front == "<strong>心臓</strong>"
    assert card(result, "心臓").back == "<p><strong>(</strong><em><strong>shinzō</strong></em><strong>):</strong> heart</p>"
    assert card(result, "肺 (hai)").front == "<strong>肺 (</strong><em><strong>hai</strong></em><strong>)</strong>"
    assert "display" not in card(result, "Anatomy terms in Japanese").back


def test_generic_html_details_and_whitespace():
    result = convert_fixture("generic_details.html")
    assert fronts(result) == [
        "What is the normal GFR?",
        "Which hormone acts on the collecting duct?",
        "Where is ADH produced?",
    ]
    # Pretty-printing whitespace collapses as in a browser; it is not turned into line breaks.
    assert card(result, "What is the normal GFR?").back == "<p>About <strong>125 mL/min</strong> in healthy adults.</p>"
    assert card(result, "Where is ADH produced?").depth == 1


def test_coloured_toggle_title_keeps_its_colour():
    result = convert_fixture("notion_rich.html")
    coloured = card(result, "Coloured toggle")
    assert coloured.front == (
        '<span style="background-color: rgba(231, 243, 248, 1); color: rgb(55, 53, 47)">Coloured toggle</span>'
    )
