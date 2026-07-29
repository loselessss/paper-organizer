# PaperPack File Format

## Status

- File extension: `.paperpack`
- Container: standard ZIP64-compatible archive
- Media type: `application/vnd.paper-organizer.paperpack+zip`
- Current schema version: `1`
- Text encoding: UTF-8 JSON

The format is intentionally recoverable without Paper Organizer. A user can copy a
`.paperpack` file, change the copy's extension to `.zip`, and open it with a normal
ZIP tool. The application must never encrypt or obfuscate the embedded PDF or JSON.

## Required entries

```text
mimetype
manifest.json
document/paper.pdf
metadata/paper.json
content/content.json
history/revision-0001.json
```

`mimetype` must be the first ZIP entry, stored without compression, and contain the
media type exactly. `document/paper.pdf` is also stored without compression so its
bytes are preserved and extracting it does not require recompressing the source.
JSON entries use DEFLATE compression.

## Manifest

`manifest.json` records:

- `format` and `schema_version`
- creation and update timestamps
- current revision number
- the original PDF filename
- PDF media type, byte length, and SHA-256
- current metadata and content entry names, byte lengths, and SHA-256 values
- the history prefix and revision count

Readers must reject unsupported schema versions, duplicate ZIP names, unsafe paths,
missing required entries, invalid JSON objects, checksum mismatches, and incomplete
revision sequences. Version 1 limits the manifest to 1 MiB, metadata to 8 MiB, and
content JSON to 64 MiB.

## Metadata and content

`metadata/paper.json` is the editable canonical record. It contains identity,
bibliography, classification, language-selected summary, contributions, limitations,
experimental details, curation, and provenance fields.

An optional `translations.analysis.<language>` object may cache a user-requested
translation for display without replacing the canonical analysis. It records the
translated plain text, source analysis SHA-256, provider, model, prompt version, and
translation timestamp. Readers must ignore a cached translation when its source hash
no longer matches the current analysis.

`content/content.json` contains page-, section-, and chunk-level extracted text used
for evidence and full-text search. Evidence objects in metadata may refer to content
chunk IDs and physical PDF page numbers.

## Revision history

Each successful edit appends `history/revision-NNNN.json`. A history entry records
the complete metadata snapshot, the content checksum, edit timestamp, and editor.
The embedded PDF is not duplicated in history.

When an embedded PDF is replaced, the revision also contains a `change` object with
`kind: "pdf_replaced"`, the previous and current PDF SHA-256 values, and the new byte
length. PDF replacement uses the current PDF checksum and package revision as an
optimistic lock, so a stale working copy cannot overwrite a concurrently edited
package.

## Updates and extensions

An update is written to a temporary archive in the same directory, fully verified,
and installed with an atomic file replacement. The existing paperpack remains intact
if generation or verification fails.

Version 1 writers preserve unknown safe ZIP entries when rewriting a package. New
optional data should live below `extensions/<vendor-or-project>/`. A reader must not
silently rewrite a package with a newer unsupported schema version.

Applications should edit an extracted working copy, never `document/paper.pdf` in
place. A saved working copy is committed only after explicit user confirmation. A
discard operation deletes only the working copy. Successful PDF replacement updates
the package metadata identity and marks derived content and AI analysis as stale.

## Library and cloud synchronization

A `.paperpack` is the local authoritative copy. `index/library.json` and
`index/search.sqlite` are disposable library-level indexes that can be rebuilt from
paperpacks.

Cloud folders such as OneDrive receive a lightweight `portable-library.json` for
editing and conflict resolution. They do not edit a live paperpack in place. An
accepted cloud edit creates a new local paperpack revision through the same atomic
update path.

## Legacy migration

Existing PDF + `*.paper.json` + `*.content.json` sets remain readable as migration
inputs. Migration must create and verify the paperpack before offering to remove the
legacy files. Keeping the input PDF is the default; removal requires a separate
explicit option and confirmation. A failed removal must roll back the newly created
paperpack or report both surviving copies unambiguously.

## Bulk PDF handoff export

For archival handoff, an implementation may extract PDFs from multiple paperpacks in
one operation. It must validate every selected package before removing any source,
must not overwrite existing output PDFs, and must verify every extracted PDF against
the manifest SHA-256 and byte length.

Keeping source paperpacks is the default. Source removal requires a separate explicit
confirmation and may begin only after every extraction succeeds. If package validation
or extraction fails, no source paperpack may be removed.
