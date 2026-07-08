#!/usr/bin/env python3
"""Convert PDF and TXT files into audiobooks (MP3/WAV)."""

import argparse
import os
import re
import sys


def extract_text_from_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text_from_txt(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def clean_text(text):
    text = re.sub(r"-\n(\w)", r"\1", text)      # de-hyphenate line breaks
    text = re.sub(r"\s*\n\s*", " ", text)         # collapse newlines
    text = re.sub(r"\s{2,}", " ", text)           # collapse spaces
    return text.strip()


def synthesize(text, out_path, engine="gtts", lang="en", voice=None, rate=175):
    if engine == "gtts":
        from gtts import gTTS
        # gTTS splits long text into fragments internally and writes them to a
        # single concatenated MP3, so no ffmpeg/pydub stitching is needed.
        gTTS(text=text, lang=lang).save(out_path)
    elif engine == "pyttsx3":
        import pyttsx3
        eng = pyttsx3.init()
        eng.setProperty("rate", rate)
        if voice:
            eng.setProperty("voice", voice)
        eng.save_to_file(text, out_path)
        eng.runAndWait()
    else:
        raise ValueError(f"Unknown engine: {engine}")


def convert(input_path, output_path=None, engine="gtts", lang="en", voice=None, rate=175):
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".pdf":
        raw = extract_text_from_pdf(input_path)
    elif ext == ".txt":
        raw = extract_text_from_txt(input_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    text = clean_text(raw)
    if not text:
        raise ValueError("No extractable text found. If this is a scanned PDF, it has no selectable text.")

    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + ".mp3"

    synthesize(text, output_path, engine=engine, lang=lang, voice=voice, rate=rate)
    return output_path


def main():
    p = argparse.ArgumentParser(description="Convert PDF/TXT files into audiobooks.")
    p.add_argument("input", help="Path to a .pdf or .txt file")
    p.add_argument("-o", "--output", help="Output audio path")
    p.add_argument("-e", "--engine", choices=["gtts", "pyttsx3"], default="gtts",
                   help="TTS engine (gtts=online, pyttsx3=offline)")
    p.add_argument("-l", "--lang", default="en", help="Language code (gtts only)")
    p.add_argument("-v", "--voice", help="Voice id (pyttsx3 only)")
    p.add_argument("-r", "--rate", type=int, default=175, help="Speech rate (pyttsx3 only)")
    args = p.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"Error: file not found: {args.input}")

    try:
        out = convert(args.input, args.output, args.engine, args.lang, args.voice, args.rate)
        print(f"Audiobook written to: {out}")
    except Exception as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
