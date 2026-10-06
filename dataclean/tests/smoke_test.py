"""End-to-end smoke test of the whole DataClean flow (run manually, not part of CI)."""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app

app = create_app()
app.config["TESTING"] = True

MESSY_FILE = Path(__file__).parent / "sample_messy_data.csv"

failures = []


def check(name, condition, extra=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(name)
    print(f"[{status}] {name}" + (f" -> {extra}" if extra and not condition else ""))


with app.test_client() as client:

    # Seed the session + obtain a valid CSRF token by hitting any GET page.
    client.get("/upload")
    with client.session_transaction() as sess:
        csrf = sess.get("csrf_token", "test-token")

    # 1. Home page
    response = client.get("/")
    check("home page renders", response.status_code == 200 and b"DataClean" in response.data)

    # 2. Unsupported file type is rejected
    response = client.post(
        "/upload",
        data={"file": (io.BytesIO(b"hello"), "notes.txt"), "csrf_token": csrf},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    check("unsupported file rejected", b"Unsupported file type" in response.data)

    # 3. Corrupted CSV shows a friendly error
    response = client.post(
        "/upload",
        data={"file": (io.BytesIO(b"\x00\x01\x02garble"), "broken.csv"), "csrf_token": csrf},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    check(
        "corrupted file rejected",
        response.status_code == 200
        and (b"couldn't read this file" in response.data or b"no data rows" in response.data),
    )

    # 4. Valid upload redirects to the dataset page
    with open(MESSY_FILE, "rb") as fh:
        response = client.post(
            "/upload",
            data={"file": (fh, "sample_messy_data.csv"), "csrf_token": csrf},
            content_type="multipart/form-data",
        )
    check("valid upload redirects", response.status_code == 302)

    # 5. Dataset overview page
    response = client.get("/dataset")
    check("dataset page renders", response.status_code == 200 and b"Column Information" in response.data)
    check("overview shows 20 rows", b"20" in response.data and b"StudentID" in response.data)
    check("column selection panel renders", b"Column Selection" in response.data)
    check("column checkboxes present", b'name="selected_columns"' in response.data)

    # 6. Analysis/cleaning page
    response = client.get("/dataset/analysis")
    check("cleaning page renders", response.status_code == 200 and b"Cleaning Operations" in response.data)
    check("duplicates detected", b"Duplicate Records" in response.data)
    check("inconsistent text detected", b"Text Casing" in response.data)
    check("invalid email detected", b"do not look like valid email" in response.data)

    # 7. Apply cleaning: duplicates + trim + median CGPA + title-case Department + convert Age
    form = {
        "csrf_token": csrf,
        "op_duplicates": "1",
        "op_trim": "1",
        "misscol_1": "CGPA",
        "missing_1": "median",
        "misscol_2": "Age",
        "missing_2": "remove",
        "textcol_1": "Department",
        "case_1": "title",
        "convert_column": "StudentID",
        "convert_type": "integer",
    }
    response = client.post("/clean", data=form, follow_redirects=False)
    check("clean redirects to result", response.status_code == 302)

    response = client.get("/cleaning/result")
    check("result page renders", response.status_code == 200 and b"Cleaning" in response.data)
    check("history mentions duplicates", b"Removed 2 duplicate row(s)" in response.data)
    check("history mentions median fill", b"median" in response.data)
    check("history mentions case fix", b"to title" in response.data)
    check("history mentions age removal", b"&#39;Age&#39; was missing" in response.data)

    # 8. Downloads
    response = client.get("/download?format=csv")
    check("csv download works", response.status_code == 200 and b"StudentID" in response.data)
    response = client.get("/download?format=excel")
    check("excel download works", response.status_code == 200 and response.data[:2] == b"PK")

    # 9. Download without a processed file 404s
    with app.test_client() as fresh_client:
        response = fresh_client.get("/download")
        check("download with no session 404s", response.status_code == 404)

print()
if failures:
    print(f"{len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All checks passed.")
