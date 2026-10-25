# Notion → Anki (local-first)

Convert Notion **HTML exports** into Anki flashcards: an **Anki package (.apkg)** that carries the
cards together with their images and audio, or a TSV/CSV text file.
**Every toggle becomes a card, and the toggles nested inside it are part of its answer**, open or
closed (click to open, as in Notion). The toggle's title is the front and its content is the back.
If you prefer, nested toggles can also become cards of their own.

Everything runs on your own computer. There is no account, no Notion API token and
no cloud service, and the app needs no internet connection once it is installed.

## Quick start (Windows)

1. Install Python from [python.org/downloads](https://www.python.org/downloads/) (the default options are fine).
2. On this page, click the green **Code** button → **Download ZIP**.
3. Right-click the downloaded ZIP → **Extract All…** → **Extract**.
4. Open the extracted folder and double-click **start.bat**. If Windows says "Windows protected
   your PC", click **More info → Run anyway**. The first start installs what the app needs (a few
   minutes); if the window asks a question, type `y` and press Enter.
5. The app opens in your browser. Keep the black window open while you use it; close it when you're done.

Next time, just double-click **start.bat** again.

## Quick start (Mac)

Your Mac needs macOS 12 (Monterey) or newer: check under Apple menu → **About This Mac**.

1. Install Python from [python.org/downloads](https://www.python.org/downloads/): open the downloaded
   `.pkg` file and click through the installer.
2. On this page, click the green **Code** button → **Download ZIP**. Safari unzips it into a
   **Notion-Anki-main** folder in Downloads (with another browser, double-click the ZIP to unzip it).
3. Open **Terminal** (press ⌘ Space, type `Terminal`, press Return). Type `sh` and a space, drag
   **start.command** from the Notion-Anki-main folder into the Terminal window, and press Return.
   If your Mac asks whether Terminal may access your Downloads folder, click **Allow**. The first
   start installs what the app needs (a few minutes).
4. The app opens in your browser. Keep Terminal open while you use it; press **Control-C** in
   Terminal (or close the window) when you're done.

Next time, open Terminal, press **↑** to bring back the last command, and press Return.

---

## 1. Install and run

You need **Python 3.11 or newer** ([python.org](https://www.python.org/downloads/)).

### The easy way: double-click a launcher

* **Windows:** double-click **`start.bat`**. If Windows asks whether to run a downloaded file,
  choose **More info → Run anyway**.
* **macOS / Linux:** open Terminal, type `sh ` (with a space), drag **`start.command`** into the
  window and press Enter.

The first start sets everything up in a `.venv` folder next to the app (a few minutes, needs the
internet once). Later starts take seconds. Your browser then opens the app at
**http://127.0.0.1:8501**. Keep the window open while you use the app; close it (or press **Ctrl+C**)
to stop the app.

### Manual setup

In VS Code, open the project folder (**File → Open Folder… → `notion-anki-converter`**),
then open a terminal (**Terminal → New Terminal**) and run the commands for your system.

#### Windows (PowerShell)

```powershell
cd $HOME\notion-anki-converter
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

If PowerShell refuses to run `Activate.ps1`, either run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or skip activation and call the
venv's Python directly: `.\.venv\Scripts\python.exe run.py`.

#### macOS / Linux

```bash
cd ~/notion-anki-converter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

`python run.py` starts the app at **http://127.0.0.1:8501** and opens your browser.
Stop it with **Ctrl+C** in the terminal. Next time you only need the `cd`, the activation
line and `python run.py`.

Alternatives:

* `streamlit run app.py`, run from the project folder so `.streamlit/config.toml` applies.
* In VS Code: **Run and Debug → "Notion → Anki (app)"**. The bundled `.vscode/settings.json`
  points at the Windows venv path; on macOS/Linux pick `.venv/bin/python` with
  **Python: Select Interpreter**.

## 2. Export from Notion

1. Open the page that contains your toggles.
2. Click **•••** (top right) → **Export**.
3. Choose **Export format: HTML**. Keep **Include content: Everything** so images are
   included, and turn on **Include subpages** if you want them.
4. Save the `.zip`. **Don't unzip it**: the app reads the ZIP directly. This includes the
   "ZIP inside a ZIP" (`ExportBlock-…-Part-1.zip`) that Notion produces for large exports.
   On a Mac, Safari unzips downloads automatically. If you get a folder instead of a ZIP,
   right-click the folder → **Compress** and use the new ZIP.

## 3. Use the app

1. **Upload:** drop the `.zip` (or single `.html`/`.htm` pages) onto the upload area.
2. **Pages:** for a ZIP, the table lists every page with its number of toggles and cards.
   Tick the pages you want; all ticked pages go into one file.
   **Detection details (debug view)** shows what was recognised on each page.
3. **Settings:**
   * Output format: **Anki package (.apkg)** (default: cards, images and audio in one file), TSV or CSV.
   * Deck name (.apkg only; leave empty to use the page title). `::` makes a sub-deck,
     e.g. `Neurology::Lecture 2.3`.
   * Toggles inside other toggles: part of the main toggle's answer, **open** (default) or
     **closed** (click to open, as in Notion), or also as cards of their own.
   * File name (leave empty to use the suggested name).
   * **Media bundle** (TSV/CSV only): also produce a ZIP with the images and audio.
   * *Advanced:* skip toggles with empty answers; keep or drop images linked from the web.
4. **Convert.** You then see the card count, the pages processed, the toggles skipped,
   the warnings and the media files.
5. **Preview:** search cards by their front text, expand any card to check its front and back,
   or show only cards with warnings (marked ⚠️).
6. **Export:** download the `.apkg` (or the TSV/CSV and, if enabled, the ZIP with media).

## 4. Import into Anki

### Anki package (.apkg, recommended)

Double-click the `.apkg` file, or in Anki choose **File → Import…** and pick it. Anki adds the cards
to the deck you named, **together with their images and audio**. There is nothing to copy by hand.

**Re-importing after you edit your notes:** export the same pages again and import the new `.apkg`.
Anki recognises the cards it already has and updates them instead of adding copies. Your review
progress is kept, and so are style changes you make to the **Notion → Anki (Basic)** note type.
Each card carries a stable ID taken from its Notion block (current exports include these IDs), so
this works even after you rename a toggle, and toggles that share a title never overwrite each other.
(Behaviour checked with Anki 26.09's importer.)

Cards imported earlier from a TSV/CSV file use Anki's own Basic note type and are not recognised.
Delete them before importing the `.apkg`, or you'll have each card twice.

### TSV/CSV

1. **If your cards have images or audio:** unzip the media bundle and copy every file
   *inside* its `media` folder (not the folder itself) into Anki's `collection.media` folder:

   | System | Folder (replace *User 1* with your profile name) |
   |---|---|
   | Windows | `%APPDATA%\Anki2\User 1\collection.media` (paste into Explorer's address bar) |
   | macOS | `~/Library/Application Support/Anki2/User 1/collection.media` (Finder: hold ⌥ Option → Go → Library) |
   | Linux | `~/.local/share/Anki2/User 1/collection.media` |

   Put the files directly in `collection.media`, not in a sub-folder. Afterwards,
   **Tools → Check Media** confirms that nothing is missing.
2. In Anki: **File → Import…** and choose the `.tsv`/`.csv` file.
3. Choose note type **Basic** (or any type whose first two fields are Front and Back) and a deck.
4. Make sure **Allow HTML in fields** is **on**. The file's `#html:true` header normally
   ticks it for you. If it is off, formatting, images and equations appear as raw code.
5. Check that column 1 → **Front** and column 2 → **Back**, then click **Import**.

**Re-importing after you edit your notes:** Anki recognises existing notes by their front
(first field). With the default **Existing notes: Update**, re-importing an updated export
refreshes the backs of the cards you already have instead of creating copies. The exception
is cards whose front appears more than once (the app warns about these): they can overwrite
each other's answers on re-import. Give them unique titles in Notion, or re-import with
**Existing notes: Preserve**. A first import always keeps every card. (Behaviour checked with
Anki 26.09's importer.)

The file starts with Anki header lines (`#separator:Tab`, `#html:true`, `#columns:…`).
Anki reads them as settings, never as cards; older versions treat them as comments.

## 5. How toggles become cards

```
Toggle A                      →  one card   front: Toggle A
  Paragraph 1                               back:  Paragraph 1, Paragraph 2,
  Paragraph 2                                      ▾ Toggle B
  Toggle B                                           Explanation B
    Explanation B                                    ▾ Toggle C
    Toggle C                                           Explanation C
      Explanation C
```

* Each outermost toggle becomes one card, in document order. The toggles nested inside it, at any
  depth, are part of its answer, so a sub-question such as "What is the main cause?" always appears
  next to the toggle it belongs to.
* Nested toggles are shown **open** (default) or **closed**: then you click a title during review to
  open it, as in Notion.
* Optionally, nested toggles also become **cards of their own** (the behaviour of version 1). A
  child card's front is then only its own title. The parent's answer shows the nested toggles open,
  or only their titles (`▸ Toggle B`).
* Text outside toggles (page titles, headings, paragraphs) never becomes a card.
* Toggles without a usable title are skipped and counted; the toggles nested inside them still become cards.
* Toggles with an empty answer are exported but flagged. You can skip them under *Advanced*.
* Duplicate titles are kept, because two toggles may legitimately ask the same question (see the
  re-import note in §4).

Recognised markup (see `converter/toggles.py`), checked against real Notion exports:

| Pattern | Markup |
|---|---|
| Notion toggle list | `<ul class="toggle"><li><details><summary>…` (also with the newer `display:contents` wrappers) |
| Notion toggle heading | `<details><summary style="font-weight:600;font-size:…">…</summary><div class="indented">…` |
| Generic HTML | any `<details>` with a `<summary>` |

## 6. What formatting is kept

| Notion content | In the card |
|---|---|
| Paragraphs, soft line breaks (Shift+Enter), headings | `<p>`, `<br>`, `<h1>`–`<h6>` |
| Bold, italic, underline, strike-through, inline code | `<strong>`, `<em>`, `<u>`, `<del>`, `<code>` |
| Text and background colours | inline colours from the page's own palette (readable in night mode) |
| Bulleted, numbered (incl. start number) and to-do lists, nested | `<ul>`, `<ol start>`, ☑/☐ |
| Tables, code blocks, quotes, callouts, columns, dividers | `<table>` with borders, `<pre>`, styled boxes, `<hr>` |
| Equations (block and inline) | Anki MathJax: `\[…\]` and `\(…\)` |
| Links | web/mailto links kept; links to other Notion pages become plain text |
| Images (local) | flat, unique media files: `<img src="name_hash.png">` |
| Audio (local) | `[sound:name_hash.mp3]` |
| Nested toggles | open `<details open>` or closed `<details>` blocks (or only titles, for separate cards) |
| Greek letters, arrows, units, sub/superscripts, emoji | unchanged UTF-8 |

Anything that cannot be kept is listed as a warning and marked in the card, e.g.
`[Video not embedded: echo.mp4]` or `[Image not available: scan.png]`.

## 7. Images and audio

* Images and audio found in the export are renamed to `<name>_<content hash>.<ext>`.
  Names are stable and unique (two different `Untitled.png` files never collide) and contain
  no folders, as Anki requires.
* An **.apkg** carries the images and audio its cards use, and Anki puts them in place on import.
* TSV/CSV files cannot hold images, so enable the **media bundle**. It is a ZIP containing the
  card file, a flat `media/` folder and a `README.txt` with these instructions.
* **With TSV/CSV and without the bundle, images and audio won't display after import**, unless you
  copy the files yourself.
* Only images *inside* a toggle become part of a card. An image placed below a toggle, at the same
  level instead of indented into it, belongs to no card. In Notion, drag it into the toggle (or
select it and press Tab).
* Images linked from the web are kept as links to the web address (they show only while
  online) and are **never downloaded** by this app. The preview doesn't load them either.
* Videos, PDFs and other attachments are not embedded; a warning tells you which.
* A single `.html` upload has no images. Upload the whole Notion ZIP to include them.

## 8. Troubleshooting

**"No toggles were detected".** Only Notion **toggle blocks** and **toggle headings** become
cards; headings, bullets and paragraphs don't. Check that:
* you exported with **Export format: HTML** (not "Markdown & CSV"; the app says so if it sees `.md` files);
* you used Notion's **Export** menu rather than the browser's "Save page as…" (the app detects saved pages);
* the right pages are ticked. Open **Detection details** to see what was found in each file.

**Images don't show in Anki.** Export as **.apkg**, which includes them. With TSV/CSV, copy the
files from the bundle's `media` folder into `collection.media` (see §4), then run **Tools → Check Media**.

**Cards show HTML code such as `<p>`.** Re-import with **Allow HTML in fields** turned on.

**Equations show as `\( … \)`.** The preview shows raw TeX; Anki renders it. If Anki also shows
raw TeX, make sure HTML is allowed, and use a recent Anki version (MathJax is built in).

**The upload is too large.** The limit is 1 GB per file (`.streamlit/config.toml`, `maxUploadSize`).

**Port 8501 is busy.** `run.py` automatically uses the next free port and prints the address.

**Strange characters (`Ã©`).** The file wasn't UTF-8; the app guesses the encoding and warns.
Re-export from Notion, which always writes UTF-8.

## 9. Privacy and security

* The server listens on **127.0.0.1 only**, so other devices on your network cannot reach it.
* No telemetry (`gatherUsageStats = false`), no analytics and no AI or cloud APIs. The page's
  scripts and fonts are served locally.
* Note content is never logged; the terminal shows only counts and error types.
* Uploads are untrusted input. HTML is parsed, never executed. Card HTML is rebuilt from an
  allowlist (no scripts, event handlers, iframes, forms, SVG, `javascript:` links or CSS `url()`).
  The preview adds a second layer of sanitising (DOMPurify) and runs no JavaScript.
* ZIPs are read in memory. Path-traversal names, symlinks, zip bombs and oversized files are
  refused, nothing from an archive is ever written to disk under its own name, and files are
  only read if a card references them as an image or audio.

## 10. Command line (optional)

```bash
python -m converter Export.zip --list                     # pages and toggle counts
python -m converter Export.zip                            # <page title>.apkg, images included
python -m converter Export.zip -o cards.apkg --deck "Neurology::Lecture 2.3"
python -m converter Export.zip -o cards.tsv --bundle      # cards.tsv + cards.zip (media)
python -m converter Export.zip --collapsed                # nested toggles closed in the answer
python -m converter page.html --format csv --nested-cards --titles-only -o cards.csv
```

The format comes from `--format`, otherwise from the extension of `-o`, otherwise it is `.apkg`.

## 11. Tests

```bash
python -m pytest
```

The suite covers the toggle rules (siblings, nesting up to 150 levels, both parent modes,
toggles inside lists), rich formatting and Unicode, empty and duplicate toggles, malformed
HTML and odd encodings, ZIPs with sub-pages, colliding image names, special-character
filenames and nested part-ZIPs, TSV/CSV quoting, Anki headers, the `.apkg` package (contents,
media, stable note IDs, deck names), the command line, malicious HTML and archives, and the full
Streamlit workflow (via Streamlit's headless `AppTest`). The fixtures in `tests/fixtures/` are
synthetic; `build_fixtures.py` regenerates the ZIPs.

Optional: `tests/test_anki_import.py` imports the exports (TSV, CSV and `.apkg`) with **Anki's own
importer** into a throwaway collection. It checks that every card arrives unchanged and that no
media is missing, and that re-importing an updated `.apkg` updates the notes while keeping review
progress. It is skipped unless the official `anki` package is installed. To run it, use a separate
venv (`pip install anki beautifulsoup4 lxml pytest`) so the app's dependencies stay small.

## 12. Project structure

```
start.bat              Double-click launcher for Windows (first start installs everything)
start.command          The same for macOS/Linux (sh start.command)
app.py                 Streamlit interface (thin layer, no conversion logic)
run.py                 Launcher: 127.0.0.1 only, telemetry off, opens the browser
converter/
  loader.py            Uploads, ZIPs (incl. nested), safety limits, encoding detection
  toggles.py           Toggle detection: the only place that knows toggle markup
  parser.py            Page parsing: title, content root, palette, whitespace mode
  rendering.py         Notion/HTML blocks → clean card HTML
  sanitize.py          Output allowlist and URL/CSS validation
  media.py             Media resolution, unique flat filenames, media bundle
  apkg.py              Anki package: notes, note type, deck and media in one file
  exporters.py         TSV/CSV with Anki headers and validation
  pipeline.py          scan_pages() / convert(): orchestration and diagnostics
  models.py            Data classes (Card, ConversionResult, options, …)
  __main__.py          Command-line interface
tests/                 pytest suite and fixtures
```

The `converter` package never imports Streamlit, so it can be reused unchanged by a desktop
front-end.

## 13. Limitations

* Only Notion **HTML** exports (and generic HTML using `<details>`) are supported; Markdown
  exports and pages saved from the browser are diagnosed but not converted.
* Every note has two fields (Front, Back) and one card. There are no tags, and TSV/CSV files
  have no deck or note-type columns.
* Videos, PDFs, embeds and other attachments are not embedded (they are reported).
* The preview shows equations as TeX code rather than rendered formulas.
* Tested so far on Windows 11 with Python 3.14. The macOS/Linux commands and `start.command`
  are standard but have not been run on those systems yet. Only the Notion markup patterns listed
  in §5 have been verified; other variants may need a new pattern in `converter/toggles.py`.

## 14. Sharing the app with a friend

Your friend needs **Python 3.11 or newer** ([python.org](https://www.python.org/downloads/)) and a
copy of this folder **without** `.venv`. That folder is large and only works on the computer that
created it; the launcher builds a fresh one. Then they start the app as in §1: double-click
`start.bat` on Windows, or run `sh start.command` on macOS/Linux.

* **Through GitHub:** put the project in a repository. `.gitignore` already leaves out `.venv` and
  other local files. Your friend clicks **Code → Download ZIP**, unzips it and double-clicks
  `start.bat`, with no git or GitHub account needed if the repository is public (for a private one,
  add them as a collaborator). For a newer version they download the ZIP again; the first start then
  sets up again.
* **Without GitHub:** zip the folder without `.venv` and send the ZIP.

Everything still runs on your friend's own computer, so their notes never go to you or to GitHub.

Later, a self-contained program that needs no Python could be built with PyInstaller
(`pyinstaller --onedir run.py` plus Streamlit's static files). The engine is ready for that:
`run.py` is the single entry point and `converter` doesn't depend on Streamlit.
