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
