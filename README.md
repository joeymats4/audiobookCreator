# pdf2audiobook

Convert PDF and TXT files — or text you paste in — into audiobooks.
Comes with a **basic web UI** and a command-line tool.

## Features

- 🎧 **Web UI**: paste text or drag & drop a `.pdf` / `.txt` file, click a button, download the audio
- Reads `.pdf` and `.txt` files, or raw pasted text
- Cleans extracted text (de-hyphenation, whitespace normalization)
- Two TTS engines:
  - **Offline** (`pyttsx3`) — uses your system voices, no internet, no ffmpeg. Outputs WAV.
  - **Online** (`gtts`) — Google TTS, more natural voice. Needs an internet connection. Outputs MP3.
- Chunks long text automatically

## Quick start (Web UI)

### Windows

Double-click **`run.bat`**. On the first run it creates a virtual environment,
installs the dependencies, and opens the app at <http://127.0.0.1:5000>.

> Requires Python 3. If you don't have it, install it from
> <https://www.python.org/downloads/> and tick **"Add Python to PATH"** during setup.

### macOS / Linux

```bash
./run.sh
```

On the first run this creates a virtual environment, installs dependencies, and
opens the app at <http://127.0.0.1:5000>.

### Manual (any OS)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>.

In the UI: drop a file **or** paste text, pick the voice engine, and click
**Create audiobook**. The file downloads automatically and also plays inline.

## Command-line usage

```bash
# Offline engine (no internet / ffmpeg needed)
python src/pdf2audiobook.py book.pdf -e pyttsx3 -o book.wav

# Online engine, Google TTS
python src/pdf2audiobook.py book.pdf

# Plain text file, explicit output
python src/pdf2audiobook.py notes.txt -o notes.mp3

# Another language (online engine)
python src/pdf2audiobook.py libro.pdf -l es
```

## Options (CLI)

| Flag | Description |
|------|-------------|
| `-o, --output` | Output audio path (default: same name as input) |
| `-e, --engine` | `gtts` (default) or `pyttsx3` |
| `-l, --lang` | Language code, gtts only (default `en`) |
| `-v, --voice` | Voice id, pyttsx3 only |
| `-r, --rate` | Speech rate, pyttsx3 only (default 175) |

## Notes

- **No ffmpeg or other native tools required** — everything installs with
  `pip install -r requirements.txt`. The **Online (gtts)** engine just needs an
  internet connection; the **Offline (pyttsx3)** engine works fully offline.
- On **Linux**, the offline engine speaks through `espeak`. If it's missing,
  install it with `sudo apt install espeak-ng` (Windows and macOS need nothing
  extra). The online engine works everywhere with no extra setup.
- Scanned PDFs (images with no selectable text) can't be converted — there's no
  text to extract. Run OCR on them first.

## Project layout

```
app.py                 Flask web server (the UI backend)
templates/index.html   The web UI (drag & drop + paste)
src/pdf2audiobook.py   Core converter, also usable from the command line
requirements.txt       Python dependencies
run.bat                One-click launcher for Windows
run.sh                 One-click launcher for macOS / Linux
```

## License

MIT
