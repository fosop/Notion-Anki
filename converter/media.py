"""Local media: resolving image/audio references, naming files for Anki, and
building the optional media bundle.

Anki keeps all media in one flat folder (``collection.media``) that is shared by
every deck, so names must be unique and contain no folders. Each file becomes::

    <ascii-slug-of-original-name>_<12 hex chars of SHA-256 of the content><ext>

That name is *stable* (the same file always gets the same name) and *unique*
(two different ``image.png`` files from different export folders get different
names). Identical files are stored once.

Nothing is downloaded. Web images stay as web references, and data: URIs are
decoded locally.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
import posixpath
import re
import unicodedata
import urllib.parse
import zipfile
from dataclasses import dataclass
from typing import Mapping

from .loader import MB, InputSet, LoaderError
from .models import MediaFile
from .sanitize import URL_LOCAL, URL_WEB, classify_url, local_path

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif", ".ico", ".tif", ".tiff", ".heic"})
BROWSER_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif", ".ico"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac", ".opus", ".weba"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".ogv", ".wmv"})
HTML_LINK_EXTENSIONS = frozenset({".html", ".htm"})

MEDIA_FOLDER = "media"

_MIME_EXTENSIONS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp", "image/avif": ".avif",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/flac": ".flac",
}
_EXTENSION_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".avif": "image/avif",
    ".ico": "image/x-icon", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
    ".opus": "audio/ogg", ".weba": "audio/webm",
}
_DATA_URI = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?((?:;[\w.+-]+=[^;,]*)*)(;base64)?,(.*)$", re.IGNORECASE | re.DOTALL)
_ALREADY_COMPRESSED = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".heic", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"})

STATUS_LOCAL = "local"
STATUS_EXTERNAL = "external"
STATUS_MISSING = "missing"
STATUS_UNSAFE = "unsafe"
STATUS_TOO_LARGE = "too-large"


@dataclass(frozen=True)
class MediaResolution:
    status: str
    filename: str | None = None
    url: str | None = None
    display_name: str = ""
    code: str = ""
    message: str = ""


def media_filename(original_name: str, data: bytes) -> str:
    """A flat, Anki-safe, content-addressed filename for a media file."""
    base = posixpath.basename(original_name.replace("\\", "/"))
    stem, ext = posixpath.splitext(base)
    ext = ext.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        stem, ext = base, sniff_extension(data) or ""
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_stem).strip("_")[:40] or "media"
    digest = hashlib.sha256(data).hexdigest()[:12]
    return f"{slug}_{digest}{ext}"


def sniff_extension(data: bytes) -> str | None:
    """Guess a file extension from the first bytes of the file."""
    head = data[:64]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if head.startswith(b"OggS"):
        return ".ogg"
    if head[4:8] == b"ftyp" and head[8:11] in (b"M4A", b"mp4", b"iso"):
        return ".m4a"
    stripped = head.lstrip()
    if stripped.startswith(b"<svg") or (stripped.startswith(b"<?xml") and b"<svg" in data[:1024]):
        return ".svg"
    return None


def mime_type_for(filename: str) -> str:
    return _EXTENSION_MIME.get(posixpath.splitext(filename)[1].lower(), "application/octet-stream")


def zip_compression_for(filename: str) -> int:
    """Store already-compressed formats as they are; deflate everything else."""
    ext = posixpath.splitext(filename)[1].lower()
    return zipfile.ZIP_STORED if ext in _ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED


def as_data_uri(media: MediaFile) -> str:
    """A ``data:`` URI for previewing a media file in the browser (preview only)."""
    return f"data:{mime_type_for(media.filename)};base64,{base64.b64encode(media.data).decode('ascii')}"


class MediaCollector:
    """Resolves media references for one conversion and collects the files used."""

    def __init__(
        self,
        inputs: InputSet | None,
        max_file_bytes: int = 200 * MB,
        max_total_bytes: int = 4096 * MB,
    ) -> None:
        self.inputs = inputs
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.files: dict[str, MediaFile] = {}
        self._cache: dict[tuple[str, str, str], MediaResolution] = {}
        self._total = 0

    def resolve(self, ref: str | None, page_path: str, kind: str) -> MediaResolution:
        """Resolve *ref* (an ``src``/``href`` value) found on *page_path*.

        *kind* is ``"image"`` or ``"audio"``.
        """
        key = (ref or "", posixpath.dirname(page_path), kind)
        cached = self._cache.get(key)
        if cached is None:
            cached = self._resolve(ref or "", page_path, kind)
            self._cache[key] = cached
        return cached

    # -- internals ---------------------------------------------------------

    def _resolve(self, ref: str, page_path: str, kind: str) -> MediaResolution:
        raw = ref.strip()
        noun = "Image" if kind == "image" else "Audio file"
        if not raw:
            return MediaResolution(STATUS_MISSING, code="media-missing", message=f"An {kind} element has no source.")
        if raw[:5].lower() == "data:":
            return self._from_data_uri(raw, kind)

        url_kind, value = classify_url(raw)
        if url_kind == URL_WEB:
            if value.lower().startswith("mailto:"):
                return MediaResolution(STATUS_UNSAFE, display_name=value, code="unsafe-media", message=f"{noun} source is not a file.")
            return MediaResolution(STATUS_EXTERNAL, url=value, display_name=_short_url(value), code=f"external-{kind}")
        if url_kind != URL_LOCAL:
            return MediaResolution(
                STATUS_UNSAFE,
                display_name="unsupported address",
                code="unsafe-media",
                message=f"{noun} with an unsupported address (for example file: or javascript:) was removed.",
            )

        rel = local_path(value)
        display = posixpath.basename(rel) or rel
        if self.inputs is None or not self.inputs.has_assets:
            return MediaResolution(
                STATUS_MISSING,
                display_name=display,
                code="media-not-uploaded",
                message=(
                    f"'{display}' is not part of the upload. Upload the complete Notion ZIP export "
                    "to include images and audio."
                ),
            )
        entry = self._find(rel, value, page_path)
        if entry is None:
            return MediaResolution(
                STATUS_MISSING,
                display_name=display,
                code="media-missing",
                message=f"{noun} '{display}' is referenced but was not found in the export.",
            )
        if entry.size > self.max_file_bytes:
            return MediaResolution(
                STATUS_TOO_LARGE,
                display_name=display,
                code="media-too-large",
                message=f"{noun} '{display}' is larger than {self.max_file_bytes // MB} MB and was not included.",
            )
        try:
            data = entry.read(self.max_file_bytes)
        except LoaderError as exc:
            return MediaResolution(STATUS_MISSING, display_name=display, code="media-unreadable", message=f"{noun} '{display}' could not be read ({exc}).")
        return self._register(entry.name, data, kind, entry.path, display)

    def _find(self, rel: str, raw: str, page_path: str):
        assert self.inputs is not None
        base = posixpath.dirname(page_path)
        candidates = [rel]
        raw_path = raw.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
        if raw_path != rel:
            candidates.append(raw_path)  # a file literally named "a%20b.png"
        for candidate in candidates:
            for joined in (posixpath.join(base, candidate) if base else candidate, candidate):
                normal = posixpath.normpath(joined)
                if normal.startswith("../") or normal in ("..", ".") or normal.startswith("/"):
                    continue
                entry = self.inputs.get(normal)
                if entry is not None and not entry.is_html:
                    return entry
        # Last resort: a *unique* file with the same name anywhere in the upload.
        matches = [e for e in self.inputs.find_by_basename(posixpath.basename(rel)) if not e.is_html]
        return matches[0] if len(matches) == 1 else None

    def _register(self, original_name: str, data: bytes, kind: str, source: str, display: str) -> MediaResolution:
        filename = media_filename(original_name, data)
        existing = self.files.get(filename)
        if existing is None:
            if self._total + len(data) > self.max_total_bytes:
                return MediaResolution(
                    STATUS_TOO_LARGE,
                    display_name=display,
                    code="media-too-large",
                    message=f"The media files exceed {self.max_total_bytes // MB} MB in total; '{display}' was not included.",
                )
            self._total += len(data)
            existing = MediaFile(filename=filename, data=data, kind=kind)
            self.files[filename] = existing
        if source not in existing.sources:
            existing.sources.append(source)
        ext = posixpath.splitext(filename)[1]
        message = ""
        if kind == "image" and ext not in BROWSER_IMAGE_EXTENSIONS:
            message = f"Image '{display}' uses a format ({ext or 'unknown'}) that Anki may not be able to display."
        return MediaResolution(STATUS_LOCAL, filename=filename, display_name=display, code="media-format" if message else "", message=message)

    def _from_data_uri(self, raw: str, kind: str) -> MediaResolution:
        match = _DATA_URI.match(raw)
        mime = (match.group(1) or "").lower() if match else ""
        ext = _MIME_EXTENSIONS.get(mime)
        if match is None or ext is None or not mime.startswith(f"{kind}/"):
            return MediaResolution(STATUS_UNSAFE, display_name="embedded data", code="unsafe-media", message=f"An embedded {kind} with an unsupported type was removed.")
        payload = match.group(4)
        if len(payload) > self.max_file_bytes * 4 // 3 + 1024:
            return MediaResolution(STATUS_TOO_LARGE, display_name="embedded data", code="media-too-large", message=f"An embedded {kind} is too large and was not included.")
        try:
            if match.group(3):
                data = base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
            else:
                data = urllib.parse.unquote_to_bytes(payload)
        except (binascii.Error, ValueError):
            return MediaResolution(STATUS_MISSING, display_name="embedded data", code="media-unreadable", message=f"An embedded {kind} could not be decoded.")
        return self._register(f"embedded_{kind}{ext}", data, kind, "(embedded data URI)", "embedded data")


def _short_url(url: str, limit: int = 80) -> str:
    return url if len(url) <= limit else url[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# The optional media bundle
# ---------------------------------------------------------------------------


def build_media_bundle(export_filename: str, export_data: bytes, media: Mapping[str, MediaFile]) -> bytes:
    """A ZIP holding the card file, a flat ``media/`` folder and a README.

    The media folder contains files only, never sub-folders, because Anki's
    ``collection.media`` folder must stay flat.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(export_filename, export_data)
        for name in sorted(media):
            if "/" in name or "\\" in name:  # defensive: names are generated flat
                raise ValueError(f"Media filename must be flat: {name!r}")
            bundle.writestr(f"{MEDIA_FOLDER}/{name}", media[name].data, compress_type=zip_compression_for(name))
        bundle.writestr("README.txt", bundle_readme(export_filename, len(media)))
    return buffer.getvalue()


def bundle_readme(export_filename: str, media_count: int) -> str:
    return f"""Notion -> Anki export
=====================

This bundle contains:
  {export_filename}   the flashcards (Front / Back)
  {MEDIA_FOLDER}/       {media_count} image/audio file(s) used by the cards

1. Copy the media files into Anki's media folder
------------------------------------------------
Copy every file INSIDE the "{MEDIA_FOLDER}" folder (not the folder itself) into
your Anki profile's "collection.media" folder. Do not create sub-folders there,
because Anki only looks at files placed directly in collection.media.

Default locations (replace "User 1" with your Anki profile name):
  Windows: %APPDATA%\\Anki2\\User 1\\collection.media
  macOS:   ~/Library/Application Support/Anki2/User 1/collection.media
           (in Finder, hold Option and choose Go > Library)
  Linux:   ~/.local/share/Anki2/User 1/collection.media

Close Anki first, or run Tools > Check Media afterwards.

2. Import the cards
-------------------
In Anki: File > Import, then choose {export_filename}.
  * Note type: Basic (or any note type whose first two fields are Front and Back)
  * Field separator: detected automatically from the file header
  * Make sure "Allow HTML in fields" is ON. Otherwise formatting, images and
    equations will show up as raw code.
  * Map column 1 to Front and column 2 to Back.

Images and audio only appear once their files are in collection.media.
"""
