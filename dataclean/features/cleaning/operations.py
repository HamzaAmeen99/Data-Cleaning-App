"""Deterministic cleaning operations.

Each function takes a DataFrame plus explicit user-approved parameters and
returns the cleaned DataFrame together with a count of what changed.
Nothing here silently guesses on the user's behalf: merging categories,
changing case or dropping columns only happens when explicitly requested.
"""

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd
from utils.validators import validate_column

DATE_FORMATS = (
    "",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%Y/%m/%d",
)

VALIDATION_RULES = ("required", "unique", "range", "allowed", "pattern", "outlier")
VALIDATION_ACTIONS = ("report", "set_missing", "remove")
GROUP_FILL_METHODS = ("mean", "median", "mode", "ffill")
RECIPE_FORMAT = "dataclean-recipe-audit"
RECIPE_VERSION = 1


def _regex_error(pattern, *, max_length: int = 128) -> str | None:
    """Return why a user-supplied pattern is unsafe or invalid."""
    if not isinstance(pattern, str) or not pattern:
        return f"pattern must contain 1 to {max_length} characters"
    if len(pattern) > max_length:
        return f"pattern must contain 1 to {max_length} characters"
    if re.search(r"\\[1-9]|\(\?<[=!]|\([^)]*[+*][^)]*\)[+*{]", pattern):
        return "pattern uses a construct that can take too long to evaluate"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"invalid pattern ({exc})"
    return None


def operation_plan_token(operations: list[dict]) -> str:
    """Return a stable digest proving which exact plan was previewed."""
    payload = json.dumps(operations, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def remove_duplicate_rows(
    df: pd.DataFrame, mode: str = "first", subset: list[str] | None = None
) -> tuple[pd.DataFrame, int]:
    """Remove duplicated rows honoring the user's selected mode.

    mode:
        first - keep the first occurrence
        last  - keep the last occurrence
        all   - remove every occurrence of a duplicated row
    subset: columns that define a duplicate (None = the entire row).
    Rows whose keys are missing are still compared against each other.

    Returns:
        (cleaned_df, number_of_rows_removed)
    """
    if mode == "all":
        dup_mask = df.duplicated(subset=subset, keep=False)
        removed = int(dup_mask.sum())
        return df[~dup_mask].copy(), removed
    keep = "last" if mode == "last" else "first"
    dup_mask = df.duplicated(subset=subset, keep=keep)
    removed = int(dup_mask.sum())
    return df[~dup_mask].copy(), removed


def trim_whitespace(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip leading/trailing whitespace from every text (object) column.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    cleaned = df.copy()
    changed = 0
    for col in cleaned.select_dtypes(include="object").columns:
        original = cleaned[col]
        stripped = original.astype(str).str.strip()
        # Only count non-null cells that actually changed
        mask = original.notna() & (original != stripped)
        changed += int(mask.sum())
        cleaned[col] = original.where(original.isna(), stripped)
    return cleaned, changed


def standardize_text_case(
    df: pd.DataFrame, column: str, case_type: str
) -> tuple[pd.DataFrame, int]:
    """Convert a text column to lower, upper or title case.

    Preserves existing whitespace and column dtypes without unintended stripping.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    if not validate_column(df, column):
        return df, 0

    cleaners = {
        "lower": str.lower,
        "upper": str.upper,
        "title": str.title,
    }
    cleaner = cleaners.get(case_type)
    if cleaner is None:
        return df, 0

    cleaned = df.copy()
    original = cleaned[column]
    converted = original.map(
        lambda v: cleaner(str(v)) if pd.notna(v) and isinstance(v, (str, bytes)) else v
    )
    # Compare while safely ignoring NaN == NaN
    changed = int(((converted != original) & (original.notna() | converted.notna())).sum())
    cleaned[column] = converted
    return cleaned, changed


def missing_mask(series: pd.Series, tokens=None) -> pd.Series:
    """Boolean mask of cells that count as missing.

    Counts NaN/None, whitespace-only and empty text (always), plus any of the
    user-selected missing tokens matched case-insensitively after trimming.
    Both analysis and cleaning use this helper so their counts always agree.
    """
    mask = series.isna()
    text = series.astype("string").str.strip().str.lower()
    token_set = {""} | {str(t).strip().lower() for t in (tokens or ())}
    return mask | (text.notna() & text.isin(token_set))


def handle_missing_values(
    df: pd.DataFrame, column: str, method: str, custom_value=None, tokens=None
) -> tuple[pd.DataFrame, int]:
    """Fill or remove missing values in one column.

    "Missing" follows missing_mask(): real NaNs, whitespace-only/empty text,
    and — only when the user explicitly selects it — the configured missing
    tokens (N/A, NULL, -, …).

    Methods:
        remove  - drop rows where this column is missing
        mean / median - numeric columns only
        mode    - most frequent value among non-missing cells (any column);
                  ties are reported by the caller
        custom  - fill with the user-provided value; rejected with ValueError
                  when incompatible with the column's type

    Returns:
        (cleaned_df, number_of_rows_or_cells_affected)
    """
    if not validate_column(df, column):
        return df, 0

    cleaned = df.copy()
    mask = missing_mask(cleaned[column], tokens)
    missing_count = int(mask.sum())
    if missing_count == 0:
        return df, 0

    if method == "remove":
        return cleaned[~mask].copy(), missing_count

    if method == "mean":
        values = pd.to_numeric(cleaned.loc[~mask, column], errors="coerce")
        if values.empty or values.isna().any():
            return df, 0
        cleaned.loc[mask, column] = values.mean()
        return cleaned, missing_count

    if method == "median":
        values = pd.to_numeric(cleaned.loc[~mask, column], errors="coerce")
        if values.empty or values.isna().any():
            return df, 0
        cleaned.loc[mask, column] = values.median()
        return cleaned, missing_count

    if method == "mode":
        modes = cleaned.loc[~mask, column].mode(dropna=True)
        if modes.empty:
            return df, 0
        cleaned.loc[mask, column] = modes.iloc[0]
        return cleaned, missing_count

    if method == "custom":
        if custom_value is None or str(custom_value).strip() == "":
            return df, 0
        fill_val = custom_value
        if pd.api.types.is_numeric_dtype(cleaned[column]):
            try:
                fill_num = float(str(custom_value).strip())
            except (ValueError, TypeError):
                raise ValueError(
                    f"Custom value '{custom_value}' is not a number, but "
                    f"'{column}' is numeric."
                )
            if pd.api.types.is_integer_dtype(cleaned[column]) and not fill_num.is_integer():
                raise ValueError(
                    f"Custom value '{custom_value}' is not a whole number, but "
                    f"'{column}' holds integers."
                )
            fill_val = int(fill_num) if pd.api.types.is_integer_dtype(cleaned[column]) else fill_num
        cleaned.loc[mask, column] = fill_val
        return cleaned, missing_count

    return df, 0


def clean_invalid_dates(
    df: pd.DataFrame,
    column: str,
    action: str = "coerce",
    dayfirst: bool = False,
    date_format: str = "",
) -> tuple[pd.DataFrame, int]:
    """Handle unparseable date values in a column.

    Actions:
        coerce - set invalid date values to missing (NaN)
        remove - drop rows containing unparseable dates
    dayfirst interprets slash dates as day/month when set.

    Returns:
        (cleaned_df, number_of_rows_or_cells_affected)
    """
    if not validate_column(df, column):
        return df, 0

    non_null = df[column].dropna()
    if non_null.empty:
        return df, 0

    parsed, _ = _parse_dates(non_null, dayfirst=dayfirst, date_format=date_format)
    bad_mask = parsed.isna()
    bad_count = int(bad_mask.sum())
    if bad_count == 0:
        return df, 0

    bad_indices = non_null[bad_mask].index
    cleaned = df.copy()

    if action == "remove":
        return cleaned.drop(index=bad_indices), bad_count
    elif action == "coerce":
        cleaned.loc[bad_indices, column] = pd.NA
        return cleaned, bad_count

    return df, 0


@dataclass
class ConversionReport:
    """Value changes are counted separately from a column's dtype change."""

    applied: bool = False
    changed_values: int = 0
    invalid_count: int = 0
    coerced_count: int = 0
    invalid_examples: list[str] = field(default_factory=list)
    source_dtype: str = ""
    target_dtype: str = ""
    error: str = ""
    tz_normalized: bool = False

    @property
    def type_changed(self) -> bool:
        return self.applied and self.source_dtype != self.target_dtype

    def describe(self, column: str, target_type: str) -> str:
        if not self.applied:
            examples = ", ".join(repr(value) for value in self.invalid_examples)
            reason = self.error or (
                f"{self.invalid_count} invalid value(s)"
                + (f" (examples: {examples})" if examples else "")
            )
            return f"Conversion of '{column}' to {target_type} rejected: {reason}. Column unchanged."
        dtype_note = (
            f"Type changed from {self.source_dtype} to {self.target_dtype}."
            if self.type_changed else "Type unchanged."
        )
        return (
            f"Converted '{column}' to {target_type}: {self.changed_values} value(s) changed; "
            f"{self.coerced_count} invalid value(s) set to missing. {dtype_note}"
            + (
                " Values were normalized to UTC as requested."
                if self.tz_normalized
                else ""
            )
        )


def _parse_integer(value):
    # Decimal avoids floating-point rounding of large integer strings.
    if pd.isna(value):
        return pd.NA
    if isinstance(value, bool):
        return int(value)
    try:
        number = Decimal(str(value).strip())
        if (
            number.is_finite()
            and -(2**63) <= number <= 2**63 - 1
            and number == number.to_integral_value()
        ):
            return int(number)
    except (InvalidOperation, ValueError):
        pass
    return pd.NA


def _parse_dates(
    series: pd.Series,
    dayfirst: bool = False,
    date_format: str = "",
    timezone_policy: str = "preserve",
) -> tuple[pd.Series, bool]:
    """Parse dates using explicit user choices and preserve offsets by default."""
    text = series.astype("string").str.strip().replace("", pd.NA)
    kwargs = {"errors": "coerce", "format": date_format or None}
    if not date_format:
        kwargs["dayfirst"] = dayfirst
    if timezone_policy == "utc":
        return pd.to_datetime(text, utc=True, **kwargs), True

    # Parse each value independently so mixed offsets remain exactly as given
    # instead of being silently normalized to UTC.
    parsed = text.map(
        lambda value: pd.NaT if pd.isna(value) else pd.to_datetime(value, **kwargs)
    )
    try:
        parsed = pd.Series(pd.array(parsed), index=series.index, name=series.name)
    except (ValueError, TypeError):
        parsed = parsed.astype(object)
    return parsed, False


def _parse_number(value, thousands: str = "", decimal: str = "."):
    """Parse one numeric cell honoring explicit separators; Decimal-exact.

    Returns int for whole values, float otherwise, pd.NA when unparseable.
    """
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    if not text:
        return pd.NA
    if thousands == "," and not re.fullmatch(
        r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text
    ):
        return pd.NA
    if thousands == "." and decimal == "," and not re.fullmatch(
        r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?", text
    ):
        return pd.NA
    if thousands:
        text = text.replace(thousands, "")
    if decimal != ".":
        text = text.replace(decimal, ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return pd.NA
    if not number.is_finite():
        return pd.NA
    if number == number.to_integral_value() and -(2**63) <= number <= 2**63 - 1:
        return int(number)
    return float(number)


def convert_column_type(
    df: pd.DataFrame,
    column: str,
    target_type: str,
    invalid_policy: str = "reject",
    *,
    dayfirst: bool = False,
    number_format: str = "",
    date_format: str = "",
    timezone_policy: str = "preserve",
) -> tuple[pd.DataFrame, ConversionReport]:
    """Convert a whole column, rejecting invalid values unless coercion is explicit.

    number_format: "" (plain), "us" (1,234.56) or "eu" (1.234,56) — applied to
    numeric targets. dayfirst only applies to the date target.

    Existing missing values are preserved and excluded from invalid counts.
    Equal values (e.g. 1 and 1.0) are not counted as changed; dtype changes
    are reported independently. The input DataFrame is never mutated.
    """
    report = ConversionReport()
    if not validate_column(df, column):
        report.error = "column does not exist"
        return df, report
    series = df[column]
    report.source_dtype = str(series.dtype)
    if invalid_policy not in ("reject", "coerce_to_missing"):
        report.error = "unsupported invalid-value policy"
        return df, report
    if number_format not in ("", "us", "eu"):
        report.error = "unsupported number format"
        return df, report
    if date_format not in DATE_FORMATS:
        report.error = "unsupported date format"
        return df, report
    if timezone_policy not in ("preserve", "utc"):
        report.error = "unsupported timezone policy"
        return df, report

    thousands, decimal_sep = {
        "": ("", "."), "us": (",", "."), "eu": (".", ",")
    }[number_format]

    try:
        if target_type == "integer":
            if thousands or decimal_sep != ".":
                parsed = [_parse_number(value, thousands, decimal_sep) for value in series]
                result = pd.Series(
                    [v if isinstance(v, int) else pd.NA for v in parsed],
                    index=series.index,
                    dtype="Int64",
                )
            else:
                result = pd.Series(
                    [_parse_integer(value) for value in series], index=series.index, dtype="Int64"
                )
        elif target_type == "float":
            if thousands or decimal_sep != ".":
                result = pd.Series(
                    [_parse_number(value, thousands, decimal_sep) for value in series],
                    index=series.index,
                    dtype="Float64",
                )
            else:
                result = pd.to_numeric(series, errors="coerce").astype("Float64")
            result = result.mask(result.isin([float("inf"), float("-inf")]))
        elif target_type == "string":
            result = series.astype("string")
        elif target_type == "date":
            result, normalized = _parse_dates(
                series,
                dayfirst=dayfirst,
                date_format=date_format,
                timezone_policy=timezone_policy,
            )
            report.tz_normalized = normalized
        elif target_type == "boolean":
            if pd.api.types.is_bool_dtype(series):
                result = series.astype("boolean")
            else:
                normalized = series.astype("string").str.strip().str.lower()
                result = normalized.map({
                    "true": True,
                    "yes": True,
                    "y": True,
                    "1": True,
                    "1.0": True,
                    "false": False,
                    "no": False,
                    "n": False,
                    "0": False,
                    "0.0": False,
                }).astype("boolean")
        else:
            report.error = "unsupported target type"
            return df, report
    except (ValueError, TypeError, OverflowError):
        report.error = "values cannot be represented in the selected type"
        return df, report

    invalid = series.notna() & result.isna()
    report.invalid_count = int(invalid.sum())
    report.invalid_examples = [_describe_value(value) for value in series[invalid].head(3)]
    report.target_dtype = str(result.dtype)
    if report.invalid_count and invalid_policy == "reject":
        return df, report

    same = pd.Series(
        (_values_match(a, b) for a, b in zip(series, result.astype(object))),
        index=series.index,
    )
    report.changed_values = int((~same).sum())
    report.coerced_count = report.invalid_count
    report.applied = True
    cleaned = df.copy()
    cleaned[column] = result
    return cleaned, report


def standardize_categories(
    df: pd.DataFrame, column: str, mapping: dict
) -> tuple[pd.DataFrame, int]:
    """Replace values in a column using an explicit user-provided mapping.

    The application never merges categories silently: every replacement in
    `mapping` was chosen by the user.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    if not validate_column(df, column) or not mapping:
        return df, 0

    clean_mapping = {
        str(k).strip(): str(v).strip()
        for k, v in mapping.items()
        if str(k).strip() != str(v).strip() and str(v).strip() != ""
    }
    if not clean_mapping:
        return df, 0

    cleaned = df.copy()
    original = cleaned[column]

    # Replace on both raw and stripped string forms
    lookup = dict(clean_mapping)
    def _replace_val(val):
        if pd.isna(val):
            return val
        sval = str(val)
        stripped = sval.strip()
        if stripped in lookup:
            return lookup[stripped]
        if sval in lookup:
            return lookup[sval]
        return val

    replaced = original.map(_replace_val)
    # Accurately count only actual value changes, ignoring NaN == NaN
    changed = int(((replaced != original) & (original.notna() | replaced.notna())).sum())
    cleaned[column] = replaced
    return cleaned, changed


def drop_columns(df: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, int]:
    """Drop specified columns from the DataFrame.

    Returns:
        (cleaned_df, number_of_columns_removed)
    """
    valid_cols = [col for col in columns if validate_column(df, col)]
    if not valid_cols:
        return df, 0
    return df.drop(columns=valid_cols), len(valid_cols)


def remove_empty_columns(df: pd.DataFrame, tokens=None) -> tuple[pd.DataFrame, int]:
    """Drop columns that contain no data at all (only blanks/missing values).

    Blank text (empty or whitespace-only cells) counts, matching analysis.

    Returns:
        (cleaned_df, number_of_columns_removed)
    """
    empty_cols = [col for col in df.columns if bool(missing_mask(df[col], tokens).all())]
    if not empty_cols:
        return df, 0
    return df.drop(columns=empty_cols), len(empty_cols)


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text_value(value):
    if not isinstance(value, str):
        return value
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def normalize_text(df: pd.DataFrame, columns=None) -> tuple[pd.DataFrame, int]:
    """Unicode- and whitespace-normalize text columns.

    Applies NFKC (so full-width characters and non-breaking spaces collapse to
    their plain equivalents) and reduces every run of whitespace to one space,
    then strips both ends. Non-text values pass through untouched.

    columns: columns to normalize; None/empty means every column, which only
    changes the ones that hold text.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    cleaned = df.copy()
    targets = [col for col in (columns or list(cleaned.columns)) if validate_column(cleaned, col)]
    changed = 0
    for col in targets:
        original = cleaned[col]
        converted = original.map(_normalize_text_value)
        changed += int(((converted != original) & (original.notna() | converted.notna())).sum())
        cleaned[col] = converted
    return cleaned, changed


def regex_replace(
    df: pd.DataFrame, column: str, pattern: str, replacement: str = ""
) -> tuple[pd.DataFrame, int]:
    """Replace text matching a regular expression in one column.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    if not validate_column(df, column) or _regex_error(pattern):
        return df, 0
    try:
        matcher = re.compile(pattern)
    except re.error:
        return df, 0
    cleaned = df.copy()
    original = cleaned[column]
    converted = original.map(
        lambda value: matcher.sub(replacement, value) if isinstance(value, str) else value
    )
    changed = int(((converted != original) & (original.notna() | converted.notna())).sum())
    cleaned[column] = converted
    return cleaned, changed


def split_column(
    df: pd.DataFrame, column: str, separator: str, new_columns: list[str]
) -> tuple[pd.DataFrame, int]:
    """Split one text column into several columns on a separator.

    The original column is replaced by the new ones; missing parts become
    missing values rather than empty text.

    Returns:
        (cleaned_df, number_of_rows_that_split)
    """
    names = [str(name) for name in (new_columns or [])]
    if not validate_column(df, column) or len(names) < 2:
        return df, 0
    cleaned = df.copy()
    source = cleaned[column].astype("string")
    parts = cleaned[column].astype("string").str.split(
        separator, n=len(names) - 1, expand=True, regex=False
    )
    for position, name in enumerate(names):
        cleaned[name] = parts[position] if position in parts.columns else pd.NA
    cleaned = cleaned.drop(columns=[column])
    rows_split = int(source.str.contains(separator, regex=False, na=False).sum())
    return cleaned, rows_split


def merge_columns(
    df: pd.DataFrame,
    columns: list[str],
    separator: str,
    new_column: str,
    drop_source: bool = True,
) -> tuple[pd.DataFrame, int]:
    """Join several columns into one text column, skipping missing parts.

    Returns:
        (cleaned_df, number_of_rows_with_a_merged_value)
    """
    sources = [col for col in (columns or []) if validate_column(df, col)]
    if len(sources) < 2 or not str(new_column or "").strip():
        return df, 0
    cleaned = df.copy()
    # shortcut: row-wise join, fine at the upload cap; vectorize if the cap grows
    merged = cleaned[sources].apply(
        lambda row: separator.join(
            str(value) for value in row if pd.notna(value) and str(value) != ""
        )
        or pd.NA,
        axis=1,
    )
    cleaned[str(new_column)] = merged
    filled = int(merged.notna().sum())
    if drop_source:
        cleaned = cleaned.drop(columns=sources)
    return cleaned, filled


def fill_by_group(
    df: pd.DataFrame, group_by: list[str], column: str, method: str
) -> tuple[pd.DataFrame, dict]:
    """Fill missing values in one column from each group's own values.

    A group whose rows are all missing cannot be filled and is reported instead
    of being filled with a value borrowed from another group.

    Returns:
        (cleaned_df, {"filled": int, "unfilled": int, "unfilled_groups": [str, ...]})
    """
    report = {"filled": 0, "unfilled": 0, "unfilled_groups": []}
    groups = [col for col in (group_by or []) if validate_column(df, col)]
    if not validate_column(df, column) or not groups:
        return df, report

    cleaned = df.copy()
    mask = missing_mask(cleaned[column])
    if not mask.any():
        return df, report
    report["unfilled"] = int(mask.sum())

    for key, positions in cleaned.groupby(groups, dropna=False).groups.items():
        positions = list(positions)
        missing_here = [index for index in positions if mask.loc[index]]
        if not missing_here:
            continue
        present = cleaned.loc[[index for index in positions if not mask.loc[index]], column]
        if method == "ffill":
            last = None
            for index in positions:  # original row order inside the group
                if not mask.loc[index]:
                    last = cleaned.at[index, column]
                elif last is not None:
                    cleaned.at[index, column] = last
            filled = sum(1 for index in missing_here if pd.notna(cleaned.at[index, column]))
        else:
            fill = _group_fill_value(present, method)
            if fill is None:
                report["unfilled_groups"].append(_describe_value(key))
                continue
            cleaned.loc[missing_here, column] = fill
            filled = len(missing_here)
        report["filled"] += filled
        report["unfilled"] -= filled
    return cleaned, report


def _group_fill_value(present: pd.Series, method: str):
    """Value to fill a group with, or None when the group has no usable value."""
    if method in ("mean", "median"):
        numeric = pd.to_numeric(present, errors="coerce").dropna()
        if numeric.empty:
            return None
        return numeric.mean() if method == "mean" else numeric.median()
    modes = present.mode(dropna=True)
    if modes.empty:
        return None
    return modes.iloc[0]


def evaluate_validation_rule(df: pd.DataFrame, operation: dict) -> dict:
    """Return the rows that violate one explicit validation rule."""
    column = operation.get("column", "")
    rule = operation.get("rule", "")
    if not validate_column(df, column):
        raise ValueError(f"Column '{column}' does not exist.")

    series = df[column]
    missing = missing_mask(series, operation.get("tokens") or None)
    violations = pd.Series(False, index=df.index, dtype=bool)
    details = ""

    if rule == "required":
        violations = missing
    elif rule == "unique":
        violations = ~missing & series.duplicated(keep=False)
    elif rule == "range":
        numeric = pd.to_numeric(series, errors="coerce")
        comparable = ~missing & numeric.notna()
        minimum = operation.get("minimum")
        maximum = operation.get("maximum")
        if minimum is not None:
            violations |= comparable & (numeric < float(minimum))
        if maximum is not None:
            violations |= comparable & (numeric > float(maximum))
        # A present value that is not numeric cannot satisfy a numeric range.
        violations |= ~missing & numeric.isna()
        details = f"range {minimum if minimum is not None else '-inf'} to {maximum if maximum is not None else 'inf'}"
    elif rule == "allowed":
        allowed = {str(value).strip() for value in operation.get("values", [])}
        normalized = series.astype("string").str.strip()
        violations = ~missing & ~normalized.isin(allowed)
        details = "allowed values: " + ", ".join(sorted(allowed)[:8])
    elif rule == "pattern":
        pattern = re.compile(operation.get("pattern", ""))
        normalized = series.astype("string")
        matches = normalized.str.fullmatch(pattern, na=False)
        violations = ~missing & ~matches
        details = f"pattern {operation.get('pattern', '')!r}"
    elif rule == "outlier":
        numeric = pd.to_numeric(series, errors="coerce")
        valid = numeric[~missing & numeric.notna()]
        multiplier = float(operation.get("multiplier", 1.5))
        if len(valid) >= 4:
            first_quartile = float(valid.quantile(0.25))
            third_quartile = float(valid.quantile(0.75))
            spread = third_quartile - first_quartile
            if spread > 0:
                lower = first_quartile - multiplier * spread
                upper = third_quartile + multiplier * spread
                violations = ~missing & numeric.notna() & ((numeric < lower) | (numeric > upper))
                details = f"IQR bounds {lower:g} to {upper:g} ({multiplier:g}x)"
            else:
                details = "IQR is zero; no outliers can be distinguished"
        else:
            details = "IQR needs at least four numeric values"
    else:
        raise ValueError(f"Unknown validation rule '{rule}'.")

    examples = [_describe_value(value) for value in series[violations].head(5)]
    return {
        "mask": violations,
        "count": int(violations.sum()),
        "examples": examples,
        "details": details,
    }


def apply_validation_rule(df: pd.DataFrame, operation: dict) -> tuple[pd.DataFrame, dict]:
    """Report or explicitly resolve values that violate a validation rule."""
    report = evaluate_validation_rule(df, operation)
    action = operation.get("action", "report")
    if not report["count"] or action == "report":
        return df, report

    cleaned = df.copy()
    if action == "set_missing":
        cleaned.loc[report["mask"], operation["column"]] = pd.NA
    elif action == "remove":
        cleaned = cleaned.loc[~report["mask"]].copy()
    return cleaned, report


def _values_match(a, b) -> bool:
    # pd.NA == anything raises ("NA is ambiguous"), so equality is decided per value.
    a_na = pd.isna(a)
    b_na = pd.isna(b)
    if a_na or b_na:
        return a_na and b_na
    return bool(a == b)


def _unmapped_categories(values, mapping) -> list[str]:
    """Source values a category mapping left untouched (not a key and not a target)."""
    keys = {str(key).strip() for key in mapping}
    targets = {str(value).strip() for value in mapping.values()}
    return sorted({str(value) for value in values if str(value) not in keys and str(value) not in targets})


def _describe_value(value) -> str:
    """Short human-readable rendering of a value for the cleaning history."""
    text = str(value)
    return text if len(text) <= 30 else text[:27] + "..."


def _preview_value(value):
    return None if pd.isna(value) else str(value)


# Priority order used by apply_cleaning_operations to sort user-selected ops
# into a stable, logically correct pipeline regardless of form submission order.
def _diff_cells(before: pd.DataFrame, after: pd.DataFrame):
    """Changed-cell count plus up to 5 before/after examples between two frames.

    NA-safe via _values_match, so a missing-value fill counts exactly once.
    """
    if len(before) != len(after):
        return 0, []  # row-level changes are reported separately, not as cell diffs
    try:
        before_values = before.to_numpy(dtype=object)
        after_values = after.to_numpy(dtype=object)
    except (ValueError, TypeError):
        return 0, []
    if before_values.shape != after_values.shape:
        return 0, []

    changed = 0
    examples: list[dict] = []
    columns = [str(col) for col in before.columns]
    for row_position, (before_row, after_row) in enumerate(zip(before_values, after_values)):
        before_label = str(before.index[row_position])
        for column_position, (old, new) in enumerate(zip(before_row, after_row)):
            if not _values_match(old, new):
                changed += 1
                if len(examples) < 5:
                    examples.append({
                        "column": columns[column_position],
                        "row": before_label,
                        "before": _preview_value(old),
                        "after": _preview_value(new),
                    })
    return changed, examples


def preview_plan(df: pd.DataFrame, operations: list[dict]) -> dict:
    """Execute a validated plan on a copy and summarize what it would do.

    Never touches the working dataset. Returns per-step affected counts,
    warnings (rejected fills, no-op steps) and up to 5 representative
    before/after cell examples.
    """
    current = df.copy()
    rows_before = len(current)
    steps: list[dict] = []
    warnings: list[str] = []
    examples: list[dict] = []

    sorted_ops = sorted(operations, key=lambda op: _OP_ORDER.get(op.get("type", ""), 99))
    for op in sorted_ops:
        before = current
        validation_report = (
            evaluate_validation_rule(current, op) if op.get("type") == "validate" else None
        )
        current, history = apply_cleaning_operations(current, [op], converted_types=None)
        rows_removed = len(before) - len(current)
        changed_cells, step_examples = _diff_cells(before, current)
        columns_removed = [str(column) for column in before.columns if column not in current.columns]
        columns_added = [str(column) for column in current.columns if column not in before.columns]
        shared_columns = [column for column in before.columns if column in current.columns]
        types_changed = [
            str(column)
            for column in shared_columns
            if str(before[column].dtype) != str(current[column].dtype)
        ]
        if rows_removed and len(examples) < 5:
            current_indices = set(current.index)
            removed_indices = [index for index in before.index if index not in current_indices]
            for index in removed_indices[: 5 - len(examples)]:
                examples.append({
                    "operation": op.get("type", ""),
                    "row": str(index),
                    "column": "(row)",
                    "before": {
                        str(column): _preview_value(value)
                        for column, value in before.loc[index].to_dict().items()
                    },
                    "after": "removed",
                })
        if columns_removed and len(examples) < 5:
            for column in columns_removed[: 5 - len(examples)]:
                examples.append({
                    "operation": op.get("type", ""),
                    "row": "all",
                    "column": column,
                    "before": "column present",
                    "after": "removed",
                })
        for example in step_examples:
            if len(examples) < 5:
                examples.append({**example, "operation": op.get("type", "")})
        label = op.get("type", "")
        if op.get("column"):
            label = f"{label}:{op['column']}"
        affected = (
            validation_report["count"]
            if validation_report is not None
            else rows_removed or changed_cells or len(columns_removed) or len(types_changed)
        )
        steps.append({
            "label": label,
            "affected": int(affected),
            "rows_removed": int(rows_removed),
            "cells_changed": int(changed_cells),
            "columns_removed": columns_removed,
            "columns_added": columns_added,
            "types_changed": types_changed,
            "violations": validation_report["count"] if validation_report else 0,
        })
        if (
            validation_report
            and validation_report["count"]
            and op.get("action", "report") == "report"
        ):
            warnings.append(
                f"{label}: {validation_report['count']} violation(s) reported; values will stay unchanged."
            )
        for line in history:
            if "No changes were needed" in line:
                warnings.append(f"{label}: no changes — nothing matches this step.")
            elif "rejected" in line.lower():
                warnings.append(f"{label}: {line}")

    return {
        "rows_before": int(rows_before),
        "rows_after": int(len(current)),
        "steps": steps,
        "warnings": warnings,
        "examples": examples,
    }


# Priority order used by apply_cleaning_operations to sort user-selected ops
# into a stable, logically correct pipeline regardless of form submission order.
_OP_ORDER = {
    "drop_columns": 0,
    "empty_columns": 1,
    "normalize_text": 2,
    "trim_whitespace": 3,
    "split_column": 4,
    "merge_columns": 5,
    "regex_replace": 6,
    "duplicates": 7,
    "categories": 8,
    "case": 9,
    "missing": 10,
    "group_fill": 11,
    "invalid_date": 12,
    "convert": 13,
    "validate": 14,
}

_OP_KEYS = {
    "drop_columns": {"type", "columns"},
    "empty_columns": {"type", "tokens"},
    "normalize_text": {"type", "columns"},
    "trim_whitespace": {"type"},
    "split_column": {"type", "column", "separator", "new_columns"},
    "merge_columns": {"type", "columns", "separator", "new_column", "drop_source"},
    "regex_replace": {"type", "column", "pattern", "replacement"},
    "duplicates": {"type", "mode", "subset"},
    "categories": {"type", "column", "mapping"},
    "case": {"type", "column", "case_type"},
    "missing": {"type", "column", "method", "value", "tokens"},
    "group_fill": {"type", "group_by", "column", "method"},
    "invalid_date": {"type", "column", "action", "dayfirst", "date_format"},
    "convert": {
        "type", "column", "target_type", "invalid_policy", "dayfirst",
        "number_format", "date_format", "timezone_policy",
    },
    "validate": {
        "type", "column", "rule", "action", "minimum", "maximum",
        "values", "pattern", "multiplier", "tokens",
    },
}


def validate_plan(df: pd.DataFrame, operations: list[dict]) -> list[str]:
    """Check a cleaning plan against the dataset before anything executes.

    Returns human-readable errors ([] = the plan is valid). Runs through the
    same _OP_ORDER as execution so "documented order" and validation agree.
    Column references, parameters, conversion policies, duplicates/empty
    column names for renames, and incompatible operation combinations are
    all checked here; nothing in the plan may touch the dataset otherwise.
    """
    errors: list[str] = []
    columns = [str(col) for col in df.columns]
    column_set = set(columns)

    def _check_column(op, name, index):
        if name not in column_set:
            errors.append(f"Step {index + 1} ({op.get('type')}): column '{name}' does not exist.")
            return False
        return True

    referenced_by_non_drop: set[str] = set()
    drops: set[str] = set()

    for index, op in enumerate(operations):
        if not isinstance(op, dict):
            errors.append(f"Step {index + 1}: operation must be an object.")
            continue
        op_type = op.get("type", "")
        if op_type not in _OP_ORDER:
            errors.append(f"Step {index + 1}: unknown operation type '{op_type}'.")
            continue
        unknown_keys = set(op) - _OP_KEYS[op_type]
        if unknown_keys:
            errors.append(
                f"Step {index + 1} ({op_type}): unknown parameter(s): "
                + ", ".join(sorted(unknown_keys))
                + "."
            )

        if op_type == "drop_columns":
            raw_names = op.get("columns", [])
            if not isinstance(raw_names, list) or any(not isinstance(name, str) for name in raw_names):
                errors.append(f"Step {index + 1} (drop_columns): columns must be a list.")
                continue
            names = [str(c) for c in raw_names]
            if not names:
                errors.append(f"Step {index + 1} (drop_columns): no columns selected.")
            for name in names:
                _check_column(op, name, index)
                if names.count(name) > 1:
                    errors.append(f"Step {index + 1} (drop_columns): '{name}' listed twice.")
            drops |= set(names)
        elif op_type in ("empty_columns", "trim_whitespace"):
            if op_type == "empty_columns" and not isinstance(
                op.get("tokens", []), (list, tuple)
            ):
                errors.append(f"Step {index + 1} (empty_columns): tokens must be a list.")
        elif op_type == "duplicates":
            if op.get("mode") not in ("first", "last", "all"):
                errors.append(f"Step {index + 1} (duplicates): unknown keep mode '{op.get('mode')}'.")
            raw_subset = op.get("subset", [])
            if not isinstance(raw_subset, list) or any(not isinstance(name, str) for name in raw_subset):
                errors.append(f"Step {index + 1} (duplicates): subset must be a list.")
                continue
            subset = [str(c) for c in raw_subset]
            for name in subset:
                _check_column(op, name, index)
                referenced_by_non_drop |= set(subset)
        elif op_type == "normalize_text":
            raw_names = op.get("columns", [])
            if not isinstance(raw_names, list) or any(not isinstance(name, str) for name in raw_names):
                errors.append(f"Step {index + 1} (normalize_text): columns must be a list.")
                continue
            for name in [str(c) for c in raw_names]:
                _check_column(op, name, index)
                referenced_by_non_drop.add(name)
        elif op_type == "regex_replace":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            pattern = op.get("pattern", "")
            if not pattern:
                errors.append(f"Step {index + 1} (regex_replace): enter a search pattern.")
            else:
                pattern_error = _regex_error(pattern)
                if pattern_error:
                    errors.append(f"Step {index + 1} (regex_replace): {pattern_error}.")
            if not isinstance(op.get("replacement", ""), str):
                errors.append(f"Step {index + 1} (regex_replace): replacement must be text.")
        elif op_type == "split_column":
            column = op.get("column", "")
            if not _check_column(op, column, index):
                continue
            if not isinstance(op.get("separator", ""), str) or not op.get("separator"):
                errors.append(f"Step {index + 1} (split_column): enter a separator.")
            raw_names = op.get("new_columns", [])
            if not isinstance(raw_names, list) or any(not isinstance(name, str) for name in raw_names):
                errors.append(f"Step {index + 1} (split_column): new columns must be a list.")
                continue
            names = [str(name).strip() for name in raw_names]
            if len(names) < 2 or len(names) > 10:
                errors.append(
                    f"Step {index + 1} (split_column): provide 2 to 10 new column names."
                )
            for name in names:
                if not name:
                    errors.append(
                        f"Step {index + 1} (split_column): new column names cannot be empty."
                    )
                elif name in column_set:
                    errors.append(
                        f"Step {index + 1} (split_column): column '{name}' already exists."
                    )
            if len(set(names)) != len(names):
                errors.append(f"Step {index + 1} (split_column): new column names must be unique.")
            drops.add(column)  # the source column is replaced by the new ones
        elif op_type == "merge_columns":
            raw_sources = op.get("columns", [])
            if not isinstance(raw_sources, list) or any(
                not isinstance(name, str) for name in raw_sources
            ):
                errors.append(f"Step {index + 1} (merge_columns): columns must be a list.")
                continue
            sources = [str(c) for c in raw_sources]
            if len(sources) < 2:
                errors.append(f"Step {index + 1} (merge_columns): select at least two columns.")
            if len(set(sources)) != len(sources):
                errors.append(f"Step {index + 1} (merge_columns): source columns must be unique.")
            for name in sources:
                _check_column(op, name, index)
            raw_new_column = op.get("new_column", "")
            new_column = raw_new_column.strip() if isinstance(raw_new_column, str) else ""
            if not new_column:
                errors.append(
                    f"Step {index + 1} (merge_columns): enter a name for the merged column."
                )
            elif new_column in column_set:
                errors.append(
                    f"Step {index + 1} (merge_columns): column '{new_column}' already exists."
                )
            if not isinstance(op.get("separator", ""), str):
                errors.append(f"Step {index + 1} (merge_columns): separator must be text.")
            if not isinstance(op.get("drop_source", True), bool):
                errors.append(f"Step {index + 1} (merge_columns): drop_source must be true or false.")
            if op.get("drop_source", True):
                drops |= set(sources)
        elif op_type == "group_fill":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            raw_groups = op.get("group_by", [])
            if not isinstance(raw_groups, list) or any(
                not isinstance(name, str) for name in raw_groups
            ):
                errors.append(f"Step {index + 1} (group_fill): group_by must be a list.")
                continue
            groups = [str(c) for c in raw_groups]
            if not groups:
                errors.append(
                    f"Step {index + 1} (group_fill): choose at least one grouping column."
                )
            for name in groups:
                _check_column(op, name, index)
                referenced_by_non_drop.add(name)
            method = op.get("method")
            if method not in GROUP_FILL_METHODS:
                errors.append(f"Step {index + 1} (group_fill): unknown method '{method}'.")
            elif method in ("mean", "median"):
                mask = missing_mask(df[op["column"]])
                numeric = pd.to_numeric(df.loc[~mask, op["column"]], errors="coerce")
                if numeric.empty or numeric.isna().any():
                    errors.append(
                        f"Step {index + 1} (group_fill): '{op['column']}' contains non-numeric values."
                    )
        elif op_type == "categories":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            mapping = op.get("mapping", {})
            if not isinstance(mapping, dict):
                errors.append(f"Step {index + 1} (categories): mapping must be an object.")
                continue
            if not mapping:
                errors.append(f"Step {index + 1} (categories): empty mapping.")
            for source, target in mapping.items():
                if (
                    not isinstance(source, str)
                    or not isinstance(target, str)
                    or not source.strip()
                    or not target.strip()
                ):
                    errors.append(
                        f"Step {index + 1} (categories): mapping entry '{source}' has an empty side."
                    )
        elif op_type == "case":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            if op.get("case_type") not in ("lower", "upper", "title"):
                errors.append(
                    f"Step {index + 1} (case): unknown case type '{op.get('case_type')}'."
                )
        elif op_type == "missing":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            if not isinstance(op.get("tokens", []), (list, tuple)):
                errors.append(f"Step {index + 1} (missing): tokens must be a list.")
            if op.get("method") not in ("remove", "mean", "median", "mode", "custom"):
                errors.append(
                    f"Step {index + 1} (missing): unknown method '{op.get('method')}'."
                )
            elif op.get("method") in ("mean", "median"):
                mask = missing_mask(df[op["column"]], op.get("tokens") or None)
                numeric = pd.to_numeric(df.loc[~mask, op["column"]], errors="coerce")
                if numeric.empty or numeric.isna().any():
                    errors.append(
                        f"Step {index + 1} (missing): '{op['column']}' contains non-numeric values."
                    )
            elif op.get("method") == "custom":
                if op.get("value") is None or not str(op.get("value")).strip():
                    errors.append(
                        f"Step {index + 1} (missing): enter a custom replacement value."
                    )
                    continue
                try:
                    handle_missing_values(
                        df,
                        op["column"],
                        "custom",
                        op.get("value"),
                        op.get("tokens") or None,
                    )
                except (ValueError, TypeError) as exc:
                    errors.append(f"Step {index + 1} (missing): {exc}")
        elif op_type == "invalid_date":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            if op.get("action") not in ("coerce", "remove"):
                errors.append(
                    f"Step {index + 1} (invalid_date): unknown action '{op.get('action')}'."
                )
            if op.get("date_format", "") not in DATE_FORMATS:
                errors.append(
                    f"Step {index + 1} (invalid_date): unsupported date format."
                )
        elif op_type == "convert":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            if op.get("target_type") not in ("integer", "float", "string", "date", "boolean"):
                errors.append(
                    f"Step {index + 1} (convert): unknown target type '{op.get('target_type')}'."
                )
            if op.get("invalid_policy", "reject") not in ("reject", "coerce_to_missing"):
                errors.append(
                    f"Step {index + 1} (convert): unknown invalid-value policy "
                    f"'{op.get('invalid_policy')}'."
                )
            if op.get("number_format", "") not in ("", "us", "eu"):
                errors.append(
                    f"Step {index + 1} (convert): unknown number format '{op.get('number_format')}'."
                )
            if op.get("date_format", "") not in DATE_FORMATS:
                errors.append(f"Step {index + 1} (convert): unsupported date format.")
            if op.get("timezone_policy", "preserve") not in ("preserve", "utc"):
                errors.append(
                    f"Step {index + 1} (convert): unknown timezone policy "
                    f"'{op.get('timezone_policy')}'."
                )
        elif op_type == "validate":
            if not _check_column(op, op.get("column", ""), index):
                continue
            referenced_by_non_drop.add(op["column"])
            rule = op.get("rule", "")
            action = op.get("action", "report")
            if rule not in VALIDATION_RULES:
                errors.append(
                    f"Step {index + 1} (validate): unknown rule '{rule}'."
                )
                continue
            if action not in VALIDATION_ACTIONS:
                errors.append(
                    f"Step {index + 1} (validate): unknown action '{action}'."
                )
            if rule == "required" and action == "set_missing":
                errors.append(
                    f"Step {index + 1} (validate): required values are already missing; "
                    "use report or remove rows."
                )
            if not isinstance(op.get("tokens", []), (list, tuple)):
                errors.append(f"Step {index + 1} (validate): tokens must be a list.")
            if rule == "range":
                minimum = op.get("minimum")
                maximum = op.get("maximum")
                if minimum is None and maximum is None:
                    errors.append(
                        f"Step {index + 1} (validate): enter a minimum, maximum, or both."
                    )
                try:
                    parsed_minimum = float(minimum) if minimum is not None else None
                    parsed_maximum = float(maximum) if maximum is not None else None
                    if any(
                        value is not None and not math.isfinite(value)
                        for value in (parsed_minimum, parsed_maximum)
                    ):
                        errors.append(
                            f"Step {index + 1} (validate): range limits must be finite numbers."
                        )
                    elif (
                        parsed_minimum is not None
                        and parsed_maximum is not None
                        and parsed_minimum > parsed_maximum
                    ):
                        errors.append(
                            f"Step {index + 1} (validate): minimum exceeds maximum."
                        )
                except (TypeError, ValueError, OverflowError):
                    errors.append(
                        f"Step {index + 1} (validate): range limits must be numbers."
                    )
            elif rule == "allowed":
                values = op.get("values", [])
                if not isinstance(values, (list, tuple)) or not values:
                    errors.append(
                        f"Step {index + 1} (validate): enter at least one allowed value."
                    )
                elif len(values) > 100 or any(not str(value).strip() for value in values):
                    errors.append(
                        f"Step {index + 1} (validate): allowed values must contain 1 to 100 non-empty entries."
                    )
            elif rule == "pattern":
                pattern = op.get("pattern", "")
                pattern_error = _regex_error(pattern)
                if pattern_error:
                    errors.append(f"Step {index + 1} (validate): {pattern_error}.")
            elif rule == "outlier":
                try:
                    multiplier = float(op.get("multiplier", 1.5))
                    if not 0.5 <= multiplier <= 10:
                        raise ValueError
                except (TypeError, ValueError, OverflowError):
                    errors.append(
                        f"Step {index + 1} (validate): IQR multiplier must be between 0.5 and 10."
                    )

    # Incompatible combinations: dropping a column another step still uses.
    for name in sorted(drops & referenced_by_non_drop):
        errors.append(
            f"Incompatible plan: column '{name}' is dropped in this run but another "
            "operation still references it. Remove one of the two steps."
        )
    return errors


def apply_cleaning_operations(
    df: pd.DataFrame,
    operations: list[dict],
    *,
    converted_types: dict | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Apply a list of user-approved operations in a clean, predictable order.

    Operations are always executed in the canonical pipeline order defined by
    ``_OP_ORDER``, regardless of the order in which they were submitted from the
    HTML form.  This guarantees, for example, that whitespace is trimmed before
    deduplication so near-duplicates are caught.

    Returns:
        (cleaned_df, cleaning_history) where cleaning_history is a list of
        human-readable sentences describing exactly what was done.
    """
    cleaned = df.copy()
    history: list[str] = []

    # Sort operations into the canonical pipeline order.
    sorted_ops = sorted(operations, key=lambda op: _OP_ORDER.get(op.get("type", ""), 99))

    for op in sorted_ops:
        op_type = op.get("type", "")

        if op_type == "drop_columns":
            cols = op.get("columns", [])
            cleaned, n = drop_columns(cleaned, cols)
            if n:
                history.append(f"Removed column(s): {', '.join(cols)}.")

        elif op_type == "empty_columns":
            cleaned, n = remove_empty_columns(cleaned, op.get("tokens") or None)
            if n:
                history.append(f"Removed {n} empty column(s).")

        elif op_type == "normalize_text":
            columns = [str(c) for c in op.get("columns", [])]
            cleaned, n = normalize_text(cleaned, columns or None)
            if n:
                scope = ", ".join(columns) if columns else "all text columns"
                history.append(f"Normalized Unicode and whitespace in {n} cell(s) ({scope}).")

        elif op_type == "split_column":
            column = op.get("column", "")
            names = [str(name) for name in op.get("new_columns", [])]
            cleaned, n = split_column(cleaned, column, op.get("separator", ""), names)
            if n:
                history.append(
                    f"Split {n} row(s) of '{column}' into columns: {', '.join(names)}."
                )

        elif op_type == "merge_columns":
            columns = [str(c) for c in op.get("columns", [])]
            new_column = str(op.get("new_column", ""))
            cleaned, n = merge_columns(
                cleaned,
                columns,
                str(op.get("separator", " ")),
                new_column,
                bool(op.get("drop_source", True)),
            )
            if n:
                history.append(
                    f"Merged {n} row(s) from {', '.join(columns)} into '{new_column}'."
                )

        elif op_type == "regex_replace":
            column = op.get("column", "")
            cleaned, n = regex_replace(
                cleaned, column, op.get("pattern", ""), str(op.get("replacement", ""))
            )
            if n:
                history.append(
                    f"Replaced text matching {op.get('pattern')!r} in {n} cell(s) of '{column}'."
                )

        elif op_type == "trim_whitespace":
            cleaned, n = trim_whitespace(cleaned)
            if n:
                history.append(f"Trimmed whitespace in {n} cell(s).")

        elif op_type == "duplicates":
            mode = op.get("mode", "first")
            subset = op.get("subset")
            cleaned, n = remove_duplicate_rows(cleaned, mode, subset)
            if n:
                keys = ", ".join(subset) if subset else "the entire row"
                kept = {
                    "first": "kept the first occurrence",
                    "last": "kept the last occurrence",
                    "all": "removed all occurrences",
                }[mode]
                history.append(
                    f"Removed {n} duplicate row(s) matching keys ({keys}); {kept} — "
                    f"{len(cleaned)} row(s) retained."
                )

        elif op_type == "categories":
            column = op.get("column", "")
            mapping = op.get("mapping", {})
            before_values = (
                cleaned[column].dropna().astype(str).str.strip().unique()
                if validate_column(cleaned, column)
                else []
            )
            cleaned, n = standardize_categories(cleaned, column, mapping)
            if n:
                pairs = ", ".join(
                    f"'{_describe_value(k)}' -> '{_describe_value(v)}'"
                    for k, v in list(mapping.items())[:10]
                )
                history.append(f"Standardized {n} value(s) in '{column}': {pairs}.")
                unmapped = _unmapped_categories(before_values, mapping)
                if unmapped:
                    examples = ", ".join(repr(value) for value in unmapped[:5])
                    history.append(
                        f"{len(unmapped)} value(s) in '{column}' had no mapping and were left "
                        f"unchanged (for example: {examples})."
                    )

        elif op_type == "case":
            column = op.get("column", "")
            case_type = op.get("case_type", "")
            cleaned, n = standardize_text_case(cleaned, column, case_type)
            if n:
                history.append(
                    f"Standardized case of {n} value(s) in '{column}' to {case_type}."
                )

        elif op_type == "missing":
            column = op.get("column", "")
            method = op.get("method", "")
            tokens = set(op.get("tokens") or ())
            pre_stats = None
            if validate_column(cleaned, column):
                pre_mask = missing_mask(cleaned[column], tokens or None)
                pre_base = missing_mask(cleaned[column])
                pre_stats = {
                    "token_cells": int((pre_mask & ~pre_base).sum()),
                    "mode_candidates": (
                        cleaned.loc[~pre_mask, cleaned.columns[cleaned.columns.get_loc(column)]]
                        .mode(dropna=True)
                        if method == "mode" else pd.Series(dtype=object)
                    ),
                }
            try:
                cleaned, n = handle_missing_values(
                    cleaned, column, method, op.get("value"), tokens or None
                )
            except (ValueError, TypeError) as exc:
                history.append(f"Fill in '{column}' rejected: {exc} Column left unchanged.")
                continue
            labels = {
                "remove": f"Removed {n} row(s) where '{column}' was missing.",
                "mean": f"Filled {n} missing value(s) in '{column}' using the mean.",
                "median": f"Filled {n} missing value(s) in '{column}' using the median.",
                "mode": f"Filled {n} missing value(s) in '{column}' using the most frequent value.",
                "custom": f"Filled {n} missing value(s) in '{column}' using a custom value.",
            }
            if n and method in labels:
                message = labels[method]
                if pre_stats:
                    if pre_stats["token_cells"]:
                        message += (
                            f" Count includes {pre_stats['token_cells']} missing-token "
                            "cell(s) (e.g. N/A, NULL, -)."
                        )
                    if method == "mode" and len(pre_stats["mode_candidates"]) > 1:
                        tied = ", ".join(
                            str(value) for value in pre_stats["mode_candidates"].iloc[1:4]
                        )
                        message += (
                            f" The most frequent value was tied (used "
                            f"{pre_stats['mode_candidates'].iloc[0]!r}; tied with: {tied})."
                        )
                history.append(message)

        elif op_type == "group_fill":
            column = op.get("column", "")
            groups = [str(c) for c in op.get("group_by", [])]
            method = op.get("method", "")
            cleaned, report = fill_by_group(cleaned, groups, column, method)
            if report["filled"]:
                history.append(
                    f"Filled {report['filled']} missing value(s) in '{column}' from each group "
                    f"of {', '.join(groups)} using {method}."
                )
            if report["unfilled"]:
                examples = ", ".join(repr(value) for value in report["unfilled_groups"][:5])
                note = f" (for example group {examples})" if examples else ""
                history.append(
                    f"{report['unfilled']} value(s) in '{column}' could not be filled: "
                    f"their group has no known value{note}."
                )

        elif op_type == "invalid_date":
            column = op.get("column", "")
            action = op.get("action", "coerce")
            cleaned, n = clean_invalid_dates(
                cleaned,
                column,
                action,
                dayfirst=bool(op.get("dayfirst")),
                date_format=op.get("date_format", ""),
            )
            if n:
                if action == "remove":
                    history.append(f"Removed {n} row(s) with invalid dates in '{column}'.")
                else:
                    history.append(f"Cleared {n} invalid date value(s) in '{column}'.")

        elif op_type == "convert":
            column = op.get("column", "")
            target_type = op.get("target_type", "")
            cleaned, report = convert_column_type(
                cleaned,
                column,
                target_type,
                op.get("invalid_policy", "reject"),
                dayfirst=bool(op.get("dayfirst")),
                number_format=op.get("number_format", ""),
                date_format=op.get("date_format", ""),
                timezone_policy=op.get("timezone_policy", "preserve"),
            )
            history.append(report.describe(column, target_type))
            if report.applied and converted_types is not None:
                converted_types[column] = target_type

        elif op_type == "validate":
            column = op.get("column", "")
            rule = op.get("rule", "")
            action = op.get("action", "report")
            cleaned, report = apply_validation_rule(cleaned, op)
            count = report["count"]
            examples = ", ".join(repr(value) for value in report["examples"])
            detail = f" ({report['details']})" if report["details"] else ""
            if not count:
                history.append(f"Validation passed for '{column}': {rule}{detail}; no violations.")
            elif action == "remove":
                history.append(
                    f"Validation '{rule}' on '{column}' removed {count} violating row(s){detail}."
                )
            elif action == "set_missing":
                history.append(
                    f"Validation '{rule}' on '{column}' set {count} violating value(s) to missing{detail}."
                )
            else:
                history.append(
                    f"Validation '{rule}' on '{column}' found {count} violation(s){detail}; "
                    f"dataset unchanged" + (f" (examples: {examples})." if examples else ".")
                )

    if not history:
        history.append("No changes were needed.")
    return cleaned, history


def normalize_recipe_payload(
    payload, *, max_runs: int = 50, max_operations: int = 200
) -> list[dict]:
    """Validate the JSON envelope and return a JSON-safe recipe."""
    if not isinstance(payload, dict):
        raise ValueError("The recipe file must contain a JSON object.")
    if payload.get("format") != RECIPE_FORMAT:
        raise ValueError(f"Recipe format must be '{RECIPE_FORMAT}'.")
    if payload.get("version") != RECIPE_VERSION:
        raise ValueError(f"Only recipe version {RECIPE_VERSION} is supported.")
    recipe = payload.get("recipe")
    if not isinstance(recipe, list) or not recipe:
        raise ValueError("The recipe must contain at least one run.")
    if len(recipe) > max_runs:
        raise ValueError(f"A recipe can contain at most {max_runs} runs.")

    total = 0
    normalized: list[dict] = []
    for index, run in enumerate(recipe, start=1):
        if not isinstance(run, dict) or set(run) != {"event", "operations"}:
            raise ValueError(f"Recipe run {index} must contain only event and operations.")
        event = run.get("event")
        if event not in ("clean", "select_columns"):
            raise ValueError(f"Recipe run {index} has unknown event '{event}'.")
        operations = run.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError(f"Recipe run {index} must contain at least one operation.")
        if any(not isinstance(operation, dict) for operation in operations):
            raise ValueError(f"Recipe run {index} contains a malformed operation.")
        total += len(operations)
        if total > max_operations:
            raise ValueError(f"A recipe can contain at most {max_operations} operations.")
        normalized.append({
            "event": event,
            "operations": json.loads(json.dumps(operations, ensure_ascii=False)),
        })
    return normalized


def _apply_selection_recipe(
    df: pd.DataFrame, operation: dict, converted_types: dict
) -> tuple[pd.DataFrame, str, list[str]]:
    allowed_keys = {"type", "columns", "renames", "conversions"}
    if set(operation) - allowed_keys or operation.get("type") != "select_columns":
        raise ValueError("Column-selection recipe has unknown parameters.")
    selected = operation.get("columns")
    renames = operation.get("renames", {})
    conversions = operation.get("conversions", {})
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(column, str) or not column for column in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("Column-selection recipe must list unique column names.")
    if not isinstance(renames, dict) or not isinstance(conversions, dict):
        raise ValueError("Column-selection renames and conversions must be objects.")
    missing = [column for column in selected if column not in df.columns]
    if missing:
        raise ValueError("Recipe requires missing column(s): " + ", ".join(missing) + ".")
    if any(
        not isinstance(source, str)
        or source not in selected
        or not isinstance(target, str)
        or not target.strip()
        for source, target in renames.items()
    ):
        raise ValueError("Column-selection recipe contains an invalid rename.")
    if any(
        not isinstance(column, str)
        or column not in selected
        or target not in ("integer", "float", "string", "date", "boolean")
        for column, target in conversions.items()
    ):
        raise ValueError("Column-selection recipe contains an invalid conversion.")
    final_columns = [renames.get(column, column) for column in selected]
    if len(set(final_columns)) != len(final_columns):
        raise ValueError("Column-selection recipe creates duplicate column names.")

    current = df.copy()
    for column, target_type in conversions.items():
        current, report = convert_column_type(current, column, target_type)
        if not report.applied:
            raise ValueError(report.describe(column, target_type))
        converted_types[renames.get(column, column)] = target_type
    current = current[selected].rename(columns=renames).copy()
    extras = [str(column) for column in df.columns if column not in selected]
    summary = f"Kept {len(selected)} column(s)"
    if renames:
        summary += f", renamed {len(renames)}"
    if conversions:
        summary += f", converted {len(conversions)} type(s)"
    summary += "."
    return current, summary, extras


def inspect_recipe(
    df: pd.DataFrame, recipe: list[dict], *, converted_types: dict | None = None
) -> tuple[pd.DataFrame, dict, dict, list[str]]:
    """Dry-run a normalized recipe and return its result and review summary."""
    current = df.copy()
    overrides = dict(converted_types or {})
    rows_before = len(current)
    columns_before = [str(column) for column in current.columns]
    steps: list[dict] = []
    warnings: list[str] = []
    examples: list[dict] = []
    history: list[str] = []

    for run_index, run in enumerate(recipe, start=1):
        operations = run["operations"]
        if run["event"] == "select_columns":
            if len(operations) != 1:
                raise ValueError(f"Recipe run {run_index}: column selection needs one operation.")
            before = current
            current, summary, extras = _apply_selection_recipe(current, operations[0], overrides)
            history.append(summary)
            if extras:
                warnings.append(
                    f"Run {run_index} leaves out {len(extras)} extra dataset column(s): "
                    + ", ".join(extras[:8])
                    + ("." if len(extras) <= 8 else ", …")
                )
            steps.append({
                "label": f"Run {run_index}: select columns",
                "affected": len(before.columns) - len(current.columns),
                "rows_removed": 0,
                "cells_changed": 0,
                "columns_removed": extras,
                "columns_added": [name for name in current.columns if name not in before.columns],
                "types_changed": list(operations[0].get("conversions", {})),
                "violations": 0,
            })
            continue

        errors = validate_plan(current, operations)
        if errors:
            raise ValueError(f"Recipe run {run_index}: " + " ".join(errors))
        preview = preview_plan(current, operations)
        current, run_history = apply_cleaning_operations(
            current, operations, converted_types=overrides
        )
        history.extend(run_history)
        for step in preview["steps"]:
            steps.append({**step, "label": f"Run {run_index}: {step['label']}"})
        warnings.extend(f"Run {run_index}: {warning}" for warning in preview["warnings"])
        for example in preview["examples"]:
            if len(examples) < 5:
                examples.append(example)

    overrides = {name: target for name, target in overrides.items() if name in current.columns}
    report = {
        "runs": len(recipe),
        "rows_before": int(rows_before),
        "rows_after": int(len(current)),
        "columns_before": len(columns_before),
        "columns_after": int(current.shape[1]),
        "steps": steps,
        "warnings": warnings,
        "examples": examples,
    }
    return current, report, overrides, history


def replay_recipe(df: pd.DataFrame, recipe: list[dict]) -> pd.DataFrame:
    """Replay normalized recipe runs against a compatible dataset."""
    current, _, _, _ = inspect_recipe(df, recipe)
    return current
