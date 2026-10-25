"""Command-line interface.

Examples::

    python -m converter Export.zip --list
    python -m converter Export.zip                              # <page title>.apkg, images included
    python -m converter Export.zip -o cards.apkg --deck "Neurology::Lecture 2.3"
    python -m converter Export.zip -o cards.tsv --bundle        # TSV plus a ZIP with the media
    python -m converter "My Page.html" --format csv --titles-only -o cards.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .apkg import export_apkg, suggest_deck_name
from .exporters import ExportError, export_cards, suggest_filename
from .loader import load_paths
from .media import build_media_bundle
from .models import ConversionOptions, NestedToggleMode, OutputFormat, Severity
from .pipeline import convert, scan_pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m converter",
        description="Convert Notion HTML exports to an Anki package (.apkg) or a TSV/CSV file.",
    )
    parser.add_argument("inputs", nargs="+", type=Path, help=".html/.htm/.zip files or extracted export folders")
    parser.add_argument("-o", "--output", type=Path, help="output file (default: derived from the page title)")
    parser.add_argument(
        "--format",
        choices=[f.value for f in OutputFormat],
        help="apkg (cards and media in one file), tsv or csv; default: the extension of -o, otherwise apkg",
    )
    parser.add_argument("--deck", help="deck name for .apkg files (default: the page title); use :: for sub-decks")
    parser.add_argument("--titles-only", action="store_true", help="show only nested toggle titles in parent answers")
    parser.add_argument("--skip-empty", action="store_true", help="skip toggles whose answer is empty")
    parser.add_argument("--no-external-images", action="store_true", help="replace web images with links")
    parser.add_argument("--bundle", action="store_true", help="TSV/CSV only: also write a ZIP with the cards, media and a README")
    parser.add_argument("--page", action="append", default=None, help="convert only this page path (repeatable)")
    parser.add_argument("--list", action="store_true", help="list pages and toggle counts, then exit")
    args = parser.parse_args(argv)

    inputs = load_paths(args.inputs)
    for issue in inputs.issues:
        print(f"[{issue.severity.value}] {issue.message}", file=sys.stderr)

    if args.list:
        for summary in scan_pages(inputs):
            status = summary.error or f"{summary.toggles} toggle(s), {summary.cards} card(s), depth {summary.max_depth}"
            print(f"{summary.path}\t{status}")
        return 0

    options = ConversionOptions(
        nested_mode=NestedToggleMode.TITLES_ONLY if args.titles_only else NestedToggleMode.EXPANDED,
        skip_empty_answers=args.skip_empty,
        keep_external_images=not args.no_external_images,
    )
    fmt = _output_format(args.format, args.output)
    result = convert(inputs, args.page, options)
    issues = result.issues_for(fmt)
    for issue in issues:
        cards = f" (cards {', '.join(map(str, issue.cards[:10]))}{'…' if len(issue.cards) > 10 else ''})" if issue.cards else ""
        page = f"{issue.page}: " if issue.page else ""
        print(f"[{issue.severity.value}] {page}{issue.message}{cards}", file=sys.stderr)
    if not result.cards:
        return 1

    titles = [p.title for p in result.pages if p.cards]
    name = args.output.name if args.output else suggest_filename(titles, fmt)
    try:
        if fmt is OutputFormat.APKG:
            output = export_apkg(result.cards, result.media, args.deck or suggest_deck_name(titles), name)
        else:
            output = export_cards(result.cards, fmt, name)
    except ExportError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    target = (args.output.parent if args.output else Path.cwd()) / output.filename
    target.write_bytes(output.data)
    print(
        f"Wrote {output.rows} card(s) from {result.pages_processed} page(s) to {target} "
        f"({result.skipped_total} toggle(s) skipped, {len(result.media)} media file(s), "
        f"{len([i for i in issues if i.severity is not Severity.INFO])} warning(s))."
    )
    if fmt is OutputFormat.APKG:
        if args.bundle:
            print("Note: --bundle is not needed for .apkg files; the media files are inside the package.")
    elif args.bundle:
        bundle = target.with_suffix(".zip")
        bundle.write_bytes(build_media_bundle(output.filename, output.data, result.media))
        print(f"Wrote media bundle to {bundle}")
    elif result.media:
        print(
            "Note: the cards reference media files; use --bundle to export them for Anki's collection.media "
            "folder, or --format apkg to put them inside the file."
        )
    return 0


def _output_format(name: str | None, output: Path | None) -> OutputFormat:
    if name:
        return OutputFormat(name)
    extension = output.suffix.lower().lstrip(".") if output else ""
    return OutputFormat(extension) if extension in {f.value for f in OutputFormat} else OutputFormat.APKG


if __name__ == "__main__":
    raise SystemExit(main())
