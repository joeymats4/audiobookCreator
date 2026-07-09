#!/usr/bin/env python3
"""Web UI for pdf2audiobook.

Paste text, drop a PDF/TXT, or pick a public-domain book; conversion runs as a
background job with live progress; finished audiobooks land in outputs/ (the
Library) and can be auto-uploaded to Google Drive.

Synthesis runs in a subprocess (a fresh process per job keeps the pyttsx3
SAPI engine happy); the worker thread supervises it via TOTAL/PROGRESS lines
on stdout.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from flask import Flask, request, render_template, send_file, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
SCRIPT = os.path.join(SRC_DIR, "pdf2audiobook.py")
DOWNLOADS = os.path.join(BASE_DIR, "downloads")
OUTPUTS = os.path.join(BASE_DIR, "outputs")
ALLOWED_EXT = {".pdf", ".txt"}
AUDIO_EXT = {".mp3", ".wav"}

sys.path.insert(0, SRC_DIR)
import book_search    # noqa: E402  (imported after src/ is added to sys.path)
import drive          # noqa: E402
import pdf2audiobook  # noqa: E402  (text extraction only; synthesis stays in a subprocess)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload

# Accent variants map to a base language + Google TTS domain.
GTTS_TLD = {"en-uk": ("en", "co.uk")}

# Rough words-per-minute for duration estimates. pyttsx3's rate property is
# approximately wpm; gTTS speaks at a fixed ~170 wpm.
GTTS_WPM = 170

JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_TTL = 3600  # forget finished jobs after an hour


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _safe_basename(name, fallback="audiobook"):
    """Strip anything that isn't a safe filename character."""
    name = (name or "").strip()
    cleaned = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    return cleaned[:80] or fallback


def _resolve_in(directory, name, allowed_ext):
    """Safely resolve a basename inside `directory` (no traversal, right type)."""
    if not name or os.path.basename(name) != name:
        return None
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        return None
    if os.path.splitext(name)[1].lower() not in allowed_ext:
        return None
    return path


def _unique_path(directory, base, ext):
    """Return a path in `directory` that doesn't collide, appending (2), (3)…"""
    candidate = os.path.join(directory, base + ext)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({n}){ext}")
        n += 1
    return candidate


def _estimate_seconds(words, engine, rate):
    wpm = max(80, rate) if engine == "pyttsx3" else GTTS_WPM
    return int(words / wpm * 60)


def _prune_jobs():
    now = time.time()
    with JOBS_LOCK:
        dead = [jid for jid, j in JOBS.items()
                if j["state"] in ("done", "error", "cancelled")
                and now - j["updated"] > JOB_TTL]
        for jid in dead:
            JOBS.pop(jid, None)


# --------------------------------------------------------------------------
# Pages & search
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error="File is too large — the upload limit is 50 MB."), 413


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(results=[])
    try:
        return jsonify(results=book_search.search(q, limit=20))
    except Exception as e:
        return jsonify(error=f"Search failed: {e}"), 502


@app.route("/fetch", methods=["POST"])
def fetch():
    source = request.form.get("source", "")
    book_id = request.form.get("id", "")
    if source not in ("gutenberg", "archive") or not book_id:
        return jsonify(error="Missing or invalid book selection."), 400
    try:
        _path, filename, ext = book_search.download(source, book_id, DOWNLOADS)
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(filename=filename, ext=ext)


# --------------------------------------------------------------------------
# Voices
# --------------------------------------------------------------------------
_VOICES_CACHE = None


@app.route("/voices")
def voices():
    global _VOICES_CACHE
    if _VOICES_CACHE is None:
        try:
            proc = subprocess.run([sys.executable, SCRIPT, "--list-voices"],
                                  capture_output=True, text=True, timeout=30)
            found = []
            for line in (proc.stdout or "").splitlines():
                if "|" in line:
                    vid, name = line.split("|", 1)
                    found.append({"id": vid.strip(), "name": name.strip()})
            _VOICES_CACHE = found
        except Exception:
            _VOICES_CACHE = []
    return jsonify(voices=_VOICES_CACHE)


# --------------------------------------------------------------------------
# Conversion jobs
# --------------------------------------------------------------------------
def _gather_input(req, workdir):
    """Materialize the request's input into workdir.

    Precedence: uploaded file > downloaded book > pasted text.
    Returns (input_path, default_title). Raises ValueError on bad input.
    """
    uploaded = req.files.get("file")
    book_file = (req.form.get("book_file") or "").strip()
    text = (req.form.get("text") or "").strip()

    if uploaded and uploaded.filename:
        ext = os.path.splitext(uploaded.filename)[1].lower()
        if ext not in ALLOWED_EXT:
            raise ValueError(f"Unsupported file type '{ext or 'unknown'}'. Please use a PDF or TXT file.")
        path = os.path.join(workdir, "input" + ext)
        uploaded.save(path)
        return path, os.path.splitext(os.path.basename(uploaded.filename))[0]
    if book_file:
        src = _resolve_in(DOWNLOADS, book_file, ALLOWED_EXT)
        if src is None:
            raise ValueError("That downloaded book is no longer available. Please search for it again.")
        # Snapshot into the job dir so later fetches can't swap content mid-job.
        dst = os.path.join(workdir, "input" + os.path.splitext(src)[1].lower())
        shutil.copyfile(src, dst)
        return dst, os.path.splitext(book_file)[0]
    if text:
        path = os.path.join(workdir, "input.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path, "audiobook"
    raise ValueError("Nothing to convert. Paste text, add a PDF/TXT file, or pick a book.")


def _parse_convert_options(form):
    engine = form.get("engine", "pyttsx3")
    if engine not in ("pyttsx3", "gtts"):
        raise ValueError(f"Unknown engine: {engine}")
    lang = form.get("lang", "en") or "en"
    lang, tld = GTTS_TLD.get(lang, (lang, "com"))
    voice = (form.get("voice") or "").strip() or None
    try:
        rate = max(80, min(400, int(form.get("rate", "175"))))
    except ValueError:
        rate = 175
    upload = form.get("upload_to_drive") in ("1", "true", "on", "yes")
    return engine, lang, tld, voice, rate, upload


@app.route("/convert", methods=["POST"])
def convert():
    _prune_jobs()
    try:
        engine, lang, tld, voice, rate, upload = _parse_convert_options(request.form)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    workdir = tempfile.mkdtemp(prefix="p2a_job_")
    try:
        input_path, default_title = _gather_input(request, workdir)
    except ValueError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify(error=str(e)), 400

    out_base = _safe_basename(request.form.get("filename"), fallback=default_title)

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id, "state": "starting", "phase": "Extracting text…",
        "done": 0, "total": 0, "percent": 0,
        "words": 0, "est_seconds": 0,
        "filename": None, "drive_link": None, "drive_error": None,
        "error": None, "updated": time.time(),
        "_workdir": workdir, "_proc": None, "_cancel": False,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    args = (job, input_path, out_base, engine, lang, tld, voice, rate, upload)
    threading.Thread(target=_run_job, args=args, daemon=True).start()
    return jsonify(job_id=job_id)


def _job_update(job, **kw):
    job.update(kw)
    job["updated"] = time.time()


def _run_job(job, input_path, out_base, engine, lang, tld, voice, rate, upload):
    workdir = job["_workdir"]
    try:
        # 1) Extract text up front: gives the word count/estimate and lets the
        #    subprocess work from clean .txt no matter the original format.
        text = pdf2audiobook.extract_text(input_path)
        if not text:
            raise ValueError("No extractable text found. If this is a scanned PDF, it has no selectable text.")
        words = len(text.split())
        _job_update(job, words=words, est_seconds=_estimate_seconds(words, engine, rate))

        clean_input = os.path.join(workdir, "clean.txt")
        with open(clean_input, "w", encoding="utf-8") as f:
            f.write(text)

        out_ext = ".mp3" if engine == "gtts" else ".wav"
        out_tmp = os.path.join(workdir, "out" + out_ext)

        cmd = [sys.executable, SCRIPT, clean_input, "-o", out_tmp,
               "-e", engine, "-l", lang, "-t", tld, "-r", str(rate), "--progress"]
        if voice:
            cmd += ["-v", voice]

        # 2) Run the converter, streaming progress. stderr goes to a file to
        #    avoid pipe-buffer deadlocks.
        stderr_path = os.path.join(workdir, "stderr.txt")
        with open(stderr_path, "w", encoding="utf-8") as errf:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf,
                                    text=True, encoding="utf-8", errors="replace")
        job["_proc"] = proc
        _job_update(job, state="running", phase="Synthesizing audio…")

        for line in proc.stdout:
            parts = line.split()
            if len(parts) == 2 and parts[0] == "TOTAL" and parts[1].isdigit():
                _job_update(job, total=int(parts[1]))
            elif len(parts) == 3 and parts[0] == "PROGRESS":
                try:
                    done, total = int(parts[1]), int(parts[2])
                    pct = int(done / total * 100) if total else 0
                    _job_update(job, done=done, total=total, percent=pct)
                except ValueError:
                    pass
        proc.wait()

        if job["_cancel"]:
            _job_update(job, state="cancelled", phase="Cancelled")
            return
        if proc.returncode != 0 or not os.path.exists(out_tmp):
            detail = ""
            try:
                with open(stderr_path, encoding="utf-8", errors="replace") as f:
                    detail = f.read().strip()
            except OSError:
                pass
            raise RuntimeError(detail or "Conversion failed.")

        # 3) Move the finished audio into the library with a sidecar.
        os.makedirs(OUTPUTS, exist_ok=True)
        final_path = _unique_path(OUTPUTS, out_base, out_ext)
        final_name = os.path.basename(final_path)
        shutil.move(out_tmp, final_path)
        meta = {
            "filename": final_name,
            "title": out_base,
            "engine": engine,
            "lang": lang,
            "words": job["words"],
            "est_seconds": job["est_seconds"],
            "size": os.path.getsize(final_path),
            "created": time.time(),
            "drive_link": None,
        }
        _write_sidecar(final_name, meta)
        _job_update(job, filename=final_name, percent=100)

        # 4) Optional Drive upload as its own phase — errors are reported in
        #    the job JSON, never smuggled through response headers.
        if upload and drive.is_connected():
            _job_update(job, state="uploading", phase="Uploading to Google Drive…")
            try:
                link = drive.upload(final_path, final_name)
                meta["drive_link"] = link
                _write_sidecar(final_name, meta)
                _job_update(job, drive_link=link)
            except Exception as e:
                _job_update(job, drive_error=str(e))

        _job_update(job, state="done", phase="Done")
    except Exception as e:
        state = "cancelled" if job["_cancel"] else "error"
        _job_update(job, state=state, error=_friendly_error(str(e), engine),
                    phase="Failed" if state == "error" else "Cancelled")
    finally:
        job["_proc"] = None
        shutil.rmtree(workdir, ignore_errors=True)


def _friendly_error(detail, engine):
    detail = (detail or "Conversion failed.").strip()
    if "ModuleNotFoundError" in detail or "No module named" in detail:
        detail += "\n\nA required package isn't installed. Re-run run.bat, or: pip install -r requirements.txt"
    if engine == "gtts" and any(k in detail.lower() for k in
                                ("urlopen", "connection", "timed out", "getaddrinfo", "network", "429")):
        detail += "\n\nThe online engine needs an internet connection (and can be rate-limited on very long books). The Offline engine avoids both."
    return detail


@app.route("/jobs/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return jsonify(error="Unknown job."), 404
    public = {k: v for k, v in job.items() if not k.startswith("_")}
    return jsonify(public)


@app.route("/jobs/<job_id>/cancel", methods=["POST"])
def job_cancel(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return jsonify(error="Unknown job."), 404
    job["_cancel"] = True
    proc = job["_proc"]
    if proc is not None:
        try:
            proc.terminate()
        except OSError:
            pass
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------
@app.route("/preview", methods=["POST"])
def preview():
    """Synthesize just the first few sentences of the current input, inline."""
    try:
        engine, lang, tld, voice, rate, _ = _parse_convert_options(request.form)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    workdir = tempfile.mkdtemp(prefix="p2a_prev_")
    try:
        try:
            input_path, _title = _gather_input(request, workdir)
        except ValueError as e:
            return jsonify(error=str(e)), 400

        out_ext = ".mp3" if engine == "gtts" else ".wav"
        out_path = os.path.join(workdir, "preview" + out_ext)
        cmd = [sys.executable, SCRIPT, input_path, "-o", out_path,
               "-e", engine, "-l", lang, "-t", tld, "-r", str(rate),
               "--max-chars", "300"]
        if voice:
            cmd += ["-v", voice]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return jsonify(error="Preview timed out."), 504
        if proc.returncode != 0 or not os.path.exists(out_path):
            detail = (proc.stderr or proc.stdout or "Preview failed.").strip()
            return jsonify(error=_friendly_error(detail, engine)), 500
        # Read into memory (a few hundred KB) so the workdir can be removed.
        with open(out_path, "rb") as f:
            data = f.read()
        mime = "audio/mpeg" if out_ext == ".mp3" else "audio/wav"
        return app.response_class(data, mimetype=mime)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------
def _sidecar_path(audio_name):
    return os.path.join(OUTPUTS, audio_name + ".json")


def _write_sidecar(audio_name, meta):
    os.makedirs(OUTPUTS, exist_ok=True)
    with open(_sidecar_path(audio_name), "w", encoding="utf-8") as f:
        json.dump(meta, f)


@app.route("/library")
def library():
    items = []
    if os.path.isdir(OUTPUTS):
        for name in os.listdir(OUTPUTS):
            if os.path.splitext(name)[1].lower() not in AUDIO_EXT:
                continue
            meta = {"filename": name, "title": os.path.splitext(name)[0]}
            try:
                with open(_sidecar_path(name), encoding="utf-8") as f:
                    meta.update(json.load(f))
            except (OSError, ValueError):
                path = os.path.join(OUTPUTS, name)
                meta.setdefault("size", os.path.getsize(path))
                meta.setdefault("created", os.path.getmtime(path))
            items.append(meta)
    items.sort(key=lambda m: m.get("created", 0), reverse=True)
    return jsonify(items=items)


@app.route("/library/file/<path:name>")
def library_file(name):
    path = _resolve_in(OUTPUTS, name, AUDIO_EXT)
    if path is None:
        return jsonify(error="No such audiobook."), 404
    # conditional=True enables range requests so the player can seek.
    return send_file(path, conditional=True)


@app.route("/library/delete", methods=["POST"])
def library_delete():
    name = request.form.get("name", "")
    path = _resolve_in(OUTPUTS, name, AUDIO_EXT)
    if path is None:
        return jsonify(error="No such audiobook."), 404
    os.remove(path)
    try:
        os.remove(_sidecar_path(name))
    except OSError:
        pass
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Google Drive
# --------------------------------------------------------------------------
@app.route("/drive/status")
def drive_status():
    return jsonify(drive.status())


@app.route("/drive/connect", methods=["POST"])
def drive_connect():
    try:
        drive.connect()
    except Exception as e:
        return jsonify(error=str(e)), 400
    return jsonify(drive.status())


@app.route("/drive/disconnect", methods=["POST"])
def drive_disconnect():
    drive.disconnect()
    return jsonify(drive.status())


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    print("\n  Audiobook Creator UI is running.")
    print(f"  Open this in your browser:  {url}\n")
    app.run(host="127.0.0.1", port=5000, threaded=True, debug=False)
