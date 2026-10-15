"""Refresh the self-hosted Google Fonts; run only when updating font assets."""

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1] / "static"
ICONS = (
    "add,arrow_forward,auto_fix_high,check_circle,cloud_upload,close,content_copy,"
    "dark_mode,data_alert,data_object,delete_forever,delete_sweep,difference,download,"
    "error,expand_more,history,home,info,light_mode,lock,logout,menu,play_arrow,"
    "query_stats,rule,schema,swap_horiz,sync,table_chart,table_rows,table_view,"
    "task_alt,text_format,tips_and_updates,tune,upload_file,verified,verified_user,"
    "view_column,warning"
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": UA}), timeout=60) as response:
        return response.read()


def main():
    folder = ROOT / "fonts"
    folder.mkdir(exist_ok=True)
    sources = [
        "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400..700"
        "&family=IBM+Plex+Mono:wght@400;500;600&display=swap",
        "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined"
        f"&icon_names={ICONS}&display=swap",
    ]
    faces, downloads = [], []
    for source in sources:
        css = fetch(source).decode()
        for subset, face in re.findall(r"(?:/\* ([\w-]+) \*/\s*)?(@font-face\s*\{[^}]+\})", css):
            family = re.search(r"font-family: '([^']+)'", face)[1]
            weight = re.search(r"font-weight: ([^;]+)", face)[1].replace(" ", "-")
            url = re.search(r"url\(([^)]+)\)", face)[1]
            name = f"{family.lower().replace(' ', '-')}-{weight}-{subset or 'icons'}.woff2"
            data = (url, folder / name)
            downloads.append(data)
            faces.append(face.replace(url, f"/static/fonts/{name}"))

    def save_font(item):
        url, path = item
        data = fetch(url)
        if not data.startswith(b"wOF2"):
            raise ValueError(f"Expected WOFF2 for {url}")
        path.write_bytes(data)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save_font, downloads))
    (ROOT / "css" / "fonts.css").write_text("\n".join(faces) + "\n", encoding="utf-8")
    for name, url in {
        "OFL-IBM-Plex-Sans.txt": "https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexsans/OFL.txt",
        "OFL-IBM-Plex-Mono.txt": "https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexmono/OFL.txt",
        "LICENSE-Material-Symbols.txt": "https://raw.githubusercontent.com/google/material-design-icons/master/LICENSE",
    }.items():
        (folder / name).write_bytes(fetch(url))
    print(f"Vendored {len(downloads)} WOFF2 subsets and their licenses.")


if __name__ == "__main__":
    main()
