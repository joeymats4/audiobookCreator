#!/usr/bin/env python3
"""Convert PDF and TXT files into audiobooks (MP3/WAV).

Both engines synthesize in chunks so long books report progress and survive
transient network errors. With --progress, machine-readable lines are printed
to stdout for a supervising process (the web UI):

    TOTAL <n>        announced once, number of chunks
    PROGRESS <i> <n> after each finished chunk
"""

import argparse
import os
import re
import sys
import time
import wave

GTTS_CHUNK = 2500       # chars per gTTS request batch
PYTTSX3_CHUNK = 2000    # chars per offline synthesis piece
GTTS_RETRIES = 3


def extract_text_from_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text_from_txt(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_text(input_path):
    """Extract and clean text from a .pdf or .txt file."""
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".pdf":
        raw = extract_text_from_pdf(input_path)
    elif ext == ".txt":
        raw = extract_text_from_txt(input_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    return clean_text(raw)


def clean_text(text):
    text = re.sub(r"-\n(\w)", r"\1", text)      # de-hyphenate line breaks
    text = re.sub(r"\s*\n\s*", " ", text)         # collapse newlines
    text = re.sub(r"\s{2,}", " ", text)           # collapse spaces
    return text.strip()


def chunk_text(text, size):
    """Split text into ~size-char chunks on word boundaries."""
    words, chunks, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > size:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [""]


def _emit(progress, msg):
    if progress:
        print(msg, flush=True)


def synthesize(text, out_path, engine="gtts", lang="en", tld="com",
               voice=None, rate=175, progress=False):
    if engine == "gtts":
        _synthesize_gtts(text, out_path, lang, tld, progress)
    elif engine == "pyttsx3":
        _synthesize_pyttsx3(text, out_path, voice, rate, progress)
    else:
        raise ValueError(f"Unknown engine: {engine}")


def _synthesize_gtts(text, out_path, lang, tld, progress):
    """Online engine: one concatenated MP3, chunked with per-chunk retry.

    MP3 frames are self-contained, so appending each chunk's stream to one
    file yields a single playable MP3 without ffmpeg.
    """
    from gtts import gTTS
    chunks = chunk_text(text, GTTS_CHUNK)
    _emit(progress, f"TOTAL {len(chunks)}")
    with open(out_path, "wb") as f:
        for i, chunk in enumerate(chunks, 1):
            last_err = None
            for attempt in range(1, GTTS_RETRIES + 1):
                try:
                    gTTS(text=chunk, lang=lang, tld=tld).write_to_fp(f)
                    last_err = None
                    break
                except Exception as e:  # network blip / rate limit — retry
                    last_err = e
                    if attempt < GTTS_RETRIES:
                        time.sleep(2 * attempt)
            if last_err is not None:
                raise RuntimeError(
                    f"Online synthesis failed on chunk {i}/{len(chunks)} "
                    f"after {GTTS_RETRIES} attempts: {last_err}"
                )
            _emit(progress, f"PROGRESS {i} {len(chunks)}")


def _synthesize_pyttsx3(text, out_path, voice, rate, progress):
    """Offline engine: synthesize chunks to temp WAVs, concatenate via stdlib.

    All utterances are queued up front and processed by a single runAndWait()
    (repeated runAndWait() calls deadlock on Windows SAPI); a
    finished-utterance callback emits per-chunk progress. All pieces come from
    the same voice/rate, so their WAV parameters match and frames can be
    appended directly with the wave module.
    """
    import pyttsx3
    import threading
    chunks = chunk_text(text, PYTTSX3_CHUNK)
    _emit(progress, f"TOTAL {len(chunks)}")

    eng = pyttsx3.init()
    eng.setProperty("rate", rate)
    if voice:
        eng.setProperty("voice", voice)

    pieces = [f"{out_path}.part{i}.wav" for i in range(1, len(chunks) + 1)]
    for chunk, piece in zip(chunks, pieces):
        eng.save_to_file(chunk, piece)

    # SAPI writes piece files strictly in order, so piece i is finished once
    # piece i+1 exists. (The driver doesn't fire finished-utterance callbacks
    # for file output, so we watch the filesystem instead.)
    stop_watch = threading.Event()

    def _watch():
        emitted = 0
        while not stop_watch.wait(0.5):
            existing = sum(1 for p in pieces if os.path.exists(p))
            done = max(0, min(existing - 1, len(chunks) - 1))
            if done > emitted:
                emitted = done
                _emit(progress, f"PROGRESS {done} {len(chunks)}")

    watcher = threading.Thread(target=_watch, daemon=True)
    if progress:
        watcher.start()
    try:
        eng.runAndWait()
    finally:
        stop_watch.set()
        if progress:
            watcher.join(timeout=2)
    _emit(progress, f"PROGRESS {len(chunks)} {len(chunks)}")

    out = None
    try:
        for piece in pieces:
            with wave.open(piece, "rb") as r:
                if out is None:
                    out = wave.open(out_path, "wb")
                    out.setparams(r.getparams())
                out.writeframes(r.readframes(r.getnframes()))
            os.remove(piece)
    finally:
        if out is not None:
            out.close()


def list_voices():
    """Print installed offline voices as 'id|name' lines."""
    import pyttsx3
    eng = pyttsx3.init()
    for v in eng.getProperty("voices"):
        print(f"{v.id}|{v.name}", flush=True)


def convert(input_path, output_path=None, engine="gtts", lang="en", tld="com",
            voice=None, rate=175, progress=False, max_chars=None):
    text = extract_text(input_path)
    if not text:
        raise ValueError("No extractable text found. If this is a scanned PDF, it has no selectable text.")
    if max_chars:
        text = text[:max_chars]

    if output_path is None:
        default_ext = ".mp3" if engine == "gtts" else ".wav"
        output_path = os.path.splitext(input_path)[0] + default_ext

    synthesize(text, output_path, engine=engine, lang=lang, tld=tld,
               voice=voice, rate=rate, progress=progress)
    return output_path


def main():
    p = argparse.ArgumentParser(description="Convert PDF/TXT files into audiobooks.")
    p.add_argument("input", nargs="?", help="Path to a .pdf or .txt file")
    p.add_argument("-o", "--output", help="Output audio path")
    p.add_argument("-e", "--engine", choices=["gtts", "pyttsx3"], default="gtts",
                   help="TTS engine (gtts=online, pyttsx3=offline)")
    p.add_argument("-l", "--lang", default="en", help="Language code (gtts only)")
    p.add_argument("-t", "--tld", default="com",
                   help="Google TTS accent domain, e.g. co.uk (gtts only)")
    p.add_argument("-v", "--voice", help="Voice id (pyttsx3 only)")
    p.add_argument("-r", "--rate", type=int, default=175, help="Speech rate (pyttsx3 only)")
    p.add_argument("--progress", action="store_true",
                   help="Print TOTAL/PROGRESS lines to stdout for a supervisor")
    p.add_argument("--max-chars", type=int, default=None,
                   help="Only synthesize the first N characters (for previews)")
    p.add_argument("--list-voices", action="store_true",
                   help="List installed offline voices as 'id|name' lines and exit")
    args = p.parse_args()

    if args.list_voices:
        list_voices()
        return

    if not args.input:
        p.error("input file is required (or use --list-voices)")
    if not os.path.isfile(args.input):
        sys.exit(f"Error: file not found: {args.input}")

    try:
        out = convert(args.input, args.output, args.engine, args.lang, args.tld,
                      args.voice, args.rate, progress=args.progress,
                      max_chars=args.max_chars)
        print(f"Audiobook written to: {out}")
    except Exception as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
