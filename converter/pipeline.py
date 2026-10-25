"""High-level API: scan pages for toggles, convert them to cards, report issues.

Typical use::

    inputs = load_uploads([("Export.zip", data)])
    summaries = scan_pages(inputs)                 # toggle counts per page
    result = convert(inputs, [s.path for s in summaries if s.cards])
    output = export_cards(result.cards, OutputFormat.TSV)

Logging only records counts and error types, never note content.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from typing import Callable, Iterator, Sequence

from .loader import InputSet, LoaderError, decode_html, load_uploads
from .media import MediaCollector
from .models import (
    Card,
    ConversionOptions,
    ConversionResult,
    Issue,
    PageResult,
    PageSummary,
    Severity,
)
from .parser import KIND_GENERIC, KIND_NOTION_APP, ParsedPage, parse_page, title_from_path
from .rendering import CardRenderer
from .toggles import pattern_counts

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]
"""``progress(done, total, current_page_path)``"""

INFO_CODES = frozenset({"internal-link", "skipped-empty"})


@contextlib.contextmanager
def _recursion_headroom(limit: int = 20_000) -> Iterator[None]:
    """Deeply nested toggles recurse deeply. Python 3.11+ function calls don't use the C stack."""
    previous = sys.getrecursionlimit()
    if previous < limit:
        sys.setrecursionlimit(limit)
    try:
        yield
    finally:
        sys.setrecursionlimit(previous)


def _load_page(inputs: InputSet, path: str) -> tuple[ParsedPage, str | None, str]:
    entry = inputs.get(path)
    if entry is None:
        raise LoaderError(f"{path} is not part of the upload.")
    decoded = decode_html(entry.read(inputs.limits.max_html_bytes))
    return parse_page(decoded.text, entry.path), decoded.note, decoded.encoding


def scan_pages(inputs: InputSet, progress: ProgressCallback | None = None) -> list[PageSummary]:
    """Quick diagnostic pass: how many toggles and cards each page would produce."""
    summaries: list[PageSummary] = []
    pages = inputs.pages
    with _recursion_headroom():
        for index, entry in enumerate(pages):
            if progress:
                progress(index, len(pages), entry.path)
            summary = PageSummary(path=entry.path, title=title_from_path(entry.path), origin=entry.origin)
            try:
                page, note, encoding = _load_page(inputs, entry.path)
                renderer = CardRenderer(page, None)
                usable = sum(1 for toggle in page.toggles if renderer.title_is_usable(toggle))
            except (LoaderError, RecursionError, ValueError) as exc:
                summary.error = _describe(exc)
                logger.warning("Scan failed for a page: %s", type(exc).__name__)
            else:
                summary.title = page.title
                summary.toggles = len(page.toggles)
                summary.cards = usable
                summary.untitled = len(page.toggles) - usable
                summary.max_depth = max((t.depth + 1 for t in page.toggles), default=0)
                summary.patterns = pattern_counts(page.toggles)
                summary.kind = page.kind
                summary.encoding = encoding
                summary.notes = tuple(n for n in (note, *page.notes) if n)
            summaries.append(summary)
    if progress:
        progress(len(pages), len(pages), "")
    logger.info("Scanned %d page(s): %d toggle(s)", len(summaries), sum(s.toggles for s in summaries))
    return summaries


def convert(
    inputs: InputSet,
    selected: Sequence[str] | None = None,
    options: ConversionOptions | None = None,
    progress: ProgressCallback | None = None,
) -> ConversionResult:
    """Convert the *selected* pages (default: all pages) to cards.

    Every toggle with a usable title becomes exactly one card, in document
    order. Pages are processed in the order of :attr:`InputSet.pages`.
    """
    options = options or ConversionOptions()
    paths = [entry.path for entry in inputs.pages]
    if selected is not None:
        wanted = set(selected)
        paths = [path for path in paths if path in wanted]

    media = MediaCollector(inputs, max_file_bytes=inputs.limits.max_media_bytes)
    cards: list[Card] = []
    pages: list[PageResult] = []
    issues: list[Issue] = list(inputs.issues)
    card_notes: list[tuple[int, str, str, str]] = []

    with _recursion_headroom():
        for index, path in enumerate(paths):
            if progress:
                progress(index, len(paths), path)
            result = PageResult(path=path, title=title_from_path(path))
            pages.append(result)
            try:
                page, note, _encoding = _load_page(inputs, path)
            except (LoaderError, RecursionError, ValueError) as exc:
                result.error = _describe(exc)
                issues.append(Issue("page-error", f"{path}: {result.error}", Severity.ERROR, page=path))
                logger.warning("Could not read a page: %s", type(exc).__name__)
                continue
            result.title, result.kind, result.toggles = page.title, page.kind, len(page.toggles)
            if note:
                issues.append(Issue("encoding", note, Severity.WARNING, page=page.title))
            for page_note in page.notes:
                issues.append(Issue("unsupported-page", page_note, Severity.WARNING, page=page.title))
            try:
                _convert_page(page, path, CardRenderer(page, media, options), options, result, cards, card_notes, issues)
            except RecursionError:
                result.error = "The page is nested too deeply to convert."
                issues.append(Issue("page-error", f"{page.title}: {result.error}", Severity.ERROR, page=page.title))
                logger.warning("Recursion limit reached while converting a page")
    if progress:
        progress(len(paths), len(paths), "")

    issues.extend(_aggregate(card_notes))
    issues.extend(_duplicate_fronts(cards))
    if not cards:
        issues.append(_no_cards_issue(inputs, pages, options))
    logger.info(
        "Converted %d page(s): %d toggle(s), %d card(s), %d skipped, %d media file(s)",
        len(pages), sum(p.toggles for p in pages), len(cards),
        sum(p.skipped_untitled + p.skipped_empty for p in pages), len(media.files),
    )
    return ConversionResult(cards=cards, pages=pages, media=dict(media.files), issues=issues, options=options)


def _convert_page(
    page: ParsedPage,
    path: str,
    renderer: CardRenderer,
    options: ConversionOptions,
    result: PageResult,
    cards: list[Card],
    card_notes: list[tuple[int, str, str, str]],
    issues: list[Issue],
) -> None:
    titles: dict[int, str] = {}
    untitled_context: list[str] = []
    for toggle in page.toggles:
        rendered = renderer.render(toggle)
        titles[id(toggle)] = rendered.front_text
        breadcrumbs = tuple(t for t in (titles.get(id(a), "") for a in toggle.ancestors()) if t)
        if rendered.front_empty:
            result.skipped_untitled += 1
            untitled_context.append(f"inside '{_short(breadcrumbs[-1])}'" if breadcrumbs else "at the top level")
            continue
        if rendered.back_empty:
            result.empty_answers += 1
            if options.skip_empty_answers:
                result.skipped_empty += 1
                issues.append(Issue(
                    "skipped-empty",
                    f"Skipped '{_short(rendered.front_text)}' because its answer is empty.",
                    Severity.INFO,
                    page=page.title,
                ))
                continue
        number = len(cards) + 1
        notes = [message for _code, message in rendered.notes]
        if rendered.back_empty:
            notes.append("The answer is empty.")
            card_notes.append((number, page.title, "empty-answer", f"'{_short(rendered.front_text)}' has an empty answer."))
        for code, message in rendered.notes:
            card_notes.append((number, page.title, code, message))
        cards.append(Card(
            front=rendered.front_html,
            back=rendered.back_html,
            front_text=rendered.front_text,
            page_title=page.title,
            page_path=path,
            breadcrumbs=breadcrumbs,
            depth=toggle.depth,
            media=rendered.media,
            notes=tuple(notes),
            empty_answer=rendered.back_empty,
            number=number,
            source_id=toggle.block_id or "",
        ))
        result.cards += 1
    if result.skipped_untitled:
        where = "; ".join(dict.fromkeys(untitled_context))
        issues.append(Issue(
            "untitled-toggles",
            f"Skipped {result.skipped_untitled} toggle(s) without a usable title ({_short(where, 200)}). "
            "Their nested toggles were still converted.",
            Severity.WARNING,
            page=page.title,
        ))


def _aggregate(card_notes: list[tuple[int, str, str, str]]) -> list[Issue]:
    """One issue per distinct (page, code, message), listing the affected cards."""
    grouped: dict[tuple[str, str, str], list[int]] = {}
    for number, page, code, message in card_notes:
        numbers = grouped.setdefault((page, code, message), [])
        if not numbers or numbers[-1] != number:
            numbers.append(number)
    return [
        Issue(code, message, Severity.INFO if code in INFO_CODES else Severity.WARNING, page=page, cards=tuple(numbers))
        for (page, code, message), numbers in grouped.items()
    ]


def _duplicate_fronts(cards: list[Card]) -> list[Issue]:
    """Duplicates are kept, but Anki matches TSV/CSV notes by their first field when importing."""
    groups: dict[str, list[int]] = {}
    for item in cards:
        groups.setdefault(item.front, []).append(item.number)
    duplicates = [numbers for numbers in groups.values() if len(numbers) > 1]
    if not duplicates:
        return []
    affected = tuple(sorted(n for numbers in duplicates for n in numbers))
    # Verified with Anki 26.09's importer: a first import keeps every duplicate, but re-importing
    # an updated export with "Existing notes: Update" (the default) lets same-front notes overwrite
    # each other's backs, because Anki identifies existing notes by their first field.
    return [Issue(
        "duplicate-fronts",
        f"{len(affected)} cards share their front with another card ({len(duplicates)} group(s)). All of them are "
        "exported and a first import keeps them all. But Anki recognises existing notes by their front, so if you "
        "later re-import an updated export with 'Existing notes: Update', these cards can overwrite each other's "
        "answers. Make these titles unique in Notion, or re-import with 'Existing notes: Preserve'. "
        "The Anki package (.apkg) format is not affected.",
        Severity.WARNING,
        cards=affected,
    )]


def _no_cards_issue(inputs: InputSet, pages: list[PageResult], options: ConversionOptions) -> Issue:
    if not pages:
        message = "No HTML pages were found in the upload."
        if ".md" in inputs.other_files:
            message += " It contains Markdown (.md) files: in Notion choose Export → Format: HTML (not 'Markdown & CSV')."
        return Issue("no-cards", message, Severity.ERROR)
    names = ", ".join(p.path for p in pages[:8]) + (f" and {len(pages) - 8} more" if len(pages) > 8 else "")
    toggles = sum(p.toggles for p in pages)
    if toggles == 0:
        message = (
            f"No toggles were detected in the {len(pages)} page(s) inspected ({names}). "
            "Only Notion toggle blocks and toggle headings become flashcards; ordinary headings, "
            "paragraphs and lists do not. The HTML structure may be unsupported."
        )
        kinds = {p.kind for p in pages}
        if KIND_NOTION_APP in kinds:
            message += " Some pages were saved from the Notion website/app instead of being exported: use ••• → Export → HTML."
        elif kinds == {KIND_GENERIC}:
            message += " The files do not look like Notion HTML exports."
        return Issue("no-toggles", message, Severity.ERROR)
    skipped_empty = sum(p.skipped_empty for p in pages)
    detail = "every toggle was untitled"
    if skipped_empty and options.skip_empty_answers:
        detail = "the toggles were untitled or had empty answers (and 'skip empty answers' is on)"
    return Issue("no-cards", f"{toggles} toggle(s) were found in {names}, but {detail}.", Severity.ERROR)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, RecursionError):
        return "The page is nested too deeply to convert."
    return str(exc) or type(exc).__name__


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def convert_html(html: str | bytes, name: str = "page.html", options: ConversionOptions | None = None) -> ConversionResult:
    """Convenience wrapper: convert one HTML document (no media available)."""
    data = html.encode("utf-8") if isinstance(html, str) else html
    return convert(load_uploads([(name, data)]), options=options)
