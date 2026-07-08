#!/usr/bin/env python3
"""Basic web UI for pdf2audiobook.

Paste text or drop a PDF/TXT file in the browser and get an audiobook file back.
Reuses the existing converter in src/pdf2audiobook.py by invoking it as a
subprocess (a fresh process per request keeps the pyttsx3 engine happy).
"""

import os
import sys
import subprocess
import tempfile

from flask import Flask, request, render_template, send_file, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(BASE_DIR, "src", "pdf2audiobook.py")
ALLOWED_EXT = {".pdf", ".txt"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload


def _safe_basename(name, fallback="audiobook"):
    """Strip anything that isn't a safe filename character."""
    name = (name or "").strip()
    cleaned = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    return cleaned or fallback


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    engine = request.form.get("engine", "pyttsx3")
    if engine not in ("pyttsx3", "gtts"):
        return jsonify(error=f"Unknown engine: {engine}"), 400

    lang = request.form.get("lang", "en") or "en"
    out_base = _safe_basename(request.form.get("filename"))
    text = (request.form.get("text") or "").strip()
    uploaded = request.files.get("file")

    workdir = tempfile.mkdtemp(prefix="p2a_")

    # Decide the input: an uploaded file takes precedence over pasted text.
    if uploaded and uploaded.filename:
        ext = os.path.splitext(uploaded.filename)[1].lower()
        if ext not in ALLOWED_EXT:
            return jsonify(error=f"Unsupported file type '{ext or 'unknown'}'. Please use a PDF or TXT file."), 400
        input_path = os.path.join(workdir, "input" + ext)
        uploaded.save(input_path)
    elif text:
        input_path = os.path.join(workdir, "input.txt")
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        return jsonify(error="Nothing to convert. Paste some text or add a PDF/TXT file."), 400

    # pyttsx3 (SAPI5 on Windows) writes WAV; gTTS writes MP3.
    out_ext = ".mp3" if engine == "gtts" else ".wav"
    output_path = os.path.join(workdir, out_base + out_ext)

    cmd = [sys.executable, SCRIPT, input_path, "-o", output_path, "-e", engine, "-l", lang]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return jsonify(error="Conversion timed out (over 30 minutes). Try shorter text."), 504

    if proc.returncode != 0 or not os.path.exists(output_path):
        detail = (proc.stderr or proc.stdout or "Conversion failed.").strip()
        # Surface the common "missing dependency" case more clearly.
        if "ModuleNotFoundError" in detail or "No module named" in detail:
            detail += "\n\nA required package isn't installed. Re-run run.bat, or: pip install -r requirements.txt"
        if engine == "gtts" and any(k in detail.lower() for k in ("urlopen", "connection", "timed out", "getaddrinfo", "network")):
            detail += "\n\nThe online (gTTS) engine needs an internet connection. Switch to the Offline engine to work without internet."
        return jsonify(error=detail), 500

    return send_file(output_path, as_attachment=True, download_name=out_base + out_ext)


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    print("\n  Audiobook Creator UI is running.")
    print(f"  Open this in your browser:  {url}\n")
    app.run(host="127.0.0.1", port=5000, threaded=True, debug=False)
