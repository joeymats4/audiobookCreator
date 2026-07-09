#!/usr/bin/env python3
"""Search public-domain book catalogs and download books.

Two legal, free sources with real APIs (no scraping of piracy sites):
  - Project Gutenberg via the Gutendex API (clean plain text — best for TTS)
  - The Internet Archive advanced-search API (scanned-book PDFs / full text)

Downloads are SSRF-safe: callers pass only a (source, id); this module
re-resolves the real file via the fixed API hosts and never fetches an
arbitrary caller-supplied URL.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests

USER_AGENT = "audiobookCreator/1.0 (local app; book search)"
TIMEOUT = 30
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024  # 80 MB safety cap per file

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


class DownloadTooLarge(Exception):
    """Raised when a remote file exceeds MAX_DOWNLOAD_BYTES."""


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
def search(query, limit=20):
    """Return a merged, normalized list of results from both sources.

    Each result: {source, id, title, author, year, kind}
    where source in {"gutenberg", "archive"} and kind in {"text", "pdf"}.
    A failure in one source never fails the whole search.
    """
    query = (query or "").strip()
    if not query:
        return []

    # Query both catalogs in parallel — latency is max() of the two round
    # trips instead of their sum, which matters for search-as-you-type.
    # (Each helper swallows its own errors and returns [].)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_gut = ex.submit(_search_gutenberg, query, limit)
        f_arc = ex.submit(_search_archive, query, limit)
        gutenberg = f_gut.result()
        archive = f_arc.result()

    # Interleave so both sources are represented, then trim to `limit`.
    merged = []
    for i in range(max(len(gutenberg), len(archive))):
        if i < len(gutenberg):
            merged.append(gutenberg[i])
        if i < len(archive):
            merged.append(archive[i])
    return merged[:limit]


def _search_gutenberg(query, limit):
    try:
        r = _session.get("https://gutendex.com/books",
                         params={"search": query}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    out = []
    for b in data.get("results", []):
        if not _gutenberg_text_url(b.get("formats", {})):
            continue  # only keep books that offer plain text
        authors = b.get("authors") or []
        out.append({
            "source": "gutenberg",
            "id": str(b.get("id")),
            "title": b.get("title") or "(untitled)",
            "author": authors[0]["name"] if authors else "",
            "year": "",
            "kind": "text",
        })
        if len(out) >= limit:
            break
    return out


def _search_archive(query, limit):
    q = f"({query}) AND mediatype:texts AND NOT collection:inlibrary"
    params = {
        "q": q,
        "fl[]": ["identifier", "title", "creator", "year"],
        "sort[]": "downloads desc",
        "rows": limit,
        "output": "json",
    }
    try:
        r = _session.get("https://archive.org/advancedsearch.php",
                         params=params, timeout=TIMEOUT)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except Exception:
        return []

    out = []
    for d in docs:
        ident = d.get("identifier")
        if not ident:
            continue
        out.append({
            "source": "archive",
            "id": ident,
            "title": _as_text(d.get("title")) or ident,
            "author": _as_text(d.get("creator")),
            "year": _as_text(d.get("year")),
            "kind": "text",  # we prefer IA's OCR full text over the PDF
        })
    return out


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------
def download(source, book_id, dest_dir):
    """Download the best file for (source, book_id) into dest_dir.

    Returns (path, filename, ext). Raises ValueError with a user-friendly
    message on any problem.
    """
    os.makedirs(dest_dir, exist_ok=True)
    if source == "gutenberg":
        return _download_gutenberg(book_id, dest_dir)
    if source == "archive":
        return _download_archive(book_id, dest_dir)
    raise ValueError(f"Unknown source: {source}")


def _download_gutenberg(book_id, dest_dir):
    if not str(book_id).isdigit():
        raise ValueError("Invalid Gutenberg book id.")
    try:
        r = _session.get(f"https://gutendex.com/books/{book_id}", timeout=TIMEOUT)
        r.raise_for_status()
        book = r.json()
    except Exception as e:
        raise ValueError(f"Could not look up that book on Gutenberg: {e}")

    url = _gutenberg_text_url(book.get("formats", {}))
    if not url:
        raise ValueError("No plain-text edition is available for this book.")

    # Gutenberg texts are small (~1 MB); buffering is fine and the boilerplate
    # strip needs the whole text anyway.
    raw = _download_bytes(url).decode("utf-8", errors="ignore")
    text = _strip_gutenberg_boilerplate(raw)
    base = _file_base("gutenberg", book_id, book.get("title"))
    filename = base + ".txt"
    path = os.path.join(dest_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path, filename, ".txt"


def _download_archive(book_id, dest_dir):
    if not _valid_archive_id(book_id):
        raise ValueError("Invalid Internet Archive identifier.")
    try:
        r = _session.get(f"https://archive.org/metadata/{book_id}", timeout=TIMEOUT)
        r.raise_for_status()
        meta = r.json()
    except Exception as e:
        raise ValueError(f"Could not look up that item on the Internet Archive: {e}")

    if not meta or "files" not in meta:
        raise ValueError("Item not found on the Internet Archive.")
    if str(meta.get("metadata", {}).get("access-restricted-item", "")).lower() == "true":
        raise ValueError("This item is lending-only and can't be downloaded.")

    pdf_name, txt_name = _archive_candidates(meta.get("files", []))
    base = _file_base("archive", book_id, _as_text(meta.get("metadata", {}).get("title")))

    # Prefer IA's OCR full text: it always extracts (image-only PDFs don't),
    # is far smaller, and reads more cleanly for TTS. PDF is the fallback.
    if txt_name:
        path, filename = _stream_to_file(
            f"https://archive.org/download/{quote(str(book_id))}/{quote(txt_name)}",
            dest_dir, base, ".txt")
        return path, filename, ".txt"
    if pdf_name:
        try:
            path, filename = _stream_to_file(
                f"https://archive.org/download/{quote(str(book_id))}/{quote(pdf_name)}",
                dest_dir, base, ".pdf")
            return path, filename, ".pdf"
        except DownloadTooLarge:
            raise ValueError("The PDF is too large to download and no text version exists.")

    raise ValueError("No downloadable text or PDF file for this item.")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _gutenberg_text_url(formats):
    """Pick a plain-text URL from a Gutendex formats map, preferring UTF-8."""
    candidates = [(mime, url) for mime, url in (formats or {}).items()
                  if mime.startswith("text/plain") and not str(url).endswith(".zip")]
    if not candidates:
        return None
    for mime, url in candidates:
        if "utf-8" in mime.lower():
            return url
    return candidates[0][1]


def _archive_candidates(files):
    """Return (pdf_name, txt_name) — the best PDF and best full-text file names."""
    pdf = txt = None
    for f in files:
        name = f.get("name", "")
        fmt = (f.get("format") or "").lower()
        low = name.lower()
        if pdf is None and (fmt == "text pdf" or low.endswith(".pdf")):
            pdf = name
        if txt is None and low.endswith("_djvu.txt"):
            txt = name
    return pdf, txt


def _file_base(source, book_id, title):
    """Unique, human-readable filename base: '<source>-<id> <title>'.

    The id prefix guarantees two different books never collide even when
    their sanitized titles are identical.
    """
    ident = _safe_name(str(book_id), maxlen=40) or "book"
    title_part = _safe_name(title or "", maxlen=60)
    return f"{source}-{ident} {title_part}".strip()


def _download_bytes(url):
    """Buffer a (small) file in memory, size-capped. Use for Gutenberg texts."""
    with _session.get(url, timeout=TIMEOUT, stream=True) as r:
        r.raise_for_status()
        chunks, total = [], 0
        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise DownloadTooLarge(f"File exceeds the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit.")
            chunks.append(chunk)
        return b"".join(chunks)


def _stream_to_file(url, dest_dir, base, ext):
    """Stream a (possibly large) file straight to disk, size-capped.

    Keeps memory flat at the chunk size; a partial file is removed on failure.
    """
    filename = base + ext
    path = os.path.join(dest_dir, filename)
    total = 0
    try:
        with _session.get(url, timeout=TIMEOUT, stream=True) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise DownloadTooLarge(
                            f"File exceeds the {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limit.")
                    f.write(chunk)
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path, filename


def _strip_gutenberg_boilerplate(text):
    """Drop the Project Gutenberg license header/footer around the actual book."""
    start = re.search(r"\*\*\*\s*START OF TH(?:E|IS) PROJECT GUTENBERG.*?\*\*\*",
                      text, re.IGNORECASE | re.DOTALL)
    end = re.search(r"\*\*\*\s*END OF TH(?:E|IS) PROJECT GUTENBERG.*?\*\*\*",
                    text, re.IGNORECASE | re.DOTALL)
    s = start.end() if start else 0
    e = end.start() if end else len(text)
    return text[s:e].strip() or text.strip()


def _safe_name(name, maxlen=80):
    name = (name or "").strip()
    cleaned = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:maxlen].strip() or "book"


def _valid_archive_id(x):
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", str(x or "")))


def _as_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)
