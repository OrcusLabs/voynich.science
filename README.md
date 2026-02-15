Voynich Export Tool

A desktop GUI utility for exporting filtered transcriptions from voynich_transcriptions.json.

This tool allows you to:

Load a Voynich JSON container

Filter by Source

Filter by Transcriber

Filter by Folio

Select text view (raw, normalized, splat)

Export to JSON, CSV, or TXT

Built with Python + Tkinter. No external dependencies.

Expected Input File

The application expects a JSON file structured like:

voynich_transcriptions.json


The JSON must contain:

sources

transcribers

pages

lines

optional blocks

nested sources

nested views

If your file is named differently, you can browse and select it manually.

Installation

Requires Python 3.9+

No additional packages required.

python --version


Tkinter is included with standard Python distributions.

Running the Application
python voynich_export.py


When the window opens:

Click Browse

Select voynich_transcriptions.json

Click Load

Choose filters

Choose output format

Click Export

Interface Overview
Voynich JSON

Select and load your transcription container file.

Sources

Lists available source IDs extracted from the JSON.

Selecting a source enables the Transcribers list.

Transcribers

Populates only after one or more sources are selected.

Shows:

<transcriber_id> | <name>

Folios

Lists folios sorted in natural order (f1r, f1v, f2r, etc.).

“All folios” is enabled by default.

View Modes
Mode	Description
raw	Direct record value
normalized	Normalized text
splat	Tokenized/splat version
Export Formats
JSON

Structured export:

{
  "exported_utc": "...",
  "view": "...",
  "records": [...]
}

CSV

Columns:

folio

line

source_id

source_key

transcriber_id

view

text

locator_json

TXT

Tab-separated rows:

folio    line    source_id    source_key    transcriber_id    text    locator_json

Selection Logic

If no sources are selected → all sources are included.

If transcribers are selected → sources are filtered to match those transcribers.

If “All folios” is enabled → entire manuscript exports.

If disabled → only selected folios export.

Data Model Assumptions

The exporter assumes the following nested structure:

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


If your schema differs, the extraction logic may need adjustment.

Design Notes

Transcribers list is intentionally empty until a source is selected.

Folios are sorted using a natural folio key (f<nr><r|v><optional index>).

Export is read-only; the input JSON is never modified.

No caching. Everything reads fresh from the loaded JSON.

Example Use Cases

Export normalized Takahashi lines for specific folios.

Compare raw vs normalized across transcribers.

Generate CSV for statistical analysis.

Produce filtered text corpora for entropy or ED analysis.

File Naming

Recommended repo layout:

/voynich-export
    voynich_export.py
    voynich_transcriptions.json
    README.md

License

MIT