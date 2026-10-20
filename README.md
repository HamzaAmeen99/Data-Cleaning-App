# DataClean

DataClean is a small, no-code web application that lets university teachers and
students clean messy CSV/Excel datasets without writing a single line of code.

Upload a file, review the data-quality problems DataClean finds (missing
values, duplicate rows, inconsistent text, invalid emails/dates), choose the
fixes you want, preview the cleaned result and download it as CSV or Excel.

Built with **Flask + Pandas** and a simple HTML/CSS/vanilla-JS frontend.
No AI, no database, no login — just a focused departmental utility.

## Features

- Upload CSV, XLSX or XLS files (validated, size-limited)
- Dataset overview: rows, columns, missing values, duplicates, preview
- Automatic data-quality analysis with suggested fixes
- Cleaning operations (all explicit and user-approved):
  - Trim unnecessary whitespace in text columns
  - Remove duplicate rows (runs after trimming, so near-duplicates are caught)
  - Handle missing values: remove rows, mean, median, most frequent value, or a custom value
  - Standardize text case (lower / upper / title) per column
  - Map inconsistent category values to a standard form (e.g. `male` → `Male`)
  - Convert column types (integer, float, text, date) safely
  - Remove completely empty columns
- Before/after summary with a plain-language history of every change
- Preview of the cleaned data (first 20 rows)
- Download the cleaned dataset as CSV or Excel
- Friendly error messages instead of stack traces

## Folder structure

```text
dataclean/
│
├── app.py                 # Application factory + error handlers
├── config.py              # All settings (folders, limits, extensions)
├── requirements.txt
│
├── routes/                # HTTP layer: validate requests, call services
│   ├── upload_routes.py   #   / , /upload
│   ├── dataset_routes.py  #   /dataset , /dataset/analysis
│   └── cleaning_routes.py #   /clean , /cleaning/result , /download
│
├── services/              # Data-processing logic (no HTTP code here)
│   ├── file_service.py    #   save / load / export datasets
│   ├── analysis_service.py#   overview, missing, duplicates, issues
│   └── cleaning_service.py#   the actual cleaning operations
│
├── utils/
│   └── validators.py      # Tiny shared validation helpers
│
├── templates/             # Jinja2 pages
├── static/css, static/js  # Stylesheet + small UI helpers
├── uploads/               # Uploaded files (temporarily, not served publicly)
├── processed/             # Cleaned output files
└── tests/                 # Sample messy data + smoke test
```

Routes handle HTTP and call services; services hold all Pandas logic. This
keeps the app easy to test and easy to extend later (e.g. a database or API).

## Installation

Python 3.10+ is required.

```bash
# 1. Create a virtual environment
python -m venv venv

# 2. Activate it
# Windows (PowerShell / CMD):
venv\Scripts\activate
# Windows (Git Bash):
source venv/Scripts/activate
# macOS / Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

## Running the application

From the `dataclean/` folder (with the virtual environment active):

```bash
flask --app app run --debug
```

Then open <http://127.0.0.1:5000> in your browser.

Alternatively: `python app.py`.

## Frontend assets

IBM Plex and the subset of Material Symbols used by DataClean are bundled in
`dataclean/static/fonts`, with their licenses. Visitors make no Google Fonts
requests. To update these assets, run `python scripts/vendor_fonts.py` from
`dataclean/`; this maintenance command requires internet access.

At startup, `rcssmin` minifies the shared CSS, which is embedded in each page to
avoid render-blocking stylesheet requests. Edit `static/css/style.css` normally,
then restart the app to load changes. JavaScript and fonts use content-versioned
`/assets/` URLs and one-year immutable caching; source changes automatically
produce new URLs on restart. Only those public assets use this cache policy.

For PythonAnywhere, install the updated `requirements.txt` and reload the web app.
Leave `/assets/` handled by Flask (do not add a PythonAnywhere static mapping for
that prefix), so its version validation and cache headers run. The existing
`/static/` mapping can remain. Re-run PageSpeed against the deployed page after
reloading; a saved report does not update when code changes.

Before importing `app` in the PythonAnywhere WSGI configuration, provide the
production environment and a stable, private random secret:

```python
import os

os.environ["DATACLEAN_ENV"] = "production"
os.environ["DATACLEAN_SECRET_KEY"] = "your-random-secret-of-at-least-32-bytes"
```

Keep the real secret outside Git. DataClean deliberately refuses to start in
production when `DATACLEAN_SECRET_KEY` is missing or shorter than 32 bytes.
Development remains the default environment; `DATACLEAN_ENV` also accepts
`testing` for isolated tests. Production must be served over HTTPS because its
host-only session cookie is deliberately marked `Secure`.

Workspace sessions and their private files expire after 24 hours. Ordinary GET
requests do not extend that deadline automatically; users whose workspace is no
longer available are returned to upload with a recovery message. Visiting the
public landing page does not create a session; one begins when the visitor opens
the upload workspace. After an upload is accepted, DataClean replaces both the
session identifier and CSRF token before showing the dataset workspace.
Reloading an active workspace asks whether to end it; confirmation deletes its
files and returns to Upload. Switching browser tabs does not affect the session.
Session records use CacheLib's private filesystem cache rather than Flask-Session's
deprecated filesystem backend.
Every state-changing application route is covered by one central CSRF guard, so
new mutation endpoints are protected without route-specific validation calls.
All responses prevent framing and MIME sniffing, limit referrer disclosure, and
restrict forms to this origin. Production responses also enable one-year HSTS.
Workspace pages, redirects and generated downloads send `no-store, private`
cache headers so browsers do not retain dataset content. Public versioned assets
remain cacheable for one year.

## Supported file formats

| Format | Extension | Notes |
|--------|-----------|-------|
| CSV    | `.csv`    | Standard comma-separated files |
| Excel  | `.xlsx`   | First sheet is used |
| Excel  | `.xls`    | First sheet is used |

Maximum upload size: 16 MB (configurable in `config.py`).

## Example workflow

1. **Upload** — open the app and upload `student_data.csv`.
2. **Analyze** — the overview page shows rows, columns, missing values and a preview; click *Continue to Cleaning*.
3. **Fix** — DataClean lists the problems it found, e.g. *243 missing values*, *17 duplicate rows*, *inconsistent Department spellings*. Open each card, choose how to fix it (or leave it alone), then click **Apply Selected Cleaning**.
4. **Review** — the results page shows before/after numbers and a plain-language history such as:
   - Removed 17 duplicate row(s)
   - Trimmed whitespace in 43 cell(s)
   - Filled 8 missing value(s) in 'CGPA' using the median
5. **Download** — grab the cleaned file as CSV or Excel.

A ready-made messy file is included at `tests/sample_messy_data.csv` for trying this out.

## Testing

Run the end-to-end smoke test (uses the Flask test client, no browser needed):

```bash
python tests/smoke_test.py
```

## Future improvements

- Store sessions/datasets in a database or object storage
- Optional user accounts and shared workspaces
- Column renaming, reordering and splitting
- More type inference and smarter date parsing
- Undo/redo of cleaning steps
- REST API for programmatic cleaning
- Data visualization of quality issues
