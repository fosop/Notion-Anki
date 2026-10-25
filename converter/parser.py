"""Parse one HTML page and locate its toggles.

Input *loading* lives in :mod:`converter.loader`, and toggle *detection* in
:mod:`converter.toggles`. This module ties them together for a single page:

* parse leniently (lxml, falling back to Python's ``html.parser``),
* find the page title and the content root,
* decide whether literal newlines are significant (Notion's stylesheet sets
  ``body { white-space: pre-wrap }``, so Shift+Enter line breaks are stored
  as plain newlines),
* read Notion's colour palette from the page's own ``<style>`` block,
* strip inert/active content (scripts, styles, templates) before detection.
"""

from __future__ import annotations

import posixpath
import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from .sanitize import safe_color
from .toggles import Toggle, find_toggles, looks_like_notion_app_dom

KIND_NOTION_EXPORT = "notion-export"
KIND_NOTION_APP = "notion-app"
KIND_GENERIC = "generic-html"

_NOTION_ID_SUFFIX = re.compile(r"\s+[0-9a-f]{32}$", re.IGNORECASE)
_PRE_WRAP_BODY = re.compile(r"(?:^|[\s,}])body\s*\{[^}]*white-space\s*:\s*pre-wrap", re.IGNORECASE)
_PALETTE_RULE = re.compile(r"\.((?:highlight|block-color|select-value-color)-[A-Za-z]+(?:_background)?)\s*\{([^{}]*)\}")
_DECLARATION = re.compile(r"(color|background-color|background)\s*:\s*([^;]+)", re.IGNORECASE)

# Notion's light-theme palette, used when a page has no stylesheet.
_TEXT_COLORS = {
    "gray": "rgba(120, 119, 116, 1)",
    "brown": "rgba(159, 107, 83, 1)",
    "orange": "rgba(217, 115, 13, 1)",
    "yellow": "rgba(203, 145, 47, 1)",
    "teal": "rgba(68, 131, 97, 1)",
    "green": "rgba(68, 131, 97, 1)",
    "blue": "rgba(51, 126, 169, 1)",
    "purple": "rgba(144, 101, 176, 1)",
    "pink": "rgba(193, 76, 138, 1)",
    "red": "rgba(212, 76, 71, 1)",
}
_BACKGROUND_COLORS = {
    "gray": "rgba(241, 241, 239, 1)",
    "brown": "rgba(244, 238, 238, 1)",
    "orange": "rgba(251, 236, 221, 1)",
    "yellow": "rgba(251, 243, 219, 1)",
    "teal": "rgba(237, 243, 236, 1)",
    "green": "rgba(237, 243, 236, 1)",
    "blue": "rgba(231, 243, 248, 1)",
    "purple": "rgba(244, 240, 247, 0.8)",
    "pink": "rgba(249, 238, 243, 0.8)",
    "red": "rgba(253, 235, 236, 1)",
}


def _default_palette() -> dict[str, dict[str, str]]:
    palette: dict[str, dict[str, str]] = {}
    for name, value in _TEXT_COLORS.items():
        palette[f"highlight-{name}"] = {"color": value}
        palette[f"block-color-{name}"] = {"color": value}
    for name, value in _BACKGROUND_COLORS.items():
        palette[f"highlight-{name}_background"] = {"background-color": value}
        palette[f"block-color-{name}_background"] = {"background-color": value}
        palette[f"select-value-color-{name}"] = {"background-color": value}
    return palette


DEFAULT_PALETTE = _default_palette()


@dataclass(eq=False)
class ParsedPage:
    path: str
    title: str
    soup: BeautifulSoup
    root: Tag
    toggles: list[Toggle]
    kind: str
    preserve_newlines: bool
    palette: dict[str, dict[str, str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def directory(self) -> str:
        return posixpath.dirname(self.path)


def parse_document(text: str) -> BeautifulSoup:
    """Parse HTML leniently. Never executes anything: this only builds a tree."""
    from bs4 import MarkupResemblesLocatorWarning, XMLParsedAsHTMLWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        try:
            return BeautifulSoup(text, "lxml")
        except Exception:  # lxml rejected the markup; the pure-Python parser is more forgiving
            return BeautifulSoup(text, "html.parser")


def title_from_path(path: str) -> str:
    """'Cardiology 1a2b…(32 hex).html' becomes 'Cardiology'."""
    stem = posixpath.splitext(posixpath.basename(path))[0]
    return _NOTION_ID_SUFFIX.sub("", stem).strip() or stem or "Untitled"


def parse_page(text: str, path: str) -> ParsedPage:
    soup = parse_document(text)
    title = _extract_title(soup, path)

    style_text = "\n".join(tag.get_text() for tag in soup.find_all("style"))
    preserve_newlines = bool(_PRE_WRAP_BODY.search(style_text))
    palette = dict(DEFAULT_PALETTE)
    palette.update(_palette_from_css(style_text))

    kind = _detect_kind(soup)
    root = _content_root(soup)

    # Remove active/inert content before anything else looks at the tree.
    for tag in root.find_all(["script", "style", "noscript", "template", "head"]):
        tag.decompose()
    # Notion's <header> holds the page title, icon, cover and database
    # properties: page metadata, never card content.
    for header in root.find_all("header"):
        if header.find(class_=re.compile(r"^page-(title|header-icon|cover-image|description)$")) or header.find(
            "table", class_="properties"
        ):
            header.decompose()

    toggles = find_toggles(root)
    notes: list[str] = []
    if not toggles and looks_like_notion_app_dom(soup):
        kind = KIND_NOTION_APP
        notes.append(
            "This page looks like it was saved from the Notion website/app ('Save page as…'), "
            "not exported. Use Notion's menu: ••• → Export → HTML."
        )
    return ParsedPage(path, title, soup, root, toggles, kind, preserve_newlines, palette, notes)


def _extract_title(soup: BeautifulSoup, path: str) -> str:
    for candidate in (soup.find(class_="page-title"), soup.find("title"), soup.find("h1")):
        if isinstance(candidate, Tag):
            text = " ".join(candidate.get_text(" ").split())
            if text:
                return text[:200]
    return title_from_path(path)


def _detect_kind(soup: BeautifulSoup) -> str:
    if soup.find("article", class_="page") or soup.find(class_="page-body"):
        return KIND_NOTION_EXPORT
    if looks_like_notion_app_dom(soup):
        return KIND_NOTION_APP
    return KIND_GENERIC


def _content_root(soup: BeautifulSoup) -> Tag:
    for candidate in (soup.find("article"), soup.find("body")):
        if isinstance(candidate, Tag):
            return candidate
    return soup


def _palette_from_css(css: str) -> dict[str, dict[str, str]]:
    """Read Notion colour classes from the page stylesheet (palettes differ between export versions)."""
    palette: dict[str, dict[str, str]] = {}
    for match in _PALETTE_RULE.finditer(css):
        name, body = match.group(1), match.group(2)
        if "-default" in name:
            continue  # "default" means no colour; forcing it would break Anki's night mode
        rule: dict[str, str] = {}
        for prop, value in _DECLARATION.findall(body):
            color = safe_color(value)
            if color is None:
                continue
            key = "color" if prop.lower() == "color" else "background-color"
            rule[key] = color
        if name.endswith("_background") or name.startswith("select-value-color"):
            rule = {k: v for k, v in rule.items() if k == "background-color"}
        else:
            rule = {k: v for k, v in rule.items() if k == "color"}
        if rule:
            palette[name] = rule
    return palette
