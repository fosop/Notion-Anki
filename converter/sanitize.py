"""The HTML allowlist and value validators for everything this app outputs.

Card HTML is never copied from the source. :mod:`converter.rendering` builds a
fresh tree, and :func:`converter.rendering.to_html` serialises it through the
allowlist below, so only these tags and attributes can ever appear in a card or
preview, whatever the input contained:

* no ``<script>``, ``<style>``, ``<iframe>``, ``<object>``, ``<embed>``, ``<form>``, SVG or MathML;
* no event-handler attributes (``onclick``, ``onerror`` …) and no ``class`` or ``id``;
* links only to ``http:``, ``https:`` and ``mailto:``;
* images only from ``http(s):`` URLs or flat Anki media filenames;
* inline styles only from a short list of properties with plain values (no ``url()``,
  ``expression()`` or escapes).
"""

from __future__ import annotations

import re
import urllib.parse

# Attributes allowed on every output tag.
GLOBAL_ATTRIBUTES = frozenset({"style", "dir"})

# Output tag -> extra allowed attributes.
OUTPUT_TAGS: dict[str, frozenset[str]] = {
    **{tag: frozenset() for tag in (
        "p", "div", "span", "br", "hr", "blockquote", "pre", "code",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "li", "dl", "dt", "dd",
        "thead", "tbody", "tfoot", "tr", "caption",
        "b", "strong", "i", "em", "u", "ins", "s", "del", "mark", "sup", "sub",
        "small", "kbd", "samp", "var", "q", "cite", "dfn", "bdi", "ruby", "rt", "rp", "wbr",
        "summary",
    )},
    "ol": frozenset({"start", "type"}),
    "table": frozenset({"border", "cellpadding"}),
    "th": frozenset({"colspan", "rowspan"}),
    "td": frozenset({"colspan", "rowspan"}),
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title", "width", "height"}),
    "abbr": frozenset({"title"}),
    "bdo": frozenset({"dir"}),
    "details": frozenset({"open"}),
}

VOID_TAGS = frozenset({"br", "hr", "img", "wbr"})

SAFE_CSS_PROPERTIES = frozenset({
    "color", "background-color", "text-align", "font-weight", "font-size", "opacity",
    "padding", "padding-left", "margin", "border", "border-left", "border-radius",
    "border-collapse", "display", "flex", "flex-wrap", "gap", "min-width", "max-width",
    "width", "height", "list-style-type", "white-space", "vertical-align",
})

_CSS_VALUE = re.compile(r"^[#a-zA-Z0-9.,%()\s/+-]+$")
_CSS_FORBIDDEN = ("url", "expression", "javascript", "var(", "attr(", "import", "behavior")

_COLOR = re.compile(
    r"^(?:#[0-9a-fA-F]{3,8}"
    r"|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.%\s,/+-]+\)"
    r"|[a-zA-Z]{3,30})$"
)
_NOT_A_COLOR = frozenset({
    "inherit", "initial", "unset", "revert", "currentcolor", "transparent",
    "none", "auto", "default", "normal",
})

_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_CONTROL_AND_SPACE = re.compile(r"[\x00-\x20\x7f]+")
_MEDIA_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_SAFE_SCHEMES = frozenset({"http", "https", "mailto"})

URL_EMPTY = "empty"
URL_FRAGMENT = "fragment"
URL_WEB = "web"
URL_LOCAL = "local"
URL_UNSAFE = "unsafe"


def safe_color(value: str | None) -> str | None:
    """Return *value* if it is a plain CSS colour, else ``None``."""
    if not value:
        return None
    color = re.sub(r"\s*!important\s*$", "", value.strip().rstrip(";").strip(), flags=re.IGNORECASE)
    if len(color) > 60 or not _COLOR.match(color):
        return None
    if color.lower() in _NOT_A_COLOR:
        return None
    return color


def parse_style(value: object) -> dict[str, str]:
    """Parse an inline ``style`` attribute into ``{property: value}`` (lower-case properties)."""
    if not value:
        return {}
    if isinstance(value, list):
        value = ";".join(str(v) for v in value)
    declarations: dict[str, str] = {}
    for part in str(value).split(";"):
        if ":" not in part:
            continue
        prop, val = part.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip()
        if prop and val:
            declarations[prop] = val
    return declarations


def sanitize_style(value: str | None) -> str | None:
    """Keep only allowlisted properties with plain values. ``None`` if nothing survives."""
    if not value:
        return None
    kept: list[str] = []
    for prop, val in parse_style(value).items():
        if prop not in SAFE_CSS_PROPERTIES or len(val) > 120:
            continue
        lowered = val.lower()
        if not _CSS_VALUE.match(val) or any(word in lowered for word in _CSS_FORBIDDEN):
            continue
        kept.append(f"{prop}: {val}")
    return "; ".join(kept) or None


def css_text(css: dict[str, str]) -> str:
    return "; ".join(f"{prop}: {val}" for prop, val in css.items())


def classify_url(raw: str | None) -> tuple[str, str]:
    """Classify a link or media address. Returns ``(kind, cleaned_value)``.

    Kinds: ``empty``, ``fragment`` (``#…``), ``web`` (http/https/mailto or
    protocol-relative), ``local`` (a relative path inside the export) and
    ``unsafe`` (any other scheme: ``javascript:``, ``data:``, ``file:``, drive letters …).
    """
    value = (raw or "").strip()
    if not value:
        return URL_EMPTY, ""
    # Browsers ignore control characters and whitespace inside schemes
    # ("java\tscript:"), so the scheme is checked on a compacted copy.
    compact = _CONTROL_AND_SPACE.sub("", value)
    if not compact:
        return URL_EMPTY, ""
    if compact.startswith("#"):
        return URL_FRAGMENT, value
    if compact.startswith("//"):
        return URL_WEB, "https:" + _clean_web_url(value.lstrip())
    match = _SCHEME.match(compact)
    if match:
        if match.group(1).lower() in _SAFE_SCHEMES:
            return URL_WEB, _clean_web_url(value)
        return URL_UNSAFE, value
    return URL_LOCAL, value


def _clean_web_url(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", value)
    return cleaned.replace(" ", "%20")


def local_path(value: str) -> str:
    """Decode a relative reference ('Page%20abc/img.png?x#y' becomes 'Page abc/img.png')."""
    path = value.split("#", 1)[0].split("?", 1)[0]
    return urllib.parse.unquote(path).replace("\\", "/")


def is_safe_href(value: str) -> bool:
    kind, _ = classify_url(value)
    return kind == URL_WEB


def is_safe_src(value: str) -> bool:
    """``src`` may be a web URL or a flat media filename (no folders, no scheme)."""
    kind, _ = classify_url(value)
    if kind == URL_WEB:
        return not value.lower().startswith("mailto:")
    return kind == URL_LOCAL and bool(_MEDIA_FILENAME.match(value))


def safe_attribute(tag: str, name: str, value: str | None) -> tuple[bool, str | None]:
    """Validate one attribute for the serializer. Returns ``(keep, value)``."""
    if name not in GLOBAL_ATTRIBUTES and name not in OUTPUT_TAGS.get(tag, frozenset()):
        return False, None
    if name == "open":
        return True, None
    if value is None:
        return False, None
    if name == "style":
        cleaned = sanitize_style(value)
        return cleaned is not None, cleaned
    if name == "dir":
        return value.lower() in ("ltr", "rtl", "auto"), value.lower()
    if name == "href":
        return is_safe_href(value), value
    if name == "src":
        return is_safe_src(value), value
    if name == "type":
        return value in ("1", "a", "A", "i", "I"), value
    if name in ("start", "colspan", "rowspan", "width", "height", "border", "cellpadding"):
        return value.isdigit() and len(value) <= 7, value
    if name in ("alt", "title"):
        return True, value[:500]
    return False, None
