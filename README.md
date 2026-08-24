# Paper Organizer

[Korean README](README.ko.md)

Paper Organizer is a local-first Windows desktop app for collecting academic
PDFs, storing them as portable `.paperpack` archives, summarizing them with AI,
and searching their full text later.

The latest installer is available from
[GitHub Releases](https://github.com/loselessss/paper-organizer/releases/latest).

## Why It Exists

Academic PDFs scatter quickly. Download folders fill with unclear filenames,
bibliographic metadata is often incomplete, and later you may remember only an
experimental condition, a result sentence, or a method detail rather than the
paper title.

Paper Organizer keeps that workflow in one place:

- Detect new academic PDFs.
- Store papers and patents as single `.paperpack` archives.
- Clean up titles, authors, years, journals, conferences, and research fields.
- Summarize papers with local AI or cloud AI only when the user allows it.
- Index full text so papers can be found again by keyword or natural-language
  questions.

The goal is not just to make a neat folder of PDFs. It is to build a personal
paper library that remains useful when you need to reuse the work.

## What It Does

Paper Organizer currently covers the main flow from watching folders to searching
the library.

1. New PDF discovery
   - Scans download and user-selected watch folders.
   - Waits until files are stable before reading them.
   - Separates likely papers, review papers, patents, duplicate candidates,
     damaged files, and repository wrapper pages.
   - Uses the bundled RapidOCR path when a document needs text recovery.

2. PaperPack archiving
   - Stores the PDF, metadata, full text, analysis results, and edit history in a
     `.paperpack` ZIP package.
   - Treats `index/library.json` and `index/search.sqlite` as rebuildable caches.
   - Keeps the `.paperpack` file as the single source of truth.

3. Bibliography and field cleanup
   - Combines regex extraction, external bibliography verification, and AI
     analysis.
   - Tracks sources for title, author, year, and journal fields.
   - Preserves fields edited by the user so later AI runs do not overwrite them.
   - Lets the user manage research fields and subfields.

4. AI analysis
   - Uses local Ollama models by default.
   - Supports OpenAI and Anthropic only for requests the user explicitly allows.
   - Uses separate prompts for research papers, review papers, and patents.
   - Runs the background analysis queue one paper at a time to reduce load on
     modest PCs.

5. Search and reuse
   - Shows title, author, year, field, analysis status, and version information
     in the library table.
   - Supports keyword search and natural-language paper search.
   - Shows search evidence from actual PaperPack full-text pages.
   - Opens PDFs through the bundled sPDF module and can apply saved PDF edits
     back into the PaperPack.

## Features

- Download folder and custom watch folder scanning
- Academic paper, review paper, and patent detection
- Duplicate PDF and multi-document bundle detection
- `.paperpack` creation, validation, extraction, and reindexing
- External bibliography verification for title, author, year, and journal fields
- Research field and subfield classification with user-managed taxonomy
- Local Ollama-based AI summaries
- Optional OpenAI and Anthropic integration
- Separate summary prompts for research papers, review papers, and patents
- Background analysis queue with retry handling
- Korean translation cache for AI analysis
- SQLite FTS5 full-text search
- Natural-language paper search with evidence snippets
- User-configurable library columns and ordering
- Bundled sPDF integration for opening PDFs and applying edited copies
- Bundled RapidOCR-based text recovery
- GitHub Releases-based update checks
- PyInstaller and Inno Setup Windows installer build

## Local-First Policy

Paper Organizer is designed to run locally by default.

- PDFs and PaperPacks stay on the user's PC.
- Ollama model weights are not bundled with the installer; the user chooses which
  models to install.
- Cloud AI is used only for requests the user explicitly permits.
- API keys are read from the OS credential store or environment variables, not
  committed to Git or stored in plain project files.
- Error records avoid API keys, tracebacks, and paper body text.

## sPDF Integration

Paper Organizer includes sPDF as a pinned Git submodule. The current integration
targets the sPDF international edition, so the bundled PDF viewer/editor can show
its English UI while keeping Paper Organizer's PDF edit workflow and update
checks controlled by Paper Organizer.

When Paper Organizer opens sPDF internally, sPDF's own update service and update
notifications remain disabled.

## Installation

Download one of these files from the
[latest release](https://github.com/loselessss/paper-organizer/releases/latest):

- `PaperOrganizer_Setup_latest.exe`
- `PaperOrganizer_Setup_<version>.exe`

The installer includes the app, bundled sPDF integration, and the default OCR
runtime. Ollama LLM weights are not included.

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
sPDF is pinned through `vendor/spdf`; Paper Organizer does not use sPDF's own
update notification or self-update features when sPDF is opened internally.
