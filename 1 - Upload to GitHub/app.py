"""Notion → Anki: the local Streamlit interface.

Start it from this folder with ``python run.py`` (or ``streamlit run app.py``).
All parsing and conversion happens in this process on this computer.
Uploaded files only go to the local server at 127.0.0.1, never to the internet.

The UI is a thin layer over the ``converter`` package, which does not depend
on Streamlit.
"""

from __future__ import annotations

import hashlib
import html
import logging
import math
import re
import traceback

import pandas as pd
import streamlit as st

from converter import (
    ConversionOptions,
    ConversionResult,
    ExportError,
    NestedToggleMode,
    OutputFormat,
    PageSummary,
    Severity,
    build_media_bundle,
    convert,
    export_apkg,
    export_cards,
    load_uploads,
    safe_deck_name,
    safe_filename,
    scan_pages,
    suggest_deck_name,
    suggest_filename,
)
from converter.loader import format_size
from converter.media import as_data_uri

logger = logging.getLogger("converter.app")

CARDS_PER_PAGE = 20
PREVIEW_MEDIA_LIMIT = 15 * 1024 * 1024
_NOTION_ID = re.compile(r"\s+[0-9a-f]{32}$", re.IGNORECASE)
_IMG_TAG = re.compile(r"<img\b[^>]*>")
_SRC_ATTR = re.compile(r'\bsrc="([^"]*)"')
_SOUND_TAG = re.compile(r"\[sound:([A-Za-z0-9._-]+)\]")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~$])")

SIDE_STYLE = (
    "border:1px solid rgba(128,128,128,.35);border-radius:8px;padding:.6rem .9rem;"
    "margin:.2rem 0 .8rem 0;overflow-x:auto;line-height:1.5"
)
LABEL_STYLE = "font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;opacity:.6;margin-bottom:.3rem"
PLACEHOLDER_STYLE = "display:inline-block;padding:.1rem .45rem;border:1px dashed rgba(128,128,128,.7);border-radius:4px;font-size:.85em"

FORMAT_LABELS = {
    OutputFormat.APKG: "Anki package (.apkg): cards and images in one file (recommended)",
    OutputFormat.TSV: "TSV: tab-separated text",
    OutputFormat.CSV: "CSV: comma-separated text",
}
NESTED_LABELS = {
    NestedToggleMode.EXPANDED: "Fully expanded (title and complete answer)",
    NestedToggleMode.TITLES_ONLY: "Titles only",
}


def main() -> None:
    st.set_page_config(page_title="Notion → Anki", page_icon="🗂️", layout="wide")
    _configure_logging()
    _sidebar()

    st.title("Notion → Anki")
    st.caption(
        "Every Notion toggle, including toggles nested inside other toggles, becomes a flashcard. "
        "Everything runs on this computer and nothing is uploaded to the internet."
    )

    # 1. Upload ----------------------------------------------------------------
    st.header("1. Upload", divider="gray")
    files = st.file_uploader(
        "Drop a Notion HTML export (.zip) or individual pages (.html / .htm)",
        type=["zip", "html", "htm"],
        accept_multiple_files=True,
        help="No need to unzip the export. You can upload several files at once.",
    )
    if not files:
        _reset_session()
        st.info(_EXPORT_HELP)
        return

    inputs, summaries = _load(files)
    total_size = sum(f.size for f in files)
    st.caption(
        f"Uploaded {len(files)} file(s), {format_size(total_size)}: "
        + ", ".join(html.escape(f.name) for f in files[:5])
        + (" …" if len(files) > 5 else "")
        + f". Found {len(summaries)} HTML page(s) and {len(inputs.entries) - len(summaries)} other file(s)."
    )
    for issue in inputs.issues:
        (st.error if issue.severity is Severity.ERROR else st.warning)(issue.message)
    if not summaries:
        hint = ""
        if ".md" in inputs.other_files:
            hint = " The upload contains Markdown (.md) files. In Notion, export with **Export format: HTML**."
        st.error("No HTML pages were found in the upload." + hint)
        return

    # 2. Pages -----------------------------------------------------------------
    st.header("2. Pages", divider="gray")
    selected = _page_selector(summaries)
    _diagnostics(summaries, inputs.other_files)
    if sum(s.toggles for s in summaries) == 0:
        st.error(_no_toggles_message(summaries))
        return

    # 3. Settings --------------------------------------------------------------
    st.header("3. Settings", divider="gray")
    left, right = st.columns(2, gap="large")
    with left:
        fmt = st.radio(
            "Output format",
            list(FORMAT_LABELS),
            format_func=FORMAT_LABELS.get,
            help=(
                "An .apkg carries the images and audio and opens in Anki with a double-click. TSV/CSV files hold "
                "text only: their images must be copied into Anki's media folder by hand."
            ),
        )
        nested_mode = st.radio(
            "Nested toggles inside a parent's answer",
            list(NESTED_LABELS),
            format_func=NESTED_LABELS.get,
            help="Either way, every nested toggle also becomes its own card.",
        )
    with right:
        titles = _naming_titles(selected, summaries)
        suggested = suggest_filename(titles, fmt)
        custom_name = st.text_input("File name", placeholder=suggested, help="Leave empty to use the suggested name.")
        deck, want_bundle = None, False
        if fmt is OutputFormat.APKG:
            suggested_deck = suggest_deck_name(titles)
            custom_deck = st.text_input(
                "Deck name",
                placeholder=suggested_deck,
                help="Anki adds the cards to this deck and creates it if needed. Use :: for a sub-deck, e.g. Neurology::Lecture 2.3.",
            )
            deck = safe_deck_name(custom_deck, default=suggested_deck)
        else:
            want_bundle = st.checkbox(
                "Also create a ZIP with the images and audio (media bundle)",
                value=inputs.has_assets,
                help="Text files cannot contain images. The ZIP holds the card file, a flat media folder and instructions.",
            )
    with st.expander("Advanced"):
        skip_empty = st.checkbox("Skip toggles whose answer is empty", value=False)
        keep_external = st.checkbox(
            "Keep images that are linked from the web",
            value=True,
            help="They are never downloaded by this app. Anki shows them only while online.",
        )

    # 4. Convert ---------------------------------------------------------------
    st.header("4. Convert", divider="gray")
    options = ConversionOptions(nested_mode=nested_mode, skip_empty_answers=skip_empty, keep_external_images=keep_external)
    conversion_key = (st.session_state.upload_signature, tuple(selected), options)
    if not selected:
        st.warning("Select at least one page to convert.")
    if st.button("Convert", type="primary", disabled=not selected, icon=":material/play_arrow:"):
        _run_conversion(inputs, selected, options, conversion_key)

    state = st.session_state
    result: ConversionResult | None = state.get("result") if state.get("result_key") == conversion_key else None
    if result is None:
        if state.get("result") is not None:
            st.info("The page selection or settings changed since the last conversion. Click **Convert** to update the cards.")
        return
    if not _summary(result, fmt):
        return

    # 5. Preview ---------------------------------------------------------------
    st.header("5. Preview", divider="gray")
    _preview(result)

    # 6. Export ----------------------------------------------------------------
    st.header("6. Export", divider="gray")
    _export(result, fmt, safe_filename(custom_name or suggested, fmt), deck, want_bundle, conversion_key)


# ---------------------------------------------------------------------------
# Loading and page selection
# ---------------------------------------------------------------------------


def _upload_signature(files) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(f"{item.name}\0{item.size}\0{getattr(item, 'file_id', '')}\0".encode())
    return digest.hexdigest()


def _reset_session() -> None:
    state = st.session_state
    previous = state.get("inputs")
    if previous is not None:
        previous.close()
    for key in ("inputs", "summaries", "upload_signature", "result", "result_key", "export", "bundle", "export_key"):
        state.pop(key, None)


def _load(files):
    state = st.session_state
    signature = _upload_signature(files)
    if state.get("upload_signature") != signature:
        _reset_session()
        bar = st.progress(0.0, text="Reading files…")
        inputs = load_uploads([(item.name, item.getvalue()) for item in files])

        def progress(done: int, total: int, _path: str) -> None:
            bar.progress(done / total if total else 1.0, text=f"Scanning pages for toggles… {done}/{total}")

        summaries = scan_pages(inputs, progress)
        bar.empty()
        state.upload_signature = signature
        state.inputs = inputs
        state.summaries = summaries
        state.page_defaults = "toggles"
        state.selector_version = state.get("selector_version", 0) + 1
    return state.inputs, state.summaries


def _page_selector(summaries: list[PageSummary]) -> list[str]:
    total_toggles = sum(s.toggles for s in summaries)
    total_cards = sum(s.cards for s in summaries)
    if len(summaries) == 1:
        only = summaries[0]
        st.write(f"**{only.title}**: {only.toggles} toggle(s) detected, {only.cards} card(s) will be created.")
        return [only.path]

    st.write(
        f"Found **{total_toggles}** toggle(s) in **{len(summaries)}** pages; "
        f"**{total_cards}** card(s) would be created. Tick the pages to convert. All ticked pages go into one file."
    )
    state = st.session_state
    first, second, third, _rest = st.columns([1, 1, 1, 3])
    if first.button("All pages"):
        state.page_defaults, state.selector_version = "all", state.selector_version + 1
    if second.button("Pages with toggles"):
        state.page_defaults, state.selector_version = "toggles", state.selector_version + 1
    if third.button("None"):
        state.page_defaults, state.selector_version = "none", state.selector_version + 1

    defaults = state.get("page_defaults", "toggles")
    table = pd.DataFrame({
        "Convert": [defaults == "all" or (defaults == "toggles" and s.cards > 0) for s in summaries],
        "Page": [s.title for s in summaries],
        "Folder": [_pretty_folder(s.path) for s in summaries],
        "Toggles": [s.toggles for s in summaries],
        "Cards": [s.cards for s in summaries],
        "Untitled": [s.untitled for s in summaries],
        "Depth": [s.max_depth for s in summaries],
        "Notes": [s.error or " ".join(s.notes) for s in summaries],
    })
    edited = st.data_editor(
        table,
        key=f"page_table_{state.selector_version}",
        hide_index=True,
        disabled=[column for column in table.columns if column != "Convert"],
        column_config={
            "Convert": st.column_config.CheckboxColumn("Convert", help="Include this page in the export"),
            "Untitled": st.column_config.NumberColumn("Untitled", help="Toggles without a title (skipped)"),
            "Depth": st.column_config.NumberColumn("Depth", help="Deepest level of nested toggles"),
        },
        height=min(38 + 35 * len(summaries), 420),
    )
    return [s.path for s, keep in zip(summaries, edited["Convert"].tolist()) if keep]


def _diagnostics(summaries: list[PageSummary], other_files: dict[str, int]) -> None:
    with st.expander("Detection details (debug view)"):
        st.write(
            f"{sum(s.toggles for s in summaries)} toggle(s) detected → {sum(s.cards for s in summaries)} card(s) "
            f"would be generated; {sum(s.untitled for s in summaries)} untitled toggle(s) would be skipped."
        )
        rows = [
            {
                "Page": s.path,
                "Kind": s.kind,
                "Encoding": s.encoding,
                "Toggle patterns": ", ".join(f"{name}: {count}" for name, count in s.patterns.items()) or "none",
                "Max depth": s.max_depth,
                "Notes": s.error or " ".join(s.notes),
            }
            for s in summaries
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        if other_files:
            st.caption("Other files in the upload: " + ", ".join(f"{ext} × {n}" for ext, n in sorted(other_files.items())))
        st.caption(
            "Recognised toggle markup: Notion toggle lists (ul.toggle › details › summary), Notion toggle headings "
            "(details › summary + div.indented) and any HTML details/summary element."
        )


def _no_toggles_message(summaries: list[PageSummary]) -> str:
    inspected = ", ".join(s.path for s in summaries[:8]) + (f" and {len(summaries) - 8} more" if len(summaries) > 8 else "")
    message = (
        f"No toggles were detected in the {len(summaries)} page(s) inspected ({inspected}). "
        "Only Notion **toggle blocks** and **toggle headings** become flashcards. Ordinary headings, "
        "paragraphs and lists do not. The HTML structure may be unsupported."
    )
    kinds = {s.kind for s in summaries}
    if "notion-app" in kinds:
        message += " Some pages were saved from the Notion website/app: use **••• → Export → HTML** instead."
    elif kinds == {"generic-html"}:
        message += " These files don't look like Notion HTML exports."
    return message


def _pretty_folder(path: str) -> str:
    parts = path.split("/")[:-1]
    return " › ".join(_NOTION_ID.sub("", part) for part in parts) or "(top level)"


def _naming_titles(selected: list[str], summaries: list[PageSummary]) -> list[str]:
    """Titles that the suggested file and deck names are based on: the selected page, or the
    single top-level page of a selection, otherwise every selected page (generic name)."""
    chosen = [s for s in summaries if s.path in set(selected)]
    roots = [s for s in chosen if "/" not in s.path]
    if len(chosen) == 1:
        return [chosen[0].title]
    if len(roots) == 1:
        return [roots[0].title]
    return [s.title for s in chosen]


# ---------------------------------------------------------------------------
# Conversion and results
# ---------------------------------------------------------------------------


def _run_conversion(inputs, selected: list[str], options: ConversionOptions, key) -> None:
    state = st.session_state
    for stale in ("result", "result_key", "export", "bundle", "export_key"):
        state.pop(stale, None)
    bar = st.progress(0.0, text="Converting…")

    def progress(done: int, total: int, _path: str) -> None:
        bar.progress(done / total if total else 1.0, text=f"Converting page {min(done + 1, total)} of {total}…")

    try:
        result = convert(inputs, selected, options, progress)
    except Exception as exc:  # show the problem instead of a blank page
        bar.empty()
        logger.error("Conversion failed: %s", type(exc).__name__)
        st.error(f"Conversion failed: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())
        return
    bar.empty()
    state.result = result
    state.result_key = key


def _summary(result: ConversionResult, fmt: OutputFormat) -> bool:
    if not result.cards:
        for issue in result.errors or result.issues:
            st.error(issue.message)
        return False
    issues = result.issues_for(fmt)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    warnings = [i for i in issues if i.severity is Severity.WARNING]
    notices = [i for i in issues if i.severity is Severity.INFO]
    st.success(f"Conversion finished: {len(result.cards)} card(s) from {result.pages_processed} page(s).")
    columns = st.columns(5)
    columns[0].metric("Cards", len(result.cards))
    columns[1].metric("Pages processed", result.pages_processed)
    columns[2].metric("Toggles skipped", result.skipped_total, help="Untitled toggles, plus empty answers if you chose to skip them")
    columns[3].metric("Warnings", len(errors) + len(warnings))
    columns[4].metric("Media files", len(result.media))

    for issue in errors:
        st.error(f"{issue.page + ': ' if issue.page else ''}{issue.message}")
    if warnings:
        st.warning(f"There are {len(warnings)} warning(s). Please review them before importing. Affected cards are marked ⚠️ in the preview.")
    if warnings or notices:
        with st.expander(f"Warnings and notes ({len(warnings) + len(notices)})", expanded=bool(warnings) and len(warnings) <= 5):
            rows = [
                {
                    "Type": issue.severity.value,
                    "Page": issue.page or "",
                    "Message": issue.message,
                    "Cards": ", ".join(f"#{n}" for n in issue.cards[:15]) + (" …" if len(issue.cards) > 15 else ""),
                }
                for issue in warnings + notices
            ][:1000]
            st.dataframe(pd.DataFrame(rows), hide_index=True)
    return True


def _preview(result: ConversionResult) -> None:
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    query = left.text_input("Search by front text", placeholder="e.g. murmur", key="card_search")
    only_notes = right.checkbox("Only cards with warnings", key="only_warned")
    needle = query.casefold().strip()
    matches = [
        card for card in result.cards
        if (not needle or needle in card.front_text.casefold()) and (not only_notes or card.notes)
    ]
    if not matches:
        st.info("No cards match the search.")
        return
    pages = max(1, math.ceil(len(matches) / CARDS_PER_PAGE))
    controls = st.columns([1, 1, 2], vertical_alignment="bottom")
    page = 1
    if pages > 1:
        page = int(controls[0].number_input(f"Page (of {pages})", 1, pages, 1, key=f"preview_page_{needle}_{only_notes}"))
    expand_all = controls[1].toggle("Expand all", key="expand_all")
    start = (page - 1) * CARDS_PER_PAGE
    shown = matches[start : start + CARDS_PER_PAGE]
    controls[2].caption(
        f"Showing cards {start + 1}–{start + len(shown)} of {len(matches)}"
        + (f" (filtered from {len(result.cards)})" if len(matches) != len(result.cards) else "")
        + ". Equations appear as TeX code here; Anki renders them as formulas."
    )
    for card in shown:
        label = f"#{card.number} · {_escape_markdown(_shorten(card.front_text or '(image)', 110))}"
        with st.expander(label, expanded=expand_all, icon=":material/warning:" if card.notes else None):
            details = f"Page: {card.page_title}"
            if card.breadcrumbs:
                details += "  ·  Inside: " + " › ".join(card.breadcrumbs)
            st.caption(_escape_markdown(details))
            _render_side("Front", card.front, result)
            _render_side("Back", card.back, result)
            for note in card.notes:
                st.caption(f"⚠️ {_escape_markdown(note)}")


def _render_side(label: str, fragment: str, result: ConversionResult) -> None:
    body = _preview_html(fragment, result) if fragment else '<i style="opacity:.6">(empty)</i>'
    st.html(f'<div style="{SIDE_STYLE}"><div style="{LABEL_STYLE}">{label}</div>{body}</div>')


def _preview_html(fragment: str, result: ConversionResult) -> str:
    """Show local media inline in the preview. Web images are not loaded (nothing leaves this computer)."""
    cache: dict[str, str] = st.session_state.setdefault("data_uri_cache", {})

    def data_uri(name: str) -> str | None:
        media = result.media.get(name)
        if media is None or media.size > PREVIEW_MEDIA_LIMIT:
            return None
        if name not in cache:
            cache[name] = as_data_uri(media)
        return cache[name]

    def image(match: re.Match[str]) -> str:
        tag = match.group(0)
        src = _SRC_ATTR.search(tag)
        if src is None:
            return tag
        name = html.unescape(src.group(1))
        if name.startswith(("http://", "https://")):
            return f'<span style="{PLACEHOLDER_STYLE}">🌐 Web image (not loaded in the preview)</span>'
        uri = data_uri(name)
        if uri is None:
            return f'<span style="{PLACEHOLDER_STYLE}">🖼 {html.escape(name)}</span>'
        return tag.replace(src.group(0), f'src="{uri}"', 1)

    def sound(match: re.Match[str]) -> str:
        uri = data_uri(match.group(1))
        if uri is None:
            return match.group(0)
        return f'<audio controls src="{uri}"></audio> <code>{html.escape(match.group(0))}</code>'

    return _SOUND_TAG.sub(sound, _IMG_TAG.sub(image, fragment))


def _export(
    result: ConversionResult, fmt: OutputFormat, filename: str, deck: str | None, want_bundle: bool, conversion_key
) -> None:
    state = st.session_state
    export_key = (conversion_key, fmt, filename, deck, want_bundle)
    if state.get("export_key") != export_key:
        try:
            if fmt is OutputFormat.APKG:
                output = export_apkg(result.cards, result.media, deck, filename)
            else:
                output = export_cards(result.cards, fmt, filename)
        except ExportError as exc:
            st.error(f"The export could not be created: {exc}")
            return
        bundle = build_media_bundle(output.filename, output.data, result.media) if want_bundle and result.media else None
        state.export, state.bundle, state.export_key = output, bundle, export_key
    output, bundle = state.export, state.bundle

    left, right = st.columns(2)
    left.download_button(
        f"Download {output.filename}" + (f" ({format_size(len(output.data))})" if fmt is OutputFormat.APKG else ""),
        data=output.data,
        file_name=output.filename,
        mime=fmt.mime_type,
        type="primary",
        icon=":material/download:",
        on_click="ignore",
    )
    if fmt is OutputFormat.APKG:
        included = len({name for card in result.cards for name in card.media})
        st.caption(_escape_markdown(f"{output.rows} card(s) · {included} image/audio file(s) included · deck: {deck}"))
        st.markdown(_import_instructions(output.filename, fmt, False, deck))
        return
    if bundle is not None:
        bundle_name = output.filename.rsplit(".", 1)[0] + "_with_media.zip"
        right.download_button(
            f"Download {bundle_name} ({format_size(len(bundle))})",
            data=bundle,
            file_name=bundle_name,
            mime="application/zip",
            icon=":material/folder_zip:",
            on_click="ignore",
        )
    elif result.media:
        right.warning(
            f"The cards use {len(result.media)} image/audio file(s). A text file can't contain them, so they "
            "won't display in Anki unless you tick **media bundle** in Settings and copy the files into Anki's "
            "collection.media folder, or choose the **Anki package (.apkg)** format, which includes them."
        )
    st.caption(f"{output.rows} card(s) · {format_size(len(output.data))} · UTF-8 · two columns: Front, Back")
    st.markdown(_import_instructions(output.filename, fmt, bundle is not None))
    with st.expander("Where is Anki's collection.media folder?"):
        st.markdown(_MEDIA_FOLDERS)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _escape_markdown(text: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _import_instructions(filename: str, fmt: OutputFormat, has_bundle: bool, deck: str | None = None) -> str:
    if fmt is OutputFormat.APKG:
        return f"""
**Import into Anki**

1. Double-click `{filename}`, or in Anki choose **File → Import…** and pick it.
1. Anki adds the cards to the deck **{_escape_markdown(deck or "")}** together with their images and audio. There is nothing to copy by hand.

**Updating later:** export the same Notion pages again and import the new file. Anki recognises the cards it
already has and updates them instead of adding copies, and your review progress is kept. (Cards imported
earlier from a TSV/CSV file are not recognised: delete those first, or you'll have them twice.)
"""
    media_step = (
        "\n1. **Images/audio:** unzip the media bundle and copy every file *inside* its `media` folder "
        "(not the folder itself) into Anki's `collection.media` folder (see below)."
        if has_bundle
        else ""
    )
    return f"""
**Import into Anki**
{media_step}
1. In Anki choose **File → Import…** and pick `{filename}`.
1. Choose the **Basic** note type (or any type whose first two fields are Front and Back) and your deck.
1. Leave the field separator on **{"Tab" if fmt is OutputFormat.TSV else "Comma"}** (it is read from the file header).
1. Make sure **Allow HTML in fields** is switched **on**. Otherwise formatting, images and equations show up as code.
1. Check that column 1 maps to **Front** and column 2 to **Back**, then click **Import**.
"""


def _configure_logging() -> None:
    package_logger = logging.getLogger("converter")
    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        package_logger.addHandler(handler)
        package_logger.setLevel(logging.INFO)
        package_logger.propagate = False


def _sidebar() -> None:
    with st.sidebar:
        st.subheader("How it works")
        st.markdown(
            "- Each **toggle** becomes one card: its title is the front and its content is the back.\n"
            "- **Nested toggles** become cards too, at any depth.\n"
            "- Text outside toggles is ignored.\n"
            "- Formatting, lists, tables, colours, equations and images are kept."
        )
        st.subheader("Privacy")
        st.markdown(
            "- Runs only on this computer (`127.0.0.1`).\n"
            "- No accounts, no Notion token, no internet needed.\n"
            "- No telemetry or analytics. Note contents are never logged.\n"
            "- Uploaded HTML is parsed, never executed."
        )


_EXPORT_HELP = """**How to export from Notion**

1. Open the Notion page that holds your toggles.
2. Click **•••** (top right) → **Export**.
3. Choose **Export format: HTML**. Include sub-pages if you want them too.
4. Drop the downloaded **.zip** above. There is no need to unzip it.
"""

_MEDIA_FOLDERS = """
Replace *User 1* with your Anki profile name:

| System | Folder |
|---|---|
| Windows | `%APPDATA%\\Anki2\\User 1\\collection.media` (paste into the Explorer address bar) |
| macOS | `~/Library/Application Support/Anki2/User 1/collection.media` (Finder: hold ⌥ Option → Go → Library) |
| Linux | `~/.local/share/Anki2/User 1/collection.media` |

Put the files directly into `collection.media`, not in a sub-folder. Afterwards, **Tools → Check Media** in Anki confirms that nothing is missing.
"""


main()
