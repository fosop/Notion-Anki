"""Data structures shared by the conversion engine.

Nothing in the ``converter`` package imports Streamlit, so the same engine can
be driven by the Streamlit app, the command line, the test-suite or a future
desktop front-end (e.g. PySide6 / PyInstaller) without changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NestedToggleMode(str, Enum):
    """How nested toggles appear inside their parent's answer (back)."""

    EXPANDED = "expanded"
    """Default: nested toggles are shown with their full content, in place."""

    TITLES_ONLY = "titles_only"
    """Only the titles of directly nested toggles are shown, in place."""


class OutputFormat(str, Enum):
    APKG = "apkg"
    """Anki package: cards, note type, deck and media in one file."""
    TSV = "tsv"
    CSV = "csv"

    @property
    def is_text(self) -> bool:
        """True for the two-column text formats (TSV/CSV), which cannot carry media."""
        return self is not OutputFormat.APKG

    @property
    def delimiter(self) -> str:
        return "\t" if self is OutputFormat.TSV else ","

    @property
    def extension(self) -> str:
        return f".{self.value}"

    @property
    def anki_separator(self) -> str:
        """Value written to Anki's ``#separator:`` file header."""
        return "Tab" if self is OutputFormat.TSV else "Comma"

    @property
    def mime_type(self) -> str:
        return {
            OutputFormat.APKG: "application/octet-stream",
            OutputFormat.TSV: "text/tab-separated-values",
            OutputFormat.CSV: "text/csv",
        }[self]


TEXT_FORMATS = (OutputFormat.TSV, OutputFormat.CSV)

TEXT_EXPORT_ONLY_CODES = frozenset({"duplicate-fronts"})
"""Issue codes that only concern TSV/CSV files. An .apkg identifies each note by its
own ID, so cards that share a front cannot overwrite each other on re-import."""


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Issue:
    """A problem or notice found while loading or converting.

    ``cards`` lists the 1-based numbers of the affected cards (if any).
    """

    code: str
    message: str
    severity: Severity = Severity.WARNING
    page: str | None = None
    cards: tuple[int, ...] = ()


@dataclass
class Card:
    """One Anki note: a toggle title (front) and its rendered content (back)."""

    front: str
    back: str
    front_text: str
    page_title: str
    page_path: str
    breadcrumbs: tuple[str, ...] = ()
    """Plain-text titles of the ancestor toggles (preview context only, never exported)."""
    depth: int = 0
    media: tuple[str, ...] = ()
    """Anki media filenames referenced by this card."""
    notes: tuple[str, ...] = ()
    """Human-readable formatting/media notes about this card."""
    empty_answer: bool = False
    number: int = 0
    """1-based position in the conversion result."""
    source_id: str = ""
    """Notion's block ID of the toggle, when the export has one. Gives the card a stable
    identity in .apkg exports, even after its title is edited."""


@dataclass
class MediaFile:
    """A media file referenced by at least one card, under its flat Anki filename."""

    filename: str
    data: bytes
    kind: str  # "image" or "audio"
    sources: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class PageSummary:
    """Result of the quick pre-conversion scan of one HTML page."""

    path: str
    title: str
    origin: str
    toggles: int = 0
    cards: int = 0
    untitled: int = 0
    max_depth: int = 0
    patterns: dict[str, int] = field(default_factory=dict)
    kind: str = "unknown"
    encoding: str = "utf-8"
    error: str | None = None
    notes: tuple[str, ...] = ()


@dataclass
class PageResult:
    """Per-page statistics of a conversion."""

    path: str
    title: str
    toggles: int = 0
    cards: int = 0
    skipped_untitled: int = 0
    skipped_empty: int = 0
    empty_answers: int = 0
    kind: str = "unknown"
    error: str | None = None


@dataclass(frozen=True)
class ConversionOptions:
    nested_mode: NestedToggleMode = NestedToggleMode.EXPANDED
    skip_empty_answers: bool = False
    """Drop toggles whose answer is empty instead of exporting them (always reported)."""
    keep_external_images: bool = True
    """Keep ``<img>`` tags that point to web URLs. They are never downloaded by this app."""


@dataclass
class ConversionResult:
    cards: list[Card]
    pages: list[PageResult]
    media: dict[str, MediaFile]
    issues: list[Issue]
    options: ConversionOptions = field(default_factory=ConversionOptions)

    @property
    def toggles_found(self) -> int:
        return sum(p.toggles for p in self.pages)

    @property
    def skipped_untitled(self) -> int:
        return sum(p.skipped_untitled for p in self.pages)

    @property
    def skipped_empty(self) -> int:
        return sum(p.skipped_empty for p in self.pages)

    @property
    def skipped_total(self) -> int:
        return self.skipped_untitled + self.skipped_empty

    @property
    def empty_answers(self) -> int:
        return sum(p.empty_answers for p in self.pages)

    @property
    def pages_processed(self) -> int:
        return sum(1 for p in self.pages if p.error is None)

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is not Severity.INFO]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    def issues_for(self, fmt: OutputFormat) -> list[Issue]:
        """The issues that matter for an export in *fmt*."""
        if fmt.is_text:
            return list(self.issues)
        return [i for i in self.issues if i.code not in TEXT_EXPORT_ONLY_CODES]
