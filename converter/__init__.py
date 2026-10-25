"""Local-first conversion of Notion HTML exports into Anki flashcards.

Every Notion toggle becomes one card: the toggle title is the front and its
content, including the toggles nested inside it at any depth, is the back.
Optionally, nested toggles also become cards of their own.

This package has no dependency on Streamlit, so the same engine can serve the
web UI (``app.py``), the command line (``python -m converter``), tests, or a
future desktop build.
"""

from .apkg import export_apkg, safe_deck_name, suggest_deck_name
from .exporters import ExportError, ExportOutput, export_cards, parse_export, safe_filename, suggest_filename
from .loader import InputSet, LoaderError, LoaderLimits, decode_html, load_paths, load_uploads
from .media import build_media_bundle, media_filename
from .models import (
    TEXT_FORMATS,
    Card,
    ConversionOptions,
    ConversionResult,
    Issue,
    MediaFile,
    NestedToggleMode,
    OutputFormat,
    PageResult,
    PageSummary,
    Severity,
)
from .pipeline import convert, convert_html, scan_pages

__version__ = "2.0.0"

__all__ = [
    "TEXT_FORMATS",
    "Card",
    "ConversionOptions",
    "ConversionResult",
    "ExportError",
    "ExportOutput",
    "InputSet",
    "Issue",
    "LoaderError",
    "LoaderLimits",
    "MediaFile",
    "NestedToggleMode",
    "OutputFormat",
    "PageResult",
    "PageSummary",
    "Severity",
    "build_media_bundle",
    "convert",
    "convert_html",
    "decode_html",
    "export_apkg",
    "export_cards",
    "load_paths",
    "load_uploads",
    "media_filename",
    "parse_export",
    "safe_deck_name",
    "safe_filename",
    "scan_pages",
    "suggest_deck_name",
    "suggest_filename",
]
