"""Dataset inspection and data-quality analysis.

Everything here is read-only: analysis functions never modify the DataFrame.
All findings are deliberately conservative — the app only reports what can be
checked with simple, deterministic rules.
"""

import re
import pandas as pd

from config import Config

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Columns that "look like" dates by name, used only as a hint for date checks.
DATE_HINTS = ("date", "day", "month", "year", "time")
DATE_REGEX = re.compile(r"(?:\b|_)(date|day|month|year|time)(?:\b|_)", re.IGNORECASE)


def get_dataset_overview(df: pd.DataFrame) -> dict:
    """Return basic dataset information: rows, columns, names and dtypes."""
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "column_names": list(df.columns.astype(str)),
        "data_types": {str(col): str(dtype) for col, dtype in df.dtypes.items()},
    }


def get_missing_values(df: pd.DataFrame) -> dict:
    """Return the number of missing values per column (only columns with any)."""
    counts = df.isna().sum()
    return {str(col): int(count) for col, count in counts.items() if count > 0}


def get_duplicate_count(df: pd.DataFrame) -> int:
    """Return the number of fully duplicated rows."""
    return int(df.duplicated().sum())


def detect_inconsistent_text(df: pd.DataFrame) -> dict:
    """Detect simple text inconsistencies in object columns.

    Two conservative checks per column:
      1. Values that differ only by letter case ("Male" vs "male").
      2. Values with leading/trailing whitespace.

    Returns:
        {column: {"case_variants": [str, ...], "whitespace_cells": int, "unique_values": [str, ...]}}
    """
    findings: dict = {}
    for col in df.select_dtypes(include="object").columns:
        series = df[col].dropna().astype(str)
        if series.empty:
            continue

        stripped = series.str.strip()
        whitespace_cells = int((stripped != series).sum())

        # Case-insensitive groups with more than one distinct spelling.
        groups: dict = {}
        for value in stripped.unique():
            groups.setdefault(value.casefold(), set()).add(value)
        case_variants = sorted(
            sorted(variants) for variants in groups.values() if len(variants) > 1
        )

        unique_vals = sorted(series.unique()) if series.nunique() <= 20 else []

        if whitespace_cells or case_variants:
            findings[str(col)] = {
                "case_variants": case_variants,
                "whitespace_cells": whitespace_cells,
                "unique_values": unique_vals,
            }
    return findings


def detect_basic_issues(df: pd.DataFrame) -> list[dict]:
    """Detect conservative, rule-based issues.

    Checks:
      - completely empty columns
      - columns that are mostly missing
      - constant-value columns (single distinct non-null value)
      - invalid email-shaped values in obvious email columns
      - invalid dates in columns whose name mentions date/time

    Returns:
        A list of {"column", "issue", "detail"} dicts.
    """
    issues: list[dict] = []
    threshold = Config.MOSTLY_MISSING_THRESHOLD

    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        name = str(col)
        missing = int(series.isna().sum())

        if non_null.empty:
            issues.append({"column": name, "issue": "empty", "detail": "Column has no data at all."})
            continue

        if missing / len(series) > threshold:
            issues.append({
                "column": name,
                "issue": "mostly_missing",
                "detail": f"{missing} of {len(series)} values are missing (over 90%).",
            })

        if non_null.nunique() == 1 and len(series) > 1:
            issues.append({
                "column": name,
                "issue": "constant",
                "detail": f"Every non-empty row has the same value: {non_null.iloc[0]!r}.",
            })

        # Email-shaped column: most non-null values look like emails, but not all.
        stripped_non_null = non_null.map(lambda v: str(v).strip())
        is_email = stripped_non_null.map(lambda v: bool(EMAIL_RE.match(v)))
        if is_email.mean() > 0.5:
            bad = int((~is_email).sum())
            if bad > 0:
                issues.append({
                    "column": name,
                    "issue": "invalid_email",
                    "detail": f"{bad} value(s) do not look like valid email addresses.",
                })

        # Date-shaped column: check token word boundary rather than raw substring
        if DATE_REGEX.search(name) or name.lower() in DATE_HINTS:
            parsed = pd.to_datetime(non_null.astype(str), errors="coerce")
            invalid = int(parsed.isna().sum())
            if invalid > 0 and non_null.nunique() > 2:
                issues.append({
                    "column": name,
                    "issue": "invalid_date",
                    "detail": f"{invalid} value(s) could not be parsed as dates.",
                })

    return issues


def _build_suggestions(analysis: dict) -> list[dict]:
    """Turn raw findings into user-facing cleaning suggestions."""
    suggestions: list[dict] = []

    if analysis["missing_values"]:
        total = sum(analysis["missing_values"].values())
        suggestions.append({
            "id": "missing",
            "title": "Missing Values",
            "summary": f"{total} missing value(s) found across {len(analysis['missing_values'])} column(s).",
            "detail": analysis["missing_values"],
        })

    if analysis["duplicates"] > 0:
        suggestions.append({
            "id": "duplicates",
            "title": "Duplicate Rows",
            "summary": f"{analysis['duplicates']} duplicate row(s) found.",
            "detail": analysis["duplicates"],
        })

    if analysis["inconsistent_text"]:
        cols = ", ".join(analysis["inconsistent_text"].keys())
        suggestions.append({
            "id": "text",
            "title": "Inconsistent Text",
            "summary": f"Case or whitespace inconsistencies found in: {cols}.",
            "detail": analysis["inconsistent_text"],
        })

    for issue in analysis["basic_issues"]:
        suggestions.append({
            "id": f"issue_{issue['column']}_{issue['issue']}",
            "title": f"Issue in {issue['column']}",
            "summary": issue["detail"],
            "detail": issue,
        })

    if not suggestions:
        suggestions.append({
            "id": "clean",
            "title": "No obvious issues",
            "summary": "No common data-quality problems were detected. You can still download the dataset.",
            "detail": None,
        })
    return suggestions


def analyze_dataset(df: pd.DataFrame) -> dict:
    """Run the full analysis and return one structured result.

    This is the main entry point for the route layer. It calls every other
    analysis function and assembles their output plus ready-made suggestions.
    """
    analysis = {
        "overview": get_dataset_overview(df),
        "missing_values": get_missing_values(df),
        "duplicates": get_duplicate_count(df),
        "inconsistent_text": detect_inconsistent_text(df),
        "basic_issues": detect_basic_issues(df),
    }
    analysis["suggestions"] = _build_suggestions(analysis)
    return analysis
