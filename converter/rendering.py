"""Render toggle titles and answers as clean, portable, safe HTML for Anki.

The renderer walks the parsed source tree and builds a *new* tree out of
:class:`El` nodes, so no source markup is ever copied verbatim. The new tree is
serialised by :func:`to_html` through the allowlist in
:mod:`converter.sanitize`. Scripts, event handlers, unknown tags and unsafe
URLs can't get through, even if a code path here had a bug.

What is preserved, and how:

* paragraphs, headings, line breaks (Notion's soft line breaks are literal
  newlines under ``white-space: pre-wrap`` and become ``<br>``);
* bold/italic/underline/strike-through (Notion's underline is a
  ``border-bottom`` style), superscript/subscript, inline code, code blocks;
* ordered/unordered/to-do lists, including nesting and numbering (Notion
  writes each list item as its own list, so adjacent lists are merged);
* tables (with visible borders), block quotes, callouts, columns, dividers;
* text and background colours (from Notion's classes or inline styles);
* links (web and mailto only; links to other exported pages become text);
* equations: KaTeX's TeX source becomes MathJax ``\\(…\\)`` / ``\\[…\\]``,
  which Anki renders natively;
* images become flat media filenames, and local audio becomes ``[sound:…]`` tags;
* nested toggles become ``<details open>`` blocks, or only their titles.

Anything that cannot be kept (videos, attachments, embeds, missing files) is
reported as a note on the card instead of disappearing silently.
"""

from __future__ import annotations

import html
import posixpath
import re
from dataclasses import dataclass, field
from typing import Iterable, Union

from bs4 import NavigableString, Tag
from bs4.element import PageElement, PreformattedString

from .media import (
    AUDIO_EXTENSIONS,
    HTML_LINK_EXTENSIONS,
    IMAGE_EXTENSIONS,
    STATUS_EXTERNAL,
    STATUS_LOCAL,
    VIDEO_EXTENSIONS,
    MediaCollector,
    MediaResolution,
)
from .models import ConversionOptions, NestedToggleMode
from .parser import ParsedPage
from .sanitize import (
    OUTPUT_TAGS,
    URL_EMPTY,
    URL_FRAGMENT,
    URL_UNSAFE,
    URL_WEB,
    VOID_TAGS,
    classify_url,
    css_text,
    local_path,
    parse_style,
    safe_attribute,
    safe_color,
)
from .toggles import Toggle

# ---------------------------------------------------------------------------
# Output tree
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class El:
    """An output element. Attribute values are raw (unescaped) strings, or None for boolean attributes."""

    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)


Node = Union[El, str]

BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details",
    "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "html", "li", "main", "nav",
    "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "ul", "video", "audio", "iframe", "colgroup", "col", "search",
})

_DROP_SILENTLY = frozenset({
    "script", "style", "noscript", "template", "head", "title", "meta", "link", "base",
    "param", "source", "track", "colgroup", "col", "map", "area", "slot", "datalist",
})
_BLOCKS_AS_DIV = frozenset({
    "section", "article", "main", "aside", "header", "footer", "nav", "address",
    "hgroup", "center", "figure", "dialog", "search",
})
_KEPT_BLOCKS = frozenset({"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd"})
_INLINE_TAGS = {
    "b": "b", "strong": "strong", "i": "i", "em": "em", "u": "u", "ins": "ins",
    "s": "s", "strike": "s", "del": "del", "sup": "sup", "sub": "sub", "code": "code",
    "tt": "code", "kbd": "kbd", "samp": "samp", "var": "var", "small": "small",
    "abbr": "abbr", "q": "q", "cite": "cite", "dfn": "dfn", "bdi": "bdi", "bdo": "bdo",
    "ruby": "ruby", "rt": "rt", "rp": "rp", "mark": "mark", "wbr": "wbr",
}
_TABLE_PARTS = frozenset({"thead", "tbody", "tfoot", "tr", "th", "td", "caption"})
_FLATTEN_IN_TITLE = frozenset({"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"})
_PRUNABLE = frozenset({
    "b", "strong", "i", "em", "u", "ins", "s", "del", "mark", "span", "sup", "sub", "small",
    "code", "a", "q", "cite", "dfn", "var", "kbd", "samp", "abbr", "bdi", "bdo", "p", "div",
})
_DIRECTION_BLOCKS = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "blockquote", "summary", "dd", "dt"})

READABLE_TEXT = "rgb(55, 53, 47)"
IMAGE_STYLE = "max-width: 100%; height: auto"
ICON_STYLE = "height: 1.2em; width: auto; vertical-align: -0.2em"
NESTED_BODY_STYLE = "padding-left: 1.25em"
INDENT_STYLE = "padding-left: 1.5em"
QUOTE_STYLE = "margin: 0.5em 0; padding-left: 0.9em; border-left: 3px solid rgba(128, 128, 128, 0.6)"
CALLOUT_STYLE = (
    "display: flex; gap: 0.6em; padding: 0.6em 0.8em; margin: 0.5em 0; "
    "border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 6px"
)
COLUMNS_STYLE = "display: flex; flex-wrap: wrap; gap: 1em"
COLUMN_STYLE = "flex: 1 1 12em; min-width: 0"
CAPTION_STYLE = "font-size: 0.85em; opacity: 0.8"
PRE_STYLE = "white-space: pre-wrap"
TODO_LIST_STYLE = "list-style-type: none; padding-left: 0.4em"
TABLE_ATTRS = {"border": "1", "cellpadding": "4", "style": "border-collapse: collapse"}
TITLE_ONLY_MARKER = "▸ "  # "▸ "

_ONLY_WS = re.compile(r"[ \t\n\r\f]*")
_WS_RUN = re.compile(r"[ \t\n\r\f]+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_TEX_COMMENT = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
_TEX_ENCODING = re.compile(r"tex", re.IGNORECASE)
_PIXELS = re.compile(r"^\s*(\d{1,5})(?:\.\d+)?\s*px\s*$", re.IGNORECASE)
_RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-ﻼ]")


@dataclass(frozen=True)
class _Ctx:
    pre: bool = False
    link: bool = False


@dataclass
class RenderedCard:
    front_html: str
    back_html: str
    front_text: str
    front_empty: bool
    back_empty: bool
    media: tuple[str, ...]
    notes: tuple[tuple[str, str], ...]
    """``(code, message)`` pairs."""


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


class CardRenderer:
    """Renders the cards of one parsed page."""

    def __init__(self, page: ParsedPage, media: MediaCollector | None = None, options: ConversionOptions | None = None) -> None:
        self.page = page
        self.media = media
        self.options = options or ConversionOptions()
        self._toggles = {id(t.element): t for t in page.toggles}
        self._titles = {id(t.summary) for t in page.toggles if t.summary is not None}
        self._notes: dict[str, str] = {}
        self._media_used: list[str] = []

    # -- public API ---------------------------------------------------------

    def render(self, toggle: Toggle) -> RenderedCard:
        self._notes = {}
        self._media_used = []
        front = finalize(self.title_nodes(toggle))
        back = finalize(self.answer_nodes(toggle))
        return RenderedCard(
            front_html=to_html(front),
            back_html=to_html(back),
            front_text=plain_text(front),
            front_empty=not has_content(front),
            back_empty=not has_content(back),
            media=tuple(dict.fromkeys(self._media_used)),
            notes=tuple((code, message) for message, code in self._notes.items()),
        )

    def title_is_usable(self, toggle: Toggle) -> bool:
        saved = self._notes, self._media_used
        try:
            return has_content(self.title_nodes(toggle))
        finally:
            self._notes, self._media_used = saved

    def title_nodes(self, toggle: Toggle) -> list[Node]:
        if toggle.summary is None:
            return []
        nodes = _trim(_flatten_blocks(self._children(toggle.summary, _Ctx())))
        css = self._class_css(toggle.classes)
        if css and has_content(nodes):
            nodes = [El("span", {"style": css_text(css)}, nodes)]
        return nodes

    def answer_nodes(self, toggle: Toggle) -> list[Node]:
        nodes: list[Node] = []
        for child in _unwrap_body(toggle.body):
            nodes.extend(self._node(child, _Ctx()))
        return _trim(nodes)

    # -- dispatch -----------------------------------------------------------

    def _node(self, node: PageElement, ctx: _Ctx) -> list[Node]:
        if isinstance(node, PreformattedString):  # comments, CDATA, doctype, processing instructions
            return []
        if isinstance(node, NavigableString):
            return self._text(node, ctx)
        if not isinstance(node, Tag):
            return []
        key = id(node)
        if key in self._titles:
            return []  # a toggle's own title is rendered separately
        toggle = self._toggles.get(key)
        if toggle is not None:
            return self._nested_toggle(toggle)
        name = (node.name or "").lower()
        classes = node.get("class") or []
        special = self._notion_block(node, name, classes, ctx)
        if special is not None:
            return special
        return self._element(node, name, classes, ctx)

    def _children(self, node: Tag, ctx: _Ctx) -> list[Node]:
        out: list[Node] = []
        for child in list(node.children):
            out.extend(self._node(child, ctx))
        return out

    def _notion_block(self, node: Tag, name: str, classes: list[str], ctx: _Ctx) -> list[Node] | None:
        """Notion-specific blocks, recognised by their export classes."""
        if not classes and name not in ("div", "figure"):
            return None
        if name in ("ul", "ol") and "toggle" in classes:
            return self._toggle_list(node, ctx)
        if "notion-text-equation-token" in classes:
            return self._equation(node, block=False)
        if "katex-display" in classes or (name == "figure" and "equation" in classes):
            return self._equation(node, block=True)
        if "katex" in classes:
            return self._equation(node, block=False)
        if name == "figure" and "callout" in classes:
            return self._callout(node, classes, ctx)
        if "column-list" in classes:
            return [El("div", {"style": COLUMNS_STYLE}, self._children(node, ctx))]
        if name == "div" and "column" in classes:
            return [El("div", {"style": COLUMN_STYLE}, self._children(node, ctx))]
        if name == "div" and "indented" in classes:
            children = self._children(node, ctx)
            return [El("div", {"style": INDENT_STYLE}, children)] if children else []
        if "checkbox" in classes and name in ("div", "span"):
            return ["☑ " if "checkbox-on" in classes else "☐ "]
        if "to-do-children-checked" in classes:
            return [El("s", {}, self._children(node, ctx))]
        if name == "a" and "bookmark" in classes:
            return self._bookmark(node)
        if name == "figure" and "link-to-page" in classes:
            return [El("div", {}, self._children(node, ctx))]
        if name == "figure" and node.find(class_="source") is not None and node.find("img") is None:
            return self._source_figure(node, ctx)
        if name == "figure" and ("image" in classes or node.find("img") is not None):
            return self._image_figure(node, ctx)
        if name == "div" and _is_display_contents(node):
            return self._children(node, ctx)
        if name == "img" and "icon" in classes:
            return self._image(node, icon=True)
        return None

    def _element(self, node: Tag, name: str, classes: list[str], ctx: _Ctx) -> list[Node]:
        if name in _DROP_SILENTLY:
            return []
        if name == "br":
            return [El("br")]
        if name == "hr":
            return [El("hr")]
        if name == "img":
            return self._image(node)
        if name == "a":
            return self._link(node, ctx)
        if name == "audio":
            return self._audio(node)
        if name == "video":
            return self._video(node)
        if name in ("iframe", "frame", "frameset"):
            return self._iframe(node)
        if name in ("object", "embed", "applet", "canvas", "portal"):
            self._note("embed-removed", f"An embedded <{name}> element was removed; Anki cards cannot show it.")
            return []
        if name == "svg":
            if not _inside_icon(node):
                self._note("svg-removed", "An inline SVG graphic was removed (export it as an image file instead).")
            return []
        if name == "math":
            return self._equation(node, block=(node.get("display") or "").lower() == "block")
        if name == "input":
            return self._input(node)
        if name == "pre":
            return self._pre(node, ctx)
        if name == "table":
            return self._table(node, ctx)
        if name in _TABLE_PARTS:
            return self._table_part(node, name, ctx)
        if name in ("ul", "ol"):
            return self._list(node, name, classes, ctx)
        if name == "li":
            return [self._list_item(node, ctx)]
        if name == "blockquote":
            return self._block(node, "blockquote", ctx, extra_style=QUOTE_STYLE)
        if name == "figcaption":
            return self._block(node, "div", ctx, extra_style=CAPTION_STYLE)
        if name == "details":  # a <details> that is not a recognised toggle (defensive)
            return [El("details", {"open": None}, self._children(node, ctx))]
        if name == "summary":
            return self._block(node, "div", ctx)
        if name == "p":
            return self._paragraph(node, ctx)
        if name in _KEPT_BLOCKS:
            return self._block(node, name, ctx)
        if name in _BLOCKS_AS_DIV:
            return self._block(node, "div", ctx)
        if name in _INLINE_TAGS:
            return self._inline(node, _INLINE_TAGS[name], ctx)
        # <span>, <font>, form controls and unknown tags: keep content and formatting only.
        return self._formatted_inline(node, ctx)

    # -- text -------------------------------------------------------------------

    def _text(self, node: NavigableString, ctx: _Ctx) -> list[Node]:
        text = str(node)
        if "\r" in text:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("﻿", "")
        if not text:
            return []
        if ctx.pre:
            return _lines(text)
        if _ONLY_WS.fullmatch(text):
            if _touches_block(node):
                return []  # formatting whitespace between blocks
            if self.page.preserve_newlines and "\n" in text:
                return [El("br") for _ in range(text.count("\n"))]
            return [" "]
        if self.page.preserve_newlines:
            return _lines(text)
        return [_WS_RUN.sub(" ", text)]

    # -- blocks -----------------------------------------------------------------

    def _block(self, node: Tag, tag: str, ctx: _Ctx, extra_style: str | None = None) -> list[Node]:
        css, wrappers = self._formatting(node, block=True)
        attrs: dict[str, str | None] = {}
        style = "; ".join(s for s in (extra_style, css_text(css) if css else None) if s)
        if style:
            attrs["style"] = style
        direction = _direction(node)
        if direction:
            attrs["dir"] = direction
        return [El(tag, attrs, _wrap(self._children(node, ctx), wrappers))]

    def _paragraph(self, node: Tag, ctx: _Ctx) -> list[Node]:
        result = self._block(node, "p", ctx)
        paragraph = result[0]
        if isinstance(paragraph, El) and not paragraph.children and self.page.preserve_newlines and "\n" in node.get_text():
            paragraph.children = [El("br")]  # Notion's empty line: <p>\n</p>
        return result

    def _inline(self, node: Tag, tag: str, ctx: _Ctx) -> list[Node]:
        css, wrappers = self._formatting(node, block=False)
        children = self._children(node, ctx)
        if tag == "wbr":
            return [El("wbr")]
        attrs: dict[str, str | None] = {}
        if tag == "abbr" and node.get("title"):
            attrs["title"] = str(node.get("title"))
        if tag == "bdo" and _direction(node):
            attrs["dir"] = _direction(node)
        if tag == "mark" and (css or any(str(c).startswith("highlight-") for c in node.get("class") or [])):
            # Notion uses <mark class="highlight-…"> for every colour (including "default",
            # meaning none), so it must not become a yellow <mark> highlight in Anki.
            return _wrap_inline(children, css, wrappers)
        return _wrap_inline([El(tag, attrs, children)] if children else [], css, wrappers)

    def _formatted_inline(self, node: Tag, ctx: _Ctx) -> list[Node]:
        css, wrappers = self._formatting(node, block=False)
        if node.name == "font":
            color = safe_color(node.get("color"))
            if color:
                css.setdefault("color", color)
        return _wrap_inline(self._children(node, ctx), css, wrappers)

    def _formatting(self, node: Tag, block: bool) -> tuple[dict[str, str], list[str]]:
        """Colours from Notion classes and inline styles; bold/italic/underline as semantic tags."""
        css = self._class_css(node.get("class") or [])
        wrappers: list[str] = []
        style = parse_style(node.get("style"))
        if not style:
            return css, wrappers
        color = safe_color(style.get("color"))
        if color:
            css["color"] = color
        background = safe_color(style.get("background-color") or style.get("background"))
        if background:
            css["background-color"] = background
        name = node.name or ""
        weight = style.get("font-weight", "").lower()
        if (weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600)) and name not in (
            "b", "strong", "th", "h1", "h2", "h3", "h4", "h5", "h6",
        ):
            wrappers.append("b")
        if style.get("font-style", "").lower() in ("italic", "oblique") and name not in ("i", "em"):
            wrappers.append("i")
        decoration = f"{style.get('text-decoration', '')} {style.get('text-decoration-line', '')}".lower()
        if "underline" in decoration and name != "u":
            wrappers.append("u")
        if "line-through" in decoration and name not in ("s", "del", "strike"):
            wrappers.append("s")
        if name == "span" and "solid" in style.get("border-bottom", "").lower() and "u" not in wrappers:
            wrappers.append("u")  # Notion exports underlined text as a bottom border
        if block:
            align = style.get("text-align", "").lower()
            if align in ("center", "right", "justify"):
                css["text-align"] = align
        return css, wrappers

    def _class_css(self, classes: Iterable[str]) -> dict[str, str]:
        css: dict[str, str] = {}
        for cls in classes:
            rule = self.page.palette.get(cls)
            if rule:
                css.update(rule)
        if "background-color" in css and "color" not in css:
            # Notion's pastel backgrounds need dark text to stay readable in Anki's night mode.
            css["color"] = READABLE_TEXT
        return css

    def _list(self, node: Tag, name: str, classes: list[str], ctx: _Ctx) -> list[Node]:
        css, _ = self._formatting(node, block=True)
        attrs: dict[str, str | None] = {}
        styles = [TODO_LIST_STYLE] if "to-do-list" in classes else []
        if css:
            styles.append(css_text(css))
        if styles:
            attrs["style"] = "; ".join(styles)
        if name == "ol":
            start = _int_attr(node.get("start"), 0, 1_000_000)
            if start is not None:
                attrs["start"] = str(start)
            list_type = node.get("type")
            if list_type in ("1", "a", "A", "i", "I"):
                attrs["type"] = str(list_type)
        items: list[Node] = []
        for child in list(node.children):
            if isinstance(child, Tag) and child.name == "li":
                items.append(self._list_item(child, ctx))
            else:
                rendered = self._node(child, ctx)
                if has_content(rendered):  # stray content directly inside a list (malformed HTML)
                    items.append(El("li", {"style": "list-style-type: none"}, rendered))
        return [El(name, attrs, items)] if items else []

    def _list_item(self, node: Tag, ctx: _Ctx) -> El:
        css, wrappers = self._formatting(node, block=True)
        attrs: dict[str, str | None] = {"style": css_text(css)} if css else {}
        direction = _direction(node)
        if direction:
            attrs["dir"] = direction
        return El("li", attrs, _wrap(self._children(node, ctx), wrappers))

    def _table(self, node: Tag, ctx: _Ctx) -> list[Node]:
        parts = [child for child in self._children(node, ctx) if isinstance(child, El)]
        return [El("table", dict(TABLE_ATTRS), parts)] if parts else []

    def _table_part(self, node: Tag, name: str, ctx: _Ctx) -> list[Node]:
        css, wrappers = self._formatting(node, block=True)
        attrs: dict[str, str | None] = {"style": css_text(css)} if css else {}
        if name in ("td", "th"):
            for attribute in ("colspan", "rowspan"):
                value = _int_attr(node.get(attribute), 1, 1000)
                if value and value > 1:
                    attrs[attribute] = str(value)
        children = _wrap(self._children(node, ctx), wrappers)
        if name in ("thead", "tbody", "tfoot", "tr"):
            children = [child for child in children if isinstance(child, El)]
        return [El(name, attrs, children)]

    def _pre(self, node: Tag, ctx: _Ctx) -> list[Node]:
        inner = _trim_breaks(self._children(node, _Ctx(pre=True, link=ctx.link)))
        return [El("pre", {"style": PRE_STYLE}, inner)] if inner else []

    def _toggle_list(self, node: Tag, ctx: _Ctx) -> list[Node]:
        """``<ul class="toggle">`` is a container, not a visual list: render its items' content only."""
        out: list[Node] = []
        for child in list(node.children):
            if isinstance(child, Tag) and child.name == "li":
                out.extend(self._children(child, ctx))
            else:
                out.extend(self._node(child, ctx))
        return out

    def _nested_toggle(self, toggle: Toggle) -> list[Node]:
        title = self.title_nodes(toggle)
        titled = has_content(title)
        if self.options.nested_mode is NestedToggleMode.TITLES_ONLY:
            return [El("div", {}, [TITLE_ONLY_MARKER, *title])] if titled else []
        body = self.answer_nodes(toggle)
        if not titled:
            return [El("div", {"style": NESTED_BODY_STYLE}, body)] if has_content(body) else []
        summary_attrs: dict[str, str | None] = {}
        if toggle.heading_size:
            summary_attrs["style"] = f"font-weight: 600; font-size: {toggle.heading_size}"
        children: list[Node] = [El("summary", summary_attrs, title)]
        if body:
            children.append(El("div", {"style": NESTED_BODY_STYLE}, body))
        return [El("details", {"open": None}, children)]

    def _callout(self, node: Tag, classes: list[str], ctx: _Ctx) -> list[Node]:
        icon: list[Node] = []
        body: list[Node] = []
        found_icon = False
        for child in list(node.children):
            if not found_icon and isinstance(child, Tag) and _is_callout_icon(child):
                icon = self._image(child, icon=True) if child.name == "img" else self._children(child, ctx)
                found_icon = True
                continue
            body.extend(self._node(child, ctx))
        css = self._class_css(classes)
        style = CALLOUT_STYLE + (f"; {css_text(css)}" if css else "")
        parts: list[Node] = []
        if has_content(icon):
            parts.append(El("div", {"style": "flex: 0 0 auto"}, icon))
        parts.append(El("div", {"style": "flex: 1 1 auto; min-width: 0"}, _trim(body)))
        return [El("div", {"style": style}, parts)]

    def _image_figure(self, node: Tag, ctx: _Ctx) -> list[Node]:
        content: list[Node] = []
        caption: list[Node] = []
        for child in list(node.children):
            if isinstance(child, Tag) and child.name == "figcaption":
                caption = self._children(child, ctx)
            else:
                content.extend(self._node(child, ctx))
        out: list[Node] = [El("div", {}, content)] if content else []
        if has_content(caption):
            out.append(El("div", {"style": CAPTION_STYLE}, caption))
        return out

    def _source_figure(self, node: Tag, ctx: _Ctx) -> list[Node]:
        """Notion file/audio/video/PDF/embed blocks: ``<figure><div class="source"><a href>…``."""
        caption_tag = node.find("figcaption")
        caption = self._children(caption_tag, ctx) if isinstance(caption_tag, Tag) else []
        audio = node.find("audio")
        video = node.find("video")
        link = node.find("a", href=True)
        if isinstance(audio, Tag):
            main = self._audio(audio)
        elif isinstance(video, Tag):
            main = self._video(video)
        elif isinstance(link, Tag) and "bookmark" in (link.get("class") or []):
            main = self._bookmark(link)
        elif isinstance(link, Tag):
            main = self._link(link, ctx, block=True)
        else:
            main = []
            for child in list(node.children):
                if child is not caption_tag:
                    main.extend(self._node(child, ctx))
        out: list[Node] = [El("div", {}, main)] if main else []
        if has_content(caption):
            out.append(El("div", {"style": CAPTION_STYLE}, caption))
        return out

    def _bookmark(self, node: Tag) -> list[Node]:
        kind, url = classify_url(node.get("href"))
        title = _text_of(node.find(class_="bookmark-title")) or (url if kind == URL_WEB else "")
        description = _text_of(node.find(class_="bookmark-description"))
        if kind != URL_WEB:
            return [El("div", {}, [title])] if title else []
        parts: list[Node] = [El("a", {"href": url}, [title or url])]
        if description:
            parts += [El("br"), El("small", {}, [description[:300]])]
        return [El("div", {}, parts)]

    # -- equations ---------------------------------------------------------------

    def _equation(self, node: Tag, block: bool) -> list[Node]:
        tex = _tex_source(node)
        if tex is None:
            math = node.find("math")
            text = _text_of(math if isinstance(math, Tag) else node)
            if not text:
                return []
            self._note("equation-as-text", "An equation without TeX source was kept as plain text.")
            return [El("div", {}, [text])] if block else [text]
        if block:
            return [El("div", {}, [f"\\[{tex}\\]"])]
        return [f"\\({tex}\\)"]

    # -- links and media -----------------------------------------------------------

    def _link(self, node: Tag, ctx: _Ctx, block: bool = False) -> list[Node]:
        children = self._children(node, _Ctx(pre=ctx.pre, link=True))
        if ctx.link:
            return children  # never nest links
        kind, value = classify_url(node.get("href"))
        image = node.find("img")
        if isinstance(image, Tag) and image.get("src") == node.get("href") and not node.get_text(strip=True):
            return children  # Notion wraps every image in a link to the image itself
        if kind == URL_WEB:
            attrs: dict[str, str | None] = {"href": value}
            title = node.get("title")
            if title:
                attrs["title"] = str(title)
            return [El("a", attrs, children if has_content(children) else [value])]
        if kind in (URL_EMPTY, URL_FRAGMENT):
            return children
        if kind == URL_UNSAFE:
            self._note("unsafe-link", "A link with an unsafe or local-only address (e.g. javascript: or file:) was removed; its text was kept.")
            return children
        path = local_path(value)
        name = posixpath.basename(path) or path
        ext = posixpath.splitext(path)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            return self._sound(value, plain_text(children))
        if ext in VIDEO_EXTENSIONS:
            self._note("video-not-embedded", f"Video '{name}' was not embedded (Anki text imports support images and audio only).")
            return [El("i", {}, [f"[Video not embedded: {name}]"])]
        if ext in HTML_LINK_EXTENSIONS:
            self._note("internal-link", "Links to other Notion pages were kept as plain text.")
            return children
        if ext in IMAGE_EXTENSIONS and node.find("img") is not None:
            return children  # Notion wraps every image in a link to itself
        if block or ext:
            self._note("attachment-not-embedded", f"Attachment '{name}' was not embedded; only images and audio can be added to Anki cards.")
            if block:
                return [El("i", {}, [f"[Attachment not embedded: {name}]"])]
        return children

    def _resolve(self, ref: str | None, kind: str) -> MediaResolution:
        if self.media is None:
            label = posixpath.basename(local_path(ref or "")) or "file"
            return MediaResolution("missing", display_name=label, code="media-not-uploaded",
                                   message=f"'{label}' was not looked up (no media source available).")
        return self.media.resolve(ref, self.page.path, kind)

    def _image(self, node: Tag, icon: bool = False) -> list[Node]:
        src = node.get("src") or node.get("data-src") or ""
        alt = " ".join(str(node.get("alt") or "").split())[:500]
        resolution = self._resolve(str(src), "image")
        if resolution.status == STATUS_LOCAL and resolution.filename:
            self._media_used.append(resolution.filename)
            if resolution.message:
                self._note(resolution.code, resolution.message)
            attrs: dict[str, str | None] = {"src": resolution.filename}
            if alt:
                attrs["alt"] = alt
            width = None if icon else _image_width(node)
            if width:
                attrs["width"] = str(width)
            attrs["style"] = ICON_STYLE if icon else IMAGE_STYLE
            return [El("img", attrs)]
        if resolution.status == STATUS_EXTERNAL and resolution.url:
            if self.options.keep_external_images:
                self._note("external-image", f"An image is linked from the web ({resolution.display_name}) and only shows while online. It is never downloaded by this app.")
                attrs = {"src": resolution.url, "style": ICON_STYLE if icon else IMAGE_STYLE}
                if alt:
                    attrs["alt"] = alt
                return [El("img", attrs)]
            self._note("external-image", f"A web image ({resolution.display_name}) was replaced by a link.")
            return [El("a", {"href": resolution.url}, [alt or "External image"])]
        if icon:
            return []
        self._note(resolution.code or "media-missing", resolution.message or "An image could not be included.")
        label = alt or resolution.display_name or "image"
        return [El("i", {}, [f"[Image not available: {label}]"])]

    def _audio(self, node: Tag) -> list[Node]:
        src = node.get("src")
        if not src:
            source = node.find("source", src=True)
            src = source.get("src") if isinstance(source, Tag) else None
        if not src:
            return []
        return self._sound(str(src), "")

    def _sound(self, ref: str, label: str) -> list[Node]:
        resolution = self._resolve(ref, "audio")
        if resolution.status == STATUS_LOCAL and resolution.filename:
            self._media_used.append(resolution.filename)
            return [f"[sound:{resolution.filename}]"]
        if resolution.status == STATUS_EXTERNAL and resolution.url:
            self._note("external-audio", f"Audio linked from the web ({resolution.display_name}) was kept as a link.")
            return [El("a", {"href": resolution.url}, [label or resolution.display_name or "Audio"])]
        self._note(resolution.code or "media-missing", resolution.message or "An audio file could not be included.")
        return [El("i", {}, [f"[Audio not available: {resolution.display_name or label or 'audio'}]"])]

    def _video(self, node: Tag) -> list[Node]:
        src = node.get("src")
        if not src:
            source = node.find("source", src=True)
            src = source.get("src") if isinstance(source, Tag) else None
        kind, value = classify_url(str(src or ""))
        if kind == URL_WEB:
            self._note("video-not-embedded", "A web video was replaced by a link.")
            return [El("a", {"href": value}, [value])]
        name = posixpath.basename(local_path(str(src or ""))) or "video"
        self._note("video-not-embedded", f"Video '{name}' was not embedded (Anki text imports support images and audio only).")
        return [El("i", {}, [f"[Video not embedded: {name}]"])]

    def _iframe(self, node: Tag) -> list[Node]:
        kind, value = classify_url(node.get("src"))
        if kind == URL_WEB:
            self._note("embed-as-link", "An embedded frame was replaced by a link.")
            return [El("div", {}, [El("a", {"href": value}, [value])])]
        self._note("embed-removed", "An embedded frame was removed; Anki cards cannot show it.")
        return []

    def _input(self, node: Tag) -> list[Node]:
        if str(node.get("type") or "").lower() == "checkbox":
            return ["☑ " if node.has_attr("checked") else "☐ "]
        return []

    def _note(self, code: str, message: str) -> None:
        self._notes.setdefault(message, code)


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------


def _is_insignificant(node: PageElement) -> bool:
    if isinstance(node, PreformattedString):
        return True
    return isinstance(node, NavigableString) and _ONLY_WS.fullmatch(str(node)) is not None


def _unwrap_body(body: list[PageElement]) -> list[PageElement]:
    """Unwrap a lone ``div.indented`` (toggle headings) or display:contents wrapper."""
    nodes = list(body)
    while True:
        significant = [n for n in nodes if not _is_insignificant(n)]
        if len(significant) == 1 and isinstance(significant[0], Tag) and significant[0].name == "div":
            tag = significant[0]
            if "indented" in (tag.get("class") or []) or _is_display_contents(tag):
                nodes = list(tag.contents)
                continue
        return nodes


def _is_display_contents(tag: Tag) -> bool:
    return "display:contents" in str(tag.get("style") or "").replace(" ", "").lower()


def _is_callout_icon(tag: Tag) -> bool:
    if "icon" in (tag.get("class") or []):
        return True
    return tag.find(class_="icon") is not None and len(tag.get_text(strip=True)) <= 8


def _inside_icon(node: Tag) -> bool:
    parent = node.parent
    for _ in range(4):
        if not isinstance(parent, Tag):
            return False
        joined = " ".join(parent.get("class") or [])
        if "icon" in joined or "checkbox" in joined or "bookmark" in joined:
            return True
        parent = parent.parent
    return False


def _touches_block(node: NavigableString) -> bool:
    """True for whitespace that only separates block elements (insignificant)."""
    parent = node.parent
    parent_is_block = not isinstance(parent, Tag) or parent.name in BLOCK_TAGS or parent.name == "[document]"
    previous, following = node.previous_sibling, node.next_sibling
    if parent_is_block and (previous is None or following is None):
        return True
    return _is_block(previous) or _is_block(following)


def _is_block(node: PageElement | None) -> bool:
    return isinstance(node, Tag) and node.name in BLOCK_TAGS


def _lines(text: str) -> list[Node]:
    out: list[Node] = []
    for index, part in enumerate(text.split("\n")):
        if index:
            out.append(El("br"))
        if part:
            out.append(part)
    return out


def _wrap(nodes: list[Node], wrappers: list[str]) -> list[Node]:
    for tag in reversed(wrappers):
        nodes = [El(tag, {}, nodes)] if nodes else nodes
    return nodes


def _wrap_inline(nodes: list[Node], css: dict[str, str], wrappers: list[str]) -> list[Node]:
    if not nodes:
        return []
    nodes = _wrap(nodes, wrappers)
    if css:
        nodes = [El("span", {"style": css_text(css)}, nodes)]
    return nodes


def _flatten_blocks(nodes: list[Node]) -> list[Node]:
    """Titles are single-line: unwrap paragraph-like blocks, separating them with <br>."""
    out: list[Node] = []
    pending_break = False
    for node in nodes:
        if isinstance(node, El) and node.tag in _FLATTEN_IN_TITLE:
            inner = _flatten_blocks(node.children)
            if not has_content(inner):
                continue
            style = node.attrs.get("style")
            if style:
                inner = [El("span", {"style": style}, inner)]
            if any(_is_meaningful(item) for item in out):
                out.append(El("br"))
            out.extend(inner)
            pending_break = True
            continue
        if pending_break and _is_meaningful(node):
            out.append(El("br"))
            pending_break = False
        out.append(node)
    return out


def _is_meaningful(node: Node) -> bool:
    return isinstance(node, El) or bool(node.strip())


def _trim_breaks(nodes: list[Node]) -> list[Node]:
    nodes = list(nodes)
    while nodes and isinstance(nodes[-1], El) and nodes[-1].tag == "br":
        nodes.pop()
    if len(nodes) == 1 and isinstance(nodes[0], El) and nodes[0].tag == "code":
        nodes[0].children = _trim_breaks(nodes[0].children)
    return nodes


def _is_trimmable(node: Node) -> bool:
    if isinstance(node, str):
        return _ONLY_WS.fullmatch(node) is not None
    if node.tag == "br":
        return True
    return node.tag in ("p", "div") and not has_content(node.children)


def _trim(nodes: list[Node]) -> list[Node]:
    """Drop leading/trailing whitespace, line breaks and blank paragraphs."""
    nodes = list(nodes)
    while nodes and _is_trimmable(nodes[0]):
        nodes.pop(0)
    while nodes and _is_trimmable(nodes[-1]):
        nodes.pop()
    if nodes and isinstance(nodes[0], str):
        nodes[0] = nodes[0].lstrip(" \t\n\r\f")
    if nodes and isinstance(nodes[-1], str):
        nodes[-1] = nodes[-1].rstrip(" \t\n\r\f")
    return [node for node in nodes if node != ""]


def finalize(nodes: list[Node]) -> list[Node]:
    """Final clean-up of a card side: merge split lists, prune empties, trim, set RTL direction."""
    nodes = _trim(_prune(_merge_lists(nodes)))
    _strip_block_edges(nodes)
    _mark_rtl(nodes)
    return nodes


_EDGE_BLOCKS = frozenset({"p", "div", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "summary", "blockquote", "dd", "dt", "caption"})


def _strip_block_edges(nodes: list[Node]) -> None:
    """Remove insignificant spaces at the start/end of blocks (from pretty-printed HTML)."""
    for node in nodes:
        if not isinstance(node, El) or node.tag == "pre":
            continue
        if node.tag in _EDGE_BLOCKS and node.children:
            if isinstance(node.children[0], str):
                node.children[0] = node.children[0].lstrip(" \t\n\r\f")
            if isinstance(node.children[-1], str):
                node.children[-1] = node.children[-1].rstrip(" \t\n\r\f")
            node.children = [child for child in node.children if child != ""]
        _strip_block_edges(node.children)


def _merge_lists(nodes: list[Node]) -> list[Node]:
    """Notion exports every list item as its own <ul>/<ol>; merge consecutive ones."""
    out: list[Node] = []
    for node in nodes:
        if isinstance(node, El):
            node.children = _merge_lists(node.children)
            if node.tag in ("ul", "ol"):
                index = _last_element_index(out)
                if index is not None and _mergeable(out[index], node):
                    del out[index + 1 :]
                    target = out[index]
                    assert isinstance(target, El)
                    target.children.extend(node.children)
                    continue
        out.append(node)
    return out


def _last_element_index(nodes: list[Node]) -> int | None:
    for index in range(len(nodes) - 1, -1, -1):
        item = nodes[index]
        if isinstance(item, str):
            if _ONLY_WS.fullmatch(item):
                continue
            return None
        return index
    return None


def _mergeable(first: Node, second: El) -> bool:
    if not isinstance(first, El) or first.tag != second.tag:
        return False
    if {k: v for k, v in first.attrs.items() if k != "start"} != {k: v for k, v in second.attrs.items() if k != "start"}:
        return False
    if first.tag == "ol":
        count = sum(1 for child in first.children if isinstance(child, El) and child.tag == "li")
        return int(second.attrs.get("start") or 1) == int(first.attrs.get("start") or 1) + count
    return True


def _prune(nodes: list[Node]) -> list[Node]:
    out: list[Node] = []
    for node in nodes:
        if isinstance(node, El):
            node.children = _prune(node.children)
            if node.tag in _PRUNABLE:
                if not node.children:
                    continue
                if node.tag in ("p", "div"):
                    if all(isinstance(c, str) and _ONLY_WS.fullmatch(c) for c in node.children):
                        continue
                elif not has_content(node.children) and not _contains_tag(node.children, ("br", "img")):
                    if any(isinstance(c, str) and c for c in node.children):
                        out.append(" ")
                    continue
        out.append(node)
    return out


def _contains_tag(nodes: list[Node], tags: tuple[str, ...]) -> bool:
    for node in nodes:
        if isinstance(node, El) and (node.tag in tags or _contains_tag(node.children, tags)):
            return True
    return False


def _mark_rtl(nodes: list[Node]) -> None:
    """Give blocks containing Hebrew/Arabic text ``dir="auto"`` so they display right-to-left."""
    for node in nodes:
        if isinstance(node, El):
            if node.tag in _DIRECTION_BLOCKS and "dir" not in node.attrs:
                direct = "".join(c for c in node.children if isinstance(c, str))
                if _RTL.search(direct):
                    node.attrs["dir"] = "auto"
            _mark_rtl(node.children)


def has_content(nodes: Iterable[Node]) -> bool:
    """True if there is visible text or an image."""
    for node in nodes:
        if isinstance(node, str):
            if node.translate(_ZERO_WIDTH).strip():
                return True
        elif node.tag == "img" or has_content(node.children):
            return True
    return False


def plain_text(nodes: Iterable[Node]) -> str:
    parts: list[str] = []
    _collect_text(nodes, parts)
    return " ".join("".join(parts).split())


def _collect_text(nodes: Iterable[Node], parts: list[str]) -> None:
    for node in nodes:
        if isinstance(node, str):
            parts.append(node)
        elif node.tag == "br":
            parts.append(" ")
        elif node.tag == "img":
            alt = node.attrs.get("alt")
            parts.append(f" {alt} " if alt else " [image] ")
        else:
            block = node.tag in BLOCK_TAGS
            if block:
                parts.append(" ")
            _collect_text(node.children, parts)
            if block:
                parts.append(" ")


def to_html(nodes: Iterable[Node]) -> str:
    """Serialise output nodes through the allowlist (the last line of defence)."""
    parts: list[str] = []
    _serialize(nodes, parts)
    return "".join(parts)


def _serialize(nodes: Iterable[Node], parts: list[str]) -> None:
    for node in nodes:
        if isinstance(node, str):
            parts.append(html.escape(node.replace("\r", " ").replace("\n", " "), quote=False))
            continue
        if node.tag not in OUTPUT_TAGS:
            _serialize(node.children, parts)
            continue
        parts.append("<" + node.tag)
        for name, value in node.attrs.items():
            keep, cleaned = safe_attribute(node.tag, name, value)
            if not keep:
                continue
            parts.append(f" {name}" if cleaned is None else f' {name}="{html.escape(cleaned, quote=True)}"')
        parts.append(">")
        if node.tag in VOID_TAGS:
            continue
        _serialize(node.children, parts)
        parts.append(f"</{node.tag}>")


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


def clean_tex(tex: str) -> str:
    """Strip TeX comments and join lines (Anki fields must not contain raw newlines)."""
    tex = tex.replace("\r\n", "\n").replace("\r", "\n")
    tex = _TEX_COMMENT.sub("", tex)
    return " ".join(tex.split())


def _tex_source(node: Tag) -> str | None:
    annotation = node.find("annotation", attrs={"encoding": _TEX_ENCODING})
    if not isinstance(annotation, Tag):
        return None
    return clean_tex(annotation.get_text()) or None


def _text_of(tag: object) -> str:
    if not isinstance(tag, Tag):
        return ""
    return " ".join(tag.get_text(" ").split())


def _direction(node: Tag) -> str | None:
    value = str(node.get("dir") or "").lower()
    return value if value in ("rtl", "ltr") else None


def _int_attr(value: object, low: int, high: int) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _image_width(node: Tag) -> int | None:
    match = _PIXELS.match(parse_style(node.get("style")).get("width", ""))
    width = int(match.group(1)) if match else _int_attr(node.get("width"), 1, 4000)
    return width if width and 1 <= width <= 4000 else None
