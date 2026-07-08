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

    gutenberg = _search_gutenberg(query, limit)
    archive = _search_archive(query, limit)

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
            "kind": "pdf",
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

    raw = _download_bytes(url).decode("utf-8", errors="ignore")
    text = _strip_gutenberg_boilerplate(raw)
    base = _safe_name(book.get("title") or f"gutenberg-{book_id}")
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
    base = _safe_name(_as_text(meta.get("metadata", {}).get("title")) or book_id)

    # Prefer the PDF; fall back to full text if the PDF is over the size cap.
    if pdf_name:
        try:
            data = _download_bytes(f"https://archive.org/download/{quote(str(book_id))}/{quote(pdf_name)}")
            return _write_bytes(dest_dir, base, ".pdf", data)
        except DownloadTooLarge:
            if not txt_name:
                raise ValueError("The PDF is too large to download and no text version exists.")
    if txt_name:
        data = _download_bytes(f"https://archive.org/download/{quote(str(book_id))}/{quote(txt_name)}")
        return _write_bytes(dest_dir, base, ".txt", data)

    raise ValueError("No downloadable PDF or text file for this item.")


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


def _download_bytes(url):
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


def _write_bytes(dest_dir, base, ext, data):
    filename = base + ext
    path = os.path.join(dest_dir, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path, filename, ext


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
