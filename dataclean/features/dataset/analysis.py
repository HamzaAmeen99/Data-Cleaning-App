"""Dataset inspection and data-quality analysis.

Everything here is read-only: analysis functions never modify the DataFrame.
All findings are deliberately conservative — the app only reports what can be
checked with simple, deterministic rules.
"""

import re

import pandas as pd
from config import Config

from features.cleaning.operations import missing_mask

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Columns that "look like" dates by name, used only as a hint for date checks.
DATE_HINTS = ("date", "day", "month", "year", "time")
DATE_REGEX = re.compile(r"(?:\b|_)(date|day|month|year|time)(?:\b|_)", re.IGNORECASE)

# Column names that suggest one value per row, so a repeat is a duplicate key.
KEY_HINTS = re.compile(
    r"(?:^|_)(id|key|code|sku|email|ref|reference|number|no|username|uuid)(?:$|_)",
    re.IGNORECASE,
)

# Outlier bounds use the standard 1.5 x IQR rule; reported, never applied.
OUTLIER_MULTIPLIER = 1.5
MAX_EXAMPLES = 5

_INT_TEXT_RE = re.compile(r"[+-]?\d+")
_DECIMAL_TEXT_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[T ].*)?$")
_BOOLEAN_WORDS = {"true", "false", "yes", "no", "y", "n"}


def _value_kind(value) -> str:
    """Coarse kind of a text cell: number, date, boolean or text."""
    text = str(value).strip()
    if not text:
        return "text"
    if _INT_TEXT_RE.fullmatch(text):
        return "number"
    if _DECIMAL_TEXT_RE.fullmatch(text):
        return "number"
    if _AMBIGUOUS_DATE_RE.match(text) or _ISO_DATE_RE.match(text):
        return "date"
    if text.lower() in _BOOLEAN_WORDS:
        return "boolean"
    return "text"


def _examples(values) -> list[str]:
    """Up to MAX_EXAMPLES short, deduplicated string examples."""
    seen: list[str] = []
    for value in values:
        text = str(value)
        text = text if len(text) <= 40 else text[:37] + "..."
        if text not in seen:
            seen.append(text)
        if len(seen) >= MAX_EXAMPLES:
            break
    return seen


def detect_mixed_types(df: pd.DataFrame) -> dict:
    """Text columns holding more than one kind of value (numbers among text).

    Report-only: values are never changed here.

    Returns:
        {column: {"dominant": str, "minority": {kind: count}, "count": int, "examples": [...]}}
    """
    findings: dict = {}
    for col in df.columns:
        series = df[col].dropna().astype(str)
        if series.empty:
            continue
        kinds = series.map(_value_kind)
        counts = kinds.value_counts()
        if len(counts) < 2:
            continue
        dominant = str(counts.index[0])
        minority = {str(kind): int(n) for kind, n in counts.items() if str(kind) != dominant}
        findings[str(col)] = {
            "dominant": dominant,
            "minority": minority,
            "count": int(sum(minority.values())),
            "examples": _examples(series[kinds != dominant].unique()),
        }
    return findings


def detect_duplicate_keys(df: pd.DataFrame) -> dict:
    """Key-looking columns whose values repeat (e.g. two rows with the same ID).

    Returns:
        {column: {"count": int, "examples": [...]}} where count is the number of
        rows beyond the first with a repeated key.
    """
    findings: dict = {}
    for col in df.columns:
        if not KEY_HINTS.search(str(col)):
            continue
        values = df[col].dropna().astype(str).str.strip()
        values = values[values != ""]
        if values.empty:
            continue
        repeated = values[values.duplicated(keep=False)]
        if repeated.empty:
            continue
        findings[str(col)] = {
            "count": int(values.duplicated().sum()),
            "examples": _examples(sorted(repeated.unique())),
        }
    return findings


def detect_invalid_categories(df: pd.DataFrame, max_unique: int = 30) -> dict:
    """One-off values in a column that is otherwise made of repeated values.

    A value seen once while at least two other values repeat is usually a typo
    or an unmerged category, so it is reported (never changed) for review.

    Returns:
        {column: {"count": int, "examples": [...]}}
    """
    findings: dict = {}
    for col in df.columns:
        values = df[col].dropna().astype(str).str.strip()
        values = values[values != ""]
        distinct = values.nunique()
        if distinct < 3 or distinct > max_unique:
            continue
        counts = values.value_counts()
        singles = counts[counts == 1]
        if len(counts[counts >= 2]) < 2 or singles.empty:
            continue
        findings[str(col)] = {
            "count": int(len(singles)),
            "examples": _examples(singles.index),
        }
    return findings


def detect_outliers(df: pd.DataFrame, multiplier: float = OUTLIER_MULTIPLIER) -> dict:
    """Numeric outliers by the 1.5 x IQR rule. Flags them; never changes them.

    Returns:
        {column: {"count": int, "lower": float, "upper": float,
                  "multiplier": float, "examples": [...]}}
    """
    findings: dict = {}
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        valid = numeric.dropna()
        if len(valid) < 4:
            continue
        first_quartile = float(valid.quantile(0.25))
        third_quartile = float(valid.quantile(0.75))
        spread = third_quartile - first_quartile
        if spread == 0:
            continue
        lower = first_quartile - multiplier * spread
        upper = third_quartile + multiplier * spread
        mask = numeric.notna() & ((numeric < lower) | (numeric > upper))
        count = int(mask.sum())
        if not count:
            continue
        findings[str(col)] = {
            "count": count,
            "lower": lower,
            "upper": upper,
            "multiplier": multiplier,
            "examples": _examples(df.loc[mask, col].tolist()),
        }
    return findings


def get_dataset_overview(df: pd.DataFrame) -> dict:
    """Return basic dataset information: rows, columns, names and dtypes."""
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "column_names": list(df.columns.astype(str)),
        "data_types": {str(col): str(dtype) for col, dtype in df.dtypes.items()},
    }


def get_missing_values(df: pd.DataFrame) -> dict:
    """Missing cells per column (only columns with any).

    Same definition as cleaning uses (missing_mask): NaN/None, whitespace-only
    and empty text always, plus the configured missing tokens (N/A, NULL, -).
    """
    out: dict = {}
    for col in df.columns:
        count = int(missing_mask(df[col], Config.MISSING_TOKENS).sum())
        if count:
            out[str(col)] = count
    return out


def get_missing_token_counts(df: pd.DataFrame) -> dict:
    """Per-column count of cells that are missing tokens rather than true NaNs.

    These become real missing values during cleaning only when the user
    explicitly opts in per column.
    """
    out: dict = {}
    for col in df.columns:
        token_mask = missing_mask(df[col], Config.MISSING_TOKENS)
        base = missing_mask(df[col])
        n = int((token_mask & ~base).sum())
        if n:
            out[str(col)] = n
    return out


def get_duplicate_count(df: pd.DataFrame) -> int:
    """Return the number of fully duplicated rows."""
    return int(df.duplicated().sum())


_AMBIGUOUS_DATE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-]\d{2,4}$")


def count_ambiguous_dates(series: pd.Series) -> int:
    """Count values that parse differently under day-first vs month-first.

    A value like 01/02/2024 is ambiguous (both parts <= 12 and a specific
    order matters, so 1/1/2024 does not count).
    """
    count = 0
    for value in series.dropna().astype(str):
        match = _AMBIGUOUS_DATE_RE.match(value.strip())
        if match:
            first, second = int(match.group(1)), int(match.group(2))
            if first != second and 1 <= first <= 12 and 1 <= second <= 12:
                count += 1
    return count


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
        name = str(col)
        mask = missing_mask(series, Config.MISSING_TOKENS)
        missing = int(mask.sum())
        non_null = series[~mask]

        if non_null.empty:
            issues.append({
                "column": name,
                "issue": "empty",
                "detail": "Column has no non-blank values (only blanks or missing tokens).",
            })
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
            ambiguous = count_ambiguous_dates(non_null)
            if invalid > 0:
                detail = f"{invalid} value(s) could not be parsed as dates."
                if ambiguous:
                    detail += (
                        f" {ambiguous} look like day/month dates (e.g. 01/02/2024) — "
                        "toggle day-first in cleaning if parsed the wrong way."
                    )
                issues.append({
                    "column": name,
                    "issue": "invalid_date",
                    "detail": detail,
                })
            elif ambiguous:
                issues.append({
                    "column": name,
                    "issue": "ambiguous_date",
                    "detail": (
                        f"{ambiguous} date value(s) are ambiguous between day/month and "
                        "month/day. Choose an explicit date format before conversion."
                    ),
                })

    return issues


def _build_suggestions(analysis: dict) -> list[dict]:
    """Turn raw findings into user-facing cleaning suggestions."""
    suggestions: list[dict] = []

    if analysis["missing_values"]:
        total = sum(analysis["missing_values"].values())
        token_total = sum(analysis["missing_tokens"].values())
        summary = f"{total} missing value(s) found across {len(analysis['missing_values'])} column(s)."
        if token_total:
            summary += f" {token_total} of them are written as missing tokens (N/A, NULL, -) — tick the per-column box to include them."
        suggestions.append({
            "id": "missing",
            "title": "Missing Values",
            "summary": summary,
            "detail": analysis["missing_values"],
        })

    duplicate_summary = (
        f"{analysis['duplicates']} exact duplicate row(s) found."
        if analysis["duplicates"]
        else "No exact duplicates found. You can still check duplicates using selected key columns."
    )
    suggestions.append({
        "id": "duplicates",
        "title": "Duplicate Rows",
        "summary": duplicate_summary,
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

    anomalies = {
        kind: analysis.get(kind, {})
        for kind in ("mixed_types", "duplicate_keys", "invalid_categories", "outliers")
    }
    if any(anomalies.values()):
        labels = (
            ("mixed types", anomalies["mixed_types"]),
            ("duplicate keys", anomalies["duplicate_keys"]),
            ("rare category values", anomalies["invalid_categories"]),
            ("outliers", anomalies["outliers"]),
        )
        columns = sum(len(group) for _, group in labels)
        suggestions.append({
            "id": "anomalies",
            "title": "Validation & Anomalies",
            "summary": (
                f"{columns} column(s) need review: "
                + ", ".join(name for name, group in labels if group)
                + "."
            ),
            "detail": anomalies,
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


def analyze_dataset(
    df: pd.DataFrame, pending_category_columns=()
) -> dict:
    """Run the full analysis and return one structured result.

    This is the main entry point for the route layer. It calls every other
    analysis function and assembles their output plus ready-made suggestions.
    """
    inconsistent_text = detect_inconsistent_text(df)
    for column in pending_category_columns:
        if column not in df.columns:
            continue
        values = df[column].dropna().astype(str).str.strip()
        unique_values = sorted(values.unique()) if values.nunique() <= 20 else []
        if len(unique_values) <= 1:
            continue
        finding = inconsistent_text.setdefault(column, {
            "case_variants": [],
            "whitespace_cells": 0,
            "unique_values": unique_values,
        })
        finding["pending_mapping"] = True

    analysis = {
        "overview": get_dataset_overview(df),
        "missing_values": get_missing_values(df),
        "missing_tokens": get_missing_token_counts(df),
        "duplicates": get_duplicate_count(df),
        "inconsistent_text": inconsistent_text,
        "basic_issues": detect_basic_issues(df),
        "mixed_types": detect_mixed_types(df),
        "duplicate_keys": detect_duplicate_keys(df),
        "invalid_categories": detect_invalid_categories(df),
        "outliers": detect_outliers(df),
    }
    analysis["suggestions"] = _build_suggestions(analysis)
    return analysis
