"""Load uploaded HTML files, Notion ZIP exports and extracted export folders
into a single, read-only, in-memory *virtual file system*.

Security model (uploads are untrusted input):

* Archive members are **never extracted to disk** under names taken from the
  archive. They are read into memory on demand and addressed by a normalised
  POSIX path, so path-traversal names such as ``../../evil.exe`` cannot write
  anywhere. Such names are skipped and reported.
* Sizes are checked against limits before *and while* reading, because sizes
  declared in ZIP headers can lie. This defuses "zip bombs".
* Nothing is executed. HTML is only parsed as text; other files are only read
  as bytes when a card references them as media.

Notion specifics handled here:

* Exports of large workspaces are ZIPs that contain further ZIP "parts"
  (``ExportBlock-…-Part-1.zip``). Nested ZIPs are opened recursively and their
  contents merged into the same folder, which reconstructs the export.
* ZIPs written without the UTF-8 flag store names in CP437; those are re-decoded
  so that image references with accents or Greek letters still resolve.
"""

from __future__ import annotations

import codecs
import io
import logging
import os
import posixpath
import re
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from .models import Issue, Severity

logger = logging.getLogger(__name__)

KB = 1024
MB = 1024 * KB
GB = 1024 * MB

HTML_EXTENSIONS = frozenset({".html", ".htm"})
ZIP_EXTENSIONS = frozenset({".zip"})
_IGNORED_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


@dataclass(frozen=True)
class LoaderLimits:
    """Safety limits for untrusted uploads. Generous for real Notion exports."""

    max_entries: int = 50_000
    """Maximum number of files across all archives."""
    max_html_bytes: int = 64 * MB
    """Maximum size of a single HTML page."""
    max_media_bytes: int = 200 * MB
    """Maximum size of a single image/audio file."""
    max_nested_zip_bytes: int = 4 * GB
    """Maximum uncompressed size of a ZIP stored inside a ZIP."""
    max_total_bytes: int = 8 * GB
    """Maximum total number of uncompressed bytes read from archives."""
    max_compression_ratio: float = 250.0
    """Members that expand more than this (and are larger than 8 MB) are refused."""
    max_zip_depth: int = 3
    """How deeply ZIP files may be nested (1 = a plain ZIP)."""


DEFAULT_LIMITS = LoaderLimits()


def format_size(size: int) -> str:
    if size >= GB:
        return f"{size / GB:.1f} GB"
    if size >= MB:
        return f"{size / MB:.0f} MB"
    return f"{size / KB:.0f} KB"


class LoaderError(Exception):
    """A file could not be read safely."""


class _ByteBudget:
    """Tracks the total number of uncompressed bytes read from archives."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def consume(self, amount: int) -> None:
        self.used += amount
        if self.used > self.limit:
            raise LoaderError(
                "The archive expands to more data than allowed "
                f"({format_size(self.limit)}). It may be corrupt or a 'zip bomb'."
            )


@dataclass(eq=False)
class FileEntry:
    """A file in the virtual file system. Content is read lazily."""

    path: str
    """Normalised POSIX path inside the upload (NFC, no leading slash)."""
    origin: str
    """Name of the uploaded file (or folder) the entry came from."""
    size: int
    """Declared uncompressed size in bytes."""
    _reader: Callable[[int], bytes]

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def suffix(self) -> str:
        return posixpath.splitext(self.path)[1].lower()

    @property
    def is_html(self) -> bool:
        return self.suffix in HTML_EXTENSIONS

    def read(self, limit: int) -> bytes:
        """Return the content, refusing files larger than *limit* bytes."""
        if self.size > limit:
            raise LoaderError(f"{self.name} is too large ({format_size(self.size)}; limit {format_size(limit)}).")
        return self._reader(limit)


def normalise_path(raw: str) -> str | None:
    """Normalise an archive member name, or return ``None`` if it is unsafe.

    Rejects absolute paths, drive letters, ``..`` segments and NUL bytes.
    """
    if not raw or "\x00" in raw:
        return None
    name = raw.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts: list[str] = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    if not parts:
        return None
    return unicodedata.normalize("NFC", "/".join(parts))


def _member_name(info: zipfile.ZipInfo) -> str:
    """Return the member name, repairing UTF-8 names stored without the UTF-8 flag."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _should_ignore(path: str) -> bool:
    parts = path.split("/")
    if parts[0] == "__MACOSX":
        return True
    base = parts[-1]
    return base.startswith("._") or base.lower() in _IGNORED_NAMES


# ---------------------------------------------------------------------------
# HTML decoding
# ---------------------------------------------------------------------------

_META_CHARSET = re.compile(rb"""<meta[^>]+?charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str
    note: str | None = None


def decode_html(data: bytes) -> DecodedText:
    """Decode HTML bytes, preferring UTF-8 and degrading gracefully.

    Order: byte-order mark, strict UTF-8, declared ``<meta charset>``, mostly-
    UTF-8 with replacement characters, Windows-1252, Latin-1 (never fails).
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return DecodedText(_clean_text(data.decode(encoding)), encoding.replace("-sig", ""))
            except UnicodeDecodeError:
                break
    try:
        return DecodedText(_clean_text(data.decode("utf-8")), "utf-8")
    except UnicodeDecodeError:
        pass

    match = _META_CHARSET.search(data[:4096])
    if match:
        declared = match.group(1).decode("ascii", "ignore").lower()
        try:
            codec_name = codecs.lookup(declared).name
        except LookupError:
            codec_name = ""
        if codec_name and codec_name not in ("utf-8", "utf-8-sig"):
            try:
                return DecodedText(_clean_text(data.decode(codec_name)), codec_name)
            except UnicodeDecodeError:
                pass

    replaced = data.decode("utf-8", errors="replace")
    bad = replaced.count("�")
    non_ascii = sum(1 for byte in data if byte >= 0x80)
    if non_ascii and bad / non_ascii < 0.3:
        return DecodedText(
            _clean_text(replaced),
            "utf-8",
            f"The file contains {bad} invalid UTF-8 byte sequence(s); they were replaced with '�'.",
        )
    try:
        return DecodedText(
            _clean_text(data.decode("cp1252")),
            "windows-1252",
            "The file is not valid UTF-8 and was read as Windows-1252. Check special characters.",
        )
    except UnicodeDecodeError:
        return DecodedText(
            _clean_text(data.decode("latin-1")),
            "latin-1",
            "The file encoding could not be detected; it was read as Latin-1. Check special characters.",
        )


def _clean_text(text: str) -> str:
    # NUL characters can truncate documents in libxml2 and are never meaningful.
    return text.replace("\x00", "")


# ---------------------------------------------------------------------------
# The virtual file system
# ---------------------------------------------------------------------------


class InputSet:
    """All uploaded files, merged into one virtual folder tree.

    Every upload shares one namespace so that the parts of a split Notion export
    (uploaded together or nested in one ZIP) resolve each other's images.
    """

    def __init__(self, limits: LoaderLimits = DEFAULT_LIMITS) -> None:
        self.limits = limits
        self.issues: list[Issue] = []
        self.origins: list[str] = []
        self._entries: dict[str, FileEntry] = {}
        self._casefold: dict[str, list[str]] = {}
        self._by_basename: dict[str, list[str]] = {}
        self._budget = _ByteBudget(limits.max_total_bytes)
        self._closeables: list[object] = []
        self._skipped_unsafe = 0

    # -- public API ---------------------------------------------------------

    @property
    def pages(self) -> list[FileEntry]:
        """HTML pages, sorted so parent pages precede their sub-pages."""
        pages = [e for e in self._entries.values() if e.is_html]
        return sorted(pages, key=lambda e: e.path.casefold())

    @property
    def entries(self) -> list[FileEntry]:
        return list(self._entries.values())

    @property
    def has_assets(self) -> bool:
        return any(not e.is_html for e in self._entries.values())

    @property
    def other_files(self) -> dict[str, int]:
        """Count of non-HTML files by extension (for diagnostics)."""
        counts: dict[str, int] = {}
        for entry in self._entries.values():
            if not entry.is_html:
                counts[entry.suffix or "(none)"] = counts.get(entry.suffix or "(none)", 0) + 1
        return counts

    def get(self, path: str) -> FileEntry | None:
        """Find an entry by path: exact (NFC) match first, then case-insensitive if unique."""
        key = unicodedata.normalize("NFC", path)
        entry = self._entries.get(key)
        if entry is not None:
            return entry
        candidates = self._casefold.get(key.casefold(), [])
        if len(candidates) == 1:
            return self._entries[candidates[0]]
        return None

    def find_by_basename(self, name: str) -> list[FileEntry]:
        key = unicodedata.normalize("NFC", name).casefold()
        return [self._entries[p] for p in self._by_basename.get(key, [])]

    def add_upload(self, name: str, data: bytes) -> None:
        """Add one uploaded file (``.html``, ``.htm`` or ``.zip``)."""
        display = posixpath.basename(name.replace("\\", "/")) or "upload"
        self.origins.append(display)
        suffix = posixpath.splitext(display)[1].lower()
        if suffix in ZIP_EXTENSIONS or data[:4] == b"PK\x03\x04":
            self._add_zip_bytes(display, data)
        elif suffix in HTML_EXTENSIONS:
            if len(data) > self.limits.max_html_bytes:
                self._issue("file-too-large", f"{display} is larger than {format_size(self.limits.max_html_bytes)} and was skipped.", Severity.ERROR)
                return
            path = self._unique_path(normalise_path(display) or "page.html")
            self._register(FileEntry(path, display, len(data), _bytes_reader(data)))
        else:
            self._issue("unsupported-file", f"{display}: only .html, .htm and .zip files are supported.", Severity.ERROR)

    def add_directory(self, directory: Path) -> None:
        """Add an already-extracted export folder (used by the CLI / desktop builds)."""
        root = directory.resolve()
        self.origins.append(root.name)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            for filename in sorted(filenames):
                full = Path(dirpath, filename)
                if full.is_symlink():
                    self._skipped_unsafe += 1
                    continue
                rel = normalise_path(full.relative_to(root).as_posix())
                if rel is None or _should_ignore(rel):
                    continue
                if full.suffix.lower() in ZIP_EXTENSIONS:
                    # Kept open (and closed in close()) because entries are read lazily.
                    handle = full.open("rb")
                    self._closeables.append(handle)
                    self._add_zip_file(handle, posixpath.dirname(rel), root.name, depth=1)
                    continue
                size = full.stat().st_size
                self._register(FileEntry(self._unique_path(rel), root.name, size, _file_reader(full)))
        self._report_unsafe()

    def close(self) -> None:
        for item in self._closeables:
            try:
                item.close()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        self._closeables.clear()

    # -- ZIP handling ---------------------------------------------------------

    def _add_zip_bytes(self, display: str, data: bytes) -> None:
        self._add_zip_file(io.BytesIO(data), "", display, depth=1)
        self._report_unsafe()

    def _add_zip_file(self, handle: BinaryIO, base: str, origin: str, depth: int) -> None:
        try:
            archive = zipfile.ZipFile(handle)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
            self._issue("bad-zip", f"{origin}: not a valid ZIP archive.", Severity.ERROR)
            return
        self._closeables.append(archive)
        infos = archive.infolist()
        if len(self._entries) + len(infos) > self.limits.max_entries:
            self._issue(
                "too-many-files",
                f"{origin}: the archive contains more than {self.limits.max_entries} files and was refused.",
                Severity.ERROR,
            )
            return
        for info in infos:
            if info.is_dir():
                continue
            rel = normalise_path(_member_name(info))
            if rel is None or _is_symlink(info):
                self._skipped_unsafe += 1
                continue
            if _should_ignore(rel):
                continue
            path = posixpath.join(base, rel) if base else rel
            if info.flag_bits & 0x1:
                self._issue("encrypted-file", f"{origin}: skipped encrypted file {rel}.")
                continue
            if self._looks_like_bomb(info):
                self._issue(
                    "suspicious-compression",
                    f"{origin}: skipped {rel} because it expands suspiciously (possible zip bomb).",
                )
                continue
            if posixpath.splitext(rel)[1].lower() in ZIP_EXTENSIONS:
                self._add_nested_zip(archive, info, path, origin, depth)
                continue
            if path in self._entries:
                # Split exports can contain the same file twice; the first copy wins.
                continue
            reader = _zip_reader(archive, info, self._budget)
            self._register(FileEntry(path, origin, info.file_size, reader))

    def _add_nested_zip(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo, path: str, origin: str, depth: int) -> None:
        if depth >= self.limits.max_zip_depth:
            self._issue("zip-too-deep", f"{origin}: skipped {path}; ZIP files are nested too deeply.")
            return
        if info.file_size > self.limits.max_nested_zip_bytes:
            self._issue("file-too-large", f"{origin}: skipped {path}; the inner ZIP is too large.")
            return
        spool = tempfile.SpooledTemporaryFile(max_size=64 * MB)
        self._closeables.append(spool)
        try:
            _copy_member(archive, info, spool, self.limits.max_nested_zip_bytes, self._budget)
        except (LoaderError, zipfile.BadZipFile, OSError, EOFError) as exc:
            self._issue("bad-zip", f"{origin}: could not read inner archive {path} ({exc}).")
            return
        spool.seek(0)
        # Merge the inner archive into the folder that contains it.
        self._add_zip_file(spool, posixpath.dirname(path), origin, depth + 1)  # type: ignore[arg-type]

    def _looks_like_bomb(self, info: zipfile.ZipInfo) -> bool:
        if info.file_size <= 8 * MB:
            return False
        compressed = max(info.compress_size, 1)
        return info.file_size / compressed > self.limits.max_compression_ratio

    # -- bookkeeping ------------------------------------------------------------

    def _register(self, entry: FileEntry) -> None:
        if len(self._entries) >= self.limits.max_entries:
            raise LoaderError(f"Too many files (limit {self.limits.max_entries}).")
        self._entries[entry.path] = entry
        self._casefold.setdefault(entry.path.casefold(), []).append(entry.path)
        self._by_basename.setdefault(posixpath.basename(entry.path).casefold(), []).append(entry.path)

    def _unique_path(self, path: str) -> str:
        if path not in self._entries:
            return path
        stem, ext = posixpath.splitext(path)
        counter = 2
        while f"{stem} ({counter}){ext}" in self._entries:
            counter += 1
        return f"{stem} ({counter}){ext}"

    def _issue(self, code: str, message: str, severity: Severity = Severity.WARNING) -> None:
        self.issues.append(Issue(code=code, message=message, severity=severity))

    def _report_unsafe(self) -> None:
        if self._skipped_unsafe:
            self._issue(
                "unsafe-paths",
                f"Skipped {self._skipped_unsafe} archive entr{'y' if self._skipped_unsafe == 1 else 'ies'} "
                "with unsafe names (absolute paths, '..' or links).",
            )
            self._skipped_unsafe = 0


def _bytes_reader(data: bytes) -> Callable[[int], bytes]:
    def read(limit: int) -> bytes:
        if len(data) > limit:
            raise LoaderError("File is larger than allowed.")
        return data

    return read


def _file_reader(path: Path) -> Callable[[int], bytes]:
    def read(limit: int) -> bytes:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise LoaderError(f"{path.name} is larger than allowed.")
        return data

    return read


def _zip_reader(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: _ByteBudget) -> Callable[[int], bytes]:
    counted = False

    def read(limit: int) -> bytes:
        nonlocal counted
        buffer = io.BytesIO()
        # Each member counts against the total budget once, however often it is
        # re-read (pages are read again on every conversion in a session).
        _copy_member(archive, info, buffer, limit, None if counted else budget)
        counted = True
        return buffer.getvalue()

    return read


def _copy_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: BinaryIO, limit: int, budget: _ByteBudget | None) -> None:
    """Stream a member into *target*, enforcing *limit* on the real (not declared) size."""
    if info.file_size > limit:
        raise LoaderError(f"{posixpath.basename(info.filename)} is larger than allowed.")
    total = 0
    try:
        with archive.open(info) as source:
            while True:
                chunk = source.read(1 * MB)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise LoaderError(f"{posixpath.basename(info.filename)} is larger than its header claims.")
                if budget is not None:
                    budget.consume(len(chunk))
                target.write(chunk)
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, NotImplementedError) as exc:
        raise LoaderError(f"Could not read {posixpath.basename(info.filename)} from the archive ({exc}).") from exc


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def load_uploads(files: Iterable[tuple[str, bytes]], limits: LoaderLimits = DEFAULT_LIMITS) -> InputSet:
    """Build an :class:`InputSet` from ``(filename, bytes)`` pairs."""
    inputs = InputSet(limits)
    for name, data in files:
        try:
            inputs.add_upload(name, data)
        except LoaderError as exc:
            inputs.issues.append(Issue("load-error", f"{name}: {exc}", Severity.ERROR))
    logger.info("Loaded %d upload(s): %d page(s), %d other file(s)", len(inputs.origins), len(inputs.pages), len(inputs.entries) - len(inputs.pages))
    return inputs


def load_paths(paths: Iterable[Path], limits: LoaderLimits = DEFAULT_LIMITS) -> InputSet:
    """Build an :class:`InputSet` from files or extracted export folders on disk."""
    inputs = InputSet(limits)
    for path in paths:
        try:
            if path.is_dir():
                inputs.add_directory(path)
            else:
                inputs.add_upload(path.name, path.read_bytes())
        except (LoaderError, OSError) as exc:
            inputs.issues.append(Issue("load-error", f"{path.name}: {exc}", Severity.ERROR))
    return inputs
