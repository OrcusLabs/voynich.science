# Voynich Export Tool

Desktop GUI utility for exporting filtered transcriptions from `voynich_transcriptions.json`.

Built with Python + Tkinter. No external dependencies.

---

## Overview

This tool allows you to:

- Load a Voynich transcription container
- Filter by **Source**
- Filter by **Transcriber**
- Filter by **Folio**
- Select text view (`raw`, `normalized`, `splat`)
- Export to **JSON**, **CSV**, or **TXT**

The application is read-only. It never modifies the source JSON.

---

## Input File

The application expects a structured transcription file such as:

`voynich_transcriptions.json`

The JSON must contain:

- `sources`
- `transcribers`
- `pages`
  - `lines`
  - optional `blocks`
  - nested `sources`
  - nested `views`

If your file uses a different name, simply browse and select it.

---

## Requirements

- Python 3.9+
- Tkinter (included with standard Python distributions)

Check Python version:

```
python --version
```

---

## Running the Application

```
python voynich_export.py
```

When the window opens:

1. Click **Browse**
2. Select `voynich_transcriptions.json`
3. Click **Load**
4. Select filters
5. Choose output format
6. Click **Export**

---

## Interface

### Voynich JSON
Select and load the transcription container.

### Sources
Lists available source IDs.

Selecting one or more sources enables the Transcribers list.

### Transcribers
Populates only after a source is selected.

Display format:
```
<transcriber_id> | <name>
```

### Folios
Natural-sorted folio list (`f1r`, `f1v`, `f2r`, etc.).

“All folios” is enabled by default.

---

## View Modes

| Mode        | Description |
|-------------|-------------|
| raw         | Direct record value |
| normalized  | Normalized text |
| splat       | Tokenized/splat representation |

---

## Export Formats

### JSON

```
{
  "exported_utc": "...",
  "view": "...",
  "records": [...]
}
```

### CSV

Columns:

- folio
- line
- source_id
- source_key
- transcriber_id
- view
- text
- locator_json

### TXT

Tab-separated format:

```
folio    line    source_id    source_key    transcriber_id    text    locator_json
```

---

## Selection Logic

- No sources selected → all sources exported
- Transcribers selected → sources filtered to those transcribers
- “All folios” enabled → full manuscript export
- “All folios” disabled → only selected folios exported

---

## Data Model Assumptions

The exporter assumes a nested structure like:

```
pages
  └── folio_id
        ├── lines
        │     └── line_id
        │           └── sources
        │                 └── source_key
        │                       ├── source_id
        │                       ├── transcriber_id
        │                       ├── views
        │                       └── locator
        └── blocks (optional)
```

If your schema differs, extraction logic may need adjustment.

---

## Repository Layout

```
voynich-export/
├── voynich_export.py
├── voynich_transcriptions.json
└── README.md
```

---

## License