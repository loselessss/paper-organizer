# Paper Organizer

[Korean README](README.ko.md)

Paper Organizer is a Windows desktop app for collecting academic PDFs, storing
them as portable `.paperpack` archives, summarizing them with AI, and searching
their full text later.

The latest installer is available from
[GitHub Releases](https://github.com/loselessss/paper-organizer/releases/latest).

## Why It Exists

Academic PDFs are easy to collect and hard to reuse. Download folders fill with
unclear filenames, bibliography fields are often broken or incomplete, and the
thing you remember later is usually a method detail, a result sentence, or an
experimental condition rather than the exact title.

Paper Organizer keeps that workflow in one local-first library. It watches for
new PDFs, identifies papers and patents, stores them as durable PaperPacks,
checks bibliography data, creates AI summaries, and builds a full-text index so
the papers can be found again when they matter.

## What It Does

1. Finds new PDFs
   - Watches download and user-selected folders.
   - Waits until files are stable before reading them.
   - Separates research papers, review papers, patents, duplicates, damaged
     files, and multi-document bundles.
   - Uses bundled OCR support when a PDF needs text recovery.

2. Builds PaperPack archives
   - Stores the PDF, metadata, full text, analysis results, and edit history in
     one `.paperpack` ZIP file.
   - Treats `index/library.json` and `index/search.sqlite` as rebuildable
     caches.
   - Keeps `.paperpack` as the single source of truth.

3. Cleans bibliography and fields
   - Combines regex extraction, AI extraction, and PubMed/Crossref verification.
   - Records field sources for title, author, year, and journal/conference.
   - Preserves user-edited fields across later AI runs.
   - Lets users manage research fields and subfields.

4. Summarizes and translates
   - Uses app-managed local GGUF models by default.
   - Supports OpenAI and Anthropic only when the user explicitly allows cloud
     processing.
   - Uses separate prompts for research papers, review papers, and patents.
   - Keeps a Korean translation cache for AI analysis text.

5. Searches and reuses papers
   - Provides keyword search and natural-language paper search.
   - Shows evidence snippets from actual PaperPack full-text pages.
   - Lets users customize library columns and ordering.
   - Opens PDFs through the bundled sPDF integration and can apply saved PDF
     edits back into the PaperPack.

## Features

- Download folder and custom watch folder scanning
- Research paper, review paper, and patent detection
- Duplicate PDF and multi-document bundle detection
- `.paperpack` creation, validation, extraction, and reindexing
- PubMed/Crossref bibliography verification
- Bibliography anomaly detection and field-source tracking
- Research field and subfield classification with user-managed taxonomy
- App-managed local GGUF model download and selection
- Optional legacy Ollama provider for existing setups
- Optional OpenAI and Anthropic integration
- Separate summary prompts for research papers, review papers, and patents
- Background analysis queue with retry handling
- Korean translation cache for AI summaries
- SQLite FTS5 full-text search
- Natural-language search with evidence snippets
- User-configurable library columns and ordering
- Bundled sPDF integration for PDF viewing and edit application
- Bundled RapidOCR-based text recovery
- GitHub Releases-based update checks
- PyInstaller and Inno Setup Windows installer build

## Local AI Setup

The bundled runtime is llama.cpp b10715 for Windows x64 CPUs; this release does not
include GPU acceleration. For source runs, prepare it with
`python scripts/prepare_llama_runtime.py --smoke`. Installer builds do this automatically.

Paper Organizer 2.4.0 uses app-managed local GGUF models by default. The Windows
installer includes the application, bundled sPDF integration, OCR runtime, and
local AI runtime support, but it does not include large model weights.

After installing the app:

1. Open `AI Settings`.
2. Choose `Built-in Local AI`.
3. Open `Model Download/Manage`.
4. Download one of the recommended GGUF models.
5. Select separate models for background analysis and manual summaries if
   needed.

Ollama is no longer required for the default local AI workflow. Existing Ollama
users may keep using the legacy provider, but the app will not remove Ollama or
shared Ollama model files automatically.

## Local-First Policy

- PDFs and PaperPacks stay on the user's PC.
- Local model files are stored in the app-managed model folder.
- Cloud AI runs only for requests the user explicitly permits.
- API keys are read from the OS credential store or environment variables.
- Error records avoid API keys, tracebacks, and paper body text.

## Installation

Download one of these files from the
[latest release](https://github.com/loselessss/paper-organizer/releases/latest):

- `PaperOrganizer_Setup_latest.exe`
- `PaperOrganizer_Setup_<version>.exe`

Local AI model downloads can be several gigabytes, so they are handled after
installation from inside the app.

## Development

Python 3.12 or newer is required. sPDF is pinned as a submodule, so initialize it
after cloning.

```powershell
git submodule update --init
python -m pip install -e ".[gui]"
python -m unittest discover -s tests
python -m paper_organizer.gui
```

On the current development PC, the virtual environment entry point can also be
used:

```powershell
.\.venv\Scripts\paper-organizer-gui.exe
```

To build the Windows installer, install the GUI and build extras and use Inno
Setup 6:

```powershell
python -m pip install -e ".[gui,build]"
.\build_installer.bat
```

The installer is written to `Output\PaperOrganizer_Setup_<version>.exe`.

## Documentation

- [CHANGELOG.md](CHANGELOG.md): version history
- [PAPERPACK_FORMAT.md](PAPERPACK_FORMAT.md): `.paperpack` file format
- [docs/HANDOFF.md](docs/HANDOFF.md): development handoff, code map, and known
  pitfalls
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md): design notes and long-term roadmap
- [RELEASE_NOTE_RULES.md](RELEASE_NOTE_RULES.md): release note writing rules by
  language

## License And Bundled Assets

Pretendard font files and their license are included for future UI font testing.
sPDF is pinned through `vendor/spdf`; Paper Organizer disables sPDF's own update
notifications and self-update behavior when sPDF is opened internally.
