"""Toggle detection: the single place that knows what a toggle looks like in HTML.

Supported patterns (checked against real Notion "Export → HTML" output):

1. ``notion-toggle-list``: a Notion toggle-list block::

       <ul class="toggle"><li><details open=""><summary>TITLE</summary>
         …child blocks…
       </details></li></ul>

   Exports from 2025 onwards also wrap every block in
   ``<div style="display:contents" dir="auto">``. That wrapper does not affect
   detection. The ``<ul>`` may carry colour classes such as
   ``block-color-red_background``.

2. ``notion-toggle-heading``: a heading block with "toggle" switched on::

       <details open=""><summary style="font-weight:600;font-size:1.875em;…">TITLE</summary>
         <div class="indented">…child blocks…</div>
       </details>

   (``1.875em``/``1.5em``/``1.25em`` correspond to heading 1/2/3.)

3. ``html-details``: any other ``<details>`` with a ``<summary>``, e.g. HTML
   written by hand or by another tool.

A ``<details>`` without a summary is still treated as a toggle, so that its
children keep their place in the hierarchy. It has no usable title, though, so
the pipeline skips its own card and reports it.

Toggles are found anywhere in the page (inside lists, callouts, columns and
other toggles), at any nesting depth. Each toggle's parent is the nearest
enclosing toggle. Document order is preserved.

Not supported (they are diagnosed, not converted):

* Pages saved from the Notion website/app with "Save page as…". The app's DOM
  uses ``div.notion-toggle-block`` and leaves out the content of collapsed toggles.
* Notion's "Markdown & CSV" export, which contains no HTML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import Tag
from bs4.element import PageElement

PATTERN_TOGGLE_LIST = "notion-toggle-list"
PATTERN_TOGGLE_HEADING = "notion-toggle-heading"
PATTERN_HTML_DETAILS = "html-details"

_FONT_SIZE = re.compile(r"font-size\s*:\s*([0-9.]+)em", re.IGNORECASE)
_FONT_WEIGHT_BOLD = re.compile(r"font-weight\s*:\s*(?:bold|[6-9]00)", re.IGNORECASE)
_BLOCK_ID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.IGNORECASE)


@dataclass(eq=False)
class Toggle:
    """One recognised toggle and its place in the toggle hierarchy."""

    element: Tag
    """The ``<details>`` element."""
    summary: Tag | None
    """The element holding the title, or ``None`` when there is none."""
    body: list[PageElement]
    """Child nodes that form the answer (the summary is excluded)."""
    pattern: str
    wrapper: Tag
    """Outermost element of the block (``ul.toggle`` for toggle lists)."""
    heading_size: str | None = None
    """CSS font size of a toggle heading's title (e.g. ``"1.5em"``)."""
    parent: Toggle | None = None
    children: list[Toggle] = field(default_factory=list)
    depth: int = 0
    index: int = 0

    @property
    def classes(self) -> list[str]:
        """CSS classes of the block (wrapper first), e.g. Notion colour classes."""
        found: list[str] = []
        for tag in (self.wrapper, self.element):
            for cls in tag.get("class") or []:
                if cls not in found:
                    found.append(cls)
        return found

    @property
    def block_id(self) -> str | None:
        """Notion's block ID (a UUID in the block's ``id`` attribute), if the export has one.

        Newer exports put it on the ``<details>``, older ones on the ``ul.toggle`` wrapper.
        """
        for tag in (self.element, self.wrapper):
            value = tag.get("id")
            if isinstance(value, str) and _BLOCK_ID.fullmatch(value.strip()):
                return value.strip().replace("-", "").lower()
        return None

    def ancestors(self) -> list[Toggle]:
        chain: list[Toggle] = []
        node = self.parent
        while node is not None:
            chain.append(node)
            node = node.parent
        chain.reverse()
        return chain


def find_toggles(root: Tag) -> list[Toggle]:
    """Return every toggle below *root* in document order, linked to its parent."""
    toggles: list[Toggle] = []
    by_element: dict[int, Toggle] = {}
    for index, details in enumerate(root.find_all("details")):
        toggle = _match(details)
        toggle.index = index
        toggles.append(toggle)
        by_element[id(details)] = toggle

    for toggle in toggles:
        ancestor = toggle.element.parent
        while ancestor is not None:
            parent = by_element.get(id(ancestor))
            if parent is not None:
                toggle.parent = parent
                toggle.depth = parent.depth + 1
                parent.children.append(toggle)
                break
            ancestor = ancestor.parent
    return toggles


def _match(details: Tag) -> Toggle:
    summary = _find_summary(details)
    body = [child for child in details.contents if child is not summary]

    parent = details.parent
    grandparent = parent.parent if isinstance(parent, Tag) else None
    if (
        isinstance(parent, Tag)
        and parent.name == "li"
        and isinstance(grandparent, Tag)
        and grandparent.name in ("ul", "ol")
        and "toggle" in (grandparent.get("class") or [])
    ):
        return Toggle(details, summary, body, PATTERN_TOGGLE_LIST, wrapper=grandparent)

    heading_size = _heading_size(summary) if summary is not None else None
    if heading_size is not None:
        return Toggle(details, summary, body, PATTERN_TOGGLE_HEADING, wrapper=details, heading_size=heading_size)
    return Toggle(details, summary, body, PATTERN_HTML_DETAILS, wrapper=details)


def _find_summary(details: Tag) -> Tag | None:
    """The first ``<summary>`` child; tolerates malformed HTML where it is nested deeper."""
    for child in details.children:
        if isinstance(child, Tag) and child.name == "summary":
            return child
    for candidate in details.find_all("summary"):
        owner = candidate.find_parent("details")
        if owner is details:
            return candidate
    return None


def _heading_size(summary: Tag) -> str | None:
    style = summary.get("style") or ""
    if isinstance(style, list):  # defensive: some builders return lists
        style = ";".join(style)
    size = _FONT_SIZE.search(style)
    if size and _FONT_WEIGHT_BOLD.search(style):
        try:
            value = float(size.group(1))
        except ValueError:
            return None
        if 1.0 < value <= 3.0:
            return f"{value:g}em"
    heading = next((c for c in summary.children if isinstance(c, Tag)), None)
    if heading is not None and heading.name in ("h1", "h2", "h3"):
        return {"h1": "1.875em", "h2": "1.5em", "h3": "1.25em"}[heading.name]
    return None


def is_toggle_list_wrapper(tag: Tag) -> bool:
    """True for Notion's ``<ul class="toggle">`` wrapper, which is not a visual list."""
    return tag.name in ("ul", "ol") and "toggle" in (tag.get("class") or [])


def looks_like_notion_app_dom(root: Tag) -> bool:
    """True if the page was saved from the Notion app rather than exported."""
    return root.find(class_=re.compile(r"^notion-(toggle-block|page-content)$")) is not None


def pattern_counts(toggles: list[Toggle]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for toggle in toggles:
        counts[toggle.pattern] = counts.get(toggle.pattern, 0) + 1
    return counts
