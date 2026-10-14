"""Comprehensive unit and integration tests verifying all functional inconsistency fixes."""

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from services import analysis_service, cleaning_service, file_service

failures = []


def check(name, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(name)
    print(f"[{status}] {name}" + (f" -> {extra}" if extra and not condition else ""))


# --------------------------------------------------------------------------
# 1. Test Category Standardization NaN bug
# --------------------------------------------------------------------------
df_cat = pd.DataFrame({"gender": ["male", np.nan, "female", np.nan, "male"]})
cleaned_cat, count = cleaning_service.standardize_categories(df_cat, "gender", {"male": "Male"})
check(
    "standardize_categories does not count NaNs as changed",
    count == 2,
    f"expected 2 changes, got {count}",
)
check(
    "standardize_categories correctly replaces values",
    cleaned_cat["gender"].iloc[0] == "Male" and pd.isna(cleaned_cat["gender"].iloc[1]),
)

# --------------------------------------------------------------------------
# 2. Test Text Case Standardization does NOT strip whitespace
# --------------------------------------------------------------------------
df_case = pd.DataFrame({"dept": [" cs ", "cs", "CS"]})
cleaned_case, case_count = cleaning_service.standardize_text_case(df_case, "dept", "title")
check(
    "standardize_text_case preserves whitespace without stripping",
    cleaned_case["dept"].iloc[0] == " Cs ",
    f"got {cleaned_case['dept'].iloc[0]!r}",
)
check(
    "standardize_text_case accurate change count",
    case_count == 3,  # ' cs ' -> ' Cs ', 'cs' -> 'Cs', 'CS' -> 'Cs'
    f"got {case_count}",
)


# --------------------------------------------------------------------------
# 3. Test Numeric Custom Fill Preserves Numeric Type
# --------------------------------------------------------------------------
df_num = pd.DataFrame({"age": [20.0, np.nan, 30.0]})
cleaned_num, num_count = cleaning_service.handle_missing_values(df_num, "age", "custom", "25")
check("custom missing fill on numeric column count", num_count == 1)
check(
    "custom missing fill retains numeric dtype",
    pd.api.types.is_numeric_dtype(cleaned_num["age"]),
    f"dtype is {cleaned_num['age'].dtype}",
)
check("custom missing filled with numeric 25", cleaned_num["age"].iloc[1] == 25.0)

df_bool = pd.DataFrame({"active": ["yes", "No", "1", "0", "unknown"]})
cleaned_bool, bool_count = cleaning_service.convert_column_type(
    df_bool, "active", "boolean"
)
check("boolean conversion recognizes common values", bool_count == 4)
check(
    "boolean conversion marks invalid values missing",
    pd.isna(cleaned_bool["active"].iloc[4]),
)

# --------------------------------------------------------------------------
# 4. Test Invalid Date Cleaning
# --------------------------------------------------------------------------
df_date = pd.DataFrame({"date_col": ["2023-01-01", "not-a-date", "2023-05-10"]})
cleaned_date_coerce, n_coerce = cleaning_service.clean_invalid_dates(df_date, "date_col", "coerce")
check("clean_invalid_dates coerce count", n_coerce == 1)
check("clean_invalid_dates coerce sets NA", pd.isna(cleaned_date_coerce["date_col"].iloc[1]))

cleaned_date_remove, n_remove = cleaning_service.clean_invalid_dates(df_date, "date_col", "remove")
check("clean_invalid_dates remove count", n_remove == 1)
check("clean_invalid_dates removes row", len(cleaned_date_remove) == 2)

# --------------------------------------------------------------------------
# 5. Test Date Hints False Positives Prevention
# --------------------------------------------------------------------------
df_false_dates = pd.DataFrame({
    "candidate_name": ["Alice", "Bob", "Charlie", "David"],
    "centimeter_measurement": [12.5, 14.2, 10.1, 15.0],
})
basic_issues = analysis_service.detect_basic_issues(df_false_dates)
date_issues = [i for i in basic_issues if i["issue"] == "invalid_date"]
check(
    "date hint does not flag candidate or centimeter as date",
    len(date_issues) == 0,
    f"detected unexpected date issues: {date_issues}",
)

# --------------------------------------------------------------------------
# 6. Test Specific Column Drop
# --------------------------------------------------------------------------
df_drop = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
cleaned_drop, n_dropped = cleaning_service.drop_columns(df_drop, ["b", "c"])
check("drop_columns removes specified columns", n_dropped == 2 and list(cleaned_drop.columns) == ["a"])

# --------------------------------------------------------------------------
# 7. Integration: Multi-step "Clean More" Workflow via Flask Test Client
# --------------------------------------------------------------------------
app = create_app()
app.config["TESTING"] = True
messy_csv = Path(__file__).parent / "sample_messy_data.csv"

# CSRF token must come from the same client (server-side sessions), so seed it in-flow
with app.test_client() as client:
    client.get("/upload")
    with client.session_transaction() as sess:
        csrf = sess.get("csrf_token", "test-token")

    with open(messy_csv, "rb") as fh:
        client.post("/upload", data={"file": (fh, "sample_messy_data.csv"), "csrf_token": csrf})

    # Pass 1: Trim whitespace & deduplicate
    res1 = client.post("/clean", data={"op_duplicates": "1", "op_trim": "1", "csrf_token": csrf}, follow_redirects=True)
    check("pass 1 clean succeeds", res1.status_code == 200 and b"Removed 2 duplicate row(s)" in res1.data)

    # Inspect overview/analysis for "Clean More"
    res_analysis = client.get("/dataset/analysis")
    check("clean more loads cleaned state", res_analysis.status_code == 200)

    # Pass 2: Fill CGPA with median on top of pass 1
    res2 = client.post(
        "/clean",
        data={
            "misscol_1": "CGPA",
            "missing_1": "median",
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    check("pass 2 clean succeeds", res2.status_code == 200)
    # Check that history accumulated both pass 1 and pass 2!
    check(
        "history accumulated pass 1 and pass 2",
        b"Removed 2 duplicate row(s)" in res2.data and b"median" in res2.data,
    )

    # Reset to original upload
    res_reset = client.get("/dataset/reset", follow_redirects=True)
    check(
        "reset redirects to dataset overview",
        res_reset.status_code == 200 and b"Column Information" in res_reset.data,
    )
    # Overview after reset should have the original 20 rows
    check("reset restored original 20 rows", b"20" in res_reset.data)

# --------------------------------------------------------------------------
# 8. Explorer datatype selection persists into Cleaning
# --------------------------------------------------------------------------
with app.test_client() as client:
    client.get("/upload")
    with client.session_transaction() as sess:
        type_csrf = sess.get("csrf_token", "test-token")

    with open(messy_csv, "rb") as fh:
        client.post(
            "/upload",
            data={"file": (fh, "type_test.csv"), "csrf_token": type_csrf},
            content_type="multipart/form-data",
        )

    columns = [
        "StudentID", "Name", "Age", "Gender", "Email", "Department",
        "CGPA", "EnrollmentDate",
    ]
    type_response = client.post(
        "/dataset/select-columns",
        data={
            "csrf_token": type_csrf,
            "selected_columns": columns,
            "type_StudentID": "Text",
        },
    )
    check("datatype selection redirects to Cleaning", type_response.status_code == 302)

    with client.session_transaction() as sess:
        saved_types = sess.get("column_type_overrides", {})
    check(
        "datatype selection is stored",
        saved_types.get("StudentID") == "string",
        f"stored overrides: {saved_types}",
    )

    type_page = client.get("/dataset/analysis").get_data(as_text=True)
    student_type_area = type_page[type_page.find("StudentID"):type_page.find("StudentID") + 500]
    check("selected datatype appears in Cleaning", "text" in student_type_area.lower())

    client.get("/dataset/reset")
    with client.session_transaction() as sess:
        check("reset clears datatype selections", "column_type_overrides" not in sess)

# --------------------------------------------------------------------------
# 9. Ending a session deletes its files and state
# --------------------------------------------------------------------------
with app.test_client() as client:
    client.get("/upload")
    with client.session_transaction() as sess:
        end_csrf = sess.get("csrf_token", "test-token")

    with open(messy_csv, "rb") as fh:
        client.post(
            "/upload",
            data={"file": (fh, "end_session_test.csv"), "csrf_token": end_csrf},
            content_type="multipart/form-data",
        )

    client.post(
        "/clean",
        data={"op_trim": "1", "csrf_token": end_csrf},
        follow_redirects=True,
    )
    client.get("/download?format=excel")

    workspace_html = client.get("/dataset").data
    check(
        "active workspace offers end session",
        b'id="end-session-btn"' in workspace_html
        and b'id="end-session-modal"' in workspace_html,
    )

    with client.session_transaction() as sess:
        session_paths = {
            Path(path)
            for key in ("upload_path", "current_path", "processed_path", "excel_path")
            if (path := sess.get(key))
        }
    check("session files exist before ending", session_paths and all(path.exists() for path in session_paths))

    end_response = client.post(
        "/session/end",
        data={"csrf_token": end_csrf},
        follow_redirects=False,
    )
    check(
        "end session redirects to fresh upload",
        end_response.status_code == 302 and end_response.headers["Location"].endswith("/upload"),
    )
    check("end session deletes all files", all(not path.exists() for path in session_paths))
    with client.session_transaction() as sess:
        check("end session clears progress", "original_filename" not in sess)

with app.test_client() as client:
    client.get("/upload")
    with client.session_transaction() as sess:
        beacon_csrf = sess.get("csrf_token", "test-token")
    with open(messy_csv, "rb") as fh:
        client.post(
            "/upload",
            data={"file": (fh, "beacon_end_test.csv"), "csrf_token": beacon_csrf},
            content_type="multipart/form-data",
        )
    with client.session_transaction() as sess:
        beacon_upload = Path(sess["upload_path"])
    beacon_response = client.post(
        "/session/end",
        data={"csrf_token": beacon_csrf, "beacon": "1"},
    )
    check(
        "tab-close beacon ends session",
        beacon_response.status_code == 204 and not beacon_upload.exists(),
    )

# --------------------------------------------------------------------------
# 10. Test Failed Upload File Cleanup
# --------------------------------------------------------------------------
with app.test_client() as client:
    client.get("/upload")
    with client.session_transaction() as sess:
        failed_upload_csrf = sess.get("csrf_token", "test-token")
    # Upload corrupted file
    client.post(
        "/upload",
        data={"file": (io.BytesIO(b"\x00\x01broken"), "unparseable_test.csv"), "csrf_token": failed_upload_csrf},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    # Verify no unparseable_test.csv lingers in uploads
    broken_exists = (app.config["UPLOAD_FOLDER"] / "unparseable_test.csv").exists()
    check("failed upload unlinks file from disk", not broken_exists)

print()
if failures:
    print(f"FAILED: {len(failures)} test(s) failed: {failures}")
    sys.exit(1)
print("All regression tests passed successfully!")
