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
- 🔎 **Find books**: search public-domain catalogs (Project Gutenberg + Internet Archive) and download a book straight into the converter
- ☁️ **Google Drive** (optional): auto-upload the finished audiobook to your Drive
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

## Find books (public domain)

The **🔎 Find a book** panel searches two free, legal catalogs and downloads a
book straight into the converter:

- **Project Gutenberg** (via the Gutendex API) — tens of thousands of books as
  clean plain text, the best source for natural-sounding narration.
- **Internet Archive** — scanned-book PDFs and full text of public-domain works.

Type a title or author, click **Search**, then **Use** on a result. The book
downloads locally and loads into the converter — pick a voice and click **Create
audiobook** as usual. Only public-domain / freely downloadable items are offered
(lending-only Internet Archive items are filtered out). No piracy/shadow-library
sites are used.

## Google Drive setup (optional)

To auto-upload finished audiobooks to your Drive, give the app its own Google
OAuth credentials once. Nothing is typed into the app — you approve access on
Google's own sign-in page:

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a
   project (or select an existing one).
2. **Enable the Google Drive API** (APIs & Services → Library → *Google Drive API* → Enable).
3. **APIs & Services → Credentials → Create credentials → OAuth client ID.**
   If prompted, configure the consent screen (User type: *External*; add your own
   Google account under **Test users**).
4. Choose application type **Desktop app**, create it, and **Download JSON**.
5. Save that file as **`client_secret.json`** in the project folder (next to `app.py`).
6. Restart the app, click **Connect** in the Google Drive box, and approve access.
   Tick **"Upload the audiobook to Drive after converting"** — your audiobooks then
   land in an **Audiobooks** folder in your Drive.

The app requests only the `drive.file` scope, so it can see and manage **only the
files it creates**, never anything else in your Drive. `client_secret.json` and the
generated `token.json` are gitignored and never leave your machine.

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
templates/index.html   The web UI (search · drag & drop · paste)
src/pdf2audiobook.py   Core converter, also usable from the command line
src/book_search.py     Book search + download (Gutenberg + Internet Archive)
src/drive.py           Optional Google Drive upload (OAuth)
requirements.txt       Python dependencies
run.bat                One-click launcher for Windows
run.sh                 One-click launcher for macOS / Linux
```

## License

MIT
